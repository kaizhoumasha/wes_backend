# P9 WES Backend - AGY / Gemini Entry Point

本文件是 AGY / Antigravity / Gemini 的入口说明。项目规则主真源是 [`AGENTS.md`](AGENTS.md)；本文件只说明 AGY/Gemini 如何加载和执行这些规则。

## Load Order

1. 当前用户指令。
2. `GEMINI.md`。
3. `AGENTS.md` 中的项目硬规则。
4. `.agents/rules/*.md` 中的 workspace rules。
5. 代码、测试、文档中的局部约定。

如果规则冲突，以用户当前指令优先；项目级事实以 `AGENTS.md` 为准。不要在 `GEMINI.md` 中维护独立的项目事实副本。

## Non-Negotiable Project Rules

- 使用中文进行沟通、文档和 Commit Comment。
- 遵守分层架构：API → Service → Repository → Database。
- 修改函数、类、方法前运行 GitNexus impact analysis；HIGH/CRITICAL 风险必须先告知用户。
- Commit 前运行 GitNexus detect changes，确认变更范围符合预期。
- 项目命令使用 `uv run ...`，不要依赖其它 shell 已激活环境。
- 日常分支以 `develop` 为 base；仅在确需并行隔离时使用 worktree。
- 保留有价值注释，代码行为变化时同步更新注释。

## Project Snapshot

P9 WES Backend 是面向 WMS/WES 的 FastAPI + SQLModel + SQLAlchemy 2.0 后端。核心代码在 `src/`，领域模块在 `src/app/`，迁移在 `migrations/versions/`，测试在 `tests/`。

常用命令：

| Action | Command |
| --- | --- |
| 初始化环境 | `./scripts/init-env.sh dev` |
| 安装依赖 | `uv sync --dev` |
| 启动基础设施 | `docker-compose up -d` |
| 数据库迁移 | `./scripts/migrate.sh upgrade` |
| 开发服务 | `uv run uvicorn main:app --reload --host 0.0.0.0 --port 8001` |
| Celery Worker | `uv run celery -A src.celery_app.app worker --loglevel=info --queues=default,celery` |
| 质量门禁 | `./scripts/git-quality-gate.sh --profile quality` |
| 测试 | `uv run pytest tests/` |
| Ruff | `uv run ruff format . && uv run ruff check .` |

测试文件新增、移动、拆分或删除时，必须遵循 `AGENTS.md` 的 `Test Suite Governance`；不要在 `tests/` 根目录新增测试，不要把重测试目录混入默认快速回归集，提交前运行测试拓扑 guardrail。

## Workspace Rules

AGY / Antigravity 应加载 `.agents/rules/` 下的 workspace rules：

- `.agents/rules/project-core.md`：项目核心规则和命令。
- `.agents/rules/architecture.md`：分层架构、Mixin、Schema、时区和迁移约束。
- `.agents/rules/gitnexus.md`：GitNexus、RTK 和变更安全。
- `.agents/rules/workflow.md`：分支、worktree、质量门禁、注释保留和提交口径。

这些文件是 `AGENTS.md` 的拆分视图，不是独立规则源。更新项目硬规则时先改 `AGENTS.md`，再同步相关 workspace rule。

## AGY / Gemini Execution Guidance

- 先读 `AGENTS.md` 和相关 `.agents/rules/*.md`，再动手。
- 不熟悉代码时，优先用 GitNexus 查询执行流，再用 `rg` 精确定位。
- 只修改用户请求涉及的文件；发现无关问题时记录，不顺手改。
- 规划文档只写目标、边界、架构决策、验收标准和风险，不粘贴完整实现。
- 完成前运行能证明结果的验证命令，并在回复中说明实际验证结果。

## GitNexus

本仓库已由 GitNexus 索引为 `wes_backend`。如果工具提示索引 stale，先运行：

```bash
npx gitnexus analyze
```

常用 CLI fallback：

```bash
npx gitnexus status
npx gitnexus query "<concept>"
npx gitnexus context <symbol>
npx gitnexus impact <symbol> --direction upstream
npx gitnexus detect-changes
```
