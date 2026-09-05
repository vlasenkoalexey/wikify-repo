"""Stage 0 — acquire & pin the source repo (implementation.md §6, design Stage 0).

Resolves a repo to an on-disk source tree and records its pinned commit SHA.
For Phase 1 a local checkout is used in place (and surfaced under ``raw/code/<slug>``
as a symlink for traceability); a URL is cloned. ``raw/`` holds immutable inputs.
"""

from __future__ import annotations

import os
import shutil
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


def in_place(root: str | Path, slug: str) -> Acquired:
    """In-repo layout (§10.15): the project root IS the source. No symlink, no clone, no
    ``raw/``; the pin is the repo's own HEAD (``"workdir"`` outside git)."""
    repo_dir = Path(root).resolve()
    try:
        commit = commit_of(repo_dir)
    except Exception:  # not a git repo
        commit = "workdir"
    return Acquired(slug=slug, repo_dir=repo_dir, commit=commit)


def is_dirty(root: str | Path) -> bool:
    """Uncommitted changes in the working tree (the recorded pin is HEAD, the hashes come
    from the files on disk — worth a warning, not an error)."""
    try:
        return bool(_git(["status", "--porcelain", "--untracked-files=no"], root).strip())
    except Exception:
        return False


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
    ``"submodule"`` (default) adds it as a git submodule of the surrounding wiki repo so
    the pin is the committed gitlink; ``"clone"`` plain-clones it instead. Submodule mode
    falls back to a clone when ``raw/`` is not inside a git repo. If ``dest`` already
    exists as a plain clone (e.g. it was acquired before ``acquire: submodule`` was set,
    or the default changed), submodule mode converts it in place: the plain clone is
    removed and re-added as a submodule at the same path. A local-path source is used
    in place — surfaced under ``raw/code/<slug>`` as a *relative* symlink only when it lives
    outside ``raw/code/`` (a source already under ``raw/code/`` is used directly, no symlink).
    """
    raw_code = Path(raw_dir) / "code"
    raw_code.mkdir(parents=True, exist_ok=True)
    dest = raw_code / slug
    mode = (mode or "submodule").lower()

    src_path = Path(source)
    if src_path.exists():
        repo_dir = src_path.resolve()
        raw_code_abs = raw_code.resolve()
        # If the source is already under raw/code/ (e.g. `repo: raw/code/EasyDeL`), use it in
        # place — do NOT create a raw/code/<slug> symlink (it would be redundant and, if
        # absolute, non-portable). Only surface a symlink when the source lives elsewhere, and
        # make it RELATIVE so the wiki repo stays portable.
        already_in_place = repo_dir == dest.resolve() or raw_code_abs in repo_dir.parents
        if not already_in_place and not dest.exists():
            try:
                dest.symlink_to(os.path.relpath(repo_dir, raw_code), target_is_directory=True)
            except OSError:
                pass
    elif mode == "submodule":
        wiki_root = _toplevel(raw_code)
        if wiki_root is None:
            # Not a git repo — submodule is impossible; fall back to a plain clone.
            # Clone dest is the bare slug: git resolves it against cwd=raw_code, so
            # passing str(dest) here would double the path when raw_dir is relative.
            if not dest.exists():
                _git(["clone", source, slug], cwd=raw_code)
        else:
            if dest.exists() and (dest / ".git").is_dir():
                # A plain clone already sits at this slug (acquired before submodule mode
                # was requested) — remove it and re-add as a submodule at the same path.
                shutil.rmtree(dest)
            if not dest.exists():
                rel = dest.resolve().relative_to(wiki_root.resolve())
                # --force: wikify owns raw/code/, so don't let a gitignore line block the add.
                _git(["submodule", "add", "--force", source, str(rel)], cwd=wiki_root)
        repo_dir = dest.resolve()
    else:
        # Treat as a git URL; clone into raw/code/<slug>. Dest is the bare slug
        # relative to cwd=raw_code — str(dest) would re-apply raw/code when the
        # project root (and thus raw_dir) is a relative path like the CLI's default.
        if not dest.exists():
            _git(["clone", source, slug], cwd=raw_code)
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
