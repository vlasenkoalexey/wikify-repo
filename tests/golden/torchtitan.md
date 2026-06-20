# Golden questions — torchtitan (Phase 1 acceptance #6)

Five internals questions answerable from the generated wiki ALONE (no repo grep).
Manual check is fine for v1: read the cited page, confirm the answer + that every
load-bearing claim links to a SCIP symbol stub.

| # | Question | Answered by | Key cited symbols |
|---|---|---|---|
| 1 | How does one training step accumulate gradients over microbatches? | `concerns/training-step.md` (Mechanism 2–5) | `train_step`, `forward_backward_step`, `gradient_accumulation_steps`, `OptimizersContainer.zero_grad` |
| 2 | How is the loss normalized across data-parallel ranks? | `concerns/training-step.md` (Mechanism 3–4, Dynamics) | `IGNORE_INDEX`, `ParallelDims.dp_enabled`, `dist_sum`, `ParallelDims.get_mesh` |
| 3 | When and how are gradients clipped (incl. pipeline parallel)? | `concerns/training-step.md` (Mechanism 6, Edge cases) | `clip_grad_norm_`, `Training.max_norm`, `ParallelDims.get_optional_mesh` |
| 4 | How is the device mesh / parallelism configuration set up? | `concerns/distributed-parallelism.md` | `Trainer.init_distributed`, `ParallelDims`, `set_determinism` |
| 5 | What state does a checkpoint round-trip, and via what protocol? | `concerns/checkpointing.md` | `Trainer.state_dict`, `Trainer.load_state_dict`, `Trainer.step`, `Trainer.ntokens_seen` |

## How to verify
1. Open `wiki/torchtitan/index.md` → pick the concern.
2. Read the concern page; each Mechanism step ends in a `[... → `Sym`](../symbols/…)` link.
3. Click a symbol stub → confirm its `**Defined:** file:line` matches the real repo
   at the pinned commit (`15d0f5bb…`).
4. Inferences are isolated in `> [!inferred]` blocks — treat as model judgment, not fact.

Acceptance: a reader answers all five from the wiki without opening torchtitan source.
