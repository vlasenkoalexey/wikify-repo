---
slug: torch_tpu
languages: [cpp, python]
repo: /mnt/disks/persist/torch-tpu/torch_tpu
ref: ea8ca5159a167f6d239470bbc6bab0e36a7171ec
index_shards: ["torch_tpu/*"]
compile_commands: compile_commands.json
tests: ["tests/**/*.py"]
docs: ["**/README*.md", "docs/**/*.md"]
---

# torch_tpu — ingest config

PyTorch/XLA TPU backend (`torch_tpu`). Mixed C++/Python, with the C++ ops
colocated under `torch_tpu/` (ops, pjrt, distributed). Python is sharded by
`torch_tpu/*`; the C++ is indexed by scip-clang against a `compile_commands.json`
**generated from bazel** (the repo has no checked-in compile DB):

1. `bazel aquery 'mnemonic("CppCompile", //torch_tpu/...)' --output=jsonproto`
2. `bazel build //torch_tpu/...` (materialize all transitive generated headers)
3. convert the aquery to compile_commands.json: source `file` kept relative with
   `directory` = the **real repo root** (so scip-clang treats the sources as
   in-project and naturally drops external torch/XLA/llvm headers); split combined
   `-isystem path` tokens; absolutize include paths against the bazel execroot.
4. run scip-clang with `--ipc-size-hint-bytes` raised (these TUs pull the whole
   torch header graph) and a long `--receive-timeout-seconds`.

## Concepts
<!-- discovery-driven -->
