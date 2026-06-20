"""Sharded scip-python indexing (scip_index.run_indexer_sharded / merge_shards).

The risky new logic is the MERGE: scip-python emits target files relative to the
``--target-only`` dir and a few dependency-spillover files repo-relative, with the
spillover copies *partial*. ``merge_shards`` must (1) repair target-relative paths
to repo-relative, (2) keep them repo-relative for spillover, and (3) keep the most
complete document per path. These tests fabricate that exact emission shape so the
merge is validated without invoking scip-python.
"""

from __future__ import annotations

from pathlib import Path

from wikify import scip_index, scip_pb2


def _doc(rel_path: str, symbols: list[str]):
    d = scip_pb2.Document(relative_path=rel_path)
    for s in symbols:
        d.symbols.append(scip_pb2.SymbolInformation(symbol=s))
    return d


def _write_index(path: Path, docs):
    idx = scip_pb2.Index()
    idx.metadata.project_root = "file:///repo"
    for d in docs:
        idx.documents.append(d)
    path.write_bytes(idx.SerializeToString())


def _fake_repo(tmp_path: Path) -> Path:
    """A repo tree mirroring two shardable subpackages + a shared dep file."""
    repo = tmp_path / "repo"
    for rel in ("pkg/optim/adam.py", "pkg/optim/__init__.py",
                "pkg/nn/linear.py", "pkg/nn/__init__.py", "pkg/__init__.py"):
        f = repo / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("# stub\n")
    return repo


def test_merge_repairs_target_relative_paths(tmp_path: Path):
    repo = _fake_repo(tmp_path)
    # optim shard: target files are emitted RELATIVE TO the target dir.
    optim = tmp_path / "optim.scip"
    _write_index(optim, [
        _doc("adam.py", ["m `pkg.optim.adam`/Adam#"]),
        _doc("__init__.py", ["m `pkg.optim`/__init__:"]),
    ])
    out = scip_index.merge_shards(repo, [("pkg/optim", optim)], tmp_path / "out.scip")
    paths = {d.relative_path for d in scip_index.parse_index(out).documents}
    assert paths == {"pkg/optim/adam.py", "pkg/optim/__init__.py"}


def test_merge_keeps_repo_relative_spillover_and_prefers_complete(tmp_path: Path):
    repo = _fake_repo(tmp_path)
    # optim shard emits its own target + a PARTIAL spillover of the shared dep
    # pkg/__init__.py (repo-relative, 1 symbol).
    optim = tmp_path / "optim.scip"
    _write_index(optim, [
        _doc("adam.py", ["m `pkg.optim.adam`/Adam#"]),
        _doc("pkg/__init__.py", ["m `pkg`/foo."]),
    ])
    # nn shard owns pkg/__init__.py is false — but emits a MORE complete spillover
    # (2 symbols). The merge must keep the 2-symbol copy, not the 1-symbol one.
    nn = tmp_path / "nn.scip"
    _write_index(nn, [
        _doc("linear.py", ["m `pkg.nn.linear`/Linear#"]),
        _doc("pkg/__init__.py", ["m `pkg`/foo.", "m `pkg`/bar."]),
    ])
    out = scip_index.merge_shards(repo, [("pkg/optim", optim), ("pkg/nn", nn)],
                                  tmp_path / "out.scip")
    docs = {d.relative_path: d for d in scip_index.parse_index(out).documents}
    assert set(docs) == {"pkg/optim/adam.py", "pkg/nn/linear.py", "pkg/__init__.py"}
    # most-complete wins: the merged dep doc has both symbols, exactly once.
    assert len(docs["pkg/__init__.py"].symbols) == 2


def test_merged_index_builds_a_correct_graph(tmp_path: Path):
    """End-to-end: a merged index drives build_graph with global monikers intact."""
    repo = _fake_repo(tmp_path)
    optim = tmp_path / "optim.scip"
    d = _doc("adam.py", ["scip-python python p 0 `pkg.optim.adam`/Adam#"])
    _write_index(optim, [d])
    out = scip_index.merge_shards(repo, [("pkg/optim", optim)], tmp_path / "out.scip")
    g = scip_index.build_graph(scip_index.parse_index(out))
    adam = [m for m in g.symbols if m.endswith("`pkg.optim.adam`/Adam#")]
    assert adam, "global moniker survived the merge"
