# Synthesis instruction — one mechanism page from one packet

You are given ONE packet describing ONE concern of a codebase. Produce ONE
markdown mechanism page. You are documenting how the code WORKS, grounded only in
the packet. You are not summarizing files.

## Hard rules
- Use ONLY symbols present in the packet's Subgraph. Never name a symbol that is
  not there. If you need one that's missing, say so in Open questions — do not
  invent it.
- In "Entry points" and "Mechanism", every bullet MUST cite a symbol with a
  markdown link to its stub: [`Sym`](../symbols/<slug>.md). Create the stub if it
  doesn't exist (frontmatter: moniker, def file:line, callers, callees, "Cited by").
- Any claim you cannot ground in a cited symbol or an Evidence item goes in a
  `> [!inferred]` block, flagged as your inference — never stated as fact.
- "Dynamics" describes DESIGN INTENT from source + tests only. Never claim runtime
  behavior you cannot see statically. Do not write an "Observed dynamics" section.

## Page template
---
title: <concern title>
type: concern
provenance: mixed
concern: <concern-slug>
updated: <date>
status: fresh
---
# <concern title>
<one-line scope>
## Entry points
- [`Sym`](../symbols/<slug>.md) — what it is, when it's hit.
## Mechanism (step-by-step)
1. <step> [extracted → `Sym`](../symbols/<slug>.md)
## Key data structures
## Dynamics (design intent)
<grounded in tests/scheduler source; link the test-spec page>
## Edge cases
## Open questions
## See also

## Method
1. Start from Seeds; walk the Subgraph (callers/callees already provided) to find
   the spine of the mechanism.
2. Read the provided Source snippets; do not ask for files outside the packet.
3. Write the steps in execution order. Cite each.
4. Pull dynamics from Evidence (tests + scheduler/stream/collective source).
5. List honest Open questions where the packet was insufficient.

## Stub format (`symbols/<slug>.md`)
The packet gives each symbol a `stub-slug`. Create `wiki/<slug>/symbols/<stub-slug>.md`:
---
title: "<name>"
type: symbol
provenance: extracted
moniker: "<the FULL moniker from the Subgraph — copied verbatim>"
updated: <date>
---
# <name>
**Defined:** <def file:line>
**Signature:** <signature>
## Called by
- <callers as links to their stubs where they exist>
## Calls / refs
- <callees>  (reference-scoped, not true call resolution)
## Cited by
- [<concern title>](../concerns/<concern-slug>.md)

The linter resolves every citation by reading the target stub's `moniker`
frontmatter against the SCIP index — so the moniker must be copied EXACTLY.
