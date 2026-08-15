DO NOT send optional commentary
# Repository Guidelines

> 本文件适用于所有 AI Agent（Claude Code、Codex、Cursor、Windsurf 等），确保统一的架构约束和开发规范。

---

## AI Tool Entry Points

本仓库同时维护三个 AI 工具入口，职责必须保持清晰，避免规则分叉：

| 工具 | 入口文件 | 职责 |
| --- | --- | --- |
| Codex / 通用 Agent | `AGENTS.md` | 项目规则主真源，所有跨工具硬约束以本文为准 |
| Claude Code | `CLAUDE.md` | Claude/GStack/Skill routing 专用行为层，项目硬规则继承本文 |
| AGY / Antigravity / Gemini | `GEMINI.md` + `.agents/rules/` | 轻量入口和 workspace rules，项目硬规则继承本文 |

同步原则：

- 不要在 `CLAUDE.md`、`GEMINI.md` 或 `.agents/rules/` 中维护一份独立的项目事实。
- 项目架构、命令、分支、GitNexus、RTK、质量门禁等硬规则更新时，先更新 `AGENTS.md`。
- 平台入口文件只补充“该工具如何执行这些规则”的差异，例如 Claude skills、AGY workspace rules。
- 如果入口文件之间冲突，以用户当前指令优先，其次遵循对应工具入口；项目级事实以 `AGENTS.md` 为准。

### Non-Negotiable Project Rules

所有 AI 工具都必须遵守：

- 使用中文进行沟通、文档和 Commit Comment。
- 遵守分层架构：API → Service → Repository → Database。
- 修改函数、类、方法前运行 GitNexus impact analysis；HIGH/CRITICAL 风险必须先告知用户。
- Commit 前运行 GitNexus detect changes，确认变更范围符合预期。
- 项目命令使用 `uv run ...`，不要依赖其它 shell 已激活环境。
- 日常分支以 `develop` 为 base；仅在确需并行隔离时使用 worktree。
- 保留有价值注释，代码行为变化时同步更新注释。

### 术语表达

- 中文沟通和文档优先使用清晰、统一的中文名称。
- 规范文档首次定义容易歧义或项目特有的术语时，可以补充英文名称或缩写；通用术语、日常沟通和 Commit Comment 不强制双语。
- 代码标识符、路径、字段名、状态值和协议字面量保持原文，不得为了术语表达改写可执行合同。

## 🚨 Critical Architecture Rules

### Layered Architecture

```
API Layer → Service Layer → Repository Layer → Database
```

**STRICTLY FORBIDDEN**:
- ❌ API layer directly accessing database (`db.execute`, `select()`)
- ❌ API layer directly calling Repository
- ❌ Any cross-layer direct calls

**Detection Commands**:
```bash
grep -r "from sqlalchemy import select" src/app/*/v1/
grep -r "db.execute(" src/app/*/v1/
```

### Mixin Inheritance Rules

**⚠️ EnterpriseMixin already includes AuditMixin + OptimisticLockMixin**

```python
# ❌ WRONG: Duplicate inheritance
class User(UserBase, AuditMixin, EnterpriseMixin, SoftDeleteMixin, table=True)

# ✅ CORRECT
class User(UserBase, EnterpriseMixin, SoftDeleteMixin, DataTableMixin, table=True)
```

**Mixin Hierarchy**:
```
EnterpriseMixin = AuditableMixin + OptimisticLockMixin
AuditableMixin = AuditMixin + StandardMixin
AuditMixin → TimestampMixin → BaseMixin
```

### Timezone Rules

| Scenario | Method | Return Type |
|----------|--------|-------------|
| Database storage | `timezone.now_for_db()` | naive UTC |
| API response | `timezone.now_utc().isoformat()` | aware ISO |
| Timestamp calculation | `timezone.now_utc().timestamp()` | Unix seconds |

**⚠️ NEVER call `.timestamp()` on naive datetime**

### Module Export

New Service MUST be exported in `__init__.py`:

```python
from .xxx_service import XxxService, xxx_service

__all__ = ["XxxService", "xxx_service"]
```

---

## Design Principles

| Principle | Application |
|-----------|-------------|
| **DRY** | Mixin for field reuse, ModelFactory for Schema generation, Base classes for CRUD |
| **KISS** | Prefer base class defaults, avoid over-abstraction |
| **SOLID** | Repository/Service/API single responsibility, Hook/Mixin for extension |
| **YAGNI** | Implement only current requirements, no speculative design |

---

## Planning Document Readability

规划/计划文档应优先表达**目标、架构决策、业务约定、任务边界、验收标准、风险和验证方式**。

**STRICTLY FORBIDDEN**:
- ❌ 在规划文档中粘贴完整类实现、完整函数实现或大段测试代码
- ❌ 把计划文档写成可复制执行的代码脚本
- ❌ 用代码细节替代架构决策和验收标准

**Allowed**:
- ✅ 关键接口名、文件职责、状态流、错误码、数据字段
- ✅ 简短伪代码或极短示例，用于说明约定
- ✅ 验证命令、测试场景和通过标准

产品功能行为的实现细节应在编码阶段通过 TDD、diff、测试和提交体现；其他类型变更按其风险使用相称的验证，
而不是把实现细节塞进规划文档。

### Documentation-only Changes

纯文档变更指人类阅读文档（包括 `.md`、`.mdx`、`.rst`、`.txt`、`.docx`、`.pdf`）的新增、修改、移动、
归档或删除，且不改变生产代码、测试工具、机器可读配置或可执行合同。

**STRICTLY FORBIDDEN**:

- ❌ 纯文档变更使用 TDD，或为其新增、修改 pytest/测试代码
- ❌ 测试或质量门禁读取、解析、断言人类阅读文档的正文、标题、路径清单、链接、状态或措辞
- ❌ 为保持既有“文档测试”通过而保留兼容字段、转发文档、占位文件或重复真源

纯文档变更只做与文档本身相称的验证，例如格式检查、链接/引用检查、归档目标存在性、项目内原路径缺席和
`git diff --check`；不得为这些验证编写测试代码。代码与文档混合变更时，仅代码行为部分遵循 TDD 和测试要求。

位于 `docs/` 下但被程序或 CI 读取的 `.toml`、`.csv`、`.yaml`、`.yml`、`.json` 等机器可读文件属于配置或
可执行合同，不属于纯文档；其解析和行为仍可通过测试约束。

---

## Documentation Archiving

归档文档必须移出项目目录，避免历史设计继续被检索或误认为当前真源。

- 统一归档到项目根目录同级的 `../archive_docs/<project_name>/`；本项目使用
  `../archive_docs/wes_backend/`。
- 项目内不得保留归档文件的副本、占位文件、软链接或转发文档；Git 中只保留原文件的删除记录。
- 归档前必须更新或删除项目内的当前态引用；确需历史追溯时，只记录外部归档位置，并明确其不属于当前架构真源。
- 移动前必须确认目标路径不存在；保留原文件名和完整内容，不得覆盖已有归档。发生重名时先确定新的唯一归档名。
- `docs/hardware/` 保存硬件厂商提供的原始协议和联调资料，属于有价值的外部输入，不按历史设计归档。
  厂商原文应保持原貌；当前架构边界、字段归一化和实现约束必须写入架构合同、设备合同附录或插件文档，不能反向改写
  厂商原始资料。厂商资料不是 WES 核心架构真源，也不得用来替代核心能力测试。

---

## Zero-Code Development Pattern

Inherit base classes to auto-generate full CRUD capabilities:

