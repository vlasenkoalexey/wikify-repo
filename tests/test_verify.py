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
