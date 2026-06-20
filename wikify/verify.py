"""Adversarial verification — the *correctness* floor above the grounding floor.

The citation linter proves every claim cites a real symbol; it does NOT prove the
claim is *true*. A page can be fully cited and still describe the mechanism wrong.
This module is the deterministic half of an adversarial-verify pass:

  - ``load_bearing_claims`` extracts the checkable assertions from a concept page
    (Overview/Design-rationale paragraphs + Entry-points/Mechanism items) with the
    symbols each one cites — the worklist a skeptic agent must try to *refute*
    against the real source (see ``skills/prompts/verify.md``).
  - ``aggregate`` folds the agents' per-claim verdicts into a page report: a page
    fails verification if any load-bearing claim is refuted.

The refutation itself is the LLM step (it reads source and reasons); everything
here is pure Python so the worklist and the pass/fail tally are reproducible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .lint import _LINK, _LIST_ITEM, _is_symbol_link

# Sections whose content makes falsifiable claims about how the code works.
_CLAIM_SECTIONS = ("Overview", "Design rationale", "Entry points", "Mechanism")


@dataclass
class Claim:
    page: str
    line: int                       # 1-based line of the claim's start
    section: str
    text: str                       # the claim prose (item or paragraph)
    citations: list[str] = field(default_factory=list)  # catalog links it cites

    @property
    def id(self) -> str:
        return f"{self.page}:{self.line}"


def _citations(text: str) -> list[str]:
    return [t for _, t in _LINK.findall(text) if _is_symbol_link(t)]


def load_bearing_claims(page_path: str | Path) -> list[Claim]:
    """Extract the falsifiable claims from a concept page, in document order.

    A claim is one Entry-points/Mechanism *item* or one Overview/Design-rationale
    *paragraph*. ``> [!inferred]`` blocks are skipped — they are explicitly the
    page's own hedged reading, not asserted fact, so there is nothing to refute."""
    page_path = Path(page_path)
    name = page_path.name
    lines = page_path.read_text(encoding="utf-8").splitlines()

    claims: list[Claim] = []
    section = ""
    in_inferred = False
    # `block` accumulates the current claim — a list item or a prose paragraph.
    block: list[str] = []
    block_start = 0

    def flush() -> None:
        nonlocal block, block_start
        text = " ".join(b.strip() for b in block).strip()
        if text:
            claims.append(Claim(name, block_start, section, text, _citations(text)))
        block = []

    for i, line in enumerate(lines, start=1):
        stripped = line.strip()

        if stripped.startswith("## "):
            flush()
            section = stripped[3:].strip()
            in_inferred = False
            continue
        if not section.startswith(_CLAIM_SECTIONS):
            continue

        # skip `> [!inferred]` blocks — hedged reading, not asserted fact
        if "[!inferred]" in line:
            in_inferred = True
        elif in_inferred and not stripped.startswith(">") and stripped:
            in_inferred = False
        if in_inferred or stripped.startswith(">"):
            flush()
            continue

        if _LIST_ITEM.match(line):              # a new item starts a new claim
            flush()
            block, block_start = [line], i
        elif stripped == "" or stripped.startswith(("#", "```", "|")):
            flush()                             # blank/fence/table ends a block
        elif block:                             # continuation of item or paragraph
            block.append(line)
        else:                                   # first line of a prose paragraph
            block, block_start = [line], i
    flush()
    return claims


# --------------------------------------------------------------------------- #
# Verdict aggregation
# --------------------------------------------------------------------------- #
@dataclass
class Verdict:
    claim_id: str
    refuted: bool
    note: str = ""


@dataclass
class PageReport:
    page: str
    total: int
    refuted: list[Verdict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.refuted


def aggregate(page: str, claims: list[Claim], verdicts: list[Verdict]) -> PageReport:
    """Fold per-claim verdicts into a page report (refuted claims fail the page)."""
    refuted = [v for v in verdicts if v.refuted]
    return PageReport(page=page, total=len(claims), refuted=refuted)
