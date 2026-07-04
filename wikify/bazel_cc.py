"""Auto-generate a scip-clang compile DB from a bazel build (Stage 1, C++ path).

Bazel repos rarely ship a `compile_commands.json`, and the naive conversion of
`bazel aquery` output does not work with scip-clang. This module encodes the fixes
learned ingesting torch_tpu so that a mixed bazel repo indexes with ONE
`wikify prepare` (config key `bazel_targets:`):

  1. `bazel build <targets>` to materialize ALL transitive generated headers
     (`compilation_prerequisites` alone misses deep ones like llvm/Config/*).
  2. `bazel aquery 'mnemonic("CppCompile", <targets>)' --output=jsonproto` for the
     compile actions.
  3. Convert → compile_commands.json:
     - SPLIT combined "-isystem path" tokens (aquery emits flag+path as one argv
       string; clang treats it as one unknown arg → "<cstddef> not found").
     - ABSOLUTIZE include paths against the bazel execroot.
     - `directory` = the REAL repo root, `file` repo-relative — so scip-clang
       treats sources as in-project (emits their docs) AND drops external
       torch/XLA/llvm headers (their canonical paths fall outside the repo root).
     - STRIP output flags (-MD/-MF/-o/...) whose targets are read-only bazel-out.

`scip_index.run_clang_indexer` then runs scip-clang with a raised IPC buffer and a
long receive timeout (these TUs pull the whole torch header graph).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

SRC_EXT = (".cc", ".cpp", ".cxx", ".c", ".cu", ".C")
_SEP_PATH_FLAGS = {"-I", "-iquote", "-isystem", "-idirafter", "-isysroot",
                   "--sysroot", "-include", "-B", "-iprefix", "-internal-isystem"}
_ATTACHED_PREFIXES = ("-I", "-iquote", "-isystem", "-idirafter", "-isysroot=",
                      "--sysroot=", "-B")
_DROP_FLAGS = {"-MD", "-MMD", "-MG", "-MP"}
_DROP_WITH_ARG = {"-MF", "-MT", "-MQ", "-o", "--serialize-diagnostics"}


# --------------------------------------------------------------------------- #
# aquery → compile_commands conversion (pure)
# --------------------------------------------------------------------------- #
def _abs(p: str, execroot: str) -> str:
    if p and not os.path.isabs(p):
        cand = os.path.join(execroot, p)
        if os.path.exists(cand):
            return cand
    return p


def _absolutize(args: list[str], execroot: str) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in _SEP_PATH_FLAGS and i + 1 < len(args):          # "-isystem", "path"
            out += [a, _abs(args[i + 1], execroot)]; i += 2; continue
        split = False
        for flag in _SEP_PATH_FLAGS:                            # "-isystem path" (1 token)
            if a.startswith(flag + " "):
                out += [flag, _abs(a[len(flag) + 1:], execroot)]; split = True; break
        if split:
            i += 1; continue
        for pre in _ATTACHED_PREFIXES:                          # "-Ipath"
            if a.startswith(pre) and len(a) > len(pre):
                a = pre + _abs(a[len(pre):], execroot); break
        out.append(a); i += 1
    return out


def _strip_outputs(args: list[str]) -> list[str]:
    out, i = [], 0
    while i < len(args):
        a = args[i]
        if a in _DROP_FLAGS or a.startswith("-frandom-seed="):
            i += 1; continue
        if a in _DROP_WITH_ARG and i + 1 < len(args):
            i += 2; continue
        out.append(a); i += 1
    return out


def convert(aquery: dict, execroot: str, repo_root: str) -> list[dict]:
    """Convert parsed `bazel aquery --output=jsonproto` → compile_commands entries."""
    entries: list[dict] = []
    for a in aquery.get("actions", []):
        args = _strip_outputs(list(a.get("arguments", [])))
        src = next((x for i, x in enumerate(args)
                    if x.endswith(SRC_EXT) and not x.startswith("-") and args[i - 1] != "-o"),
                   None)
        if src is None:
            continue
        args = _absolutize(args, execroot)
        if args and not os.path.isabs(args[0]):
            args[0] = _abs(args[0], execroot)
        entries.append({"file": src, "arguments": args, "directory": str(repo_root)})
    return entries


# --------------------------------------------------------------------------- #
# Orchestration (runs bazel)
# --------------------------------------------------------------------------- #
def _bazel(repo_dir: Path, args: list[str], bazel: str, allow_partial: bool = False) -> str:
    proc = subprocess.run([bazel, *args], cwd=repo_dir, capture_output=True, text=True)
    if proc.returncode != 0:
        # `--keep_going` still exits non-zero when SOME targets fail to analyze/build
        # (e.g. a bzl_library-only target with an unreachable internal-only dep). That's
        # fine for our purposes: we only need the CppCompile actions for targets that DID
        # build/analyze. Two signals of "partially succeeded, not totally failed":
        # `aquery --output=jsonproto` puts its actual action-list data on stdout (bazel's
        # progress/error narration always goes to stderr), so non-empty stdout means usable
        # data came back; a plain `build` writes nothing to stdout either way, so fall back
        # to the "N of M top-level targets" summary line bazel prints to stderr on --keep_going.
        if allow_partial and (proc.stdout.strip() or "Build succeeded for only" in proc.stderr):
            return proc.stdout
        raise RuntimeError(f"`{bazel} {' '.join(args[:3])} …` failed "
                           f"({proc.returncode}):\n{proc.stderr[-2000:]}")
    return proc.stdout


def _remove_convenience_symlinks(repo_dir: Path) -> None:
    """Remove bazel's `bazel-<workspace>`/`bazel-bin`/`bazel-out`/`bazel-testlogs` convenience
    symlinks from the repo root after a build.

    These symlinks point at the (huge) output tree, and a subsequent scip-python run walks the
    whole workspace for project discovery — pyright hits its ~10s enumeration timeout on that
    tree and silently reports zero project files (every shard "succeeds" empty, so the sharded
    indexer sees "all shards failed"). The symlinks are pure convenience: `aquery`'s output
    already gets absolutized against `bazel info execution_root`, so removing them doesn't
    affect this module's own output, and bazel recreates them on the next `bazel build`."""
    for entry in repo_dir.iterdir():
        if entry.is_symlink() and entry.name.startswith("bazel-"):
            entry.unlink()


