---
slug: torchtitan
languages: [python]
repo: /mnt/disks/persist/torch-tpu/torchtitan
ref: 15d0f5bb345bba8410d997ab6d7d8f4a567764a2
tests: ["tests/**/*.py"]
docs: ["**/README*.md", "docs/**/*.md"]
---

# torchtitan — ingest config

Pure-Python trainer (Phase 1 target). Concerns below drive synthesis; seeds are
entry-point symbols that the packet builder traverses the SCIP graph from.

## Concerns
- **training-step** — seeds: `Trainer::train_step`, `Trainer::forward_backward_step`
- **distributed-parallelism** — seeds: `Trainer::init_distributed`, `ParallelDims`
- **checkpointing** — seeds: `Trainer::state_dict`, `Trainer::load_state_dict`
