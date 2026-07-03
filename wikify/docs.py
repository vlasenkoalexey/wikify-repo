"""Docs mode — the prose/documentation source type (design.md "Docs mode").

wikify's identity is *Karpathy synthesis wrapped in a deterministic shell* — a grounding
gate + a coverage floor. Code mode anchors that shell to SCIP symbols; **docs mode anchors
it to a source document + section**. The LLM synthesis (read a doc → topic/source pages →
cite → cross-link) is the same Karpathy ingest either way; this module is the deterministic
half: enumerate docs, resolve their anchors, gate citations against them, and guarantee every
doc is represented.

Only the *anchor resolver* is format-sensitive, so it is a small per-format **adapter**
(``anchors(text)``). Markdown/HTML/notebooks give fine-grained heading/cell anchors;
plain text falls back to whole-file grounding. Enumeration, coverage, and the gate are
format-agnostic.

A prose citation is a link to the ``src:`` scheme — ``[label](src:<repo-rel-doc>#<anchor>)``
— which the doc packet hands the agent verbatim (mirroring how a code packet hands over
``cite:`` catalog anchors). ``lint_docs`` resolves each against the doc map; an unresolved
one fails the build.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from .lint import LintError, LintReport

# --------------------------------------------------------------------------- #
# Anchors — GitHub-style slug + per-format adapters
# --------------------------------------------------------------------------- #
_SLUG_STRIP = re.compile(r"[^\w\- ]+")


def slugify(text: str) -> str:
    """A GitHub-flavoured heading anchor: lowercase, punctuation dropped, spaces→hyphens."""
    s = _SLUG_STRIP.sub("", text.strip().lower())
    return re.sub(r"\s+", "-", s)


_ATX = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$")
_SETEXT = re.compile(r"^(=+|-+)\s*$")


def _markdown_anchors(text: str) -> set[str]:
    anchors: set[str] = set()
    lines = text.splitlines()
    in_fence = False
    for i, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _ATX.match(line)
        if m and m.group(1):
            anchors.add(slugify(m.group(1)))
            continue
        # setext: a heading line followed by ==== or ----
        if _SETEXT.match(line) and i > 0 and lines[i - 1].strip():
            anchors.add(slugify(lines[i - 1]))
    return anchors


class _HeadingHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.anchors: set[str] = set()
        self._h: str | None = None
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        d = dict(attrs)
        if d.get("id"):
            self.anchors.add(d["id"])          # explicit anchors resolve verbatim
        if d.get("name"):
            self.anchors.add(d["name"])
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._h, self._buf = tag, []

    def handle_data(self, data: str) -> None:
        if self._h is not None:
            self._buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == self._h:
            txt = "".join(self._buf).strip()
            if txt:
                self.anchors.add(slugify(txt))
            self._h = None


def _html_anchors(text: str) -> set[str]:
    p = _HeadingHTMLParser()
    try:
        p.feed(text)
    except Exception:  # tolerant of malformed HTML — grounding degrades, never crashes
        pass
    return p.anchors


# extension → anchor extractor. Absent = whole-file grounding (anchors=∅).
_ADAPTERS = {
    ".md": _markdown_anchors, ".markdown": _markdown_anchors, ".mdx": _markdown_anchors,
    ".rst": _markdown_anchors,          # rst headings are underline-style ≈ setext
    ".html": _html_anchors, ".htm": _html_anchors,
    ".txt": lambda _t: set(),
}

DEFAULT_DOC_GLOBS = ["**/*.md", "**/*.markdown", "**/*.mdx", "**/*.rst",
                     "**/*.html", "**/*.htm", "**/*.txt"]
_SKIP = ("bazel-", ".git/", "third_party/", "vendor/", "node_modules/",
         ".ipynb_checkpoints/", "build/", "site-packages/")


# --------------------------------------------------------------------------- #
# Doc map — the prose analog of the symbol table
# --------------------------------------------------------------------------- #
@dataclass
class DocInfo:
    relpath: str            # repo-relative
    anchors: set[str] = field(default_factory=set)
    lines: int = 0

    @property
    def page_slug(self) -> str:
        """Filename-safe slug for this doc's ``sources/<page_slug>.md`` landing page."""
        return re.sub(r"[^\w.-]", "-", self.relpath.rsplit(".", 1)[0])


