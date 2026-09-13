"""Windows path handling: every path that reaches a wiki page or a slug is ``/``-separated,
regardless of what the indexer or ``os.path`` produced on the host (§ Windows support).

These pass trivially on POSIX and guard the Windows-only regressions: scip-python emits
``relative_path`` with backslashes there, and ``os.path.relpath`` returns backslashes.
"""
from __future__ import annotations

import ntpath
import os
from pathlib import Path

from wikify import connect, scip_index, scip_pb2


def test_parse_index_normalizes_backslash_document_paths(tmp_path: Path):
    idx = scip_pb2.Index()
    d = idx.documents.add()
    d.relative_path = "pkg\sub\mod.py"
    d.language = "Python"
    d2 = idx.documents.add()
    d2.relative_path = "pkg/other.py"
    path = tmp_path / "x.scip"
    path.write_bytes(idx.SerializeToString())
    parsed = scip_index.parse_index(path)
    assert [doc.relative_path for doc in parsed.documents] == ["pkg/sub/mod.py", "pkg/other.py"]


def test_connect_relpath_is_posix_even_when_os_relpath_is_not(monkeypatch):
    # Simulate a Windows host: os.path.relpath returns backslashes and os.sep is "\\".
    # (On Windows os.path *is* ntpath, so bind the original before patching.)
    real_relpath = ntpath.relpath
    monkeypatch.setattr(os.path, "relpath", lambda a, b: real_relpath(a, b))
    monkeypatch.setattr(os, "sep", "\\")
    link = connect._relpath("concepts/gex/Foo Bar.md", "code/repo/concepts/splash.md")
    assert "\\" not in link and "%5C" not in link
    assert link == "../../code/repo/concepts/splash.md"


def test_run_indexer_resolves_scip_python_via_which(monkeypatch, tmp_path: Path):
    """The indexer is looked up with shutil.which so the ``.cmd`` shim resolves on Windows."""
    seen: dict = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        class P:  # noqa: D401 - minimal CompletedProcess stand-in
            returncode, stdout, stderr = 0, "", ""
        return P()

    monkeypatch.setattr(scip_index.shutil, "which", lambda name: "C:\tools\scip-python.CMD")
    monkeypatch.setattr(scip_index.subprocess, "run", fake_run)
    monkeypatch.setattr(scip_index, "_has_documents", lambda p: True)
    scip_index.run_indexer(tmp_path, tmp_path / "out.scip", project_name="x")
    assert seen["cmd"][0] == "C:\tools\scip-python.CMD"