def generate_compile_db(
    repo_dir: str | Path,
    targets: str,
    output_path: str | Path,
    bazel: str = "bazel",
) -> Path:
    """Build, aquery, and convert → write a scip-clang-ready compile_commands.json.

    ``targets`` is a bazel target pattern, e.g. ``//torch_tpu/...``. Returns the
    written compile-DB path. The full ``bazel build`` is the slow step (it must
    generate every transitive header); it is cached on re-runs."""
    repo_dir = Path(repo_dir)
    output_path = Path(output_path)
    # 1) materialize all generated headers (a full build; cached after first run).
    #    --keep_going: don't let one broken target (e.g. an internal-only bzl_library dep)
    #    abort header materialization for the rest of the tree.
    _bazel(repo_dir, ["build", "--keep_going", targets], bazel, allow_partial=True)
    # 2) execroot (last non-empty stdout line; INFO goes to stderr).
    execroot = [ln for ln in _bazel(repo_dir, ["info", "execution_root"], bazel)
                .splitlines() if ln.strip()][-1].strip()
    # 3) compile actions. --keep_going: same broken-target tolerance as the build step above;
    #    aquery expands the same target pattern and hits the same analysis error otherwise.
    aq = _bazel(repo_dir, ["aquery", "--keep_going", f'mnemonic("CppCompile", {targets})',
                           "--output=jsonproto"], bazel, allow_partial=True)
    _remove_convenience_symlinks(repo_dir)
    entries = convert(json.loads(aq), execroot, str(repo_dir))
    if not entries:
        raise RuntimeError(f"no CppCompile actions for {targets} (nothing to index)")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(entries), encoding="utf-8")
    return output_path
