---
slug: _defaults
---

# Shared default concept set

The stable common set for framework repos (design.md Stage 5, layer 1). A
per-repo `config/<slug>.md` inherits these conceptually and overrides/extends
them with repo-specific seeds. Phase 1 reads the per-repo file directly; this
file documents the shared defaults the lane router will apply in a later phase.

## Concepts
- **compilation-pipeline** — seeds: (auto)
- **dispatch-path** — seeds: (auto)
- **compute-comm-overlap** — seeds: (auto)
- **memory-management** — seeds: (auto)
- **lowering** — seeds: (auto)
- **sharding** — seeds: (auto)
