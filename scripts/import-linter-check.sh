#!/usr/bin/env bash
# import-linter capability-isolation contract 校验 (Phase 1 CEO-009 / Packet D)。
# 用法: scripts/import-linter-check.sh
# 退出码: 0 = 全部 contract 通过; 1 = 至少一个 contract 违规

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [[ ! -f .import-linter.ini ]]; then
    echo "[import-linter] .import-linter.ini 不存在, 跳过" >&2
    exit 0
fi

if command -v uv >/dev/null 2>&1; then
    uv run lint-imports --config .import-linter.ini
else
    echo "[import-linter] 未找到 uv, 请安装 uv (https://docs.astral.sh/uv/)" >&2
    exit 127
fi
