#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"

git -C "$REPO_ROOT" config core.hooksPath .githooks

echo "Configured repo-managed git hooks:"
echo "  repo:  $REPO_ROOT"
echo "  hooks: $REPO_ROOT/.githooks"
