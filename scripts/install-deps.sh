#!/usr/bin/env bash
# Install host binaries that mcp.json servers declare under `requires`.
# Flags: --server NAME (one server only), --dry-run (show commands, run nothing).
set -euo pipefail
cd "$(dirname "$0")/.."
if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required: https://docs.astral.sh/uv/getting-started/installation/"
  exit 1
fi
exec uv run python -m src.harness.host_deps install "$@"
