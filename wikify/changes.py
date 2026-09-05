"""Version-to-version change records (§10.14, design.md "History is routing, not content").

On a ``--ref`` bump the wiki should say what changed and why, in the authors' words, and
route an agent to the commit — never copy the diff. Three layers, all deterministic:

1. **Commit context in packets**: for each page being rebuilt, the commits between the old
   and new pin that touched the files its citations resolve to (``git log old..new --
   <files>``, merges dropped, capped) go into the packet as ``## Since last ingest`` so
   synthesis can write a short ``## Recent changes`` section (not a claim section: no gate).
2. **A per-version change page** ``changes/<new-ref>.md``: counts, the pages built /
   rebuilt / relinked, and the commits grouped by the pages they affected, each commit a
   short hash + subject + files, linked to the commit on the forge when ``source_url``
   names one, else with the ``git show`` hint for the pinned checkout. The deterministic
   block lives between ``changes:auto`` markers; prose above it (an optional narrative
   the skill may write) survives regeneration — the same contract as connect blocks.
3. **A silo ``log.md`` line per ingest** (the OKF reserved log document).

``prepare`` collects the commits and writes ``.cache/plan/<slug>.reconcile.json``;
``finalize`` turns it into the change page, the log line and the index section.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

MAX_COMMITS = 400          # hard cap on collected commits per bump (nightlies can be hundreds)
MAX_PER_PAGE = 20          # commits shown in a packet's "Since last ingest" block
MAX_FILES_SHOWN = 8
AUTO_BEGIN = "<!-- changes:auto:begin -->"
AUTO_END = "<!-- changes:auto:end -->"


@dataclass
class Commit:
    sha: str
    short: str
    date: str
    subject: str
    body: str = ""
    files: list[str] = field(default_factory=list)
    pages: list[str] = field(default_factory=list)    # wiki pages whose cited files it touched


@dataclass
class Reconcile:
    """What one ``prepare`` decided, persisted for ``finalize``."""
    old_ref: str | None
    new_ref: str
    build: list[str] = field(default_factory=list)
    rebuild: list[str] = field(default_factory=list)
    relink: list[str] = field(default_factory=list)
    leave: list[str] = field(default_factory=list)
    changed: int = 0
    removed: int = 0
    moved: int = 0
    removed_files: list[str] = field(default_factory=list)
    commits: list[Commit] = field(default_factory=list)
    truncated: int = 0                                  # commits beyond MAX_COMMITS

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=1)

    @classmethod
    def from_json(cls, text: str) -> "Reconcile":
        d = json.loads(text)
        d["commits"] = [Commit(**c) for c in d.get("commits", [])]
        return cls(**d)


def reconcile_path(cache_dir: str | Path, slug: str) -> Path:
    return Path(cache_dir) / "plan" / f"{slug}.reconcile.json"


# --------------------------------------------------------------------------- #
# git
# --------------------------------------------------------------------------- #
_FMT = "%x1e%H%x1f%h%x1f%ad%x1f%s%x1f%b%x1f"


def git_commits(repo_dir: str | Path, old_ref: str, new_ref: str,
                max_commits: int = MAX_COMMITS) -> tuple[list[Commit], int]:
    """Non-merge commits in ``old_ref..new_ref`` with the files each touched, newest
    first. Returns ``(commits, truncated_count)``; empty on any git failure (not a repo,
    shallow clone without the old ref, identical refs)."""
    if not old_ref or old_ref == new_ref:
        return [], 0
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_dir), "log", "--no-merges", "--name-only",
             "--date=short", f"--format={_FMT}", f"{old_ref}..{new_ref}"],
            capture_output=True, text=True, check=True, errors="replace",
        ).stdout
    except (subprocess.CalledProcessError, OSError):
        return [], 0
    commits = parse_git_log(out)
    truncated = max(0, len(commits) - max_commits)
    return commits[:max_commits], truncated


def parse_git_log(out: str) -> list[Commit]:
    commits: list[Commit] = []
    for rec in out.split("\x1e"):
        if not rec.strip():
            continue
        parts = rec.split("\x1f")
        if len(parts) < 6:
            continue
        sha, short, date, subject, body, rest = parts[0], parts[1], parts[2], parts[3], parts[4], parts[5]
        files = [l.strip() for l in rest.splitlines() if l.strip()]
        commits.append(Commit(sha=sha.strip(), short=short.strip(), date=date.strip(),
                              subject=subject.strip(), body=body.strip(), files=files))
    return commits


def attribute_pages(commits: list[Commit], page_files: dict[str, set[str]]) -> None:
    """Mark each commit with the wiki pages whose cited files it touched (in place)."""
    for c in commits:
        touched = set(c.files)
        c.pages = sorted(p for p, files in page_files.items() if files & touched)


def commit_url(source_url: str | None, sha: str) -> str | None:
    """``https://host/org/repo/blob/<pin>`` → ``https://host/org/repo/commit/<sha>``."""
    if not source_url:
        return None
    m = re.match(r"^(https?://[^/]+/[^/]+/[^/]+)/(blob|tree)/[^/]+/?$", source_url.rstrip("/"))
    return f"{m.group(1)}/commit/{sha}" if m else None


