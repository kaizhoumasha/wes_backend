# Workflow Rules

本文件是 AGY / Antigravity workspace rule，内容来自 `AGENTS.md`。`AGENTS.md` 是项目规则主真源；如有冲突，以 `AGENTS.md` 为准。

## Branches

- 日常开发默认使用普通 Git Flow 分支。
- 基础分支统一使用 `develop`。
- 创建功能/修复分支前先更新 `develop`。
- PR 默认以 `develop` 为 base。
- 除发布、回滚、生产补丁等特殊流程外，不从 `main` 直接拉日常开发分支。

## Worktree

仅在确实需要并行隔离时使用 git worktree。

- 后端 worktree 根目录：`/Users/kaizhou/SynologyDrive/works/worktrees/wes_backend`
- 前端 worktree 根目录：`/Users/kaizhou/SynologyDrive/works/worktrees/wes_frontend`
- 目录名使用 branch slug：把分支名里的 `/` 替换成 `-`。
- 每个 worktree 维护自己的 `.venv`、`.env`、`.pytest_cache` 和临时状态。

创建后端 worktree 示例：

```bash
mkdir -p ../worktrees/wes_backend
git worktree add ../worktrees/wes_backend/<branch-slug> -b <branch> develop
cd ../worktrees/wes_backend/<branch-slug>
./scripts/init-env.sh dev
uv sync --dev
./scripts/install-git-hooks.sh
```

## Comments

修改代码时保留有价值注释：

- Section headers。
- 业务逻辑解释。
- 参数说明和设计理由。
- `TODO` / `FIXME` / `HACK` 及上下文。

行为变化时更新对应注释，不要直接删除。

## Quality And Commit

- 使用 Conventional Commits。
- Commit Message 和 Commit Comment 使用中文。
- 提交前运行与变更相关的测试或质量门禁。
- 涉及测试文件时，按 `AGENTS.md` 的 `Test Suite Governance` 归位并运行测试拓扑 guardrail，避免默认回归集膨胀或根目录测试回流。
- 如果变更涉及 callback、device event 或 scheduled task，说明是否验证了 Celery worker。
