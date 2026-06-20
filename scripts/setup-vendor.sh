#!/usr/bin/env bash
# Fetch the large vendored tools that are intentionally NOT committed to git
# (the scip-clang binary is ~130 MB — over GitHub's 100 MB/file limit). Idempotent:
# anything already present is skipped. Run once after cloning, before the C++ path.
#
#   scripts/setup-vendor.sh
#
set -euo pipefail

SCIP_CLANG_VERSION="${SCIP_CLANG_VERSION:-v0.3.3}"   # pinned: see note below
ARCH="$(uname -m)-$(uname -s | tr '[:upper:]' '[:lower:]')"   # e.g. x86_64-linux
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN="$HERE/vendor/bin"
mkdir -p "$BIN"

# 1) scip-clang — the C++ indexer (prebuilt release binary). v0.3.3 links against
#    glibc 2.35; newer builds require glibc 2.38, so pin it deliberately. The CLI
#    auto-selects vendor/bin/scip-clang* (see cli._scip_clang_bin).
DEST="$BIN/scip-clang-033"
if [[ ! -x "$DEST" ]]; then
  URL="https://github.com/sourcegraph/scip-clang/releases/download/${SCIP_CLANG_VERSION}/scip-clang-${ARCH}"
  echo "downloading scip-clang ${SCIP_CLANG_VERSION} (${ARCH}) ..."
  curl -fSL "$URL" -o "$DEST"
  chmod +x "$DEST"
fi
echo -n "scip-clang: "; "$DEST" --version | head -1

# 2) scip-python — the Python indexer (npm package).
if ! command -v scip-python >/dev/null 2>&1; then
  echo "installing scip-python via npm ..."
  npm i -g @sourcegraph/scip-python
fi
echo -n "scip-python: "; scip-python --version 2>/dev/null || echo "(installed)"

# 3) scip_pb2.py — generated from the vendored proto (gitignored, regenerable).
if [[ ! -f "$HERE/wikify/scip_pb2.py" ]]; then
  echo "generating wikify/scip_pb2.py from vendor/scip.proto ..."
  python -m grpc_tools.protoc -I "$HERE/vendor" --python_out="$HERE/wikify" "$HERE/vendor/scip.proto"
fi

echo "vendor setup complete."
