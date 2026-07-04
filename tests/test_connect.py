"""Stage 7 connect — the deterministic cross-repo concept index (Depth 0).

Pinning tests for `wikify/connect.py`: silo discovery, vocabulary loading, the
tag-vs-name correspondence rule, prefix-token matching, and the rendered index.
Pure Python, no model — this is the concept-axis analog of the coverage floor.
"""

from __future__ import annotations

from pathlib import Path

from wikify import connect


def _silo(wiki: Path, subdir: str, slug: str, pages: dict[str, str]) -> None:
    """Create a silo: wiki/<subdir>/<slug>/{overview.md, concepts/*.md}."""
    d = wiki / subdir / slug
    (d / "concepts").mkdir(parents=True)
    (d / "overview.md").write_text(f"# {slug} overview\n")
    for name, text in pages.items():
        (d / "concepts" / f"{name}.md").write_text(text)


def _vocab(wiki: Path, keys: list[str]) -> None:
    v = wiki / "concepts"
    v.mkdir(parents=True, exist_ok=True)
    for k in keys:
        (v / f"{k}.md").write_text(f"---\ntitle: {k}\n---\n# {k}\n")


def _build(wiki: Path):
    vocab = connect.load_vocabulary(wiki)
    silos = connect.discover_silos(wiki)
    return connect.build_index(silos, vocab), vocab, silos


def test_name_match_across_repos(tmp_path):
    wiki = tmp_path / "wiki"
    _vocab(wiki, ["splash-attention", "sharding", "unused-concept"])
    _silo(wiki, "code", "maxtext", {
        "maxtext-kernels-attention-splash_attention_kernel":
            "---\ntitle: SplashAttention backward\nconcept: maxtext-kernels-attention-splash_attention_kernel\n---\nbody",
        "maxtext-layers-sharding": "---\ntitle: Sharding rules\n---\nbody",
    })
    _silo(wiki, "code", "tokamax", {
        "tokamax-splash_attention": "---\ntitle: splash attention\n---\nbody",
    })

    index, vocab, silos = _build(wiki)
    assert len(vocab) == 3
    assert len({p.repo for p in silos}) == 2
    # splash-attention: both repos match by name/token
    assert set(m.repo for m in index["splash-attention"]) == {"maxtext", "tokamax"}
    assert all(m.confidence == "name" for m in index["splash-attention"])
    # sharding: only maxtext
    assert [m.repo for m in index["sharding"]] == ["maxtext"]
    # a vocabulary concept with no implementation is absent from the index
    assert "unused-concept" not in index


def test_explicit_tag_beats_name_and_ranks_first(tmp_path):
    wiki = tmp_path / "wiki"
    _vocab(wiki, ["remat"])
    # one page tags explicitly; one only matches by prefix token (remat ↔ rematerialization)
    _silo(wiki, "code", "a", {
        "a-checkpoint": "---\ntitle: Checkpointing\nconcepts: [remat]\n---\nbody",
    })
    _silo(wiki, "code", "b", {
        "b-rematerialization": "---\ntitle: Rematerialization pass\n---\nbody",
    })
    index, _, _ = _build(wiki)
    hits = index["remat"]
    assert len(hits) == 2
    # tag match ranks before name match
    assert hits[0].repo == "a" and hits[0].confidence == "tag"
    assert hits[1].repo == "b" and hits[1].confidence == "name"


def test_prefix_token_matching():
    assert connect._token_matches("remat", {"rematerialization"})
    assert connect._token_matches("shard", {"sharding"})
    assert connect._token_matches("splash", {"splash"})
    # too short a shared prefix must not match (avoids co→collective)
    assert not connect._token_matches("co", {"collective"})
    # multi-token key needs ALL tokens present
    toks = {"maxtext", "kernels", "attention", "splash"}
    assert all(connect._token_matches(t, toks) for t in connect._tokens("splash-attention"))
    assert not all(connect._token_matches(t, toks) for t in connect._tokens("ring-attention"))


def test_curated_vocab_dir_is_not_a_silo(tmp_path):
    # wiki/concepts/ is the vocabulary, never mistaken for a silo (it has no overview.md).
    wiki = tmp_path / "wiki"
    _vocab(wiki, ["sharding"])
    _silo(wiki, "codebases", "jax", {"jax-sharding": "---\ntitle: sharding\n---\nb"})
    silos = connect.discover_silos(wiki)
    assert {p.repo for p in silos} == {"jax"}  # NOT "concepts"


def test_apply_writes_inline_bidirectional_links(tmp_path):
    wiki = tmp_path / "wiki"
    _vocab(wiki, ["splash-attention", "sharding"])
    _silo(wiki, "codebases", "maxtext",
          {"maxtext-splash_attention": "---\ntitle: splash\n---\n# splash\nbody"})
    _silo(wiki, "code", "tokamax",
          {"tokamax-splash_attention": "---\ntitle: splash attention\n---\n# splash attention\nbody"})

    counts = connect.apply_connections(wiki, ["splash-attention"])
    assert counts["splash-attention"] == 2

    # concept page: a down-block linking to BOTH silo pages, no side-table file
    hub = (wiki / "concepts" / "splash-attention.md").read_text()
    assert "<!-- connect:auto:begin -->" in hub and "## In this wiki's repos" in hub
    assert "../codebases/maxtext/concepts/maxtext-splash_attention.md" in hub
    assert "../code/tokamax/concepts/tokamax-splash_attention.md" in hub
    assert not (wiki / "_connect").exists()  # NO side-table primitive

    # silo page: an up-link back to the concept hub
    silo = (wiki / "codebases" / "maxtext" / "concepts" / "maxtext-splash_attention.md").read_text()
    assert "<!-- connect:up:begin -->" in silo
    assert "../../../concepts/splash-attention.md" in silo
    # a concept the human did NOT pick is not wired
    assert "<!-- connect:auto:begin -->" not in (wiki / "concepts" / "sharding.md").read_text()


def test_apply_is_idempotent_and_reflects_connection_state(tmp_path):
    wiki = tmp_path / "wiki"
    _vocab(wiki, ["remat"])
    _silo(wiki, "code", "a", {"a-rematerialization": "---\ntitle: remat\n---\n# remat\nb"})

    connect.apply_connections(wiki, ["remat"])
    first = (wiki / "concepts" / "remat.md").read_text()
    assert connect.connected_keys(wiki) == ["remat"]        # state read from the page itself
    connect.apply_connections(wiki, ["remat"])              # re-run
    assert (wiki / "concepts" / "remat.md").read_text() == first  # no churn / duplication
