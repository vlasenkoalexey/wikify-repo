"""AST fallback indexer (wikify.ast_fallback).

Recovers symbols from files a type checker can't analyze. The contract that makes
it useful: the synthesized monikers must match scip-python's scheme EXACTLY, so a
recovered definition unifies with references emitted by the real indexer (callers
populate by moniker join). These tests pin the moniker encoding and the join.
"""

from __future__ import annotations

from pathlib import Path

from wikify import ast_fallback, scip_index, scip_pb2


SAMPLE = '''\
"""module doc."""

CONST = 1


def top_level(a, b=2):
    """a function."""
    return a


class Outer(Base, metaclass=Meta):
    """outer class."""

    attr = 5

    def method(self, x):
        return x

    class Inner:
        def inner_method(self):
            pass
'''


def _write(tmp: Path, rel: str, text: str) -> Path:
    f = tmp / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)
    return tmp


def test_module_path():
    assert ast_fallback.module_path("torch/optim/adam.py") == "torch.optim.adam"
    assert ast_fallback.module_path("a/b/__init__.py") == "a.b"
    assert ast_fallback.module_path("top.py") == "top"


def test_moniker_encoding_matches_scip_scheme(tmp_path: Path):
    repo = _write(tmp_path, "pkg/mod.py", SAMPLE)
    idx = ast_fallback.synthesize_index(repo, ["pkg/mod.py"], "proj", "1.0")
    monikers = {s.symbol.split("1.0 ", 1)[1] for s in idx.documents[0].symbols}
    assert "`pkg.mod`/CONST." in monikers                       # module var
    assert "`pkg.mod`/top_level()." in monikers                 # function
    assert "`pkg.mod`/Outer#" in monikers                       # class
    assert "`pkg.mod`/Outer#attr." in monikers                  # class attr
    assert "`pkg.mod`/Outer#method()." in monikers              # method
    assert "`pkg.mod`/Outer#Inner#" in monikers                 # nested class
    assert "`pkg.mod`/Outer#Inner#inner_method()." in monikers  # nested method


def test_signature_and_docstring_captured(tmp_path: Path):
    repo = _write(tmp_path, "pkg/mod.py", SAMPLE)
    idx = ast_fallback.synthesize_index(repo, ["pkg/mod.py"], "proj", "1.0")
    outer = next(s for s in idx.documents[0].symbols if s.symbol.endswith("/Outer#"))
    assert outer.documentation[0] == "```python\nclass Outer(Base, metaclass=Meta):\n```"
    assert outer.documentation[1] == "outer class."


def test_recovered_symbol_joins_with_existing_references(tmp_path: Path):
    """A reference emitted by the 'real' indexer connects to the AST-recovered def."""
    repo = _write(tmp_path, "pkg/mod.py", SAMPLE)
    # Fabricate a separate document that REFERENCES pkg.mod.Outer (as the real
    # scip-python would), with no SymbolInformation for it.
    ref_idx = scip_pb2.Index()
    refdoc = scip_pb2.Document(relative_path="pkg/user.py")
    caller = "scip-python python proj 1.0 `pkg.user`/use()."
    refdoc.symbols.append(scip_pb2.SymbolInformation(
        symbol=caller, kind=scip_pb2.SymbolInformation.Function,
        documentation=["```python\ndef use():\n```"]))
    du = scip_pb2.Occurrence(symbol=caller, symbol_roles=scip_pb2.SymbolRole.Definition)
    du.range.extend([0, 4, 7]); du.enclosing_range.extend([0, 0, 5, 0])
    refdoc.occurrences.append(du)
    ru = scip_pb2.Occurrence(symbol="scip-python python proj 1.0 `pkg.mod`/Outer#")
    ru.range.extend([1, 4, 9])
    refdoc.occurrences.append(ru)
    ref_idx.documents.append(refdoc)

    fb = ast_fallback.synthesize_index(repo, ["pkg/mod.py"], "proj", "1.0")
    g = scip_index.build_graph(fb, ref_idx)
    outer = next(m for m in g.symbols if m.endswith("`pkg.mod`/Outer#"))
    assert caller in g.callers(outer)            # the reference connected by moniker
    assert g.symbols[outer].def_path == "pkg/mod.py"


def test_unparseable_file_skipped(tmp_path: Path):
    repo = _write(tmp_path, "pkg/bad.py", "def (((: syntax error")
    idx = ast_fallback.synthesize_index(repo, ["pkg/bad.py", "pkg/missing.py"], "p")
    assert list(idx.documents) == []  # both skipped, no crash
