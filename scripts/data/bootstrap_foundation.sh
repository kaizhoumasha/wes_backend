#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

cd "$BACKEND_ROOT"
if command -v uv >/dev/null 2>&1; then
    exec uv run python scripts/data/bootstrap_foundation.py "$@"
fi

exec python scripts/data/bootstrap_foundation.py "$@"