```python
# 1. Base fields (for Schema reuse)
class UserBase(BaseMixin):
    username: str
    email: str

# 2. Database table model (Base + Mixins + table-specific fields)
class User(UserBase, DataTableMixin, EnterpriseMixin, SoftDeleteMixin, table=True):
    __tablename__ = "users"
    hashed_password: str

# 3. Schema (ModelFactory auto-generates)
class UserCreate(ModelFactory(UserBase).for_create()):
    password: str

class UserUpdate(ModelFactory(UserBase).for_update()):
    pass

# 4. Repository/Service/API (zero code)
class UserRepository(BaseRepository[User]):
    pass

class UserService(BaseService[User, UserRepository]):
    def __init__(self):
        super().__init__(UserRepository(), enable_cache=True)

user_api = BaseAPI(
    module_name="admin",
    model=User,
    service=UserService(),
    create_schema=UserCreate,
    update_schema=UserUpdate,
    response_schema=UserResponse,
    prefix="/users",
)
```

**Auto-generated Routes**:
- `POST /users` - Create (permission: `admin:user:create`)
- `PUT /users/{id}` - Update (`admin:user:update`)
- `DELETE /users/{id}` - Delete (`admin:user:delete`)
- `GET /users/{id}` - Get single (`admin:user:detail`)
- `POST /users/query` - List query (`admin:user:list`)
- `POST /users/{id}/restore` - Restore (soft delete)
- `GET /users/trash` - Trash bin (soft delete)

---

## Documentation Index

| Document | Path | Content |
|----------|------|---------|
| Software Requirements Specification | `docs/architecture/SRS.md` | Product scope, actor responsibilities, functional and non-functional requirements |
| Project File Index | `docs/architecture/file_index.md` | Code structure, directory docs, quick find, response codes |
| Architecture Details | `.claude/context/architecture.md` | Layered architecture, Hook/Mixin system, status validation, JWT/RBAC |
| Development Rules | `.claude/context/rules.md` | Layer rules, Service calls, module export, timezone rules |
| Common Tasks | `.claude/context/howto.md` | Create module, custom logic, status validation, tree structure |
| Troubleshooting | `.claude/context/troubleshooting.md` | Cache issues, N+1 queries, architecture violations, ImportError |

---

## Project Structure & Module Organization
Core code lives in `src/`. Domain modules sit under `src/app/`; infrastructure lives in `src/core/`, `src/database/`, `src/middleware/`, `src/utils/`, and `src/celery_app/`. The FastAPI entrypoint is [`main.py`](/Users/kaizhou/codeDev/wes_backend/main.py), and Alembic revisions live in `migrations/versions/`. Tests are grouped under `tests/`, including `api/`, `auth/`, `e2e/`, `resilience/`, `load/`, and `mock/`.

## Build, Test, and Development Commands
Use `uv` locally.

- `./scripts/init-env.sh dev`: generate or refresh `.env` from the selected environment profile before first run or when switching environments.
- `docker-compose up -d`: start Postgres/TimescaleDB and Redis.
- `uv sync --dev`: install runtime and dev dependencies from `pyproject.toml` and `uv.lock`.
- `./scripts/migrate.sh upgrade`: apply the latest database migrations.
- New Alembic migrations must be created with Alembic's revision generator, for example `uv run alembic revision -m "<message>"` or the repository wrapper if one exists. Do not hand-write template-like `revision` IDs; let Alembic generate the random revision ID, then edit the generated file.
- `uv run uvicorn main:app --reload --host 0.0.0.0 --port 8001`: run the API locally.
- `uv run celery -A src.celery_app.app worker --loglevel=info --queues=default,celery`: start the async worker used by callback and workline flows.
- `sh src/celery_app/dev_worker_autoreload.sh`: run the Celery worker with source watching during active backend development.
- `./scripts/install-git-hooks.sh`: enable the repo-managed `pre-commit` quality gate for the current worktree.
- `./scripts/git-quality-gate.sh --profile quality`: run the same local quality gate used by the tracked `pre-commit` hook.
- `uv run pytest tests/`: run the default FAST test suite; QUALITY and affected HEAVY use their explicit commands below.
- `uv run ruff format . && uv run ruff check .`: match the formatter and linter used in CI.
- `uv run bandit -r src/`: run the same security scan used by Jenkins.

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

