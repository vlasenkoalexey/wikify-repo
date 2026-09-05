"""Per-repo config parser (design.md "Per-repo config (markdown, not TOML)").

The config is **markdown with YAML frontmatter** — the same shape as a wiki
page, so the agent edits it with no second syntax. Frontmatter carries typed
scalars (slug, languages, build, ref, tests/docs globs); the body's
``## Concepts`` list is the wiki's table of contents, with optional per-concept
seed entry-point symbols.

This module is pure Python (no model call): it parses the file into a
``RepoConfig`` and validates structure (known frontmatter keys, ``slug`` present,
a ``## Concepts`` section) — the strict parse TOML would give, recovered with the
linter tooling already in the build.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Frontmatter keys the schema allows; anything else is a config error.
_ALLOWED_KEYS = {"slug", "languages", "build", "ref", "tests", "docs", "repo",
                 "compile_commands", "index_shards", "bazel_targets", "source_url",
                 "acquire", "wiki_subdir", "source_type", "doc_globs",
                 "coverage_collapse", "coverage_exclude", "synthesis_focus",
                 "agenda", "agenda_max", "agenda_exclude", "in_repo", "wiki_dir"}

# ``agenda:`` — how the derived agenda is planned (implementation.md §10.11).
_AGENDA_MODES = ("subsystems", "modules")

# Separators between a concept name and its ``seeds:`` clause: em-dash or hyphen.
_DASH = "—"

# ``- **name** — seeds: ...`` or ``- name - seeds: ...``
_CONCEPT_RE = re.compile(
    r"^-\s+"                               # list bullet
    r"(?:\*\*(?P<bold>[^*]+)\*\*|(?P<word>\S+))"  # **name** or first word
    r"(?P<rest>.*)$"                       # remainder (dash + seeds + note)
)

# ``— seeds: <payload>`` / ``- seeds: <payload>`` — capture the seeds payload.
_SEEDS_RE = re.compile(
    rf"[{_DASH}\-]\s*seeds:\s*(?P<payload>.*)$",
    re.IGNORECASE,
)

_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_BACKTICK_TOKEN_RE = re.compile(r"`([^`]+)`")
_AUTO_RE = re.compile(r"^\(\s*(?:auto|discover\b.*?)\)\s*$", re.IGNORECASE)
# ``(subsystem: <dir prefix>)`` — seed the concept from a whole subsystem (every library
# module at/under the prefix): entry points + hubs, re-derived at each prepare.
_SUBSYSTEM_RE = re.compile(r"^\(\s*subsystem:\s*(?P<prefix>[^)]+?)\s*\)\s*$", re.IGNORECASE)


@dataclass
class Concept:
    """One architectural concept from the ``## Concepts`` list.

    ``seeds`` are backtick-quoted symbol tokens (backticks stripped); empty when
    the seeds clause was ``(auto)`` or ``(discover: ...)``, in which case
    ``auto`` is True (Stage 5 discovers entry points instead). ``(subsystem: <prefix>)``
    sets ``subsystem`` — the concept is seeded from that directory's entry points and
    hubs (``subsystems.subsystem_for_prefix``), re-derived at each ``prepare``.
    """

    slug: str
    seeds: list[str] = field(default_factory=list)
    auto: bool = False
    note: str = ""
    subsystem: str | None = None


@dataclass
class RepoConfig:
    """Parsed ``config/<slug>.md`` — an authored ingest input, not a product."""

    slug: str
    languages: list[str] = field(default_factory=list)
    build: str | None = None
    ref: str | None = None
    repo: str | None = None  # local path or git URL of the source (Stage 0)
    # How Stage 0 brings a git-URL source into ``raw/code/<slug>``: "clone" (default —
    # plain clone, ``raw/`` gitignored, pin recorded in state) or "submodule" (add it as
    # a git submodule so the pin is the committed gitlink — reproducible via
    # ``git submodule update --init``; requires the wiki to be a git repo). Local-path
    # sources are always symlinked in place regardless.
    acquire: str | None = None
    # Where this repo's wiki is placed under ``wiki/``: ``wiki/<wiki_subdir>/<slug>``.
    # Default "code" (so ``wiki/code/<slug>``, leaving ``wiki/`` for a curated index +
    # prose); set "" to put it directly at ``wiki/<slug>`` (the classic single-wiki layout).
    wiki_subdir: str = "code"
    # "code" (default — SCIP symbol grounding, per-module catalogs) or "docs" (prose/doc
    # ingestion — grounding anchored to a source document + section, coverage over files;
    # see docs.py / design.md "Docs mode"). "auto" picks docs when SCIP finds no symbols.
    source_type: str = "code"
    # In docs mode, repo-relative globs selecting which docs to ingest. Empty →
    # ``docs.DEFAULT_DOC_GLOBS`` (markdown/rst/html/txt).
    doc_globs: list[str] = field(default_factory=list)
    # Coverage trimming (Stage 6b). ``coverage_collapse`` globs → the module still gets a
    # catalog page with its citeable symbol map but the detailed member body is omitted
    # (model zoos / boilerplate — keeps citations resolving, drops the bulk).
    # ``coverage_exclude`` globs → no catalog page at all (ONLY for uncited noise:
    # tests/vendored — a dropped symbol cannot be cited). ``*`` spans ``/``.
    coverage_collapse: list[str] = field(default_factory=list)
    coverage_exclude: list[str] = field(default_factory=list)
    # A domain **lens** foregrounded during synthesis: the overview + concept pages organize around
    # it and lead with the symbols that matter for it (e.g. "TPU performance — kernels, sharding,
    # attention, autotune knobs, precision, memory"). Surfaced in every packet + the doc worklist;
    # honored by prompts/synthesis.md, overview.md, ingest-docs.md. Empty → neutral synthesis.
    synthesis_focus: str = ""
    # Agenda planner (Stage 5, §10.11). ``subsystems`` (directory-shaped units with entry
    # points — the default for a fresh silo) or ``modules`` (legacy: single modules by
    # centrality). Unset on a silo that already has state → ``modules`` (no surprise
    # rebuilds on a --ref bump); set it explicitly to switch. ``agenda_max`` caps the
    # planned pages; ``agenda_exclude`` globs drop subsystems by directory prefix.
    agenda: str | None = None
    agenda_max: int | None = None
    agenda_exclude: list[str] = field(default_factory=list)
    # In-repo layout (§10.15): the config is ``<repo>/wikify.md``, the source is the repo
    # itself (no raw/), the wiki lives at ``<repo>/<wiki_dir>/`` (flat, no slug level) and
    # the cache at ``<repo>/.wikify/``. Set by ``wikify init``; host-wiki projects never set it.
    in_repo: bool = False
    wiki_dir: str = "wiki"
    compile_commands: str | None = None  # path to a pre-existing compile_commands.json
    # bazel target pattern (e.g. "//pkg/...") to AUTO-generate the C++ compile DB
    # from — `prepare` runs bazel build+aquery and converts it (wikify/bazel_cc.py),
    # so a mixed bazel repo indexes with one command. Takes precedence over
    # compile_commands when set.
    bazel_targets: str | None = None
    # Source links in catalogs. Default (unset): a path RELATIVE to each catalog
    # page, pointing at the local indexed repo (never an absolute path). Set to a
    # base URL (e.g. "https://github.com/org/repo/blob/<commit>") for a published
    # wiki, or "" to disable links.
    source_url: str | None = None
    # Repo-relative globs to shard the Python index across processes (scip-python
    # `--target-only`). Each expanded path is one concurrent indexer with a bounded
    # working set — the only way to index repos too large for one pyright process
    # (e.g. pytorch OOMs whole-repo). Empty → single whole-repo indexer.
    index_shards: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    docs: list[str] = field(default_factory=list)
    concepts: list[Concept] = field(default_factory=list)


def _split_frontmatter(text: str) -> tuple[str, str]:
    """Return ``(frontmatter_yaml, body)`` from leading ``---`` fences."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("config must start with a '---' YAML frontmatter fence")
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[1:i]), "\n".join(lines[i + 1:])
    raise ValueError("unterminated YAML frontmatter (missing closing '---')")


