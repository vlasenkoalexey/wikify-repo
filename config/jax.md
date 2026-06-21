---
slug: jax
languages: [python]
repo: /mnt/disks/persist/torch-tpu/jax_src
ref: jax-v0.9.1
index_shards: ["jax/*"]
tests: ["tests/**/*.py"]
docs: ["**/README*.md", "docs/**/*.md"]
---

# jax — ingest config

JAX (jax-ml/jax) @ v0.9.1. Python-primary: the bulk of the library is `jax/_src`
(the implementation) re-exported through thin public modules (`jax/numpy`,
`jax/lax`, …). Indexed with scip-python, sharded by `jax/*` subpackage (each a
bounded `--target-only` process). The C++ backend (`jaxlib/`) is a separate bazel
build and is not indexed here; add it later via a `compile_commands.json`.

## Concepts
<!-- discovery-driven -->
