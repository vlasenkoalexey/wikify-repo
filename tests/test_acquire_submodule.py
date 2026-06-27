"""Stage 0 acquire: `acquire: submodule` adds the source as a git submodule.

Pinning test for the submodule mode (config field + acquire.py branch). Uses a local
`file://` URL so it exercises the URL/submodule path without network access.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from wikify import acquire
from wikify.config import load_config


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), check=True,
                   capture_output=True, text=True)


def _make_source_repo(path: Path) -> str:
    path.mkdir(parents=True)
    _git(["init", "-q"], path)
    _git(["config", "user.email", "t@t"], path)
    _git(["config", "user.name", "t"], path)
    (path / "mod.py").write_text("def f():\n    return 1\n")
    _git(["add", "-A"], path)
    _git(["commit", "-qm", "init"], path)
    return f"file://{path.resolve()}"


def test_config_parses_acquire_field(tmp_path):
    cfg = tmp_path / "s.md"
    cfg.write_text("---\nslug: s\nrepo: https://x/y\nacquire: submodule\n---\n## Concepts\n")
    assert load_config(cfg).acquire == "submodule"
    cfg.write_text("---\nslug: s\n---\n## Concepts\n")
    assert load_config(cfg).acquire is None  # default unset


def test_wiki_subdir_defaults_to_code_and_is_configurable(tmp_path):
    from wikify.cli import Paths

    p = Paths(tmp_path, "s")                     # default
    assert p.wiki_slug == tmp_path / "wiki" / "code" / "s"
    p.set_wiki_subdir("")                        # classic flat layout
    assert p.wiki_slug == tmp_path / "wiki" / "s"
    p.set_wiki_subdir("codebases")               # custom
    assert p.wiki_slug == tmp_path / "wiki" / "codebases" / "s"

    cfg = tmp_path / "s.md"
    cfg.write_text("---\nslug: s\n---\n## Concepts\n")
    assert load_config(cfg).wiki_subdir == "code"   # config default
    cfg.write_text('---\nslug: s\nwiki_subdir: ""\n---\n## Concepts\n')
    assert load_config(cfg).wiki_subdir == ""


def test_submodule_mode_adds_gitlink(tmp_path, monkeypatch):
    # git blocks the file:// transport for submodule clones by default (a CVE mitigation);
    # real https:// URLs are unaffected. Inject the override into every git subprocess.
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "protocol.file.allow")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "always")

    url = _make_source_repo(tmp_path / "src")
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    _git(["init", "-q"], wiki)
    _git(["config", "user.email", "t@t"], wiki)
    _git(["config", "user.name", "t"], wiki)

    acq = acquire.acquire(url, "s", wiki / "raw", mode="submodule")

    assert (wiki / ".gitmodules").exists()              # submodule registered
    assert (wiki / "raw/code/s/mod.py").exists()         # contents checked out
    # the gitlink is staged (a commit-type entry in the index for the submodule path)
    ls = subprocess.run(["git", "ls-files", "--stage", "raw/code/s"],
                        cwd=wiki, capture_output=True, text=True).stdout
    assert ls.startswith("160000"), ls                   # 160000 == gitlink mode
    assert acq.commit                                    # SHA recorded


def test_clone_mode_is_default(tmp_path):
    url = _make_source_repo(tmp_path / "src")
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    _git(["init", "-q"], wiki)
    acquire.acquire(url, "s", wiki / "raw")  # no mode -> clone
    assert not (wiki / ".gitmodules").exists()
    assert (wiki / "raw/code/s/mod.py").exists()
