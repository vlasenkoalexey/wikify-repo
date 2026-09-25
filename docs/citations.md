# Citations link the source, titled with the index key

Since 0.4. Code: `wikify/cite.py`.

## The form

A citation names one row of the symbol index by its **key**, `<module>#<QualifiedName>`,
the first column of `catalog/symbols.tsv` (`<module>` is the catalog path without `.md`:
`torch_tpu/common/compilation_cache.cc`, `torchtitan/components/lora`). When the silo has a
`source_url`, the citation is a link to the symbol's source line at the pinned commit,
titled with that key:

```
[`GetOrCompile`](https://github.com/google-pytorch/torch_tpu/blob/4c0c7c57.../torch_tpu/common/compilation_cache.cc#L791 "torch_tpu/common/compilation_cache.cc#CompilationCache.GetOrCompile")
```

| reader | what the citation gives them |
|---|---|
| human with repository access | one click to the line at the pin |
| human or agent without access (private repo: 404) | the key: `grep -P '^<key>\t' catalog/symbols.tsv` gives path, line, kind, callers, signature and doc |
| agent with a local checkout or installed package | the path and line are in the URL |
| agent with `gh` access | `gh api -H "Accept: application/vnd.github.raw" "repos/<o>/<r>/contents/<path>?ref=<pin>"` |
| wikify (lint, fix, verify, OKF, relink, coverage) | the key, resolved in the graph's symbol index |

A silo with no `source_url` keeps the 0.3 catalog form, `../catalog/<module>.md#<QualifiedName>`
(optionally titled `"path:Lnn"`). Both forms carry the same key and every consumer resolves
through `cite.key_of`, so a silo can hold a mix while it migrates.

## Why not the catalog page

0.3 made the symbol index the catalog and stopped rendering per-module pages by default
(`catalog-index.md`), but kept the citation grammar `catalog/<module>.md#<Symbol>`. In the
`index` tier those pages do not exist, so every citation was a dead link in a browser (1,785
in torch_tpu, 2,447 in torchtitan). 0.3.5 rendered the pages back (`catalog: anchors`) so the
fragment had something to land on; that ships a page per module whose only job is to hold a
link to the source line. Linking the source line directly removes the hop and the pages,
and the title keeps what the page offered an agent: a key into the index.

## Where the line comes from

`finalize` writes the index first, then rewrites every citation in the silo (concepts,
doc-concepts, areas, overview) from the rows it just wrote: the href's path and line always
match the shipped index, and a `--ref` bump refreshes them. A key missing from the index is
left as written for the linter to report (rule 1). `wikify source-links <slug>` runs the same
rewrite from an already-shipped index, with no SCIP index or checkout: the migration for a
silo built before 0.4. Both are idempotent.

Packets emit the source form in their `cite:` lines, so synthesis pastes it directly.
`relink` (moved symbols) rewrites the key and the file in the href; the line is refreshed at
the next `finalize`.

## Verify cache

A claim's cache key is its prose with each citation reduced to `[label](<key>)`, so changing a
link's form or refreshing its line does not re-open a verified claim. Caches written before
0.4 are keyed by the raw prose; lookups also try the claim respelled in the catalog form
(untitled and `"path:Lnn"`-titled), so recorded verdicts carry over.

## Limits

- The href is only as current as the pin in `source_url`; bump it together with `ref`.
- GitHub `#Lnn` fragments on a private repo work only for signed-in readers with access.
- The key must contain no whitespace and one `#`; wikify's anchors satisfy this (checked on
  torch_tpu and torchtitan: 0 exceptions in 20,796 keys).