def enumerate_docs(repo_dir: Path, globs: list[str] | None) -> list[str]:
    """Repo-relative doc paths matched by ``globs`` (sorted, deduped, vendor-skipped)."""
    out: list[str] = []
    seen: set[str] = set()
    for pat in (globs or DEFAULT_DOC_GLOBS):
        for m in sorted(Path(repo_dir).glob(pat)):
            rel = m.relative_to(repo_dir).as_posix()
            if rel in seen or any(s in rel for s in _SKIP) or not m.is_file():
                continue
            seen.add(rel)
            out.append(rel)
    return sorted(out)


def build_doc_map(repo_dir: Path, docs: list[str]) -> dict[str, DocInfo]:
    """Parse each doc's anchors via its format adapter → the resolvable-anchor table."""
    dm: dict[str, DocInfo] = {}
    for rel in docs:
        p = Path(repo_dir) / rel
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        extractor = _ADAPTERS.get(p.suffix.lower(), lambda _t: set())
        dm[rel] = DocInfo(relpath=rel, anchors=extractor(text), lines=text.count("\n") + 1)
    return dm


# --------------------------------------------------------------------------- #
# Prose citations — the ``src:`` scheme, resolved against the doc map
# --------------------------------------------------------------------------- #
_SRC_CITE = re.compile(r"\]\(src:([^)#\s]+)(?:#([^)\s]+))?\)")


def page_source_cites(page_path: Path) -> list[tuple[str, str | None, int]]:
    """(doc, anchor|None, line) for every ``src:`` citation on the page."""
    out: list[tuple[str, str | None, int]] = []
    for i, line in enumerate(page_path.read_text(encoding="utf-8").splitlines(), 1):
        for doc, anchor in _SRC_CITE.findall(line):
            out.append((doc, anchor or None, i))
    return out


def lint_docs(wiki_slug_dir: str | Path, doc_map: dict[str, DocInfo]) -> LintReport:
    """Gate: every ``src:`` citation must resolve to a real doc + (if given) a real anchor.

    Prose may state freely (no subgraph/uncited gate) — it just may not cite a section that
    doesn't exist. This is the docs-mode analog of the code citation linter (rule 1)."""
    errors: list[LintError] = []
    base = Path(wiki_slug_dir)
    for sub in ("topics", "sources", "concepts", "doc-concepts"):
        d = base / sub
        if not d.is_dir():
            continue
        for page in sorted(d.glob("*.md")):
            rel_page = f"{sub}/{page.name}"
            for doc, anchor, line in page_source_cites(page):
                info = doc_map.get(doc)
                if info is None:
                    errors.append(LintError(rel_page, line, 1,
                                  f"cites source '{doc}' — no such doc in the repo"))
                elif anchor and info.anchors and anchor not in info.anchors:
                    errors.append(LintError(rel_page, line, 1,
                                  f"cites '{doc}#{anchor}' — no such section in that doc"))
    return LintReport(errors)


# --------------------------------------------------------------------------- #
# Coverage — the prose analog of the set-difference over modules
# --------------------------------------------------------------------------- #
@dataclass
class DocsCoverage:
    total: int
    covered: set[str]
    uncovered: list[str]

    def render(self) -> str:
        pct = 100.0 * len(self.covered) / self.total if self.total else 100.0
        head = f"docs coverage: {len(self.covered)}/{self.total} represented ({pct:.0f}%)"
        if not self.uncovered:
            return head
        tail = "\n".join(f"  - uncovered: {d}" for d in self.uncovered[:20])
        more = "" if len(self.uncovered) <= 20 else f"\n  … +{len(self.uncovered) - 20} more"
        return f"{head}\n{tail}{more}"


def docs_coverage(doc_map: dict[str, DocInfo], wiki_slug_dir: str | Path) -> DocsCoverage:
    """A doc is *represented* if it has a ``sources/<page_slug>.md`` page or is cited anywhere.

    Set-difference over the doc file set (enumeration, not reachability) — no doc silently
    dropped, exactly as code coverage guarantees per module."""
    base = Path(wiki_slug_dir)
    cited: set[str] = set()
    for sub in ("topics", "sources", "concepts", "doc-concepts"):
        d = base / sub
        if d.is_dir():
            for page in d.glob("*.md"):
                for doc, _a, _l in page_source_cites(page):
                    cited.add(doc)
    covered: set[str] = set(cited)
    src_dir = base / "sources"
    for rel, info in doc_map.items():
        if (src_dir / f"{info.page_slug}.md").exists():
            covered.add(rel)
    covered &= set(doc_map)
    uncovered = sorted(set(doc_map) - covered)
    return DocsCoverage(total=len(doc_map), covered=covered, uncovered=uncovered)


