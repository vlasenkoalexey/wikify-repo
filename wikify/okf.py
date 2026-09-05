"""OKF v0.2 compatibility — Google Cloud's Open Knowledge Format, minimal by design.

OKF (knowledge-catalog/okf/SPEC.md) is a naming convention for markdown knowledge
bundles: a page's front matter carries ``generated`` (who produced it), ``verified``
(who checked it), ``sources`` (what it derives from) and an optional lifecycle
(``status``: draft | stable | deprecated, ``stale_after``); actors are written
``producer/version`` for tools, ``human:<id>`` for people, ``process:<id>`` for
automation; a reader derives a trust tier from ``verified`` (none → unverified, tools
only → machine-confirmed, any ``human:`` → human-reviewed). There is no schema and no
validator; the value is that any reader told "use OKF trust fields" understands the
pages without a custom explanation.

wikify emits exactly the useful subset (design.md decisions log "OKF: compatible by
naming, minimal by design"):

- ``finalize`` stamps ``generated: {by: wikify/<version>, at}`` on concept, doc-concept and
  overview pages (refreshed when a concept page's *body* changes — front matter edits do
  not advance it), writes file-level ``sources`` on concept pages (the definition files the
  page's citations resolve to, most-cited first, capped) and drops ``status: fresh``, which
  is not an OKF status. Per-symbol citations stay inline in the body; ``sources`` is the
  file-level projection, not a copy.
- ``verify --record`` stamps ``verified: [{by: wikify-verify/<version>, at}]`` once every
  claim on a page holds at current evidence, keeping any ``human:`` entries.
- The silo ``index.md`` declares ``okf_version: "0.2"`` and one snapshot ``sources`` entry.
- ``warnings`` checks the three shapes (actor pattern, offset-qualified datetime, status
  enum) — warnings, never a gate.

Front matter is edited textually, key by key: everything not owned here is preserved
byte for byte, and a second run produces no diff.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .lint import _LINK, _is_symbol_link, _resolve_citation

OKF_VERSION = "0.2"
MAX_SOURCES = 10
STATUS_VALUES = ("draft", "stable", "deprecated")
ACTOR_RE = re.compile(r"^(human:[^\s/]+|process:\S+|[^\s/]+/\S+)$")
_DT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})$")
OWNED_KEYS = ("generated", "verified", "sources")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso(v) -> str:
    """YAML parses an unquoted ISO datetime into a datetime; render it back the same way."""
    if isinstance(v, datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(v)


def actor(producer: str, version: str) -> str:
    return f"{producer}/{version}"


# --------------------------------------------------------------------------- #
# Front matter editing (textual, key-scoped, idempotent)
# --------------------------------------------------------------------------- #
def split(text: str) -> tuple[list[str] | None, str]:
    """``(front_matter_lines, body)``; lines exclude the ``---`` fences. None if no fm."""
    if not text.startswith("---"):
        return None, text
    lines = text.split("\n")
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines[1:i], "\n".join(lines[i + 1:])
    return None, text


def join(fm: list[str], body: str) -> str:
    return "---\n" + "\n".join(fm) + "\n---\n" + body


def body_sha(text: str) -> str:
    _fm, body = split(text)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def frontmatter(text: str) -> dict:
    fm, _ = split(text)
    if fm is None:
        return {}
    try:
        data = yaml.safe_load("\n".join(fm)) or {}
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def set_keys(text: str, updates: dict[str, str | None]) -> str:
    """Replace, add or remove top-level keys. ``updates`` maps key → rendered YAML for that
    key (``key: value`` or a block whose continuation lines are indented) or None to delete.
    Existing lines for an owned key and their indented continuation are removed; new
    renderings are appended at the end of the front matter, in ``updates`` order. Every
    other line is untouched."""
    fm, body = split(text)
    if fm is None:
        fm = []
    keys = set(updates)
    kept: list[str] = []
    skipping = False
    for line in fm:
        m = re.match(r"^([A-Za-z_][\w-]*):", line)
        if m:
            skipping = m.group(1) in keys
            if skipping:
                continue
        elif skipping and (line.startswith((" ", "\t")) or line.strip() == ""):
            continue
        else:
            skipping = False
        kept.append(line)
    while kept and kept[-1].strip() == "":
        kept.pop()
    for key, rendered in updates.items():
        if rendered is not None:
            kept.extend(rendered.rstrip("\n").split("\n"))
    return join(kept, body)


# --------------------------------------------------------------------------- #
# Renderers
# --------------------------------------------------------------------------- #
def _q(s: str) -> str:
    """Quote a scalar for a YAML flow mapping when it needs it."""
    return s if re.match(r"^[A-Za-z0-9_./:+@-]+$", s) else '"' + s.replace('"', '\\"') + '"'


def render_event(by: str, at: str) -> str:
    return f"{{by: {_q(by)}, at: {at}}}"


def render_generated(by: str, at: str) -> str:
    return f"generated: {render_event(by, at)}"


def render_verified(events: list[dict]) -> str:
    return "verified: [" + ", ".join(render_event(str(e.get("by", "")), _iso(e.get("at", ""))) for e in events) + "]"


def render_sources(entries: list[tuple[str, str]]) -> str | None:
    """``[(resource, title), ...]`` → a block list, or None when empty."""
    if not entries:
        return None
    lines = ["sources:"]
    for resource, title in entries:
        lines.append(f"  - {{resource: {_q(resource)}, title: {_q(title)}}}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Stamps
# --------------------------------------------------------------------------- #
def stamp_generated(text: str, by: str, at: str, refresh: bool) -> str:
    """Set ``generated`` if absent; refresh its ``at`` only when ``refresh`` (body changed)."""
    cur = frontmatter(text).get("generated")
    if isinstance(cur, dict) and cur.get("by") and cur.get("at") and not refresh:
        return text
    return set_keys(text, {"generated": render_generated(by, at)})


def stamp_verified(text: str, by: str | None, at: str) -> str:
    """Add/replace the tool entry whose producer matches ``by``'s producer, keep every
    other entry (a ``human:`` review survives). ``by=None`` removes the tool entry."""
    cur = frontmatter(text).get("verified")
    events = cur if isinstance(cur, list) else ([cur] if isinstance(cur, dict) else [])
    events = [e for e in events if isinstance(e, dict)]
    producer = (by or "wikify-verify/").split("/", 1)[0]
    others = [e for e in events if not str(e.get("by", "")).startswith(producer + "/")]
    new = others + ([{"by": by, "at": at}] if by else [])
    if not new:
        return set_keys(text, {"verified": None})
    return set_keys(text, {"verified": render_verified(new)})


def cited_files(page_path: str | Path, graph) -> list[tuple[str, int]]:
    """Definition files the page's citations resolve to, with occurrence counts, most cited
    first. Counts occurrences (a file cited five times outranks one cited once)."""
    page_path = Path(page_path)
    counts: Counter = Counter()
    for line in page_path.read_text(encoding="utf-8").splitlines():
        for _label, target in _LINK.findall(line):
            if not _is_symbol_link(target):
                continue
            m = _resolve_citation(page_path, target)
            if m and m in graph.symbols and graph.symbols[m].def_path:
                counts[graph.symbols[m].def_path] += 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def source_entries(files: list[tuple[str, int]], base: str | None, max_sources: int = MAX_SOURCES
                   ) -> list[tuple[str, str]]:
    """``(resource, title)`` per file: ``<base>/<path>`` when a base is known (a github blob
    base from ``source_url``, or a page-relative path into the pinned checkout)."""
    if base is None:
        return []
    return [(f"{base.rstrip('/')}/{path}", path) for path, _n in files[:max_sources]]


def snapshot_resource(source_url: str | None, fallback: str | None) -> str | None:
    """Bundle-level provenance for the silo index: the pinned tree. A github ``.../blob/<sha>``
    base becomes ``.../tree/<sha>``; otherwise the base itself or the local fallback."""
    if source_url == "":
        return None
    if source_url:
        return source_url.rstrip("/").replace("/blob/", "/tree/")
    return fallback


def strip_invalid_status(text: str) -> str:
    """Drop a ``status`` whose value is not an OKF status (wikify's old ``status: fresh``)."""
    cur = frontmatter(text).get("status")
    if cur is None or str(cur) in STATUS_VALUES:
        return text
    return set_keys(text, {"status": None})


# --------------------------------------------------------------------------- #
# Shape warnings (never a gate)
# --------------------------------------------------------------------------- #
def warnings(page_path: str | Path) -> list[str]:
    fm = frontmatter(Path(page_path).read_text(encoding="utf-8", errors="replace"))
    out: list[str] = []
    name = Path(page_path).name

    def check_event(e, field: str) -> None:
        if not isinstance(e, dict) or not e.get("by"):
            out.append(f"{name}: {field} entry must be a {{by, at}} mapping with a non-empty by")
            return
        if not ACTOR_RE.match(str(e["by"])):
            out.append(f"{name}: {field}.by {e['by']!r} is not an OKF actor "
                       f"(producer/version, human:<id>, process:<id>)")
        at = e.get("at")
        if at is not None and not isinstance(at, datetime) and not _DT_RE.match(str(at)):
            out.append(f"{name}: {field}.at {at!r} is not an ISO 8601 datetime with an offset")

    if "generated" in fm:
        check_event(fm["generated"], "generated")
    if "verified" in fm:
        v = fm["verified"]
        for e in (v if isinstance(v, list) else [v]):
            check_event(e, "verified")
    if "status" in fm and str(fm["status"]) not in STATUS_VALUES:
        out.append(f"{name}: status {fm['status']!r} is not one of {', '.join(STATUS_VALUES)}")
    if "stale_after" in fm and not isinstance(fm["stale_after"], datetime) \
            and not _DT_RE.match(str(fm["stale_after"])):
        out.append(f"{name}: stale_after is not an ISO 8601 datetime with an offset")
    return out
