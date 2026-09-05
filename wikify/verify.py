"""Adversarial verification — the *correctness* floor above the grounding floor.

The citation linter proves every claim cites a real symbol; it does NOT prove the
claim is *true*. A page can be fully cited and still describe the mechanism wrong.
This module is the deterministic half of an adversarial-verify pass:

  - ``load_bearing_claims`` extracts the checkable assertions from a concept page
    (Overview/Design-rationale paragraphs + Entry-points/Mechanism items) with the
    symbols each one cites — the worklist a skeptic agent must try to *refute*
    against the real source (see ``.agents/skills/wikify-ingest-repo/prompts/verify.md``).
  - ``aggregate`` folds the agents' per-claim verdicts into a page report: a page
    fails verification if any load-bearing claim is refuted.

The refutation itself is the LLM step (it reads source and reasons); everything
here is pure Python so the worklist and the pass/fail tally are reproducible.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .lint import _LINK, _LIST_ITEM, _is_symbol_link, _resolve_citation

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

    @property
    def key(self) -> str:
        """Content key (§10.4 verify cache): the claim's normalized prose, independent of
        its line number — lines shift on every edit, prose changes only when the claim does."""
        norm = " ".join(self.text.split())
        return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


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


# --------------------------------------------------------------------------- #
# Verdict cache — memoize what a reviewer already checked (§10.4)
# --------------------------------------------------------------------------- #
# A verdict is expensive (a skeptic agent reads source); the worklist is cheap. So
# holds are persisted per page, keyed on the claim's content and the body hashes of
# the symbols it cites, and dropped from the next worklist while both are unchanged.
# Nothing can be marked as holding that a reviewer did not confirm: a miss costs a
# re-verify, a hit reuses a real verdict. Two guards keep a weak first verdict from
# becoming permanent: ``--all`` forces a full pass, and a deterministic RESAMPLE_PCT of
# cached holds is re-verified on every run (rotating with the commit ref).
CACHE_SCHEMA = 1
RESAMPLE_PCT = 5


def cache_path(cache_dir: str | Path, slug: str, page_stem: str) -> Path:
    return Path(cache_dir) / "verify" / slug / f"{page_stem}.json"


def load_cache(path: str | Path) -> dict:
    path = Path(path)
    if not path.exists():
        return {"schema": CACHE_SCHEMA, "claims": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema": CACHE_SCHEMA, "claims": {}}
    if data.get("schema") != CACHE_SCHEMA or not isinstance(data.get("claims"), dict):
        return {"schema": CACHE_SCHEMA, "claims": {}}
    return data


def save_cache(path: str | Path, data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def claim_evidence(page_path: str | Path, claim: Claim, hashes: dict[str, str]) -> dict[str, str]:
    """``{moniker: body_sha}`` for the symbols the claim cites. A cited symbol that no
    longer has a hash (removed) maps to '' so the comparison fails and the claim is
    re-verified. Unresolvable citations (no catalog yet) are simply absent."""
    ev: dict[str, str] = {}
    for target in claim.citations:
        m = _resolve_citation(Path(page_path), target)
        if m:
            ev[m] = hashes.get(m, "")
    return ev


def _resampled(key: str, ref: str, pct: int = RESAMPLE_PCT) -> bool:
    """Deterministic re-sample: the same claim is re-verified at the same ref, and the
    sampled set rotates when the ref changes."""
    h = int(hashlib.sha256(f"{key}:{ref}".encode("utf-8")).hexdigest()[:8], 16)
    return h % 100 < pct


@dataclass
class Worklist:
    to_verify: list[Claim] = field(default_factory=list)
    cached: list[Claim] = field(default_factory=list)      # holds carried forward
    invalid: list[Claim] = field(default_factory=list)     # evidence changed since the hold
    resampled: list[Claim] = field(default_factory=list)   # cached hold, re-checked by design
    refuted: list[Claim] = field(default_factory=list)     # recorded refuted, prose unchanged
    reason: dict[str, str] = field(default_factory=dict)   # claim.id → why it is on the list


def plan_worklist(
    page_path: str | Path,
    claims: list[Claim],
    cache: dict,
    hashes: dict[str, str],
    ref: str,
    force: bool = False,
) -> Worklist:
    """Split a page's claims into what a reviewer must check now and what carries over."""
    wl = Worklist()
    entries = cache.get("claims", {})
    for c in claims:
        ev = claim_evidence(page_path, c, hashes)
        entry = entries.get(c.key)
        cacheable = not c.citations or bool(ev)   # cited but unresolvable → never cache
        if force or entry is None or not cacheable:
            wl.to_verify.append(c)
            wl.reason[c.id] = "forced" if force else ("new" if entry is None else "unresolved citations")
            continue
        if entry.get("refuted"):
            wl.to_verify.append(c)
            wl.refuted.append(c)
            wl.reason[c.id] = "previously refuted, prose unchanged"
            continue
        if entry.get("evidence", {}) != ev:
            wl.to_verify.append(c)
            wl.invalid.append(c)
            wl.reason[c.id] = "cited code changed"
            continue
        if _resampled(c.key, ref):
            wl.to_verify.append(c)
            wl.resampled.append(c)
            wl.reason[c.id] = "re-sample"
            continue
        wl.cached.append(c)
    return wl


def record_verdicts(
    cache: dict,
    page_path: str | Path,
    claims: list[Claim],
    verdicts: list[dict],
    hashes: dict[str, str],
    ref: str,
    date: str,
) -> tuple[int, list[int]]:
    """Store the reviewer's verdicts (the STRICT JSON of prompts/verify.md, matched by
    ``claim_line``) under each claim's content key with its current evidence.
    Returns ``(recorded, unmatched_lines)``."""
    by_line = {c.line: c for c in claims}
    entries = cache.setdefault("claims", {})
    recorded = 0
    unmatched: list[int] = []
    for v in verdicts:
        try:
            line = int(v.get("claim_line"))
        except (TypeError, ValueError):
            continue
        c = by_line.get(line)
        if c is None:
            unmatched.append(line)
            continue
        entries[c.key] = {
            "line": c.line,
            "section": c.section,
            "text": " ".join(c.text.split())[:200],
            "evidence": claim_evidence(page_path, c, hashes),
            "refuted": bool(v.get("refuted")),
            "note": str(v.get("note", ""))[:200],
            "ref": ref,
            "verified_at": date,
        }
        recorded += 1
    cache["schema"] = CACHE_SCHEMA
    cache["page"] = Path(page_path).name
    return recorded, unmatched
