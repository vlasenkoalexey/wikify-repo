"""Tests for slug.py — moniker → filename stem (implementation.md §5.2)."""

from __future__ import annotations

import re

from wikify.slug import SlugAllocator, slug_for

_SAFE = re.compile(r"\A[A-Za-z0-9._-]*\Z")

# Realistic SCIP symbols (grammar: <scheme> <manager> <package> <version> desc).
TRAINER_STEP = "scip-python python torchtitan 0.0.0 `torchtitan.train`/Trainer#step()."
COMPUTE = "scip-python python mathlib 0.0.0 `mathlib`/compute()."


def _assert_clean(slug: str) -> None:
    assert _SAFE.match(slug), f"unsafe chars in {slug!r}"
    assert not slug.endswith(".md")
    assert not slug.startswith("-") and not slug.endswith("-")
    assert "--" not in slug


def test_deterministic() -> None:
    assert slug_for(TRAINER_STEP) == slug_for(TRAINER_STEP)


def test_clean_and_readable() -> None:
    slug = slug_for(TRAINER_STEP)
    _assert_clean(slug)
    # <lang>-<package>-<descriptor names joined by ->
    assert slug == "python-torchtitan-torchtitan.train-Trainer-step"


def test_compute_descriptor_path_readable() -> None:
    slug = slug_for(COMPUTE)
    _assert_clean(slug)
    assert "mathlib" in slug
    assert "compute" in slug


def test_case_preserved() -> None:
    # Trainer is capitalized in the moniker and must stay capitalized.
    slug = slug_for(TRAINER_STEP)
    assert "Trainer" in slug
    assert "trainer" not in slug  # not lowercased


def test_fallback_to_scheme_when_no_manager() -> None:
    # Empty manager → fall back to scheme.
    moniker = "scip-python  pkg 0.0.0 `pkg`/Thing#"
    slug = slug_for(moniker)
    _assert_clean(slug)
    assert slug.startswith("scip-python")


def test_local_symbol() -> None:
    slug = slug_for("local 1")
    _assert_clean(slug)
    assert slug == "local-1"


def test_allocator_same_moniker_same_slug() -> None:
    alloc = SlugAllocator()
    first = alloc.allocate(TRAINER_STEP)
    second = alloc.allocate(TRAINER_STEP)
    assert first == second


def test_allocator_disambiguates_collision() -> None:
    # Two DIFFERENT monikers whose base slugs sanitize identically: one uses a
    # ``/`` namespace suffix, the other a ``#`` type suffix on the terminal —
    # both punctuation chars sanitize to ``-`` and then collapse away.
    a = "scip-python python pkg 0.0.0 `pkg`/Thing#"
    b = "scip-python python pkg 0.0.0 `pkg`/Thing."
    assert slug_for(a) == slug_for(b)  # base slugs collide

    alloc = SlugAllocator()
    slug_a = alloc.allocate(a)
    slug_b = alloc.allocate(b)
    assert slug_a != slug_b
    _assert_clean(slug_a)
    _assert_clean(slug_b)
    # First-allocated keeps the clean base; second gets the hash suffix.
    assert slug_a == slug_for(a)
    assert slug_b.startswith(slug_for(b) + "-")


def test_allocator_stable_regardless_of_repeats() -> None:
    alloc = SlugAllocator()
    a = "scip-python python pkg 0.0.0 `pkg`/Thing#"
    b = "scip-python python pkg 0.0.0 `pkg`/Thing."
    s1 = alloc.allocate(a)
    s2 = alloc.allocate(b)
    assert alloc.allocate(a) == s1
    assert alloc.allocate(b) == s2
