#!/usr/bin/env bash

set -euo pipefail

if [[ $# -eq 0 ]]; then
    cat >&2 <<'EOF'
Usage: scripts/markdownlint.sh PATH [PATH...]

Examples:
  scripts/markdownlint.sh docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md
  scripts/markdownlint.sh README.md docs/architecture/
EOF
    exit 2
fi

exec uv run pymarkdown \
    --enable-extensions front-matter,markdown-tables,markdown-task-list-items \
    --disable-rules md013,md024,md025,md026,md033,md034,md041,md044,md045 \
    scan --recurse --respect-gitignore "$@"
