"""Adversarial-verify support (wikify.verify): claim extraction + aggregation.

The LLM does the refuting; these test the deterministic halves — that the
worklist captures exactly the falsifiable claims (Overview/Design paragraphs +
Entry-points/Mechanism items, excluding hedged `> [!inferred]` blocks) with their
citations, and that verdicts fold into a correct pass/fail.
"""

from wikify import verify

PAGE = """\
---
title: x
---
# X

## Overview
This subsystem turns A into B via the [`run`](../catalog/m.md#run) entry.
It is built around a single table.

## Design rationale
The table is intrusive [`Tab`](../catalog/m.md#Tab) so deletes are O(1).

> [!inferred]
> This part is a guess and must not be verified as fact.

## Entry points
- [`run`](../catalog/m.md#run) — called once per request.

## Mechanism (step-by-step)
1. First it builds the table with [`build`](../catalog/m.md#build).
   The build is lazy.
2. Then it returns [`out`](../catalog/m.md#out).

## Key data structures
The Tab dict — NOT a claim section, must be ignored.
"""


def _write(tmp_path):
    p = tmp_path / "c.md"
    p.write_text(PAGE)
    return p


def test_extracts_claims_from_claim_sections_only(tmp_path):
    claims = verify.load_bearing_claims(_write(tmp_path))
    sections = {c.section for c in claims}
    assert sections == {"Overview", "Design rationale", "Entry points",
                        "Mechanism (step-by-step)"}
    # the Key-data-structures line is not a claim
    assert all("NOT a claim" not in c.text for c in claims)


def test_inferred_block_excluded(tmp_path):
    claims = verify.load_bearing_claims(_write(tmp_path))
    assert all("must not be verified" not in c.text for c in claims)


def test_overview_split_into_paragraphs_and_carries_citation(tmp_path):
    claims = verify.load_bearing_claims(_write(tmp_path))
    overview = [c for c in claims if c.section == "Overview"]
    assert len(overview) == 1  # two lines, one paragraph
    assert "../catalog/m.md#run" in overview[0].citations


def test_mechanism_item_absorbs_continuation_line(tmp_path):
    claims = verify.load_bearing_claims(_write(tmp_path))
    mech = [c for c in claims if c.section.startswith("Mechanism")]
    assert len(mech) == 2
    step1 = mech[0]
    assert "lazy" in step1.text                       # continuation line folded in
    assert "../catalog/m.md#build" in step1.citations


def test_aggregate_fails_page_on_any_refutation(tmp_path):
    claims = verify.load_bearing_claims(_write(tmp_path))
    vs = [verify.Verdict(claims[0].id, refuted=False),
          verify.Verdict(claims[1].id, refuted=True, note="source says O(n)")]
    rep = verify.aggregate("c.md", claims, vs)
    assert not rep.ok and len(rep.refuted) == 1 and rep.total == len(claims)


def test_aggregate_passes_when_nothing_refuted(tmp_path):
    claims = verify.load_bearing_claims(_write(tmp_path))
    vs = [verify.Verdict(c.id, refuted=False) for c in claims]
    assert verify.aggregate("c.md", claims, vs).ok



# --------------------------------------------------------------------------- #
# Verdict cache (memoized on content + evidence, never on position)
# --------------------------------------------------------------------------- #
def _catalog(tmp_path):
    """A catalog page so ../catalog/m.md#anchor resolves to a moniker."""
    cat = tmp_path / "catalog"
    cat.mkdir(exist_ok=True)
    (cat / "m.md").write_text(
        "---\nsymbol_base: 'pkg '\nsymbols:\n  run: 'run().'\n  Tab: 'Tab#'\n  build: 'build().'\n  out: 'out().'\n---\n")


def _page_in(tmp_path, text=PAGE):
    _catalog(tmp_path)
    d = tmp_path / "concepts"
    d.mkdir(exist_ok=True)
    p = d / "c.md"
    p.write_text(text)
    return p


HASHES = {"pkg run().": "h1", "pkg Tab#": "h2", "pkg build().": "h3", "pkg out().": "h4"}


