"""``wikify setup`` / ``wikify doctor`` — everything an install needs after the CLI itself.

Folds the two former scripts into the CLI (implementation.md §9, §10.16):
- **indexers**: check ``scip-python`` and ``scip-clang``; install missing ones into a user
  prefix under ``$WIKIFY_HOME/vendor`` (default ``~/.wikify/vendor``) — no sudo, announced —
  or lazily on the first ``prepare`` that needs them (the same on-demand path TS/Go/Rust use);
- **skill**: install the ``wikify-ingest-repo`` skill (shipped inside the package as data,
  so this works from a pipx install with no checkout) at user level for Claude Code
  (``~/.claude/skills/``) and/or into a project's ``.agents/skills/`` (Codex, Antigravity),
  with the ``.claude/skills`` symlink and ``.gitignore`` line the old script wrote;
- **doctor**: one report of what is present and the fix for what is not.
"""

from __future__ import annotations

import filecmp
import os
import platform
import shutil
import subprocess
import urllib.request
from pathlib import Path

SCIP_CLANG_VERSION = "v0.3.3"      # links against glibc 2.35; newer builds need 2.38
SCIP_PYTHON_SPEC = "@sourcegraph/scip-python"
SKILLS = ("wikify-ingest-repo", "wikify-connect-repo")


def wikify_home() -> Path:
    return Path(os.environ.get("WIKIFY_HOME", "~/.wikify")).expanduser()


def vendor_bin() -> Path:
    return wikify_home() / "vendor" / "bin"


def skill_source(name: str) -> Path:
    """The skill as shipped inside the package."""
    return Path(__file__).parent / "skills" / name


# --------------------------------------------------------------------------- #
# Skills
# --------------------------------------------------------------------------- #
def _same_tree(a: Path, b: Path) -> bool:
    if not (a.is_dir() and b.is_dir()):
        return False
    cmp = filecmp.dircmp(a, b)
    if cmp.left_only or cmp.right_only or cmp.diff_files or cmp.funny_files:
        return False
    return all(_same_tree(a / d, b / d) for d in cmp.common_dirs)


def _copy_skill(src: Path, dest: Path) -> str:
    if _same_tree(src, dest):
        return "unchanged"
    status = "updated" if dest.exists() else "created"
    if dest.exists() or dest.is_symlink():
        shutil.rmtree(dest) if dest.is_dir() and not dest.is_symlink() else dest.unlink()
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest)
    return status


def install_skill_user(claude_dir: Path, name: str = "wikify-ingest-repo") -> tuple[Path, str]:
    """User-level install for Claude Code: ``<claude_dir>/skills/<name>``."""
    dest = Path(claude_dir).expanduser() / "skills" / name
    return dest, _copy_skill(skill_source(name), dest)


def install_skill_project(project: Path, name: str = "wikify-ingest-repo") -> tuple[Path, str]:
    """Project-level install: ``<project>/.agents/skills/<name>`` (Codex, Antigravity) plus a
    ``.claude/skills`` symlink for Claude Code and a ``.gitignore`` line for the mirror."""
    project = Path(project).resolve()
    dest = project / ".agents" / "skills" / name
    src = skill_source(name)
    status = "unchanged" if dest.resolve() == src.resolve() else _copy_skill(src, dest)
    cdir = project / ".claude" / "skills"
    cdir.mkdir(parents=True, exist_ok=True)
    link = cdir / name
    target = Path("..") / ".." / ".agents" / "skills" / name
    if link.is_symlink() and os.readlink(link) == str(target):
        pass
    else:
        if link.exists() or link.is_symlink():
            shutil.rmtree(link) if link.is_dir() and not link.is_symlink() else link.unlink()
        link.symlink_to(target, target_is_directory=True)
    gi = project / ".gitignore"
    if (project / ".git").exists() or gi.exists():
        text = gi.read_text(encoding="utf-8") if gi.exists() else ""
        if "/.claude/skills/" not in text.splitlines():
            gi.write_text(text.rstrip("\n") + ("\n" if text else "")
                          + "# Claude-only mirror of .agents/skills/ (wikify setup) — not committed\n"
                          + "/.claude/skills/\n", encoding="utf-8")
    return dest, status


# --------------------------------------------------------------------------- #
# Indexers
# --------------------------------------------------------------------------- #
def find_tool(name: str) -> str | None:
    hit = shutil.which(name)
    if hit:
        return hit
    for cand in sorted(vendor_bin().glob(f"{name}*"), reverse=True):
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    return None


def install_scip_python(echo=print) -> str | None:
    """``npm i -g --prefix <vendor> @sourcegraph/scip-python`` — a user prefix, no sudo."""
    if not shutil.which("npm"):
        echo("  npm not found: install Node.js (https://nodejs.org) and re-run `wikify setup`")
        return None
    prefix = vendor_bin().parent
    prefix.mkdir(parents=True, exist_ok=True)
    cmd = ["npm", "i", "-g", "--prefix", str(prefix), SCIP_PYTHON_SPEC]
    echo("  $ " + " ".join(cmd))
    rc = subprocess.run(cmd).returncode
    return find_tool("scip-python") if rc == 0 else None


def install_scip_clang(echo=print, version: str = SCIP_CLANG_VERSION) -> str | None:
    arch = f"{platform.machine()}-{platform.system().lower()}"      # e.g. x86_64-linux
    dest = vendor_bin() / f"scip-clang-{version.lstrip('v').replace('.', '')}"
    if dest.is_file():
        return str(dest)
    url = f"https://github.com/sourcegraph/scip-clang/releases/download/{version}/scip-clang-{arch}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    echo(f"  downloading scip-clang {version} ({arch}) -> {dest}")
    try:
        urllib.request.urlretrieve(url, dest)
    except Exception as e:  # noqa: BLE001
        echo(f"  download failed: {e}")
        return None
    dest.chmod(0o755)
    return str(dest)


# --------------------------------------------------------------------------- #
# Doctor
# --------------------------------------------------------------------------- #
def doctor(claude_dir: Path | None = None, project: Path | None = None) -> list[tuple[str, bool, str, str]]:
    """``(check, ok, detail, fix)`` rows."""
    rows: list[tuple[str, bool, str, str]] = []
    from . import __version__
    rows.append(("wikify", True, f"{__version__} ({Path(__file__).parent})", ""))
    for tool, fix in (("git", "install git"), ("node", "install Node.js (for scip-python)"),
                      ("npm", "install Node.js (for scip-python)")):
        hit = shutil.which(tool)
        rows.append((tool, bool(hit), hit or "not found", "" if hit else fix))
    for tool, fix in (("scip-python", "wikify setup --indexers python"),
                      ("scip-clang", "wikify setup --indexers cpp  (only for C++ repos)")):
        hit = find_tool(tool)
        rows.append((tool, bool(hit), hit or "not found (installed on demand by prepare)", "" if hit else fix))
    cd = Path(claude_dir or "~/.claude").expanduser() / "skills" / "wikify-ingest-repo"
    rows.append(("skill (Claude Code, user)", cd.is_dir(), str(cd) if cd.is_dir() else "not installed",
                 "" if cd.is_dir() else "wikify setup"))
    if project is not None:
        pd = Path(project).resolve() / ".agents" / "skills" / "wikify-ingest-repo"
        rows.append(("skill (project .agents/skills)", pd.is_dir(), str(pd) if pd.is_dir() else "not installed",
                     "" if pd.is_dir() else f"wikify setup --project {project}"))
    return rows
