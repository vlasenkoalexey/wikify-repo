#!/usr/bin/env bash
# Kept for one release as a wrapper: `wikify setup` now installs the indexers (user prefix
# ~/.wikify/vendor, no sudo) and the skill; `prepare` installs indexers on demand anyway.
# Developers regenerating wikify/scip_pb2.py after a SCIP schema bump: see docs/implementation.md
# §10.16 (grpcio-tools 1.71, `python -m grpc_tools.protoc`); the generated module is committed.
set -euo pipefail
exec wikify setup --indexers python,cpp "$@"
