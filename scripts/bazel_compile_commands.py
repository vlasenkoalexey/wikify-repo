#!/usr/bin/env python3
"""Generate a scip-clang-ready compile_commands.json from a bazel C++ build.

Thin CLI around ``wikify.bazel_cc`` (the conversion logic lives there and is what
``wikify prepare`` uses for `bazel_targets:` repos). Use this only to generate a
compile DB by hand; normally just set `bazel_targets:` in the repo config.

  scripts/bazel_compile_commands.py --targets //torch_tpu/... \
      --repo-root "$(pwd)" --output compile_commands.json
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wikify import bazel_cc  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--targets", required=True, help='bazel target pattern, e.g. //pkg/...')
    ap.add_argument("--repo-root", required=True, help="real source repo root (bazel workspace)")
    ap.add_argument("--output", required=True)
    ap.add_argument("--bazel", default="bazel")
    args = ap.parse_args()
    out = bazel_cc.generate_compile_db(args.repo_root, args.targets, args.output, bazel=args.bazel)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
