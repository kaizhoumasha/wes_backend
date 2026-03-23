#!/usr/bin/env bash

set -euo pipefail

cd "$(dirname "$0")/.."

if [ "$#" -eq 0 ]; then
  set -- .
fi

export PYTHONPATH="${PYTHONPATH:-.}"

exec uv run basedpyright --level warning "$@"