## Coding Style & Naming Conventions
Target Python 3.13. Ruff enforces formatting, imports, and lint rules: spaces, double quotes, and a 120-character line limit. Keep the existing layering: `v1/` for routes, `services/` for business logic, `repositories/` for data access, and `models/` for schemas. Use `snake_case` for files, functions, and variables, and `PascalCase` for classes.

## Comment Preservation Rules
When modifying existing code, **preserve all valuable comments** — they are critical for long-term maintainability.

- **Preserve**: Section headers (`# ===`), business logic explanations, parameter descriptions, inline clarifications, design rationale notes.
- **Preserve**: `TODO` / `FIXME` / `HACK` markers with their context.
- **Update**: When code behavior changes, update the corresponding comment to reflect the new behavior rather than deleting it.
- **Add**: When introducing non-obvious logic (workarounds, edge cases, performance considerations), add a brief comment explaining *why*, not just *what*.
- **Format**: Follow the project's existing comment style (e.g., `# ===` section dividers in config files).

**Examples**:
```python
# ✅ CORRECT: Preserve and update comment when modifying code
# Outbox 消息派发 - 将命令下发给设备（兜底）
# 正常流程由编排完成后即时 send_task 触发，Beat 仅处理遗漏/重试
"dispatch-outbox-batch": {
    "task": "src.celery_app.tasks.workline.dispatch_outbox_batch",
    "schedule": 10.0,  # 兜底轮询（原 1s，优化后 10s）
},

# ❌ WRONG: Stripping comments when modifying code
"dispatch-outbox-batch": {
    "task": "src.celery_app.tasks.workline.dispatch_outbox_batch",
    "schedule": 10.0,
},
```

## Testing Guidelines
Pytest uses `test_*.py`, `Test*`, and `test_*` discovery from `pyproject.toml`. 除纯文档变更外，新增或修改行为时应在受影响领域附近补充测试，并覆盖成功和失败路径。

### TDD 适用边界

TDD 只对新增产品功能，或修复、修改会改变产品可观察运行行为的生产代码强制执行。此类实施必须先建立能够失败的行为测试，
再完成最小实现并验证转绿。

**STRICTLY FORBIDDEN**:

- ❌ 对文档、规划、项目规则、注释、格式化、代码评审、调查分析等非功能性工作强制要求 TDD
- ❌ 对仅测试维护、测试归属调整、selector/测试治理、CI/构建/开发工具脚本、配置、依赖或元数据变更，把 RED→GREEN 作为硬性前置条件
- ❌ 对不改变产品可观察行为的重构、性能整理、迁移维护或数据治理，机械地新增失败测试以满足流程形式

上述非功能性变更仍必须执行与风险相称的验证；必要时可以补充测试，但不得把 TDD 作为开工、验收或提交的强制门槛。
代码与其他类型变更混合时，只对其中改变产品功能行为的生产代码切片执行 TDD。

### Test Suite Governance

所有 Agent 新增、移动、拆分或删除测试时，必须遵守 [`tests/README.md`](tests/README.md) 的目录归属、默认快速回归和重测试边界。

测试治理硬约束：

- 必须先建立目标对象测试并通过，再删除对应旧测试；不得反向。清理“人类阅读文档内容测试”不需要承接测试，按 `NONE` 删除。
- 同一行为只能有一个主要测试所有者。
- 删除测试的 Commit message 或 PR 描述必须标注承接的目标测试路径，或明确标注 `NONE`。
- 不得按 `replay`、`legacy`、`reconciliation` 等关键词批量删除测试。
- 默认 `pytest` 收集路径下的 `test_*.py` 不得依赖真实数据库、HTTP、Celery、Redis、容器等真实服务；重测试边界由目录位置和 `norecursedirs` 共同保证。
- WES 核心 `tests/` 只验证 SPEC 定义的最小执行内核、通用 WorkLine 能力、共享外部合同和可靠性不变量。
- 具体工作线和业务插件测试必须位于 `workline_plugins/<plugin_key>/tests/`，并与该插件的 `pyproject.toml`、`src/` 和 `fixtures/` 同包交付。
- 所有设备供应商必须适配 `docs/integration/third_party_integration_whitepaper.md` 定义的统一接口（wire）。核心合同测试只
  验证固定路径、公共包络、身份、幂等和 ACK/CALLBACK；供应商内部 DTO、认证、原始 Payload、原始码转换和设备行为由
  供应商一致性验收拥有，不在 WES 仓库建立 `device_adapters/` 私有适配包。
