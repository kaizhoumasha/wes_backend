# Project Core Rules

本文件是 AGY / Antigravity workspace rule，内容来自 `AGENTS.md`。`AGENTS.md` 是项目规则主真源；如有冲突，以 `AGENTS.md` 为准。

## Communication

- 使用中文进行沟通、文档和 Commit Comment。
- 回答要直接、具体，优先说明证据、风险和下一步。
- 用户当前指令优先；如果与项目硬规则冲突，先说明冲突和风险。

## Project Shape

- 后端主仓库：`/Users/kaizhou/codeDev/wes_backend`
- 前端主仓库：`/Users/kaizhou/codeDev/wes_frontend`
- 后端 worktree 根目录：`/Users/kaizhou/codeDev/wes_backend-worktrees`
- 前端 worktree 根目录：`/Users/kaizhou/codeDev/wes_frontend-worktrees`
- 核心代码在 `src/`，领域模块在 `src/app/`。
- Alembic 迁移在 `migrations/versions/`。
- 测试在 `tests/`。
- 新增、移动、拆分或删除测试时，先遵循 `AGENTS.md` 的“测试所有权与 HEAVY”；不要在 `tests/` 根目录新增测试，不要把重测试目录混入默认快速回归集。

## Commands

项目命令使用 `uv run ...`，不要依赖其它 shell 已激活环境。

```bash
./scripts/init-env.sh dev
docker-compose up -d
uv sync --dev
./scripts/migrate.sh upgrade
uv run uvicorn main:app --reload --host 0.0.0.0 --port 8001
uv run celery -A src.celery_app.app worker --loglevel=info --queues=default,celery
./scripts/git-quality-gate.sh --profile quality
uv run pytest tests/
uv run ruff format . && uv run ruff check .
uv run bandit -r src/
```

## Planning Docs

规划/计划文档只写目标、架构决策、业务约定、任务边界、验收标准、风险和验证方式。不要粘贴完整类实现、完整函数实现或大段测试代码。
