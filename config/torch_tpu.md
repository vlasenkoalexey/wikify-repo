---
slug: torch_tpu
languages: [cpp, python]
repo: /mnt/disks/persist/torch-tpu/torch_tpu
ref: ea8ca5159a167f6d239470bbc6bab0e36a7171ec
index_shards: ["torch_tpu/*"]
bazel_targets: "//torch_tpu/..."
tests: ["tests/**/*.py"]
docs: ["**/README*.md", "docs/**/*.md"]
---

# torch_tpu — ingest config

PyTorch/XLA TPU backend (`torch_tpu`). Mixed C++/Python, with the C++ ops
colocated under `torch_tpu/` (ops, pjrt, distributed). Python is sharded by
`torch_tpu/*`; the C++ is indexed automatically from bazel via `bazel_targets`:
`wikify prepare torch_tpu` runs `bazel build` + `aquery`, converts to a
scip-clang compile DB (`wikify/bazel_cc.py`), and indexes it. The first run does a
full bazel build to materialize generated headers (slow; cached after).

## Concepts
<!-- discovery-driven -->