- 核心仓库不得存在 `tests/workline_plugins/` 或 `tests/device_adapters/`；核心测试不得导入根目录二次开发插件包或包含供应商私有协议。
- 供应商一致性验收和插件测试不得进入核心默认 pytest、核心覆盖率、核心质量门禁或核心 HEAVY selector。
- 产品内唯一 WMS 北向 Adapter 是 `src/app/wms_adapter/` 下的业务系统 ACL，不属于设备厂商二次开发包；其跨系统 FAST
  合同位于 `tests/contracts/wms_adapter/`，真实持久化与事务场景位于 `tests/integration/wms_adapter/`。这些测试只验证
  WMS Adapter，不得用于证明 `src/core/outbound_http/` 基础传输或 WES 最小执行内核。

**STRICTLY FORBIDDEN**:
- ❌ 在 `tests/` 根目录新增 `test_*.py`
- ❌ 把 integration / e2e / resilience / load / mock 测试混入默认快速回归集
- ❌ 为了快速通过门禁删除有业务价值的断言或失败路径覆盖
- ❌ 把 API facade 测试写成 service / repository / projection / orchestrator 大杂烩
- ❌ 把具体工作线、插件或供应商内部协议测试改名后放入 contracts / runtime / integration / e2e 等核心目录
- ❌ 创建只有测试、没有对应插件代码和 fixture 的二次开发包
- ❌ 新增超过 `3000` 行的测试文件；单文件超过 `1000` 行必须优先拆分或说明原因

**Required placement**:
- `tests/api/`: route、permission、response model、API facade 行为
- `tests/workline/`: WorkLine 静态身份、物理拓扑、配置校验和 `LineRunEpoch` 等通用能力
- `tests/runtime/`: 与具体插件无关的最小执行对象、投影、可靠性和诊断能力；旧平台测试必须逐步改写或删除
- `tests/contracts/`: 跨系统/跨模块契约
- `tests/core/`, `tests/database/`, `tests/sys/`, `tests/api_auth/`, `tests/deployment/`, `tests/utils/`: 对应基础设施或领域边界
- `tests/integration/`, `tests/e2e/`, `tests/resilience/`, `tests/load/`, `tests/mock/`: 显式运行的重测试目录，默认 pytest 不收集
- `workline_plugins/<plugin_key>/tests/`: 具体插件独立测试树，不属于核心 `tests/`，由插件包自己的 Pytest 配置和 CI 运行
- 供应商一致性验收：在供应商 ECS/网关交付边界独立运行，不属于核心 `tests/`

**Required verification for test changes**:
```bash
uv run pytest tests/architecture/test_suite_topology_guardrail.py tests/architecture/test_core_plugin_test_ownership_guardrail.py -q
uv run pytest <changed-test-files-or-domain> -q
uv run pytest --collect-only -q -o addopts='' | tail -5
./scripts/git-quality-gate.sh --profile quality
```

正常提交时，最后一条完整 QUALITY 由仓库 `pre-commit` hook 执行并作为本地提交证据；同一 staged 快照不得在提交前手工运行后又立即由 hook 重复运行。仅在不提交或诊断 hook 时手工执行完整 profile。

启动正常 `git commit` 前，如果暂存区包含 Python 文件，必须先对这些变更文件运行聚焦
`uv run ruff format --check <files>` 和 `uv run ruff check <files>`。该快速预检用于在完整 QUALITY 之前发现格式与静态检查错误，
不能替代正常提交 hook，也不得因此重复手工运行完整 QUALITY。

