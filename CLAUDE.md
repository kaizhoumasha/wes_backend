# CLAUDE.md

P9 WES Backend 快速开发框架指南 - 基于 FastAPI + SQLModel + SQLAlchemy 2.0

## Claude Entry Point

`AGENTS.md` 是本仓库项目规则主真源；Claude Code 必须先遵守 `AGENTS.md` 中的架构、命令、分支、GitNexus、RTK、质量门禁和中文沟通规则。

本文件只补充 Claude Code 专用行为层：

- Karpathy 风格的编码行为约束。
- GStack skill routing 和 Claude 使用方式。
- Claude 在长任务中的计划、验证、技能调用习惯。

如果本文与 `AGENTS.md` 的项目事实冲突，以用户当前指令优先，其次以 `AGENTS.md` 为项目事实准绳；本文只决定 Claude 如何执行。

## Non-Negotiable Project Rules

- 使用中文进行沟通、文档和 Commit Comment。
- 遵守分层架构：API → Service → Repository → Database。
- 修改函数、类、方法前运行 GitNexus impact analysis；HIGH/CRITICAL 风险必须先告知用户。
- Commit 前运行 GitNexus detect changes，确认变更范围符合预期。
- 项目命令使用 `uv run ...`，不要依赖其它 shell 已激活环境。
- 日常分支以 `develop` 为 base；仅在确需并行隔离时使用 worktree。
- 保留有价值注释，代码行为变化时同步更新注释。

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:

- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:

- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:

- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:

- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:

```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

## 5. Planning Document Readability

**计划文档用于对齐方向，不用于承载大段实现代码。**

规划/计划文档应优先表达：

- 目标和非目标
- 架构决策和业务约定
- 模块边界和文件职责
- 状态流、错误码、数据字段
- 验收标准、测试场景、验证命令
- 风险、取舍和迁移影响

严格禁止：

- 在计划文档中粘贴完整类实现、完整函数实现或大段测试代码
- 把计划文档写成可复制执行的代码脚本
- 用实现细节替代架构决策和验收标准

允许少量简短伪代码或极短示例，但只用于说明约定。实现细节应在编码阶段通过 TDD、diff、测试和提交体现。

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

## 项目概述

专为 WMS/WES 系统设计的快速开发框架，采用**分层架构**和**零代码开发模式**。

**核心特性**：
- **零代码 CRUD**：继承 BaseAPI 自动生成 REST API
- **ModelFactory**：自动生成 Create/Update Schema
- **Hook 系统**：Repository 层业务逻辑扩展
- **Mixin 组合**：复用模型字段和行为
- **RBAC 权限**：基于角色的访问控制
- **TimescaleDB**：时序数据存储

**基础设施**：
- Postgres (TimescaleDB) `wes_postgres`
- Redis `wes_redis`

## 开发命令

```bash
# 环境管理
docker-compose up -d          # 启动基础设施
uv sync --dev                 # 安装依赖
./scripts/migrate.sh upgrade  # 数据库迁移
uv run uvicorn main:app --reload  # 开发服务器

