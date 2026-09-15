# Area page synthesis — the middle tier between the overview and a mechanism page

`wikify prepare` wrote one page per top-level area at `wiki/code/<slug>/areas/<area>.md`
(prose-budget.md). Each page has two prose sections you fill and one regenerated block you
never touch:

- `## Purpose` — two or three paragraphs: what this area of the repo is for, what enters it
  (the entry points the block lists), what it hands to the rest of the repo, and which of its
  units carry the load-bearing mechanism (link those units' concept pages).
- `## How the units connect` — one paragraph or a short list: how the units in the block
  relate (which calls which, what state they share), in the authors' words where the block's
  docstrings give them. Small units with no page of their own get their one-sentence role here.
- The block between `<!-- area:auto:begin -->` and `<!-- area:auto:end -->` — the units
  table, the small units' modules and entry points as catalog citations. `finalize`
  regenerates it; anything you write inside it is lost.

Rules:
- Cite with the citations already in the block (copy a `[\`Name\`](../catalog/...#Name "path:Lnn")`
  link verbatim). The linter checks every citation resolves (rule 1); you may write prose
  freely otherwise. Read the source at the cited `path:Lnn` before you describe a unit.
- Do not restate a mechanism a concept page explains; link it.
- Replace the placeholder line `_(not yet synthesized ...)_` entirely; leave the front matter
  alone except `description:` (one sentence, the index reuses it).
- Budget: this is a fifth of a mechanism page. No diagram unless the area has a single
  obvious spine, and then one small flowchart with a legend.
