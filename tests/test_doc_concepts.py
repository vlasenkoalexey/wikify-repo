"""Doc-concept ingest support — doc discovery + the light doc-concept lint.

The extraction itself is an LLM step (skills/prompts/ingest-docs.md); these test
the deterministic halves: `_find_docs` globs the authored docs (skipping
vendored/build noise), and `lint_doc_concepts` gates `doc-concepts/` on rule 1
only (catalog citations must resolve) — no subgraph/uncited gates, since these
come from a doc, not a packet.
"""

from pathlib import Path

import yaml

from wikify import lint
from wikify.cli import _find_docs
from wikify.graph import Symbol, SymbolGraph

MON = "scip-python python demo 0.0.0 `demo`/Foo#"


def _graph():
    g = SymbolGraph()
    g.add_symbol(Symbol(moniker=MON, kind="Class", suffix="Type", name="Foo",
                        def_path="demo.py", def_line=1))
    return g


def _catalog(wiki_slug: Path):
    cat = wiki_slug / "catalog" / "demo.md"
    cat.parent.mkdir(parents=True, exist_ok=True)
    fm = {"title": "Module: demo.py", "type": "catalog", "symbols": {"Foo": MON}}
    cat.write_text("---\n" + yaml.safe_dump(fm) + "---\n# demo\n", encoding="utf-8")


def _doc_concept(wiki_slug: Path, body: str):
    d = wiki_slug / "doc-concepts"
    d.mkdir(parents=True, exist_ok=True)
    (d / "c.md").write_text(body, encoding="utf-8")


def test_find_docs_globs_and_skips_noise(tmp_path):
    for rel in ("README.md", "docs/guide.md", "third_party/x/README.md",
                "bazel-out/y/README.md", ".ipynb_checkpoints/README-checkpoint.md"):
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("# doc\n")
    found = _find_docs(tmp_path, ["**/README*.md", "docs/**/*.md"])
    assert "README.md" in found and "docs/guide.md" in found
    assert not any("third_party" in f or "bazel-out" in f or "ipynb" in f for f in found)


def test_doc_concept_lint_passes_on_resolving_citation(tmp_path):
    _catalog(tmp_path)
    _doc_concept(tmp_path, "---\ntype: doc-concept\n---\n# C\n"
                 "Uses [`Foo`](../catalog/demo.md#Foo).\n")
    assert lint.lint_doc_concepts(tmp_path, _graph()).ok


def test_doc_concept_lint_fails_on_dead_or_unknown_citation(tmp_path):
    _catalog(tmp_path)
    _doc_concept(tmp_path, "---\ntype: doc-concept\n---\n# C\n"
                 "Bad [`Ghost`](../catalog/demo.md#Ghost).\n")     # anchor not in catalog
    rep = lint.lint_doc_concepts(tmp_path, _graph())
    assert not rep.ok and rep.errors[0].rule == 1


def test_doc_concept_lint_ignores_subgraph_and_uncited(tmp_path):
    """A doc-concept may state prose freely and is not bound to any packet subgraph."""
    _catalog(tmp_path)
    _doc_concept(tmp_path, "---\ntype: doc-concept\n---\n# C\n"
                 "## Mechanism\n1. A step with no citation at all — fine for a doc-concept.\n"
                 "Also cites [`Foo`](../catalog/demo.md#Foo) which resolves.\n")
    assert lint.lint_doc_concepts(tmp_path, _graph()).ok        # no rule-2/rule-3 here


def test_no_doc_concepts_dir_is_clean(tmp_path):
    assert lint.lint_doc_concepts(tmp_path, _graph()).ok        # absent dir → no errors
