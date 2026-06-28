#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"

git -C "$REPO_ROOT" config core.hooksPath .githooks

# Phase 2 launch PR 起:为 git hook 子进程注入 ARCHITECTURE_PHASE=phase1 默认值。
# CI 环境下 git config hook.env 会被传递到 hook 子进程,作为 WES_ARCHITECTURE_PHASE_OVERRIDE 的
# 二次保险。开发者本地 shell 未设置 env 时,git hook 仍能以 phase1 触发 guardrail。
# 注:git config hook.env 是 Git 2.30+ 特性;低于此版本由 .githooks/pre-commit 内 `export` 兜底。
if git -C "$REPO_ROOT" config --get-regexp '^hook\.env\.' >/dev/null 2>&1; then
    :
else
    git -C "$REPO_ROOT" config --add hook.env.ARCHITECTURE_PHASE phase1 2>/dev/null || true
fi

echo "Configured repo-managed git hooks:"
echo "  repo:  $REPO_ROOT"
echo "  hooks: $REPO_ROOT/.githooks"