# --------------------------------------------------------------------------- #
# Renderers
# --------------------------------------------------------------------------- #
def render_since_block(page: str, commits: list[Commit], old_ref: str, max_per_page: int = MAX_PER_PAGE) -> str:
    """The packet's ``## Since last ingest`` block for one rebuilt page."""
    mine = [c for c in commits if page in c.pages][:max_per_page]
    if not mine:
        return ""
    lines = ["## Since last ingest",
             f"Commits since `{old_ref[:10]}` that touched files this page cites (authors' own words — "
             f"the *why* behind the change; quote them by short hash in a `## Recent changes` section). "
             f"The diff itself is in git: `git show <sha>` in the pinned checkout.", ""]
    for c in mine:
        lines.append(f"- `{c.short}` ({c.date}) {c.subject}")
        for bl in [b for b in c.body.splitlines() if b.strip()][:4]:
            lines.append(f"    {bl.strip()[:160]}")
        shown = ", ".join(f"`{f}`" for f in c.files[:MAX_FILES_SHOWN])
        more = f" (+{len(c.files) - MAX_FILES_SHOWN} more)" if len(c.files) > MAX_FILES_SHOWN else ""
        lines.append(f"    files: {shown}{more}")
    lines.append("")
    return "\n".join(lines)


def _link(c: Commit, source_url: str | None) -> str:
    url = commit_url(source_url, c.sha)
    return f"[`{c.short}`]({url})" if url else f"`{c.short}`"