def test_key_is_content_not_position(tmp_path):
    p = _page_in(tmp_path)
    before = {c.text: c.key for c in verify.load_bearing_claims(p)}
    p.write_text("---\ntitle: x\n---\n\n\n\n" + PAGE.split("---\n", 2)[2])   # shift every line
    after = {c.text: c.key for c in verify.load_bearing_claims(p)}
    assert before == after


def test_record_then_plan_carries_holds_and_invalidates_on_evidence(tmp_path):
    p = _page_in(tmp_path)
    claims = verify.load_bearing_claims(p)
    cache = verify.load_cache(tmp_path / "nope.json")
    verdicts = [{"claim_line": c.line, "refuted": False} for c in claims]
    n, unmatched = verify.record_verdicts(cache, p, claims, verdicts, HASHES, "ref1", "2026-09-05")
    assert n == len(claims) and unmatched == []
    wl = verify.plan_worklist(p, claims, cache, HASHES, "ref1")
    # everything holds and is unchanged: only the deterministic re-sample comes back
    assert len(wl.cached) + len(wl.resampled) == len(claims)
    assert set(wl.to_verify) == set(wl.resampled)
    # a cited symbol's body changed → that claim is back, tagged
    changed = dict(HASHES, **{"pkg build().": "h3-new"})
    wl2 = verify.plan_worklist(p, claims, cache, changed, "ref1")
    build_claims = [c for c in claims if "build" in c.text]
    assert build_claims and all(c in wl2.invalid for c in build_claims)
    assert all(wl2.reason[c.id] == "cited code changed" for c in build_claims)


def test_refuted_stays_on_worklist_until_prose_changes(tmp_path):
    p = _page_in(tmp_path)
    claims = verify.load_bearing_claims(p)
    cache = verify.load_cache(tmp_path / "nope.json")
    target = next(c for c in claims if "O(1)" in c.text)
    verify.record_verdicts(cache, p, claims, [{"claim_line": target.line, "refuted": True, "note": "no"}],
                           HASHES, "ref1", "2026-09-05")
    wl = verify.plan_worklist(p, claims, cache, HASHES, "ref1")
    assert target in wl.refuted and wl.reason[target.id].startswith("previously refuted")
    # fixing the prose gives a new key → it is simply new again
    p.write_text(p.read_text().replace("O(1)", "amortized O(1)"))
    claims2 = verify.load_bearing_claims(p)
    wl2 = verify.plan_worklist(p, claims2, cache, HASHES, "ref1")
    fixed = next(c for c in claims2 if "amortized" in c.text)
    assert wl2.reason[fixed.id] == "new"


def test_force_and_unresolved_citations_never_cache(tmp_path):
    p = _page_in(tmp_path)
    claims = verify.load_bearing_claims(p)
    cache = verify.load_cache(tmp_path / "nope.json")
    verify.record_verdicts(cache, p, claims, [{"claim_line": c.line, "refuted": False} for c in claims],
                           HASHES, "ref1", "2026-09-05")
    assert len(verify.plan_worklist(p, claims, cache, HASHES, "ref1", force=True).to_verify) == len(claims)
    (tmp_path / "catalog" / "m.md").unlink()          # citations no longer resolve
    wl = verify.plan_worklist(p, claims, cache, HASHES, "ref1")
    cited = [c for c in claims if c.citations]
    assert all(wl.reason[c.id] == "unresolved citations" for c in cited)


def test_resample_is_deterministic_and_rotates_with_ref():
    keys = [f"k{i}" for i in range(2000)]
    a = {k for k in keys if verify._resampled(k, "ref1")}
    assert a == {k for k in keys if verify._resampled(k, "ref1")}
    assert 40 <= len(a) <= 160                        # ~5% of 2000
    assert a != {k for k in keys if verify._resampled(k, "ref2")}


def test_save_and_load_cache_roundtrip(tmp_path):
    path = tmp_path / "v" / "c.json"
    data = {"schema": verify.CACHE_SCHEMA, "claims": {"abc": {"refuted": False, "evidence": {}}}}
    verify.save_cache(path, data)
    assert verify.load_cache(path) == data
    path.write_text("{not json")
    assert verify.load_cache(path) == {"schema": verify.CACHE_SCHEMA, "claims": {}}