def _as_list(value: object) -> list[str]:
    """Coerce an absent/scalar/list frontmatter value to a list of str."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]


def _parse_seeds(payload: str) -> tuple[list[str], bool, str, str | None]:
    """Parse a ``seeds:`` payload → ``(seeds, auto, note, subsystem)``.

    ``(auto)`` / ``(discover: ...)`` → ``auto=True`` with no seeds. ``(subsystem: <prefix>)``
    → ``auto=True`` plus the prefix. Otherwise the backtick-quoted tokens are the seeds;
    any trailing non-token text is the note.
    """
    payload = payload.strip()
    if _AUTO_RE.match(payload):
        return [], True, "", None
    sm = _SUBSYSTEM_RE.match(payload)
    if sm:
        prefix = sm.group("prefix").strip().strip("`").strip("/")
        return [], True, f"subsystem: {prefix}", prefix
    seeds = [m.group(1).strip() for m in _BACKTICK_TOKEN_RE.finditer(payload)]
    note = _BACKTICK_TOKEN_RE.sub("", payload)
    note = note.strip().strip(",").strip()
    return seeds, False, note, None


def _parse_concept(line: str) -> Concept:
    """Parse one ``- ...`` concept list item into a ``Concept``."""
    raw = line.rstrip()
    note_from_comment = ""
    comments = _HTML_COMMENT_RE.findall(raw)
    if comments:
        # strip ``<!-- ... -->`` and keep its inner text as a note fallback
        note_from_comment = " ".join(
            c[len("<!--"):-len("-->")].strip() for c in comments
        ).strip()
        raw = _HTML_COMMENT_RE.sub("", raw).rstrip()

    m = _CONCEPT_RE.match(raw)
    if not m:
        raise ValueError(f"malformed concept list item: {line!r}")
    slug = (m.group("bold") or m.group("word")).strip()
    rest = m.group("rest")

    seeds: list[str] = []
    auto = False
    note = ""
    subsystem: str | None = None
    sm = _SEEDS_RE.search(rest)
    if sm:
        seeds, auto, note, subsystem = _parse_seeds(sm.group("payload"))
    else:
        # no seeds clause; treat any remaining dash-prefixed text as a note
        note = rest.lstrip(f" {_DASH}-").strip()

    if not note:
        note = note_from_comment
    return Concept(slug=slug, seeds=seeds, auto=auto, note=note, subsystem=subsystem)


def _parse_concepts(body: str) -> list[Concept]:
    """Extract concepts from the ``## Concepts`` section of the body."""
    lines = body.splitlines()
    start = None
    for i, line in enumerate(lines):
        if re.match(r"^##\s+(Concepts|Concerns)\s*$", line.strip()):  # back-compat
            start = i + 1
            break
    if start is None:
        raise ValueError("config has no '## Concepts' section")

    concepts: list[Concept] = []
    for line in lines[start:]:
        stripped = line.strip()
        if stripped.startswith("## "):       # next section ends the list
            break
        if stripped.startswith("- "):
            concepts.append(_parse_concept(stripped))
    return concepts