If a change intentionally touches integration / e2e / resilience / load / mock behavior, explicitly run the affected heavy-test directory and mention it in the PR. Do not rely on default pytest collection for those suites.

### 代码评审与验证职责

代码评审 subagent 负责只读核验计划、diff、架构边界、测试所有权和既有验证证据，不负责重复执行重测试。

- reviewer subagent 不得启动 Docker、PostgreSQL、Redis、Celery，不得执行 HEAVY、部署、迁移或其它会改变外部状态的验证；需要补充验证时，应把缺失证据或证据过期明确报告给主 Agent。
- 主 Agent / 实施 Agent 是测试执行 owner，负责运行直接受影响的 FAST/合同测试、staged selector 选中的 HEAVY，以及评审收敛后的完整分支门禁。
- 发起评审时必须提供精确 review base/head、`git status --short`、评审范围、selector scope/manifest、已执行命令、结果和 JUnit 路径；存在 staged、unstaged 或 untracked 差异时必须显式列出，不得只用 `HEAD_SHA` 冒充当前工作区快照。
- 验证证据必须记录 repository、base/head 或 staged tree、selector manifest、命令、结果和报告路径。完全相同的快照与 manifest 复用原证据；不得先对等价 unstaged 快照执行完整 selected HEAVY，再为 staged 标签重复执行同一批测试。
- 证据按变更面失效：人类阅读文档只使文档核验失效；测试或 selector 配置只使对应测试/selector 证据失效；生产代码使直接 FAST/合同与受影响 HEAVY 失效；migration 使临时数据库 HEAVY 失效。review base/head 变化时先分类增量 diff：仅人类阅读文档变化只使文档评审失效，生产代码、测试、机器可读合同或迁移变化才使对应代码 diff 评审失效。失效证据标记为 `STALE`，不得写成当前通过。
- 收到评审意见后，先判断变更类型：产品功能行为修复按 TDD 运行直接受影响测试，其他修复只运行与风险相称的聚焦检查；需要提交修复时，对该修复的精确 staged diff 运行 selector、选中的 HEAVY 和 GitNexus detect changes。中间评审轮次不重复运行完整分支 HEAVY，也不重复执行同一 staged 快照已通过的门禁。
- 多轮评审采用“首轮 fresh reviewer 完整 diff → 原 reviewer 定向 closure check → 最终 fresh reviewer 完整 diff”。最终 reviewer 仍发现新问题时重新进入该序列；停止条件不降低，但中间修复不反复派发完整重审。
- Critical/Important 意见全部清零后，主 Agent 必须对最终 `origin/develop...HEAD`（或计划冻结的 implementation base）执行一次完整 QUALITY、selected HEAVY 和 GitNexus 变更检测；此最终证据生成后若生产代码、测试、机器可读合同、selector、迁移或运行配置再次变化，必须按失效面重新生成。后续仅增加人类阅读文档时只补文档核验，不得重跑完整门禁。
- 高风险计划可以要求更强的最终门禁，但不得把 HEAVY 执行下放给 reviewer subagent，也不得让多个 reviewer 对同一快照重复执行相同重测试。

### 跨任务验证证据交接

实施任务进入 review、QA 或 ship 前，实施 owner 必须提供可复用的验证交接，不得让下游任务从零猜测或机械重跑：

- 记录 repository、base ref、HEAD 或 staged tree、`git status --short`、变更分类以及是否存在并发修改。
- 记录 selector scope、精确 manifest、生产代码/测试/selector/migration 是否变化、已执行命令、结果、时间和 JUnit/报告路径。
- 下游任务先比较快照、manifest 与变更分类；完全相同的证据直接复用，只重跑已失效的部分。
- selector 实现或配置变化时，先重跑 selector 合同与选择结果；如果生产代码、HEAVY 测试资产和最终 manifest 均未变化，
  不得仅因 selector 重新计算而重复执行同一批 HEAVY。
- 人类阅读文档或报告变化不得使生产代码的 QUALITY、HEAVY 或迁移证据失效。
- QA 启动前应已有明确、干净的实施快照。QA 不得默认承担遗留变更整理、实施提交或原子提交拆分；发现脏工作树时先报告
  其所有权和对快照的影响，只有用户明确授权后才处理。

