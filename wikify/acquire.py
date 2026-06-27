"""Stage 0 — acquire & pin the source repo (implementation.md §6, design Stage 0).

Resolves a repo to an on-disk source tree and records its pinned commit SHA.
For Phase 1 a local checkout is used in place (and surfaced under ``raw/code/<slug>``
as a symlink for traceability); a URL is cloned. ``raw/`` holds immutable inputs.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Acquired:
    slug: str
    repo_dir: Path
    commit: str


def _git(args: list[str], cwd: str | Path) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def commit_of(repo_dir: str | Path) -> str:
    return _git(["rev-parse", "HEAD"], repo_dir)


def checkout(repo_dir: str | Path, ref: str) -> None:
    _git(["checkout", ref], repo_dir)


def _toplevel(path: str | Path) -> Path | None:
    """The git work-tree root containing ``path``, or None if not in a repo."""
    try:
        return Path(_git(["rev-parse", "--show-toplevel"], path))
    except RuntimeError:
        return None


def acquire(
    source: str,
    slug: str,
    raw_dir: str | Path,
    ref: str | None = None,
    mode: str | None = None,
) -> Acquired:
    """Resolve ``source`` (local path or git URL) to a pinned source tree.

    ``mode`` controls how a git-URL source lands in ``raw/code/<slug>``:
    ``"clone"`` (default) plain-clones it; ``"submodule"`` adds it as a git submodule
    of the surrounding wiki repo so the pin is the committed gitlink. Submodule mode
    falls back to a clone when ``raw/`` is not inside a git repo. Local-path sources are
    always symlinked in place.
    """
    raw_code = Path(raw_dir) / "code"
    raw_code.mkdir(parents=True, exist_ok=True)
    dest = raw_code / slug
    mode = (mode or "clone").lower()

    src_path = Path(source)
    if src_path.exists():
        repo_dir = src_path.resolve()
        # Surface under raw/code/<slug> as a symlink for traceability.
        if not dest.exists():
            try:
                dest.symlink_to(repo_dir, target_is_directory=True)
            except OSError:
                pass
    elif mode == "submodule" and not dest.exists():
        wiki_root = _toplevel(raw_code)
        if wiki_root is None:
            # Not a git repo — submodule is impossible; fall back to a plain clone.
            _git(["clone", source, str(dest)], cwd=raw_code)
        else:
            rel = dest.resolve().relative_to(wiki_root.resolve())
            # --force: wikify owns raw/code/, so don't let a gitignore line block the add.
            _git(["submodule", "add", "--force", source, str(rel)], cwd=wiki_root)
        repo_dir = dest.resolve()
    else:
        # Treat as a git URL; clone into raw/code/<slug>.
        if not dest.exists():
            _git(["clone", source, str(dest)], cwd=raw_code)
        repo_dir = dest.resolve()

    if ref:
        checkout(repo_dir, ref)
        # In submodule mode the new gitlink (the pin) is left staged for the agent/user
        # to commit; the recorded commit below is the same SHA either way.
        wiki_root = _toplevel(raw_code)
        if mode == "submodule" and wiki_root is not None:
            try:
                _git(["add", str(dest.resolve().relative_to(wiki_root.resolve()))],
                     cwd=wiki_root)
            except (RuntimeError, ValueError):
                pass
    return Acquired(slug=slug, repo_dir=repo_dir, commit=commit_of(repo_dir))
