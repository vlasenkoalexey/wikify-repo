---
slug: xla
languages: [python]
repo: /mnt/disks/persist/torch-tpu/xla
tests: ["test/**/*.py"]
docs: ["**/README*.md", "docs/**/*.md"]
---

# pytorch/xla — ingest config

PHASE 1 LIMITATION: this ingests the **Python** surface only (torch_xla). The
large C++ core (~244 .cc/.cpp files) needs `scip-clang` + a compile database
(Phase 2) and is NOT covered here. The Python wiki is still useful (the frontend,
dynamo/lazy bridge, distributed, experimental APIs) but is not the whole repo.

## Concepts
<!-- discovery-driven -->
