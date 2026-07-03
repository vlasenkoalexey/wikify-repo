"""Multi-language SCIP support — pinning tests for detection, the registry, and the
on-demand (ask-don't-auto-install) indexer behavior. The indexers themselves aren't run."""

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


def test_ensure_indexer_missing_noninteractive_skips(monkeypatch, capsys):
    monkeypatch.setattr(L.shutil, "which", lambda _b: None)
    monkeypatch.setattr(L.sys.stdin, "isatty", lambda: False)   # non-tty → never prompts/installs
    called = {"install": False}
    monkeypatch.setattr(L.subprocess, "run", lambda *a, **k: called.__setitem__("install", True))
    assert L.ensure_indexer(L.LANGS["rust"]) is False
    assert called["install"] is False                            # did NOT auto-install
    err = capsys.readouterr().err
    assert "rust-analyzer" in err and "install" in err.lower()   # instructed the user instead
