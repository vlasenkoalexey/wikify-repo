"""Multi-language SCIP support — pinning tests for detection, the registry, and the
on-demand indexer install behavior (auto by default, announced, --no-install-indexers
opts out). The indexers themselves aren't run."""

from __future__ import annotations

from wikify import languages as L


def test_detect_by_marker_file(tmp_path):
    (tmp_path / "go.mod").write_text("module x\n")
    (tmp_path / "Cargo.toml").write_text("[package]\n")
    (tmp_path / "package.json").write_text("{}\n")
    found = set(L.detect_languages(tmp_path))
    assert {"go", "rust", "typescript"} <= found


def test_detect_by_extension_threshold(tmp_path):
    for i in range(3):
        (tmp_path / f"m{i}.go").write_text("package m\n")   # ≥ _MIN_FILES, no go.mod
    (tmp_path / "one.rs").write_text("fn main(){}\n")        # only 1 → below threshold
    found = set(L.detect_languages(tmp_path))
    assert "go" in found and "rust" not in found


def test_detect_skips_vendor_dirs(tmp_path):
    nm = tmp_path / "node_modules" / "pkg"
    nm.mkdir(parents=True)
    for i in range(5):
        (nm / f"v{i}.rs").write_text("fn x(){}\n")           # vendored, must not count
    assert "rust" not in set(L.detect_languages(tmp_path))


def test_registry_autorun_langs_have_runners():
    for key in L.AUTO_RUN:
        lang = L.LANGS[key]
        assert lang.run is not None and lang.bin and lang.install
        assert lang.scip_suffix.endswith(".scip")


def test_scip_path_naming(tmp_path):
    assert L.scip_path(tmp_path, "proj", "python").name == "proj.scip"
    assert L.scip_path(tmp_path, "proj", "go").name == "proj.go.scip"
    assert L.scip_path(tmp_path, "proj", "typescript").name == "proj.ts.scip"


def test_ensure_indexer_present(monkeypatch):
    monkeypatch.setattr(L.shutil, "which", lambda _b: "/usr/bin/" + _b)
    assert L.ensure_indexer(L.LANGS["go"]) is True


def test_ensure_indexer_missing_auto_installs(monkeypatch, capsys):
    """Default: a missing indexer is installed automatically — announced, then run."""
    state = {"installed": False}

    def fake_which(_b):
        return "/home/u/.local/bin/" + _b if state["installed"] else None

    class _Proc:
        returncode = 0

    def fake_run(cmd, **kw):
        state["installed"] = True
        state["cmd"] = cmd
        return _Proc()

    monkeypatch.setattr(L.shutil, "which", fake_which)
    monkeypatch.setattr(L.subprocess, "run", fake_run)
    assert L.ensure_indexer(L.LANGS["rust"]) is True
    assert state["cmd"] == L.LANGS["rust"].install               # ran the registry command
    out = capsys.readouterr().out
    assert "installing automatically" in out                     # announced, not silent


def test_ensure_indexer_missing_no_install_opt_out(monkeypatch, capsys):
    """--no-install-indexers (auto=False): print guidance, skip, never run the installer."""
    monkeypatch.setattr(L.shutil, "which", lambda _b: None)
    called = {"install": False}
    monkeypatch.setattr(L.subprocess, "run", lambda *a, **k: called.__setitem__("install", True))
    assert L.ensure_indexer(L.LANGS["rust"], auto=False) is False
    assert called["install"] is False                            # did NOT install
    err = capsys.readouterr().err
    assert "rust-analyzer" in err and "install" in err.lower()   # instructed the user instead


def test_ensure_indexer_install_failure_skips(monkeypatch, capsys):
    """A failing installer must not be treated as success."""
    class _Proc:
        returncode = 1

    monkeypatch.setattr(L.shutil, "which", lambda _b: None)
    monkeypatch.setattr(L.subprocess, "run", lambda *a, **k: _Proc())
    assert L.ensure_indexer(L.LANGS["go"]) is False
    assert "install failed" in capsys.readouterr().err