# --------------------------------------------------------------------------- #
# Doc packets — one per doc, handed to the LLM synthesis step
# --------------------------------------------------------------------------- #
def _outbound_links(text: str, docs: set[str], self_rel: str) -> list[str]:
    """Repo-relative docs this doc links to (markdown links resolved against its dir)."""
    here = Path(self_rel).parent
    hits: set[str] = set()
    for _label, target in re.findall(r"\[([^\]]*)\]\(([^)]+)\)", text):
        t = target.split("#", 1)[0].strip()
        if not t or t.startswith(("http://", "https://", "mailto:", "src:")):
            continue
        cand = (here / t).as_posix().lstrip("./")
        # normalize ../
        try:
            cand = Path((here / t)).resolve().relative_to(Path(".").resolve()).as_posix()
        except Exception:
            pass
        if cand in docs:
            hits.add(cand)
    return sorted(hits)


def doc_packet_text(repo_dir: Path, slug: str, commit: str, info: DocInfo,
                    doc_map: dict[str, DocInfo], date: str, max_lines: int = 500) -> str:
    """The packet for one doc: provenance, ready-to-paste ``src:`` citations, the doc text
    (truncated — the agent reads the real file), and its cross-links to sibling docs."""
    raw = f"raw/code/{slug}/{info.relpath}"
    text = (Path(repo_dir) / info.relpath).read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    body = "\n".join(lines[:max_lines])
    truncated = "" if len(lines) <= max_lines else (
        f"\n\n> [truncated at {max_lines}/{len(lines)} lines — READ THE REAL FILE at `{raw}`]")

    if info.anchors:
        cites = "\n".join(f"- `[{a}](src:{info.relpath}#{a})`" for a in sorted(info.anchors))
        cite_help = ("Cite a section with the matching token below (paste verbatim); the "
                     "linter rejects any section not listed here.")
    else:
        cites = f"- `[{info.relpath}](src:{info.relpath})`  (whole-file — no sections)"
        cite_help = "This format has no sub-anchors; cite the whole file with the token below."

    neighbors = _outbound_links(text, set(doc_map), info.relpath)
    nbr = "\n".join(f"- `{n}`" for n in neighbors) or "- (none)"

    return f"""---
doc: {info.relpath}
slug: {slug}
commit: {commit}
generated: {date}
type: doc-packet
---
# Doc packet: `{info.relpath}`

Source file (immutable): `{raw}`

## Valid citations (`src:` tokens) — {cite_help}
{cites}

## Sibling docs it links to (cross-link these where relevant)
{nbr}

## Doc text
{body}{truncated}
"""


def write_doc_packets(cache_dir: Path, slug: str, repo_dir: Path,
                      doc_map: dict[str, DocInfo], commit: str, date: str) -> list[Path]:
    out_dir = Path(cache_dir) / "packets" / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for info in doc_map.values():
        pkt = out_dir / f"doc__{info.page_slug}.md"
        pkt.write_text(doc_packet_text(repo_dir, slug, commit, info, doc_map, date),
                       encoding="utf-8")
        written.append(pkt)
    return written


# --------------------------------------------------------------------------- #
# Assemble — the docs index (overview + topics + sources)
# --------------------------------------------------------------------------- #
def assemble_docs_index(wiki_slug_dir: str | Path, slug: str, commit: str, date: str,
                        cov: DocsCoverage) -> Path:
    base = Path(wiki_slug_dir)
    base.mkdir(parents=True, exist_ok=True)

    def _list(sub: str) -> str:
        d = base / sub
        pages = sorted(d.glob("*.md")) if d.is_dir() else []
        pages = [p for p in pages if p.name != "index.md"]
        if not pages:
            return "_none yet_"
        return "\n".join(f"- [{p.stem}]({sub}/{p.name})" for p in pages)

    text = f"""---
title: 'Docs wiki: {slug}'
type: docs-index
slug: {slug}
commit: {commit}
updated: {date}
mode: docs
---
# {slug} — documentation wiki

Prose knowledge base ingested from the docs in `raw/code/{slug}/`. Read `overview.md` first;
grep to a topic/source page; every claim cites its source section (`src:` → the real doc).

{cov.render()}

## Topics (synthesized, cross-source)
{_list("topics")}

## Sources (one summary per ingested doc)
{_list("sources")}
"""
    out = base / "index.md"
    out.write_text(text, encoding="utf-8")
    return out
