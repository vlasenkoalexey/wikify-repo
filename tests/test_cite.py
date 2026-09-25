"""Source-form citations (docs/citations.md): href = the source line at the pin, title =
the symbol-index key. Every consumer resolves both forms through ``cite.key_of``.

Deterministic: hand-built graphs, no scip-python.
"""

from pathlib import Path

from wikify import assemble, cite, coverage, lint, packet, relink, verify
from wikify.config import Concept
from wikify.graph import Symbol, SymbolGraph

PKG = "scip-python python demo 0.0.0"
FWD = f"{PKG} `demo.models`/Transformer#forward()."
STEP = f"{PKG} `demo.train.loop`/train_step()."
URL = "https://github.com/o/demo/blob/abc123"


def _graph():
    g = SymbolGraph()
    g.add_symbol(Symbol(moniker=FWD, kind="Method", suffix="Method", name="forward",
                        def_path="demo/models.py", def_line=19))
    g.add_symbol(Symbol(moniker=STEP, kind="Method", suffix="Method", name="train_step",
                        def_path="demo/train/loop.py", def_line=4))
    g.add_edge(STEP, FWD)
    return g


def _silo(tmp_path, body):
    wiki = tmp_path / "demo"
    coverage.emit_symbol_index(_graph(), wiki, hashes={}, slug="demo", ref="abc123")
    (wiki / "concepts").mkdir(parents=True, exist_ok=True)
    page = wiki / "concepts" / "training.md"
    page.write_text(body, encoding="utf-8")
    return wiki, page


def test_key_of_reads_both_forms_and_nothing_else():
    assert cite.key_of('../catalog/demo/models.md#Transformer.forward "demo/models.py:L20"') == \
        ("demo/models.md", "Transformer.forward")
    assert cite.key_of(f'{URL}/demo/models.py#L20 "demo/models#Transformer.forward"') == \
        ("demo/models.md", "Transformer.forward")
    assert cite.key_of(f"{URL}/demo/models.py#L20") is None                 # plain source link
    assert cite.key_of('https://example.com "Some page"') is None           # titled prose link
    assert cite.key_of("../doc-concepts/x.md") is None


def test_silo_rewrite_points_at_the_index_row_and_is_idempotent(tmp_path):
    wiki, page = _silo(tmp_path,
        "# T\n## Mechanism (step-by-step)\n"
        "1. step [`train_step`](../catalog/demo/train/loop.md#train_step)\n"
        f'2. fwd [`forward`]({URL}/demo/models.py#L999 "demo/models#Transformer.forward")\n'
        "3. gone [`old`](../catalog/demo/gone.md#old) and [docs](../doc-concepts/x.md)\n")
    assert cite.to_source_silo(wiki, URL) == {"pages": 1, "links": 2}
    text = page.read_text()
    assert f'[`train_step`]({URL}/demo/train/loop.py#L5 "demo/train/loop#train_step")' in text
    assert f'[`forward`]({URL}/demo/models.py#L20 "demo/models#Transformer.forward")' in text  # stale line refreshed
    assert "[`old`](../catalog/demo/gone.md#old)" in text          # unknown key left for the linter
    assert "[docs](../doc-concepts/x.md)" in text
    assert cite.to_source_silo(wiki, URL) == {"pages": 0, "links": 0}


def test_lint_resolves_source_form_and_names_a_dead_key(tmp_path):
    _, page = _silo(tmp_path,
        "# T\n## Mechanism (step-by-step)\n"
        f'1. ok [`forward`]({URL}/demo/models.py#L20 "demo/models#Transformer.forward")\n'
        f'2. bad [`nope`]({URL}/demo/models.py#L1 "demo/models#Nope")\n')
    errors = lint.lint_page(page, _graph(), set())
    assert [(e.line, e.rule) for e in errors] == [(4, 1)]
    assert "demo/models#Nope" in errors[0].message and URL not in errors[0].message


def test_packet_cites_the_source_line_when_the_silo_has_a_source_url(tmp_path):
    concept = Concept(slug="training", seeds=["train_step"])
    with_url, _ = packet.build_packet(_graph(), tmp_path, "demo", "abc123", concept, [], "2026-01-01",
                                      source_url=URL)
    assert f'- cite: [`train_step`]({URL}/demo/train/loop.py#L5 "demo/train/loop#train_step")' in with_url
    without, _ = packet.build_packet(_graph(), tmp_path, "demo", "abc123", concept, [], "2026-01-01")
    assert '- cite: [`train_step`](../catalog/demo/train/loop.md#train_step "demo/train/loop.py:L5")' in without


def test_relink_moves_key_and_file_of_a_source_form_citation():
    lmap = {("demo/train/loop.md", "train_step"): ("demo/loop.md", "train_step")}
    text = f'[`train_step`]({URL}/demo/train/loop.py#L5 "demo/train/loop#train_step")'
    out, n = relink.relink_text(text, lmap)
    assert (out, n) == (f'[`train_step`]({URL}/demo/loop.py#L5 "demo/loop#train_step")', 1)
    assert relink.relink_text(out, lmap) == (out, 0)


def test_coverage_and_area_read_source_form(tmp_path):
    wiki, page = _silo(tmp_path,
        f'# T\n[`train_step`]({URL}/demo/train/loop.py#L5 "demo/train/loop#train_step")\n')
    assert coverage.covered_monikers(_graph(), wiki) == {STEP: "training"}
    assert assemble.page_area(page) == "demo/train"


def test_verify_holds_survive_the_rewrite(tmp_path):
    old = "- [`train_step`](../catalog/demo/train/loop.md#train_step \"demo/train/loop.py:L5\") runs a step."
    new = f'- [`train_step`]({URL}/demo/train/loop.py#L5 "demo/train/loop#train_step") runs a step.'
    moved_line = new.replace("#L5", "#L9")
    c_old = verify.Claim(page="p.md", line=3, section="Mechanism", text=old)
    c_new = verify.Claim(page="p.md", line=3, section="Mechanism", text=new)
    assert c_new.key == c_old.key == verify.Claim("p.md", 3, "Mechanism", moved_line).key
    pre_04 = {verify._hash(old): {"refuted": False}}      # a cache written before 0.4
    assert c_new.cached(pre_04) == {"refuted": False}
