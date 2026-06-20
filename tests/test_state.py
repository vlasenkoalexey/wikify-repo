"""Tests for wikify.state — the reconcile-state ledger (implementation.md §5.5)."""

from __future__ import annotations

import json

from wikify import state as st


def test_state_path(tmp_path):
    p = st.state_path(tmp_path, "torchtitan")
    assert p == tmp_path / "state" / "torchtitan.json"


def test_load_missing_returns_empty_shape(tmp_path):
    s = st.load_state(tmp_path / "state" / "nope.json")
    assert s == {"ref": None, "symbols": {}, "pages": {}}
    assert set(s) == {"ref", "symbols", "pages"}


def test_load_partial_fills_missing_keys(tmp_path):
    path = tmp_path / "state" / "partial.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"ref": "abc"}), encoding="utf-8")
    s = st.load_state(path)
    assert s["ref"] == "abc"
    assert s["symbols"] == {}
    assert s["pages"] == {}


def test_save_creates_valid_indented_json(tmp_path):
    path = st.state_path(tmp_path, "demo")
    s = st._empty_state()
    st.set_ref(s, "deadbeef")
    st.set_symbols(s, {"scip:b": "h2", "scip:a": "h1"})
    st.save_state(path, s)

    assert path.exists()
    text = path.read_text(encoding="utf-8")
    # Valid JSON, indented, and sorted for stable diffs.
    parsed = json.loads(text)
    assert parsed["ref"] == "deadbeef"
    assert "\n  " in text  # indent=2 present
    assert text.index('"scip:a"') < text.index('"scip:b"')  # sort_keys


def test_round_trip_preserves_data(tmp_path):
    path = st.state_path(tmp_path, "rt")
    s = st._empty_state()
    st.set_ref(s, "sha123")
    st.set_symbols(s, {"scip:foo": "bodyA", "scip:bar": "bodyB"})
    st.record_page(s, "training-loop", ["scip:foo", "scip:bar"], "sha123")
    st.save_state(path, s)

    loaded = st.load_state(path)
    assert loaded == s


def test_set_symbols_replaces(tmp_path):
    s = st._empty_state()
    st.set_symbols(s, {"a": "1"})
    st.set_symbols(s, {"b": "2"})
    assert s["symbols"] == {"b": "2"}


def test_set_ref(tmp_path):
    s = st._empty_state()
    st.set_ref(s, "ref-1")
    assert s["ref"] == "ref-1"


def test_record_page_then_page_cited(tmp_path):
    s = st._empty_state()
    # Unsorted, with a duplicate → stored sorted + deduped.
    st.record_page(s, "checkpoint", ["scip:z", "scip:a", "scip:z"], "shaX")
    assert st.page_cited(s, "checkpoint") == ["scip:a", "scip:z"]
    assert s["pages"]["checkpoint"]["built_ref"] == "shaX"


def test_page_cited_absent_is_empty(tmp_path):
    s = st._empty_state()
    assert st.page_cited(s, "missing") == []


def test_has_page(tmp_path):
    s = st._empty_state()
    assert not st.has_page(s, "p")
    st.record_page(s, "p", [], "sha")
    assert st.has_page(s, "p")