# 代码质量
./scripts/install-git-hooks.sh      # 为当前 worktree 启用 repo-managed pre-commit 质量门禁
./scripts/git-quality-gate.sh --profile quality  # 本地运行默认质量门禁
uv run ruff format . && uv run ruff check . # 格式化和检查
uv run pytest --cov=src       # 测试和覆盖率
```

### Alembic 迁移规则

- 新增 Alembic 迁移必须通过 Alembic revision generator 创建，例如 `uv run alembic revision -m "<message>"`，或使用仓库已有的等价 wrapper。
- 不要手写模板化 `revision` ID。先让 Alembic 自动生成随机 revision ID，再编辑生成出来的迁移文件内容。


## 分支与 Worktree 流程
默认使用普通 Git Flow 分支。日常单任务开发从 `develop` 切 `feature/*`、`fix/*`、`chore/*` 等分支即可，不默认使用 worktree。

基础分支统一使用 `develop`。创建功能/修复分支前先更新 `develop`，PR 默认以 `develop` 为 base；除发布、回滚、生产补丁等特殊流程外，不从 `main` 直接拉日常开发分支。

仅在确实需要并行隔离时使用 git worktree：长线重构、保留当前现场处理紧急修复、AI agent 执行大计划、PR review 期间继续其他工作，或需要并行运行两套本地环境。

使用 worktree 时，每个 worktree 必须维护自己的本地运行状态。不要复用其它 worktree 的 `.venv`、`.env`、`.pytest_cache` 或其它本地临时文件。

- 后端主仓库路径：`/Users/kaizhou/codeDev/wes_backend`
- 前端主仓库路径：`/Users/kaizhou/codeDev/wes_frontend`
- 后端 Worktree 根目录：`/Users/kaizhou/codeDev/wes_backend-worktrees`
- 前端 Worktree 根目录：`/Users/kaizhou/codeDev/wes_frontend-worktrees`
- Worktree 目录名使用 branch slug：把分支名里的 `/` 替换成 `-`，例如 `feature/handling-core` → `feature-handling-core`。
- 创建示例：`mkdir -p ../worktrees/wes_backend && git worktree add ../worktrees/wes_backend/<branch-slug> -b <branch> develop`
- 进入 worktree 后先运行 `./scripts/init-env.sh dev`。
- 在该 worktree 内运行 `uv sync --dev`，创建或刷新自己的 `.venv`。
- 如需启用提交门禁，在该 worktree 内运行 `./scripts/install-git-hooks.sh`。
- 所有项目命令使用 `uv run ...`，不要依赖其它 shell 中已激活的环境。
- 如果切换分支后 `pyproject.toml`、`uv.lock` 或环境 profile 文件发生变化，重新运行 `./scripts/init-env.sh dev` 和 `uv sync --dev`。
- worktree 不再需要时，使用 `git worktree remove <path>` 删除，然后执行 `git worktree prune`。

**推荐流程**：

```bash
# 1. 创建 worktree
git worktree add ../wes_backend-feature-x -b feature/x develop

# 2. 进入 worktree
cd ../wes_backend-feature-x

# 3. 初始化当前 worktree 的环境
./scripts/init-env.sh dev
uv sync --dev
./scripts/install-git-hooks.sh

# 4. 在当前 worktree 中执行命令
uv run pytest tests/
uv run ruff check .
uv run uvicorn main:app --reload --host 0.0.0.0 --port 8001
```

**切换分支或依赖变更后**：
- 如果 `pyproject.toml` 或 `uv.lock` 变化，重新执行 `uv sync --dev`
- 如果环境配置变化，重新执行 `./scripts/init-env.sh dev`
- 删除废弃 worktree 后执行 `git worktree prune`

---

## 🚨 关键规则（CRITICAL）

### 分层架构

```
API 层 → Service 层 → Repository 层 → 数据库
```

**严格禁止**：
- ❌ API 层直接访问数据库 (`db.execute`, `select()`)
- ❌ API 层直接调用 Repository
- ❌ 任何跨层直接调用

**检测命令**：
```bash
grep -r "from sqlalchemy import select" src/app/*/v1/
grep -r "db.execute(" src/app/*/v1/
```

### Mixin 继承规范

**⚠️ EnterpriseMixin 已包含 AuditMixin + OptimisticLockMixin**

```python
# ❌ 错误：重复继承
class User(UserBase, AuditMixin, EnterpriseMixin, SoftDeleteMixin, table=True)

# ✅ 正确
class User(UserBase, EnterpriseMixin, SoftDeleteMixin, DataTableMixin, table=True)
```

### Update Schema 方法选择规则

**必须根据模型的 Mixin 组合选择正确的 Update Schema 方法**：

| 模型 Mixin 组合 | Update Schema 方法 |
|----------------|-------------------|
| 继承 `EnterpriseMixin`（包含 OptimisticLockMixin） | `for_optimistic_update()` |
| 只继承 `DataTableMixin` + `SoftDeleteMixin（无 OptimisticLockMixin） | `for_update()` |

```python
# ✅ 正确：有 OptimisticLockMixin
class User(UserBase, EnterpriseMixin, DataTableMixin, table=True):
    pass

class UserUpdate(ModelFactory(UserBase).for_optimistic_update()):
    pass

# ✅ 正确：无 OptimisticLockMixin
class WorklineSession(WorklineSessionBase, DataTableMixin, SoftDeleteMixin, table=True):
    pass

class WorklineSessionUpdate(ModelFactory(WorklineSessionBase).for_update()):
    pass
```

**原因**：
- `for_optimistic_update()` 要求模型有 `version` 字段
- 使用错误方法会导致验证失败或行为不正确

### 时区使用

| 场景 | 方法 | 返回类型 |
|------|------|----------|
| 数据库存储 | `timezone.now_for_db()` | naive UTC |
| API 响应 | `timezone.now_utc().isoformat()` | aware ISO |
| 时间戳计算 | `timezone.now_utc().timestamp()` | Unix 秒 |

**⚠️ 禁止对 naive datetime 调用 `.timestamp()`**

### 模块导出

新 Service 必须在 `__init__.py` 中导出：

```python
from .xxx_service import XxxService, xxx_service

__all__ = ["XxxService", "xxx_service"]
```

### ENUM 类型使用规范

**🔥 强制使用 VARCHAR + CHECK 约束，禁用 PostgreSQL 原生 ENUM**

```python
from enum import Enum
from sqlalchemy import Enum as SQLAEnum
from sqlmodel import Field

class AppStatus(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"

class APIApplication(..., table=True):
    # ✅ 正确：使用 VARCHAR + CHECK 约束
    status: AppStatus = Field(
        default=AppStatus.ACTIVE,
        sa_type=SQLAEnum(
            AppStatus,
            native_enum=False,      # 🔥 关键：禁用原生 ENUM
            create_constraint=True, # 自动创建 CHECK 约束
            length=50,
        ),
        description="状态",
    )
```

**为什么不用 PostgreSQL ENUM？**

| 问题 | PostgreSQL ENUM | VARCHAR + CHECK |
|------|----------------|-----------------|
| 删除值 | ❌ 无法删除 | ✅ 随时修改约束 |
| 添加值 | ⚠️ 不支持事务 | ✅ 完全支持事务 |
| 迁移复杂度 | 🔴 高（需要手动 op.execute） | 🟢 低（Alembic 自动处理） |
| 跨数据库 | ❌ PostgreSQL 专用 | ✅ 所有数据库 |
| 性能 | ✅ 4字节 | 🟢 差异可忽略 |

**已重构的模型**：
- ✅ `APIApplication` (status, app_type, validity_period)
- ✅ `Device` (device_type, protocol, device_status)
- ✅ `DeviceCommand` (task_type, status, result)
- ✅ `DeviceEventLog` (event_type)
- ✅ `WorkLine` (line_type)

**注意**：`AuditLog.status` 使用 `IntEnum`，不需要修改。

---

---

## 设计原则

| 原则 | 应用 |
|------|------|
| **DRY** | Mixin 复用字段，ModelFactory 生成 Schema，基类复用 CRUD |
| **KISS** | 优先基类默认实现，不过度抽象 |
| **SOLID** | Repository/Service/API 单一职责，Hook/Mixin 扩展功能 |
| **YAGNI** | 只实现当前需求，不预设计 |

---

## 模型定义模式

### 外键设计规则

**必须避免循环依赖，辅助追溯字段不应设置外键约束**：

| 外键类型 | 是否设置外键 | 示例 |
|---------|-------------|------|
| 核心业务约束 | ✅ 必须 | `workline_id: int = Field(foreign_key="wes_biz.work_lines.id")` |
| 辅助追溯字段 | ❌ 禁止 | `last_inbox_id: int = Field(description="最后处理ID")` |

**循环依赖检查**：
- 如果 `TableA.id` → `TableB.field` 且 `TableB.field` → `TableA.id`，构成循环依赖
- 移除其中一个外键约束（通常是非核心追溯字段）
- 保留字段用于业务逻辑，但不设数据库级别的约束

```python
# ✅ 正确：避免循环依赖
class WorklineSession(..., table=True):
    session_id: int = Field(foreign_key="wes_biz.work_lines.id")  # 核心业务
    last_inbox_id: int | None = Field(description="最后处理ID")  # 无外键
```

---

## 文档索引

| 文档 | 位置 | 内容 |
|------|------|------|
| 项目文件索引 | [docs/architecture/file_index.md](docs/architecture/file_index.md) | 代码结构、目录说明、快速查找、响应码 |
| 核心架构 | [.claude/context/architecture.md](.claude/context/architecture.md) | 分层架构、Hook/Mixin 系统、状态验证、JWT/RBAC |
| 开发规则 | [.claude/context/rules.md](.claude/context/rules.md) | 分层架构规则、Service 调用、模块导出、时区规则 |
| 常见任务 | [.claude/context/howto.md](.claude/context/howto.md) | 创建模块、自定义逻辑、状态验证、树形结构 |
| 故障排查 | [.claude/context/troubleshooting.md](.claude/context/troubleshooting.md) | 缓存问题、N+1 查询、架构违规、ImportError |

---

## 关键文件

**核心框架**：
- `src/database/base_repository.py` - Repository 基类
- `src/core/base_service.py` - Service 基类
- `src/core/base_api.py` - API 基类
- `src/database/model_factory.py` - Schema 工厂

**Mixin 系统**：
- `src/core/mixins/__init__.py` - 所有可用 Mixin

**认证权限**：
- `src/core/security.py` - JWT 认证
- `src/core/rbac.py` - RBAC 权限

---

## 文档同步规则

每次更新功能后，**必须**同步更新 `docs/architecture/file_index.md`。

```bash
# 验证文档同步
serena list_dir . --recursive --skip-ignored
# 对比 docs/architecture/file_index.md
```

## GIT 提交规范

- 每个功能/修复都必须有一个单独的提交
- 提交消息必须清晰、简洁，遵循 [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) 规范
- 不要生成 Co-Authored-By 字段
- 每个提交都必须包含相关的测试用例

---

## GStack 技能套件

**网页浏览规则**：
- 使用 `/browse` skill 进行所有网页浏览操作
- 禁止使用 `mcp__claude-in-chrome__*` 工具

**可用技能**：
| 技能 | 用途 |
|------|------|
| `/office-hours` | 办公时间管理 |
| `/plan-ceo-review` | CEO 计划评审 |
| `/plan-eng-review` | 工程计划评审 |
| `/plan-design-review` | 设计计划评审 |
| `/design-consultation` | 设计咨询 |
| `/review` | 代码审查 |
| `/ship` | 发布流程 |
| `/land-and-deploy` | 部署上线 |
| `/canary` | 金丝雀发布 |
| `/benchmark` | 性能基准测试 |
| `/browse` | 网页浏览（替代 chrome MCP） |
| `/qa` | 质量保证 |
| `/qa-only` | 仅质量保证 |
| `/design-review` | 设计评审 |
| `/setup-browser-cookies` | 设置浏览器 Cookie |
| `/setup-deploy` | 设置部署 |
| `/retro` | 回顾总结 |
| `/investigate` | 问题调查 |
| `/document-release` | 发布文档 |
| `/codex` | 代码索引 |
| `/cso` | CSO 流程 |
| `/autoplan` | 自动规划 |
| `/careful` | 谨慎模式 |
| `/freeze` | 冻结 |
| `/guard` | 守护 |
| `/unfreeze` | 解冻 |
| `/gstack-upgrade` | GStack 升级 |

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **wes_backend** (30972 symbols, 57508 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, run `npx gitnexus analyze` in terminal first.

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `gitnexus_impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `gitnexus_detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `gitnexus_query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `gitnexus_context({name: "symbolName"})`.

## Never Do

- NEVER edit a function, class, or method without first running `gitnexus_impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `gitnexus_rename` which understands the call graph.
- NEVER commit changes without running `gitnexus_detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/wes_backend/context` | Codebase overview, check index freshness |
| `gitnexus://repo/wes_backend/clusters` | All functional areas |
| `gitnexus://repo/wes_backend/processes` | All execution flows |
| `gitnexus://repo/wes_backend/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->

## Skill routing

When the user's request matches an available skill, invoke it via the Skill tool. When in doubt, invoke the skill.

Key routing rules:
- Product ideas/brainstorming → invoke /office-hours
- Strategy/scope → invoke /plan-ceo-review
- Architecture → invoke /plan-eng-review
- Design system/plan review → invoke /design-consultation or /plan-design-review
- Full review pipeline → invoke /autoplan
- Bugs/errors → invoke /investigate
- QA/testing site behavior → invoke /qa or /qa-only
- Code review/diff check → invoke /review
- Visual polish → invoke /design-review
- Ship/deploy/PR → invoke /ship or /land-and-deploy
- Save progress → invoke /context-save
- Resume context → invoke /context-restore
- Author a backlog-ready spec/issue → invoke /spec

## GBrain Configuration (configured by /setup-gbrain)
- Mode: local-stdio
- Engine: pglite
- Config file: ~/.gbrain/config.json (mode 0600)
- Setup date: 2026-06-12
- MCP registered: yes
- Artifacts sync: full
- Current repo policy: read-write

## GBrain Search Guidance (configured by /sync-gbrain)
<!-- gstack-gbrain-search-guidance:start -->

GBrain is set up and synced on this machine. The agent should prefer gbrain
over Grep when the question is semantic or when you don't know the exact
identifier yet.

**This worktree is pinned to a worktree-scoped code source** via the
`.gbrain-source` file in the repo root (kubectl-style context).
`gbrain code-def`, `code-refs`, `code-callers`, `code-callees`, `search`, and
`query` from anywhere under this worktree route to that source by default —
no `--source` flag needed (gbrain >= 0.41.38.0; on older gbrain the call-graph
commands need `--source "$(cat .gbrain-source)"`). Conductor sibling worktrees
of the same repo each have their own pin and their own indexed pages, so
semantic results match the code on disk here.

Call-graph queries (`code-callers`/`code-callees`) also need the graph to be
built first — run `/sync-gbrain --dream` (or `--full`) if they return
`count: 0`. This only works if this source's gbrain schema pack extracts code
symbols; on a non-code-aware pack `--dream` completes but the graph stays empty
and reports a WARN. `code-def`/`code-refs` need the same extraction.

Two indexed corpora available via the `gbrain` CLI:
- This worktree's code (auto-pinned via `.gbrain-source`).
- `~/.gstack/` curated memory (registered as `gstack-brain-<user>` source via
  the existing federation pipeline).

Prefer gbrain when:
- "Where is X handled?" / semantic intent, no exact string yet:
    `gbrain search "<terms>"` or `gbrain query "<question>"`
- "Where is symbol Y defined?" / symbol-based code questions:
    `gbrain code-def <symbol>` or `gbrain code-refs <symbol>`
- "What calls Y?" / "What does Y depend on?":
    `gbrain code-callers <symbol>` / `gbrain code-callees <symbol>`
- "What did we decide last time?" / past plans, retros, learnings:
    `gbrain search "<terms>" --source gstack-brain-<user>`

Grep is still right for known exact strings, regex, multiline patterns, and
file globs. Run `/sync-gbrain` after meaningful code changes; for ongoing
auto-sync across all worktrees, run `gbrain autopilot --install` once per
machine — gbrain's daemon handles incremental refresh on a schedule.

Safety: don't run `/sync-gbrain` while `gbrain autopilot` is active — the
orchestrator refuses destructive source ops when it detects a running autopilot
to avoid racing it (#1734). Prefer registering user repos with `gbrain sources
add --path <dir>` (no `--url`): URL-managed sources can auto-reclone, and the
sync code walk for them requires an explicit `--allow-reclone` opt-in.

<!-- gstack-gbrain-search-guidance:end -->