def render_change_page(rec: Reconcile, slug: str, source_url: str | None, date: str,
                       generated_by: str, existing: str | None = None) -> str:
    """The ``changes/<ref>.md`` page. Deterministic content sits between the auto markers;
    anything above the begin marker in ``existing`` (a narrative) is preserved."""
    new_short, old_short = rec.new_ref[:10], (rec.old_ref or "")[:10]
    auto: list[str] = [AUTO_BEGIN, ""]
    a = auto.append
    a("## Summary")
    a(f"| | |\n|---|---|")
    a(f"| from | `{old_short or '(first ingest)'}` |")
    a(f"| to | `{new_short}` |")
    a(f"| commits | {len(rec.commits)}{f' (+{rec.truncated} not listed)' if rec.truncated else ''} |")
    a(f"| symbols | {rec.changed} changed, {rec.removed} removed, {rec.moved} moved |")
    a(f"| pages | {len(rec.build)} built, {len(rec.rebuild)} rebuilt, {len(rec.relink)} relinked, "
      f"{len(rec.leave)} unchanged |")
    a("")
    if rec.build or rec.rebuild or rec.relink:
        a("## Pages affected")
        a("| page | why |\n|---|---|")
        for p in rec.build:
            a(f"| [{p}](../concepts/{p}.md) | new |")
        for p in rec.rebuild:
            n = sum(1 for c in rec.commits if p in c.pages)
            a(f"| [{p}](../concepts/{p}.md) | rebuilt: cited code changed ({n} commit(s)) |")
        for p in rec.relink:
            a(f"| [{p}](../concepts/{p}.md) | relinked: cited symbols moved, content unchanged |")
        a("")
    if rec.removed_files:
        a("## Removed")
        a("Files whose symbols left the graph (pages citing them were rebuilt or need review):")
        for f in rec.removed_files[:50]:
            a(f"- `{f}`")
        if len(rec.removed_files) > 50:
            a(f"- (+{len(rec.removed_files) - 50} more)")
        a("")
    if rec.commits:
        a("## Commits by page")
        hint = ("Each hash links to the commit." if commit_url(source_url, "x")
                else f"Details: `git -C raw/code/{slug} show <sha>` in the pinned checkout.")
        a(hint)
        a("")
        by_page: dict[str, list[Commit]] = {}
        other: list[Commit] = []
        for c in rec.commits:
            if c.pages:
                for p in c.pages:
                    by_page.setdefault(p, []).append(c)
            else:
                other.append(c)
        for p in sorted(by_page, key=lambda x: (-len(by_page[x]), x)):
            a(f"### [{p}](../concepts/{p}.md)")
            for c in by_page[p]:
                files = ", ".join(f"`{f}`" for f in c.files[:MAX_FILES_SHOWN])
                more = f" (+{len(c.files) - MAX_FILES_SHOWN})" if len(c.files) > MAX_FILES_SHOWN else ""
                a(f"- {_link(c, source_url)} {c.date} — {c.subject}  \n  files: {files}{more}")
            a("")
        if other:
            dirs: dict[str, int] = {}
            for c in other:
                for f in c.files:
                    d = f.rsplit("/", 1)[0] if "/" in f else "(root)"
                    dirs[d] = dirs.get(d, 0) + 1
            top = ", ".join(f"`{d}` ({n})" for d, n in sorted(dirs.items(), key=lambda kv: (-kv[1], kv[0]))[:12])
            a(f"### Other commits ({len(other)}) — no wiki page cites the files they touched")
            a(f"Most-touched directories: {top}")
            for c in other[:30]:
                a(f"- {_link(c, source_url)} {c.date} — {c.subject}")
            if len(other) > 30:
                a(f"- (+{len(other) - 30} more)")
            a("")
    a(AUTO_END)

    head = ""
    if existing and AUTO_BEGIN in existing:
        head = existing.split(AUTO_BEGIN, 1)[0]
    if not head.strip():
        head = (f"---\ntype: changelog\ntitle: {slug} changes {old_short or 'initial'} → {new_short}\n"
                f"description: What changed in {slug} between {old_short or 'the first ingest'} and {new_short}: "
                f"the pages affected and the commits behind them, grouped by page.\n"
                f"generated: {{by: {generated_by}, at: {date}}}\n---\n"
                f"# {slug}: changes {old_short or 'initial'} → {new_short}\n\n"
                f"A routing page, not a diff: which subsystems and wiki pages changed between the two "
                f"pins, and which commits caused it. Follow a hash for the change itself.\n\n")
    return head + "\n".join(auto) + "\n"


def write_change_page(silo_dir: str | Path, rec: Reconcile, slug: str, source_url: str | None,
                      date: str, generated_by: str) -> Path:
    out = Path(silo_dir) / "changes" / f"{rec.new_ref[:10]}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    existing = out.read_text(encoding="utf-8") if out.exists() else None
    out.write_text(render_change_page(rec, slug, source_url, date, generated_by, existing), encoding="utf-8")
    return out


def append_log(silo_dir: str | Path, rec: Reconcile, slug: str, date: str) -> Path:
    """One line per ingest in the silo's ``log.md`` (OKF reserved document, newest last)."""
    log = Path(silo_dir) / "log.md"
    if not log.exists():
        log.write_text(f"---\ntitle: {slug} ingest log\n---\n# {slug} ingest log\n\nOne entry per ingest, "
                       f"oldest first. Per-version detail: `changes/<ref>.md`.\n", encoding="utf-8")
    old = (rec.old_ref or "")[:10] or "(first ingest)"
    entry = (f"\n## [{date}] ingest | {slug} @ {rec.new_ref[:10]} (from {old})\n"
             f"{len(rec.commits)} commit(s); symbols {rec.changed} changed, {rec.removed} removed, "
             f"{rec.moved} moved; pages {len(rec.build)} built, {len(rec.rebuild)} rebuilt, "
             f"{len(rec.relink)} relinked, {len(rec.leave)} unchanged"
             + (f" — [changes/{rec.new_ref[:10]}.md](changes/{rec.new_ref[:10]}.md)" if rec.old_ref else "") + "\n")
    marker = f"| {slug} @ {rec.new_ref[:10]} (from {old})"
    text = log.read_text(encoding="utf-8")
    if marker in text:
        return log
    log.write_text(text.rstrip("\n") + "\n" + entry, encoding="utf-8")
    return log
