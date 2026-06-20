"""SCIP moniker → readable filename stem (implementation.md §5.2).

The filename is a readable slug ``<lang>-<package>-<descriptor-path>`` where the
descriptor path is the descriptor *names* joined by ``-``. Descriptor-suffix
punctuation and any character outside ``[A-Za-z0-9._-]`` is replaced with ``-``,
repeated ``-`` are collapsed, and leading/trailing ``-`` are stripped. Case is
preserved (descriptor names are case-significant).

The slug is **not** the authoritative identifier: the linter resolves citations
by reading the target stub's frontmatter ``moniker``, never by parsing the
filename. So the slug only needs to be deterministic + collision-free, not
invertible. ``SlugAllocator`` disambiguates two distinct monikers that map to
the same base slug by appending ``-<first8 of sha256(moniker)>``.
"""

from __future__ import annotations

import hashlib
import re

from .monikers import parse_symbol

# Anything outside the safe filename alphabet collapses to a single hyphen. This
# covers descriptor-suffix punctuation (``# . / ( ) [ ]``) too.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
_DASHES = re.compile(r"-+")


def _sanitize(text: str) -> str:
    """Replace unsafe chars with ``-``, collapse repeats, strip ends."""
    text = _UNSAFE.sub("-", text)
    text = _DASHES.sub("-", text)
    return text.strip("-")


def slug_for(moniker: str) -> str:
    """Deterministic base filename stem for ``moniker`` (no ``.md``).

    Collision handling is the allocator's job, not this function's.
    """
    parsed = parse_symbol(moniker)
    if parsed.is_local:
        return _sanitize(f"local-{parsed.local_id}")

    lang = parsed.manager or parsed.scheme
    descriptor_path = "-".join(name for name, _suffix in parsed.descriptors)
    return _sanitize(f"{lang}-{parsed.package}-{descriptor_path}")


def _hash8(moniker: str) -> str:
    return hashlib.sha256(moniker.encode("utf-8")).hexdigest()[:8]


class SlugAllocator:
    """Hands out collision-free slugs, stable across a run.

    The same moniker always yields the same slug. Two *different* monikers that
    share a base slug are disambiguated with a hash suffix so each filename maps
    to exactly one moniker.
    """

    def __init__(self) -> None:
        self._by_moniker: dict[str, str] = {}   # moniker -> allocated slug
        self._by_slug: dict[str, str] = {}       # allocated slug -> moniker

    def allocate(self, moniker: str) -> str:
        existing = self._by_moniker.get(moniker)
        if existing is not None:
            return existing

        base = slug_for(moniker)
        owner = self._by_slug.get(base)
        if owner is None or owner == moniker:
            slug = base
        else:
            slug = _sanitize(f"{base}-{_hash8(moniker)}")
            # Extremely unlikely, but keep probing to stay collision-free.
            while True:
                owner = self._by_slug.get(slug)
                if owner is None or owner == moniker:
                    break
                slug = _sanitize(f"{slug}-{_hash8(slug)}")

        self._by_moniker[moniker] = slug
        self._by_slug[slug] = moniker
        return slug
