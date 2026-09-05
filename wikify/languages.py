"""Language registry + detection + on-demand indexer install (design.md Stage 1, §"languages").

wikify grounds on SCIP, which is language-neutral — everything downstream (graph, monikers,
catalogs, coverage, lint) reads the SCIP symbol table, not a per-language AST. So adding a
language is one entry here plus a thin ``run_*`` in ``scip_index``. Python (scip-python) and C++
(scip-clang) ship with ``setup-vendor.sh``; the **TS/JS, Go, Rust indexers are NOT installed by
default** — when a repo contains one of those languages, ``prepare`` detects it and *asks the user
to install* the indexer (``ensure_indexer``), keeping the base install light.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import typer

from . import scip_index


@dataclass(frozen=True)
class Lang:
    key: str
    label: str
    exts: tuple[str, ...]
    markers: tuple[str, ...]      # root files that unambiguously mark the language
    bin: str                      # the indexer binary on PATH
    install: str                  # copy-pasteable install command (shown when missing)
    scip_suffix: str              # ``<slug><suffix>`` in .cache/scip/ (".scip" = python default)
    run: Callable[[Path, Path], Path] | None  # run_fn(repo_dir, out_path); None = special-cased


LANGS: dict[str, Lang] = {
    "python": Lang(
        "python", "Python", (".py",), ("pyproject.toml", "setup.py", "setup.cfg"),
        "scip-python", "npm i -g @sourcegraph/scip-python", ".scip", None),
    "cpp": Lang(
        "cpp", "C/C++", (".cpp", ".cc", ".cxx", ".hpp"), ("CMakeLists.txt", "BUILD.bazel"),
        "scip-clang", "(downloaded by scripts/setup-vendor.sh)", ".cpp.scip", None),
    "typescript": Lang(
        "typescript", "TypeScript/JavaScript",
        (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx"), ("tsconfig.json", "package.json"),
        "scip-typescript", "npm i -g @sourcegraph/scip-typescript", ".ts.scip",
        lambda d, o: scip_index.run_scip_typescript(d, o)),
    "go": Lang(
        "go", "Go", (".go",), ("go.mod",),
        "scip-go", "go install github.com/sourcegraph/scip-go/cmd/scip-go@latest", ".go.scip",
        lambda d, o: scip_index.run_scip_go(d, o)),
    "rust": Lang(
        "rust", "Rust", (".rs",), ("Cargo.toml",),
        "rust-analyzer",
        # rustup when available; else the standalone release binary → ~/.local/bin
        # (arch/OS resolved by the shell, so this works on a box with no Rust toolchain).
        'rustup component add rust-analyzer 2>/dev/null || '
        '(mkdir -p ~/.local/bin && curl -fsSL "https://github.com/rust-lang/rust-analyzer'
        '/releases/latest/download/rust-analyzer-$(uname -m)-$(case $(uname -s) in '
        'Darwin) echo apple-darwin;; *) echo unknown-linux-gnu;; esac).gz" '
        '| gunzip -c > ~/.local/bin/rust-analyzer && chmod +x ~/.local/bin/rust-analyzer)',
        ".rust.scip", lambda d, o: scip_index.run_rust_analyzer(d, o)),
}

# Where language installers drop binaries; prepended to PATH after an install so the
# fresh binary resolves in this process (e.g. `go install` → ~/go/bin, the rust-analyzer
# fallback → ~/.local/bin) without requiring a shell restart.
_INSTALL_BIN_DIRS = ("~/.local/bin", "~/go/bin", "~/.cargo/bin", "~/.wikify/vendor/bin")

# Languages ``prepare`` auto-runs from detection. Python has its own sharding/AST-fallback path
# and C++ needs a compile DB, so both are handled specially in cli.py; these three are uniform
# "detect → ensure indexer → run".
AUTO_RUN: tuple[str, ...] = ("typescript", "go", "rust")

_MIN_FILES = 3
_SKIP_DIRS = {".git", "node_modules", "third_party", "vendor", "target",
              "build", "dist", ".venv", "venv", "__pycache__"}
_WALK_CAP = 20000  # bound detection cost on huge repos (markers catch the common case first)


def _has_ext(repo: Path, exts: tuple[str, ...]) -> bool:
    scanned = 0
    hits = 0
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
        for f in files:
            scanned += 1
            if f.endswith(exts):
                hits += 1
                if hits >= _MIN_FILES:
                    return True
            if scanned >= _WALK_CAP:
                return False
    return False


def detect_languages(repo_dir: str | Path) -> list[str]:
    """Languages present in the repo — a root marker file, or ≥ _MIN_FILES source files."""
    repo = Path(repo_dir)
    found: list[str] = []
    for key, lang in LANGS.items():
        if any((repo / m).exists() for m in lang.markers) or _has_ext(repo, lang.exts):
            found.append(key)
    return found


def scip_path(cache_dir: str | Path, slug: str, key: str) -> Path:
    return Path(cache_dir) / "scip" / f"{slug}{LANGS[key].scip_suffix}"


def _extend_path_with_install_dirs() -> None:
    """Prepend the standard installer bin dirs to PATH (idempotent)."""
    parts = os.environ.get("PATH", "").split(os.pathsep)
    for d in _INSTALL_BIN_DIRS:
        expanded = os.path.expanduser(d)
        if expanded not in parts:
            parts.insert(0, expanded)
    os.environ["PATH"] = os.pathsep.join(parts)


def ensure_indexer(lang: Lang, auto: bool = True) -> bool:
    """True if ``lang.bin`` is available — installing it on demand when it isn't.

    Missing + ``auto`` (the default; ``prepare --no-install-indexers`` disables):
    run the registry's install command, ANNOUNCED, never silent — the command is
    echoed before it runs and its outcome after. Missing + not ``auto``: print the
    guidance and skip this language."""
    if shutil.which(lang.bin):
        return True
    _extend_path_with_install_dirs()          # a previous run may have installed it
    if shutil.which(lang.bin):
        return True
    typer.echo(f"\n⚠  {lang.label} found in the repo, but `{lang.bin}` is not installed.", err=True)
    if not auto:
        typer.echo(f"   install:  {lang.install}", err=True)
        typer.echo(f"   skipping {lang.label} (--no-install-indexers) — install it and "
                   f"re-run `wikify prepare`.", err=True)
        return False
    typer.echo(f"   installing automatically (disable with --no-install-indexers):")
    typer.echo(f"   $ {lang.install}")
    rc = subprocess.run(lang.install, shell=True).returncode
    _extend_path_with_install_dirs()
    if rc == 0 and shutil.which(lang.bin):
        typer.echo(f"   installed `{lang.bin}`.")
        return True
    typer.echo(f"   install failed (rc={rc}) — skipping {lang.label}; install manually:  "
               f"{lang.install}", err=True)
    return False
