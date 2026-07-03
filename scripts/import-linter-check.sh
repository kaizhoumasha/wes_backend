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

# Jenkins 复用 workspace 时旧 graph cache 可能污染 contract 结果；架构门禁必须每次基于当前源码重算。
if command -v lint-imports >/dev/null 2>&1; then
    lint-imports --config .import-linter.ini --no-cache
elif command -v uv >/dev/null 2>&1; then
    uv run lint-imports --config .import-linter.ini --no-cache
else
    echo "[import-linter] 未找到 lint-imports 或 uv, 请先安装项目依赖" >&2
    exit 127
fi
