"""Diagram floor + legend coverage (wikify.diagrams): warnings only, pre-legend pages quiet."""

from pathlib import Path

from wikify import diagrams

GOOD = """---
title: t
---
# t

## Diagram

```mermaid
flowchart TD
  OP["DispatchOp"] -->|"builds"| DBL["DeviceBufferList"]
  DBL --> TENSOR["MakeTensor"]
  TENSOR --> TPU
```
Legend:
- `OP` — [`DispatchOp`](../catalog/m.md#DispatchOp)
- `DBL` — [`DeviceBufferList`](../catalog/m.md#DeviceBufferList)
- `MakeTensor` — [`MakeTensor`](../catalog/m.md#MakeTensor)
- `TPU` — (concept)

## Mechanism (step-by-step)
1. x
"""


def _w(tmp_path, text):
    p = tmp_path / "c.md"
    p.write_text(text)
    return diagrams.check_page(p)


def test_good_page_is_quiet(tmp_path):
    warns, n, no_legend = _w(tmp_path, GOOD)
    assert warns == [] and n == 1 and no_legend == 0


def test_flowchart_nodes_ids_and_labels():
    ids, labels = diagrams.flowchart_nodes(GOOD.split("```mermaid\n")[1].split("```")[0])
    assert ids == {"OP", "DBL", "TENSOR", "TPU"}
    assert labels == {"OP": "DispatchOp", "DBL": "DeviceBufferList", "TENSOR": "MakeTensor"}


def test_legend_coverage_warnings(tmp_path):
    text = GOOD.replace("- `TPU` — (concept)\n", "- `GHOST` — (concept)\n")
    warns, _, _ = _w(tmp_path, text)
    assert any("legend misses node(s): TPU" in w for w in warns)
    assert any("names no node in the diagram: GHOST" in w for w in warns)


def test_no_legend_is_counted_not_warned(tmp_path):
    text = GOOD.split("Legend:")[0] + "\n## Mechanism (step-by-step)\n1. x\n"
    warns, n, no_legend = _w(tmp_path, text)
    assert warns == [] and n == 1 and no_legend == 1


def test_structural_warnings(tmp_path):
    bad = "```mermaid\nflowchart TD\n  A[\"x\" --> B\n```\n"
    assert any("unbalanced brackets" in w for w in _w(tmp_path, bad)[0])
    assert any("no diagram type" in w for w in _w(tmp_path, "```mermaid\n  A --> B\n```\n")[0])
    assert any("empty mermaid fence" in w for w in _w(tmp_path, "```mermaid\n\n```\n")[0])
    big = "```mermaid\nflowchart LR\n" + "\n".join(f"  N{i} --> N{i+1}" for i in range(25)) + "\n```\n"
    assert any("26 nodes (max 20" in w for w in _w(tmp_path, big)[0])
    # quoted brackets inside labels are fine; front matter and %% comments are skipped
    ok = "```mermaid\n---\ntitle: x\n---\n%% note\nflowchart TD\n  A[\"f(x)[0]\"] --> B\n```\n"
    assert _w(tmp_path, ok)[0] == []


def test_other_diagram_types_get_no_node_checks(tmp_path):
    seq = "```mermaid\nsequenceDiagram\n  A->>B: hi\n  B-->>A: ok\n```\n"
    warns, n, no_legend = _w(tmp_path, seq)
    assert warns == [] and n == 1 and no_legend == 0
    st = "```mermaid\nstateDiagram-v2\n  [*] --> Idle\n  Idle --> Running\n```\n"
    assert _w(tmp_path, st)[0] == []


def test_check_silo_aggregates(tmp_path):
    silo = tmp_path / "s"
    (silo / "concepts").mkdir(parents=True)
    (silo / "concepts" / "a.md").write_text(GOOD)
    (silo / "concepts" / "b.md").write_text(GOOD.split("Legend:")[0])
    warns, pages, fences, no_legend = diagrams.check_silo(silo)
    assert (pages, fences, no_legend) == (2, 2, 1) and warns == []
