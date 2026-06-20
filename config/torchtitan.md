---
slug: torchtitan
languages: [python]
repo: /mnt/disks/persist/torch-tpu/torchtitan
ref: 15d0f5bb345bba8410d997ab6d7d8f4a567764a2
tests: ["tests/**/*.py"]
docs: ["**/README*.md", "docs/**/*.md"]
---

# torchtitan — ingest config

Pure-Python trainer (Phase 1 target). The concept agenda is **derived**
(decision 8): `discover.py` ranks modules by centrality and auto-seeds them, so
no manual concept list is needed. Add entries below only to override/extend the
derived agenda (e.g. force a concept discovery would rank below the cut, or
supply better seeds).

## Concepts
<!-- discovery-driven; add manual overrides here if needed -->

