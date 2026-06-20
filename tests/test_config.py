"""Tests for wikify.config — parsing config/<slug>.md (design.md "Per-repo config")."""

from __future__ import annotations

import textwrap

import pytest

from wikify.config import Concern, RepoConfig, load_config


FIXTURE = textwrap.dedent(
    """\
    ---
    slug: torch_tpu
    languages: [cpp, python]
    build: bazel
    ref: a1b9f0c
    tests: ["test/**/*.py", "test/cpp/**/*.cpp"]
    docs:  ["**/README*.md", "docs/**/*.md"]
    ---

    # torch_tpu — ingest config

    Some prose.

    ## Concerns
    - **compilation-pipeline** — seeds: `LazyGraphExecutor::Compile`, `Compiler::LowerToHlo`
    - **dispatch-path** — seeds: (auto)
    - **compute-comm-overlap** — seeds: `CollectiveScheduler::Schedule`
    - **memory-management** — seeds: `BufferAllocator::Allocate`  <!-- added 2026-06-19 -->
    """
)


def _write(tmp_path, text: str):
    p = tmp_path / "torch_tpu.md"
    p.write_text(text, encoding="utf-8")
    return p


def test_frontmatter_parsed(tmp_path):
    cfg = load_config(_write(tmp_path, FIXTURE))
    assert isinstance(cfg, RepoConfig)
    assert cfg.slug == "torch_tpu"
    assert cfg.languages == ["cpp", "python"]
    assert cfg.build == "bazel"
    assert cfg.ref == "a1b9f0c"
    assert cfg.tests == ["test/**/*.py", "test/cpp/**/*.cpp"]
    assert cfg.docs == ["**/README*.md", "docs/**/*.md"]


def test_concern_slugs(tmp_path):
    cfg = load_config(_write(tmp_path, FIXTURE))
    assert [c.slug for c in cfg.concerns] == [
        "compilation-pipeline",
        "dispatch-path",
        "compute-comm-overlap",
        "memory-management",
    ]


def test_seeds_backticks_stripped(tmp_path):
    cfg = load_config(_write(tmp_path, FIXTURE))
    by_slug = {c.slug: c for c in cfg.concerns}
    assert by_slug["compilation-pipeline"].seeds == [
        "LazyGraphExecutor::Compile",
        "Compiler::LowerToHlo",
    ]
    assert by_slug["compilation-pipeline"].auto is False
    assert by_slug["compute-comm-overlap"].seeds == ["CollectiveScheduler::Schedule"]


def test_auto_seeds(tmp_path):
    cfg = load_config(_write(tmp_path, FIXTURE))
    dispatch = next(c for c in cfg.concerns if c.slug == "dispatch-path")
    assert dispatch.auto is True
    assert dispatch.seeds == []


def test_html_comment_stripped_from_note(tmp_path):
    cfg = load_config(_write(tmp_path, FIXTURE))
    mem = next(c for c in cfg.concerns if c.slug == "memory-management")
    assert mem.seeds == ["BufferAllocator::Allocate"]
    # the seeds list must not contain the HTML comment text
    assert all("<!--" not in s for s in mem.seeds)


def test_discover_treated_as_auto(tmp_path):
    text = FIXTURE.replace(
        "- **dispatch-path** — seeds: (auto)",
        "- **dispatch-path** — seeds: (discover: top-centrality in dispatch)",
    )
    cfg = load_config(_write(tmp_path, text))
    dispatch = next(c for c in cfg.concerns if c.slug == "dispatch-path")
    assert dispatch.auto is True
    assert dispatch.seeds == []


def test_hyphen_separator_tolerated(tmp_path):
    text = FIXTURE.replace(
        "- **compute-comm-overlap** — seeds: `CollectiveScheduler::Schedule`",
        "- **compute-comm-overlap** - seeds: `CollectiveScheduler::Schedule`",
    )
    cfg = load_config(_write(tmp_path, text))
    overlap = next(c for c in cfg.concerns if c.slug == "compute-comm-overlap")
    assert overlap.seeds == ["CollectiveScheduler::Schedule"]


def test_concern_without_bold_uses_first_word(tmp_path):
    text = FIXTURE.replace(
        "- **dispatch-path** — seeds: (auto)",
        "- dispatch-path — seeds: (auto)",
    )
    cfg = load_config(_write(tmp_path, text))
    assert any(c.slug == "dispatch-path" for c in cfg.concerns)


def test_missing_frontmatter_lists_default_empty(tmp_path):
    text = textwrap.dedent(
        """\
        ---
        slug: minimal
        ---

        ## Concerns
        - **a-concern** — seeds: (auto)
        """
    )
    cfg = load_config(_write(tmp_path, text))
    assert cfg.languages == []
    assert cfg.tests == []
    assert cfg.docs == []
    assert cfg.build is None
    assert cfg.ref is None


def test_unknown_frontmatter_key_raises(tmp_path):
    text = textwrap.dedent(
        """\
        ---
        slug: torch_tpu
        bogus: nope
        ---

        ## Concerns
        - **a** — seeds: (auto)
        """
    )
    with pytest.raises(ValueError, match="unknown frontmatter key"):
        load_config(_write(tmp_path, text))


def test_missing_slug_raises(tmp_path):
    text = textwrap.dedent(
        """\
        ---
        languages: [python]
        ---

        ## Concerns
        - **a** — seeds: (auto)
        """
    )
    with pytest.raises(ValueError, match="slug"):
        load_config(_write(tmp_path, text))


def test_missing_concerns_section_raises(tmp_path):
    text = textwrap.dedent(
        """\
        ---
        slug: torch_tpu
        ---

        # torch_tpu

        No concerns here.
        """
    )
    with pytest.raises(ValueError, match="Concerns"):
        load_config(_write(tmp_path, text))


def test_concern_dataclass_defaults():
    c = Concern(slug="x")
    assert c.seeds == []
    assert c.auto is False
    assert c.note == ""
