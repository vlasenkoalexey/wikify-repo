"""Parse SCIP symbol strings → structured monikers (implementation.md §5.1/§5.2).

A SCIP symbol (the authoritative citation id) is a string like::

    scip-python python mathlib 0.0.0 `mathlib`/compute().

Grammar (from scip.proto): ``<scheme> <manager> <name> <version> (<descriptor>)+``
or the special form ``local <id>``. Descriptors carry a suffix char encoding the
kind: ``/`` namespace, ``#`` type, ``.`` term, ``(<disambig>).`` method,
``:`` meta, ``!`` macro, ``[name]`` type-parameter, ``(name)`` parameter. Names
may be backtick-escaped (with ``` `` ``` for a literal backtick).

This parser is used both to read the terminal descriptor (kind/name) when
building the graph and to derive readable filename slugs (``slug.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field

# suffix-char → suffix-name for trailing-delimiter descriptors
_SUFFIX = {"/": "Namespace", "#": "Type", ".": "Term", ":": "Meta", "!": "Macro"}
_DELIMS = set("/#.:!([")


@dataclass
class ParsedSymbol:
    is_local: bool = False
    local_id: str = ""
    scheme: str = ""
    manager: str = ""
    package: str = ""
    version: str = ""
    descriptors: list[tuple[str, str]] = field(default_factory=list)  # (name, suffix)

    @property
    def terminal(self) -> tuple[str, str]:
        """(name, suffix) of the last descriptor, or ('', '') if none."""
        return self.descriptors[-1] if self.descriptors else ("", "")


def parse_symbol(symbol: str) -> ParsedSymbol:
    if symbol.startswith("local "):
        return ParsedSymbol(is_local=True, local_id=symbol[len("local "):])
    # scheme + 3 package fields, split on the first 4 unescaped spaces.
    parts = symbol.split(" ", 4)
    if len(parts) < 5:
        return ParsedSymbol(scheme=parts[0] if parts else "")
    scheme, manager, package, version, rest = parts
    return ParsedSymbol(
        scheme=scheme,
        manager=manager,
        package=package,
        version=version,
        descriptors=_parse_descriptors(rest),
    )


def _parse_name(s: str, i: int) -> tuple[str, int]:
    """Read a (possibly backtick-escaped) descriptor name starting at ``i``."""
    n = len(s)
    if i < n and s[i] == "`":
        i += 1
        buf = []
        while i < n:
            if s[i] == "`":
                if i + 1 < n and s[i + 1] == "`":  # `` → literal backtick
                    buf.append("`")
                    i += 2
                    continue
                i += 1  # closing backtick
                break
            buf.append(s[i])
            i += 1
        return "".join(buf), i
    start = i
    while i < n and s[i] not in _DELIMS:
        i += 1
    return s[start:i], i


def _parse_descriptors(s: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == "[":  # type-parameter: [name]
            name, j = _parse_name(s, i + 1)
            i = j + 1 if j < n and s[j] == "]" else j
            out.append((name, "TypeParameter"))
            continue
        if c == "(":  # parameter: (name)
            name, j = _parse_name(s, i + 1)
            i = j + 1 if j < n and s[j] == ")" else j
            out.append((name, "Parameter"))
            continue
        name, i = _parse_name(s, i)
        if i >= n:
            if name:
                out.append((name, "Term"))
            break
        c = s[i]
        if c == "(":  # method: name(<disambiguator>).
            depth, j = 1, i + 1
            while j < n and depth:
                if s[j] == "(":
                    depth += 1
                elif s[j] == ")":
                    depth -= 1
                j += 1
            i = j
            if i < n and s[i] == ".":
                i += 1
            out.append((name, "Method"))
        elif c in _SUFFIX:
            out.append((name, _SUFFIX[c]))
            i += 1
        else:  # unexpected char; avoid infinite loop
            i += 1
    return out