### 后端 QA 路由

QA 必须按变更面选择最小有效路径，不得把通用页面 QA 机械套用到纯后端变更：

| 变更面 | 必要 QA | 默认不做 |
| --- | --- | --- |
| 前端页面、交互或视觉合同 | 浏览器功能、视觉、控制台与截图证据 | — |
| API 路由、认证、请求或响应合同 | 受影响端点 HTTP 冒烟、API/合同测试 | 无视觉合同的页面评分与批量截图 |
| Service、Repository、Runtime、迁移或后台任务 | 聚焦 FAST/合同、selected HEAVY、迁移或 worker 闭环 | `/health`、预期 404、页面视觉与可访问性评分 |
| 文档、项目规则、测试治理、开发工具 | 与变更相称的文档、脚本或治理验证 | 产品 QA、浏览器 QA、真实 HEAVY |

- `/health` 和 `/ready` 只证明存活/就绪合同；仅当相关路由、启动配置或部署接线发生变化时才作为主要 QA 目标。
- 不得为预期的 `/`、`/docs`、`/openapi.json` 404 生成重复截图或据此计算后端健康分。
- QA 先消费上游验证交接；只有证据缺失、失效或 QA 发现新问题时才运行对应门禁，不能以“fresh QA”名义重复同一快照的
  QUALITY、HEAVY 或迁移。

### Ship 增量验证

`ship` 在执行任何昂贵门禁前，必须先消费 implementation/review/QA 的验证交接，并比较同步 `develop` 前后的增量树：

- 新提交、merge 或 review base/head 变化如果只涉及人类阅读文档或报告，必须复用既有 QUALITY、HEAVY、迁移和代码评审证据；
  禁止以“fresh ship”名义重跑，只执行文档 diff、引用、状态和发布说明检查。
- 是否属于纯文档变化以“是否被程序、CI、构建、测试或运行时读取”为准，不以目录或扩展名机械判断。OpenAPI、VERSION、
  TOML/YAML/JSON/CSV、迁移、依赖锁、运行配置和可执行合同不是人类阅读文档。
- 同步 `develop` 后必须比较生产代码、测试、HEAVY 资产、selector、迁移和机器可读合同的实际差异；不能仅因 HEAD 或提交数变化
  就判定全部证据失效。如果这些树均未变化，原验证继续有效。
- selector 实现或配置是唯一变化时，按“跨任务验证证据交接”重跑 selector 合同与选择结果；manifest 和生产/HEAVY 资产不变时
  不重跑真实 HEAVY。
- 已有最终 coverage、计划完成审计和 pre-landing review 且对应快照未失效时，ship 只做增量核对；不得再次启动完整独立审计。
  新发现但不阻断发布的 coverage GAP 只记录为后续实施输入，不得在 ship 中扩张为测试设计或功能实施。
- 版本级别应在完成 diff 分类后、昂贵验证前确定；需要用户选择 MINOR/MAJOR 或其它发布语义时应尽早询问，避免门禁全部完成后等待。

### 重复审计的增量模式

用户再次询问已审计主题时，默认执行增量审计，不从头重复完整代码库扫描：

1. 读取上一轮结论、证据快照和未决项。
2. 只核对新增文档、提交、合同或当前 checkout 与上一快照的差异。
3. 如果结论前提未变化，直接报告“结论不变”、本轮增量证据和仍待处理项。
4. 只有 authoritative contract、base/head、实现路径或关键证据发生变化，才升级为完整重审。

只读审计不得为了形式完整运行测试、QUALITY、HEAVY、迁移、服务或部署，也不得重复生成上一轮已经成立的长篇证据矩阵。

纯文档提交由 `pre-commit` 自动执行 `git diff --cached --check`，不运行 FAST、selector 合同或 HEAVY；Markdown lint、链接、引用、归档路径和残留扫描由具体文档任务按变更面显式执行。只要暂存区包含生产代码、测试、脚本、迁移或 TOML/YAML/JSON/CSV 等机器可读配置，就回退完整 QUALITY。

