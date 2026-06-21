#!/usr/bin/env python3
"""Generate a scip-clang-ready compile_commands.json from a bazel C++ build.

Bazel repos rarely ship a compile DB, and the obvious conversions don't work with
scip-clang. This script encodes the fixes learned ingesting torch_tpu (a mixed
C++/Python PyTorch-XLA backend):

  1. Source the actions from `bazel aquery 'mnemonic("CppCompile", <targets>)'
     --output=jsonproto` (materialize generated headers first with a full
     `bazel build <targets>`, or scip-clang errors on missing llvm/Config headers).
  2. SPLIT combined "-isystem path" tokens into two argv elements — bazel aquery
     emits them as a single string, which clang treats as one unknown argument
     (the symptom is "'cstddef' file not found" despite a correct -isystem path).
  3. ABSOLUTIZE include paths against the bazel execroot, so they resolve no matter
     what `directory` is.
  4. Set `directory` = the REAL repo root and keep `file` repo-relative, so
     scip-clang treats the sources as IN-PROJECT and emits documents for them —
     and naturally DROPS the external torch/XLA/llvm headers (their canonical
     paths fall outside the repo root). No separate filtering needed.
  5. Strip output-generating flags (-MD/-MF/-o/-frandom-seed) whose targets are
     read-only bazel-out paths.

Then run scip-clang with a raised --ipc-size-hint-bytes (these TUs pull the whole
torch header graph; messages exceed the 1 MB IPC default) and a long
--receive-timeout-seconds.

Usage:
  bazel build <targets>                    # materialize generated headers
  bazel aquery 'mnemonic("CppCompile", <targets>)' --output=jsonproto > aq.json
  scripts/bazel_compile_commands.py --aquery aq.json \\
      --execroot "$(bazel info execution_root)" --repo-root "$(pwd)" \\
      --output compile_commands.json
"""
from __future__ import annotations

import argparse
import json
import os

SRC_EXT = (".cc", ".cpp", ".cxx", ".c", ".cu", ".C")
SEP_PATH_FLAGS = {"-I", "-iquote", "-isystem", "-idirafter", "-isysroot",
                  "--sysroot", "-include", "-B", "-iprefix", "-internal-isystem"}
ATTACHED_PREFIXES = ("-I", "-iquote", "-isystem", "-idirafter", "-isysroot=",
                     "--sysroot=", "-B")
DROP_FLAGS = {"-MD", "-MMD", "-MG", "-MP"}
DROP_WITH_ARG = {"-MF", "-MT", "-MQ", "-o", "--serialize-diagnostics"}


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
        if a in SEP_PATH_FLAGS and i + 1 < len(args):           # "-isystem", "path"
            out += [a, _abs(args[i + 1], execroot)]; i += 2; continue
        split = False
        for flag in SEP_PATH_FLAGS:                             # "-isystem path" (one token)
            if a.startswith(flag + " "):
                out += [flag, _abs(a[len(flag) + 1:], execroot)]; split = True; break
        if split:
            i += 1; continue
        for pre in ATTACHED_PREFIXES:                           # "-Ipath"
            if a.startswith(pre) and len(a) > len(pre):
                a = pre + _abs(a[len(pre):], execroot); break
        out.append(a); i += 1
    return out


def _strip_outputs(args: list[str]) -> list[str]:
    out, i = [], 0
    while i < len(args):
        a = args[i]
        if a in DROP_FLAGS or a.startswith("-frandom-seed="):
            i += 1; continue
        if a in DROP_WITH_ARG and i + 1 < len(args):
            i += 2; continue
        out.append(a); i += 1
    return out


def convert(aquery: dict, execroot: str, repo_root: str) -> list[dict]:
    entries = []
    for a in aquery.get("actions", []):
        args = _strip_outputs(list(a.get("arguments", [])))
        src = next((x for i, x in enumerate(args)
                    if x.endswith(SRC_EXT) and not x.startswith("-") and args[i - 1] != "-o"),
                   None)
        if src is None:
            continue
        args = _absolutize(args, execroot)
        if not os.path.isabs(args[0]):
            args[0] = _abs(args[0], execroot)
        # file repo-relative, directory = real repo root → sources stay in-project.
        entries.append({"file": src, "arguments": args, "directory": repo_root})
    return entries


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--aquery", required=True, help="bazel aquery --output=jsonproto file")
    ap.add_argument("--execroot", required=True, help="bazel info execution_root")
    ap.add_argument("--repo-root", required=True, help="real source repo root")
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    aq = json.load(open(args.aquery))
    entries = convert(aq, args.execroot, args.repo_root)
    json.dump(entries, open(args.output, "w"))
    own = sum(1 for e in entries if not e["file"].startswith(("external/", "bazel-out/")))
    print(f"wrote {len(entries)} entries ({own} repo-own) → {args.output}")


if __name__ == "__main__":
    main()