def validate_config(cfg: RepoConfig) -> None:
    """Raise ``ValueError`` if the parsed config violates the schema."""
    if not cfg.slug:
        raise ValueError("config frontmatter is missing required key 'slug'")
    if cfg.agenda is not None and cfg.agenda not in _AGENDA_MODES:
        raise ValueError(
            f"agenda: must be one of {', '.join(_AGENDA_MODES)} (got {cfg.agenda!r})")
    if cfg.agenda_max is not None and cfg.agenda_max < 1:
        raise ValueError("agenda_max: must be a positive integer")


def load_config(path: str | Path) -> RepoConfig:
    """Parse and validate ``config/<slug>.md`` into a ``RepoConfig``."""
    text = Path(path).read_text(encoding="utf-8")
    fm_text, body = _split_frontmatter(text)

    fm = yaml.safe_load(fm_text) or {}
    if not isinstance(fm, dict):
        raise ValueError("config frontmatter must be a YAML mapping")

    unknown = set(fm) - _ALLOWED_KEYS
    if unknown:
        raise ValueError(
            f"unknown frontmatter key(s): {', '.join(sorted(unknown))}; "
            f"allowed: {', '.join(sorted(_ALLOWED_KEYS))}"
        )
    if "slug" not in fm or fm["slug"] in (None, ""):
        raise ValueError("config frontmatter is missing required key 'slug'")

    build = fm.get("build")
    ref = fm.get("ref")
    repo = fm.get("repo")
    cc = fm.get("compile_commands")
    bt = fm.get("bazel_targets")
    su = fm.get("source_url")
    acq = fm.get("acquire")
    wsub = fm.get("wiki_subdir")
    stype = fm.get("source_type")
    agenda = fm.get("agenda")
    amax = fm.get("agenda_max")
    cfg = RepoConfig(
        slug=str(fm["slug"]),
        languages=_as_list(fm.get("languages")),
        build=None if build is None else str(build),
        ref=None if ref is None else str(ref),
        repo=None if repo is None else str(repo),
        compile_commands=None if cc is None else str(cc),
        bazel_targets=None if bt is None else str(bt),
        source_url=None if su is None else str(su),
        acquire=None if acq is None else str(acq),
        wiki_subdir="code" if wsub is None else str(wsub),
        source_type="code" if stype is None else str(stype),
        doc_globs=_as_list(fm.get("doc_globs")),
        coverage_collapse=_as_list(fm.get("coverage_collapse")),
        coverage_exclude=_as_list(fm.get("coverage_exclude")),
        synthesis_focus="" if fm.get("synthesis_focus") is None else str(fm.get("synthesis_focus")),
        agenda=None if agenda is None else str(agenda).strip().lower(),
        agenda_max=None if amax is None else int(amax),
        agenda_exclude=_as_list(fm.get("agenda_exclude")),
        in_repo=bool(fm.get("in_repo", False)),
        wiki_dir=str(fm.get("wiki_dir") or "wiki").strip("/") or "wiki",
        index_shards=_as_list(fm.get("index_shards")),
        tests=_as_list(fm.get("tests")),
        docs=_as_list(fm.get("docs")),
        concepts=_parse_concepts(body),
    )
    validate_config(cfg)
    return cfg