### HEAVY Selector Mapping Governance

[`docs/architecture/heavy-test-impact.toml`](docs/architecture/heavy-test-impact.toml) 是 HEAVY selector 的机器可读映射真源，长期维护要求如下：

- 新增可能影响运行时的生产模块、迁移或基础设施配置时，必须同步增加精确 `[[mapping]]`；经评审确认无 HEAVY 影响时才可使用空 `heavy_tests` 表示显式 NONE，否则 selector 会 fail closed。
- 新增或移动 HEAVY 测试路径时，必须同步更新所有引用该路径的 `heavy_tests`。
- HEAVY 支撑资产与共享测试资产同样属于候选范围，不能被 `tests/**` ignore 兜底遮蔽。
- 当前尚无已验收权威 HEAVY 测试的既有业务、迁移、运行时配置和共享资产候选路径保持未映射；不得以 NONE 或臆造旧测试映射掩盖风险，待独立业务交接后补精确 mapping。
- 本地使用 `uv run scripts/select_heavy_tests.py --scope unstaged`；暂存区使用 `--scope staged`。CI 必须使用 `--base origin/${CI_TARGET_BRANCH}`，不能用工作区 scope 代替提交差异。
- selector 合同由 `uv run pytest tests/scripts -q` 验证，并永久纳入 quality profile；真实 HEAVY 测试不进入本地提交门禁。

## Commit & Pull Request Guidelines
Recent history follows Conventional Commits with scopes, for example `feat(auth,menu): ...` and `fix(user,tree): ...`. Keep subjects imperative and concise, and mention migrations when schema changes are included. PRs should summarize behavior changes, list local verification steps, link the issue, and call out config, migration, or API contract impacts.

## Configuration & Background Jobs
Environment profiles such as `.env.dev`, `.env.test`, and `.env.prod` feed the runtime `.env` generated by `./scripts/init-env.sh`; do not commit secrets. If a change touches callbacks, device events, or scheduled tasks, validate it with a running Celery worker and note that in the PR.

For git worktrees, treat `.env` as worktree-local state. Generating or editing `.env` in one worktree does not update any other worktree.

# ps.
use Chinese to Write document and Communication and Commit Comment

---

## 🛠 Agent Tooling & Efficiency

### GitNexus — 架构导航与变更安全
本项目使用 GitNexus 构建代码知识图谱。Agent 在执行写操作前必须遵守以下红线：

- **强制影响分析**：在修改任何函数、类或方法前，必须运行 `gitnexus_impact({target: "symbolName", direction: "upstream"})`。
- **风险确认**：如果影响分析返回 HIGH 或 CRITICAL 风险，必须在操作前向用户汇报并确认。
- **提交前检测**：在 Commit 前运行 `gitnexus_detect_changes()`，验证变更范围是否符合预期。
- **只读审计降级**：只读审计发现索引 stale 时，不默认刷新，也不因刷新等待阻塞结论；以当前源码、合同、`git diff`、测试和
  OpenAPI 为主证据，并将旧索引标记为补充证据。下方生成区块中的通用刷新提示主要适用于写操作前确需当前调用图的场景。
- **失败快速回退**：索引刷新因权限、registry、worktree 或工具错误失败一次后，立即回退 `rg`、精确文件读取和 Git diff；
  不得重复等待或让只读任务修改 `AGENTS.md`、`CLAUDE.md` 等生成区块。

### RTK (Rust Token Killer) — Token 优化
本项目环境通过 RTK 代理执行 Shell 命令以节省 60-90% 的 Token。

- **效率监控**：Agent 可以定期运行 `rtk gain` 查看 Token 节省情况。
- **调试模式**：如果遇到 RTK 过滤导致的输出丢失，可使用 `rtk proxy <cmd>` 获取原始输出。

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **wes_backend** (37845 symbols, 65253 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

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
