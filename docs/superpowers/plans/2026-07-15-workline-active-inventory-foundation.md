# WorkLine 活动实例清单基础 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 提供一条只读、可重复、可机器判定的命令，盘点全部未删除 WorkLine 的 capability/contract 配置和未完成运行引用，在后续能力平台切换前给出明确阻断项。

**Architecture:** 新建聚焦的 `WorklineMigrationInventoryService`，通过现有 `WorkLineRepository.get_list()` 与 `get_unfinished_workload_summary()` 读取配置和运行引用，通过静态 capability catalog/provider profile 校验合同；CLI 只负责会话生命周期、JSON 输出和退出码。该阶段不改数据库、不加 API、不修改高风险 `RuntimeInboxRepository`，也不把盘点逻辑塞进已有大型 `WorkLineService`。

**Tech Stack:** Python 3.13、Pydantic、SQLModel/SQLAlchemy async、PostgreSQL、pytest、Ruff、Bandit、GitNexus。

---

## 实施边界与锁定决策

- 本计划只实现“Active WorkLine Inventory Foundation”。粗分拣业务合同、版本 pin、迁移执行器、切流和回滚分别进入后续计划。
- 系统尚未发布，不增加旧字段、旧命令或旧输出格式的兼容层。
- 清单必须在 PostgreSQL `REPEATABLE READ`、`READ ONLY` 事务内生成，确保全部查询共享同一 MVCC 快照并由数据库拒绝意外写入；禁止 `INSERT`、`UPDATE`、`DELETE`、自动修复和隐式 commit。
- 复用 `WorkLineRepository.get_unfinished_workload_summary()` 作为未完成负载的唯一统计语义，避免复制 session/command/outbox/inbox/hold 状态集合（DRY）。
- CLI 属低频运维工具，允许按 WorkLine 逐项读取摘要；现有共享摘要最坏约 9 次 SQL/WorkLine，因此安全上限固定为 100，超限直接失败并要求先实施 bulk summary port。禁止只调大数字，暂不为未经验证的规模触碰 HIGH 风险 RuntimeInbox 查询或复制状态集合（KISS/YAGNI）。
- 任意 WorkLine 存在未完成运行引用都阻断切换；停用状态不豁免。
- `inventory_digest` 对去除 `generated_at` 后的 canonical JSON 计算 SHA-256；相同数据库快照必须得到相同 digest。
- CLI 退出码固定为 `0=foundation pass`、`1=运行失败`、`2=参数/环境错误`、`3=foundation blocked`。`--check-foundation` 在存在基础阻断项时退出 `3`，当前事实盘点基础通过时退出 `0`；该退出码不表示最终 cutover readiness。

### 与平台设计 T1 的关系

本计划是 T1 的“当前系统事实盘点”基础，不单独代表跨环境 migration matrix 已获批准：

| 平台设计要求 | 本计划覆盖 | 后续补齐点 |
|---|---|---|
| 当前环境 WorkLine 配置 | 完整覆盖未删除 WorkLine | 无 |
| Session/Command/Outbox/Inbox/Hold | 复用现有权威未完成负载摘要 | capability version pin 完成后增加按版本统计 |
| provider/Port 要求 | 部分覆盖：只盘点全局 provider profile catalog，不声称已关联到具体 WorkLine，也不参与 foundation blocker | capability requirement 与 binding 建模后验证每条 WorkLine 的现场/租户选择值 |
| WorkItem/Intent | 当前模型尚无完整 WorkLine/version 可执行引用合同 | runtime contract/version pin 计划增加后纳入同一报告 |
| 所有环境汇总与批准 | 每次执行产生一个环境的签名稳定报告 | deployment/cutover 计划聚合各环境报告并记录批准证据 |

因此，本计划完成后可以继续编写/实施粗分拣窄规格和最小 runtime contract；在 WorkItem/Intent、binding 与跨环境聚合补齐前，禁止把本报告称为最终 cutover preflight。

## NOT in scope

- 粗分拣 capability/contract 业务规格：属于独立业务合同，不由库存盘点推导。
- WorkItem/Intent 的 capability version pin：当前尚无完整可执行引用合同，需后续 runtime contract 计划建模。
- WorkLine 与 provider/Port 的 binding 及逐条 requirement 验证：本阶段只输出全局 `provider_profile_catalog`。
- 跨环境报告聚合、人工批准、切流与回滚：属于 deployment/cutover 计划。
- 超过 100 条 WorkLine 的批量引用摘要：在真实规模或超时证据触发前保留为 P3 TODO，遵守 YAGNI。
- API、UI、数据库迁移、自动修复和最终迁移执行器：本计划只交付低频只读 CLI。
- 旧字段、旧命令或旧输出合同兼容：系统未发布，不引入兼容层。

## What already exists

| 现有能力 | 本计划如何处理 |
|---|---|
| `WorkLine` 的 `plugin_key` / `contract_version` / `run_mode` / active 状态 | 直接盘点，不创建平行配置模型 |
| `WorkLineRepository.get_list()` | 复用现有未删除 WorkLine 读取语义 |
| `WorkLineRepository.get_unfinished_workload_summary()` | 作为五类未完成引用的唯一统计语义，不复制状态集 |
| `repository_wiring.workline_repository` | Service 生产单例复用该装配及其 `RuntimeInboxQueryPort`，不重复构造 repository |
| capability definitions 与 provider profile catalog loader | 只读映射为报告合同，不动态加载 plugin class/entry point |
| PostgreSQL/async session 与现有 integration fixture | 用于锁定真实状态统计、MVCC 快照和 read-only 拒写 |
| `tests/workline_runtime/`、`tests/deployment/`、`tests/integration/` 测试分层 | 按现有拓扑分开纯逻辑、CLI 与 PostgreSQL 重测试 |

### 数据流与事务所有权

```text
operator
  |
  | --expected-environment 必须等于 settings.APP_ENV
  v
scripts/workline_migration_inventory.py
  |
  | 建立 PostgreSQL REPEATABLE READ + READ ONLY transaction
  | 整个报告期间持有同一个 MVCC snapshot
  v
WorklineMigrationInventoryService
  |\
  | +--> capability catalog --------------------+
  | +--> provider profile catalog               |
  |                                             v
  +--> repository_wiring.workline_repository -> 规范化/排序 -> SHA-256 digest
           |
           +--> WorkLine 配置
           +--> Session / Command / Outbox / Inbox / RuntimeHold 摘要
                                                       |
                                                       v
                                   foundation report + 明确退出码
                                                       |
                                                       v
                                            stdout 或指定 JSON 文件
```

事务由 CLI 组合根拥有，Service 不自行 begin/commit/rollback。实现时在 Service class docstring 放置精简图，至少标出 `caller-owned REPEATABLE READ + READ ONLY transaction -> build_report -> repository/catalog -> canonical digest`；任何新增调用方都必须建立同等事务前置条件。

## 输出合同

顶层 `schema_version` 固定为 `workline-migration-inventory-foundation.v1`。条目按 `(line_code, id)` 排序，`provider_profile_catalog` 按 code 排序，issues 按 `(severity, code, workline_id)` 排序。顶层和单项均使用 `foundation_ready`，禁止在本阶段输出容易被误认为最终 cutover 门禁的通用 `ready` 字段。`provider_profile_catalog` 只表达当前静态目录，不表示任何 WorkLine 已满足 provider/Port requirement。

阻断码：

| code | 条件 |
|---|---|
| `ACTIVE_WITHOUT_PLUGIN` | 活动 WorkLine 未配置 `plugin_key` |
| `ACTIVE_WITHOUT_CONTRACT_VERSION` | 活动 WorkLine 未配置 `contract_version` |
| `UNKNOWN_PLUGIN` | `plugin_key` 不在 capability catalog |
| `CONTRACT_VERSION_MISMATCH` | 配置版本与 catalog 版本不一致 |
| `RUNTIME_REFERENCES_PRESENT` | 任一未完成 session/command/outbox/inbox/runtime hold 存在 |

非活动且完全未绑定 capability 的空壳配置允许出现在报告中，不构成阻断。非活动但配置了未知/错版 capability 仍阻断，防止之后误启用。

### 测试覆盖图

```text
CODE PATHS                                                   OPERATOR FLOWS
[+] migration_inventory models                              [+] Generate foundation report
  ├── [★★★] strict/frozen/unknown field                       ├── [★★★] correct environment -> stdout
  ├── [★★★] issue/reference Enum validation                   ├── [★★★] correct environment -> atomic file
  ├── [★★★] strict non-negative reference counts              ├── [★★★] environment mismatch -> exit 2
  ├── [★★★] aware datetime + SHA-256                          ├── [★★★] foundation_ready x check four-case matrix
  └── [★★★] JSON round-trip                                   └── [★★★] existing output survives I/O failure

[+] WorklineMigrationInventoryService                       [+] PostgreSQL snapshot [INTEGRATION]
  ├── [★★★] empty / valid / over-limit inventory              ├── [★★★] Session active/terminal matrix
  ├── [★★★] missing/unknown/mismatched capability              ├── [★★★] Command active/terminal matrix
  ├── [★★★] five sample normalizations + malformed input       ├── [★★★] Outbox active/terminal matrix
  ├── [★★★] exact by_type keys / strict values / total         ├── [★★★] Inbox active/terminal matrix
  ├── [★★★] deterministic sorting/digest                       ├── [★★★] RuntimeHold active/terminal matrix
  └── [★★★] production repository identity                    ├── [★★★] concurrent commit excluded from snapshot
                                                               └── [★★★] read-only transaction rejects UPDATE
[+] CLI
  ├── [★★★] environment guard
  ├── [★★★] REPEATABLE READ + READ ONLY
  ├── [★★★] stdout / atomic output / cleanup
  ├── [★★★] exits 0 / 1 / 2 / 3
  └── [★★★] known error sanitization / unknown error re-raise

COVERAGE TARGET: 31/31 grouped paths (100%)
QUALITY TARGET: all paths ★★★ (behavior + boundary + error)
```

空 WorkLine 集合是合法的当前环境事实，报告包含空 `worklines`、稳定 digest 和 `foundation_ready=true`；它不证明数据库身份正确。操作者仍必须通过 `--expected-environment` 与部署配置选择正确环境，最终跨环境批准属于后续计划。

### 生产失败模式与可见性

| Codepath | 现实失败 | Test | Handling / operator visibility |
|---|---|---|---|
| model contract | naive 时间、未知 issue code、非法 digest | model parameter cases | 直接构造拒绝非法数据；repository/catalog 适配时的校验失败转为 invariant error/exit 1，编程错误继续抛出 |
| repository summary adapter | 缺键、负数、bool、未知 sample shape | service malformed summary matrix | invariant error；stderr 明确，绝不生成报告 |
| capability validation | plugin 缺失、未知、版本不匹配 | service case table | 报告 blocker；普通生成退出 0，显式 check 退出 3 |
| inventory limit | WorkLine 超过 100 | service boundary test | limit error；stderr 明确要求 bulk summary 并退出 1，不输出截断报告 |
| concurrent runtime writes | 报告中途 Session/Inbox 状态变化 | PostgreSQL concurrent snapshot test | 单一 MVCC snapshot；本次报告一致，下次报告观察新状态 |
| slow/hung inventory | 单条 SQL、事务空闲或总流程超时 | CLI timeout unit cases + PostgreSQL timeout settings | 数据库/客户端取消，rollback + engine dispose，stderr + exit 1 |
| accidental DB write | 未来代码在 inventory 事务写库 | PostgreSQL no-op UPDATE rejection | 数据库拒绝并回滚；测试断言 SQLSTATE |
| DB unavailable | 连接、认证、SQL 执行失败 | CLI injected SQLAlchemyError | 脱敏 stderr + exit 1；不打印连接串/SQL 参数 |
| output interruption | 磁盘满、fsync/replace 失败、进程终止 | atomic writer failure matrix | 旧文件保持完整，临时文件清理，stderr + exit 1 |
| invalid invocation | expected environment 不匹配或参数缺失 | CLI parser cases | argparse stderr + exit 2；不连接数据库 |
| unexpected programming bug | AttributeError/TypeError 等 | CLI unexpected exception case | 不 catch-all；保留 traceback，进程非零退出 |

## 执行前置条件

- [ ] 从 `develop` 创建隔离分支/worktree；先提交或妥善保留当前设计文档和本计划，禁止覆盖工作区已有修改。
- [ ] 使用 `superpowers:using-git-worktrees` 准备环境，运行 `./scripts/init-env.sh dev`、`uv sync --dev`。
- [ ] 运行 GitNexus context/impact：`WorkLineRepository`、`WorkLineRepository.get_list`、`WorkLineRepository.get_unfinished_workload_summary`、`list_workline_capability_definitions`。若执行时为 HIGH/CRITICAL，先向用户报告并确认。
- [ ] 明确禁止修改 `RuntimeInboxRepository`；现有影响分析为 HIGH，本计划只经 `WorkLineRepository` 间接复用其查询合同。

### Task 1: 定义稳定的迁移清单合同

**Files:**

- Create: `src/app/workline/models/migration_inventory.py`
- Modify: `src/app/workline/models/__init__.py`
- Create: `tests/workline_runtime/test_workline_migration_inventory_models.py`

- [ ] **Step 1: 先写模型序列化失败测试**

先用最小合法报告锁定 `schema_version`、`foundation_ready`、Enum JSON 值、空列表和未知字段拒绝；再用参数化非法输入覆盖下方合同表，不在计划中固化完整测试实现。

- [ ] **Step 2: 运行测试并确认因模块不存在而失败**

Run: `uv run pytest tests/workline_runtime/test_workline_migration_inventory_models.py -q`

Expected: `ModuleNotFoundError: src.app.workline.models.migration_inventory`。

- [ ] **Step 3: 实现最小模型合同**

模型通过私有 `_FrozenInventoryModel` 统一继承 `ConfigDict(extra="forbid", frozen=True)`；该基类不从 package facade 导出。除该已发生的配置复用外，不增加扩展字典、兼容 alias 或通用 schema framework：

| 合同 | 必需字段/约束 |
|---|---|
| `WorklineMigrationInventorySeverity` | `BLOCKER` / `WARNING` |
| `WorklineMigrationInventoryIssueCode` | 本计划定义的 5 个稳定阻断码，未知值 fail closed |
| `WorklineRuntimeReferenceType` | `SESSION` / `COMMAND` / `OUTBOX` / `INBOX` / `RUNTIME_HOLD` |
| `WorklineRuntimeReferenceSample` | `type` + 非空 `reference` + `status` |
| `WorklineRuntimeReferenceSummary` | 五类 strict non-negative int、`total`、可选 `sample`；`bool` 不得被当作 int |
| `WorklineMigrationInventoryIssue` | code、severity、message，可选 WorkLine 身份 |
| `WorklineMigrationInventoryItem` | WorkLine 身份/状态、配置与 catalog 版本、run mode、引用摘要、issues、`foundation_ready` |
| `WorklineProviderProfileInventoryItem` | provider/contract/environment 及排序后的 query/effect capability 列表 |
| `WorklineMigrationInventoryReport` | foundation schema literal、environment、`AwareDatetime`、64 位小写十六进制 digest、worklines/catalog/issues、`foundation_ready` |

同时从 `src/app/workline/models/__init__.py` 显式导出三个 Enum 和六个公开 value model；私有 `_FrozenInventoryModel` 不导出。Service 分类与测试必须引用 Enum 成员，禁止再次散落裸错误码或 reference type 字符串。

- [ ] **Step 4: 增加基类继承、issue code 拒绝未知值、aware datetime、SHA-256 格式、`extra="forbid"`、frozen、默认 schema version 和 JSON round-trip 测试并通过**

模型测试必须分别拒绝 naive datetime、非法日期、非 64 位 digest、包含非小写十六进制字符的 digest；合法 aware UTC datetime 经 `model_dump(mode="json")` 输出 ISO 字符串。

Run: `uv run pytest tests/workline_runtime/test_workline_migration_inventory_models.py -q`

Expected: 全部通过。

- [ ] **Step 5: 提交合同**

```bash
git add src/app/workline/models/migration_inventory.py src/app/workline/models/__init__.py tests/workline_runtime/test_workline_migration_inventory_models.py
git commit -m "feat(workline): 定义迁移清单合同"
```

### Task 2: 实现清单分类与确定性摘要

**Files:**

- Create: `src/app/workline/services/migration_inventory_service.py`
- Modify: `src/app/workline/services/__init__.py`
- Create: `tests/workline_runtime/test_workline_migration_inventory_service.py`

- [ ] **Step 1: 用 fake repository 写失败测试，锁定阻断语义**

至少覆盖以下 case table：

| Case | active | plugin/version | runtime total | Expected |
|---|---:|---|---:|---|
| 基础盘点通过 | true | catalog 中且同版 | 0 | foundation_ready=true |
| 缺插件 | true | none/version none | 0 | `ACTIVE_WITHOUT_PLUGIN` + `ACTIVE_WITHOUT_CONTRACT_VERSION` |
| 未知插件 | false | unknown/v1 | 0 | `UNKNOWN_PLUGIN` |
| 版本不符 | true | known/old | 0 | `CONTRACT_VERSION_MISMATCH` |
| 有运行引用 | false | known/current | 1 | `RUNTIME_REFERENCES_PRESENT` |
| 非活动空壳 | false | none/none | 0 | foundation_ready=true |

fake 只实现服务需要的 `get_list()` 和 `get_unfinished_workload_summary()` 两个方法，并由构造参数提供 WorkLine 列表与按 ID 索引的 summary；禁止继承 BaseRepository 或模拟无关 CRUD 细节。

- [ ] **Step 2: 运行测试并确认服务模块不存在**

Run: `uv run pytest tests/workline_runtime/test_workline_migration_inventory_service.py -q`

Expected: import failure。

- [ ] **Step 3: 实现单一职责服务**

服务构造函数只注入 repository、capability definitions loader、provider profile catalog loader、clock 和 `max_worklines=100`。repository 默认值必须是 `src.app.runtime.orchestration.repository_wiring.workline_repository`，复用已注入 `RuntimeInboxQueryPort` 的唯一生产装配；测试显式传入 fake。禁止在本 Service 或 CLI 内再次构造 `WorkLineRepository`/`RuntimeInboxRepository`。公开方法仅保留异步 `build_report(db: AsyncSession, *, environment: str) -> WorklineMigrationInventoryReport`；其余分类、排序和 digest 方法均为私有纯函数。

Service class docstring 必须包含“caller-owned snapshot”精简 ASCII 图，并明确自身不创建或提交事务；测试使用 fake session 时可绕过数据库事务，生产调用方不得绕过。

实现约束：

1. `get_list(limit=max_worklines + 1, offset=0, order_by_raw=[WorkLine.line_code, WorkLine.id])`；返回总数或列表超过 100 时抛 `WorklineMigrationInventoryLimitExceeded`，错误消息明确要求先实现 bulk summary port，禁止输出不完整报告或通过配置临时扩大上限。
2. capability loader 转成 `{capability_key: definition}`，不读取 plugin class，不动态加载 entry point。
3. 对每个 WorkLine 调用现有 `get_unfinished_workload_summary()`。先验证 `by_type` 精确包含 `sessions/commands/outboxes/inboxes/runtime_holds` 五个键且每个值都是非 bool 的非负 int，再验证顶层 `count` 也是非 bool 的非负 int 且等于五类之和；缺键、多键、负数、bool、字符串或合计不一致都抛 `WorklineMigrationInventoryInvariantError`。通过后映射为全部字段必填的强类型摘要；若 repository/catalog 适配数据在 Pydantic 构造时校验失败，Service 将其转为同一 invariant error，而手写程序缺陷仍保留原异常。私有纯函数把 repository 的 `session_code/command_code/dispatch_key/inbox_id/count` 五种 sample shape 归一化为 `WorklineRuntimeReferenceSample(type, reference, status)`；未知 type、缺字段或空 reference 必须抛同一 invariant error，禁止原样透传字典。
4. 用私有纯函数分类 issue；`foundation_ready` 等于无 `BLOCKER`。
5. `provider_profile_catalog` 只输出 `provider_code/contract_version/environment/runtime_capabilities_query/runtime_capabilities_effect`；两个 capability 列表先排序。禁止把 retry、fixture path 等内部配置整体泄露到报告，也禁止据此推导 WorkLine requirement blocker。
6. digest 输入为 `schema_version + environment + worklines + provider_profile_catalog + issues + foundation_ready` 的 `model_dump(mode="json")`，使用 `json.dumps(digest_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))`；不包含 `generated_at` 和 digest 本身。
7. `generated_at` 使用注入 clock 的 aware UTC ISO；生产默认 `timezone.now_utc`。

- [ ] **Step 4: 增加稳定排序、digest、上限和 invariant 测试**

关键断言：同一 fake 数据、不同输入排列与不同 clock 时间得到相同 digest；报告顺序稳定；五种 sample 均归一化为同一合同，未知/缺字段 sample fail closed；summary 缺键、多键、负数、bool、字符串和合计不一致逐项 fail closed；100 条通过，101 条时不调用摘要查询并抛 limit error。

Run: `uv run pytest tests/workline_runtime/test_workline_migration_inventory_service.py -q`

Expected: 全部通过。

- [ ] **Step 5: 从 service facade 显式导出**

在 `src/app/workline/services/__init__.py` 导出 `WorklineMigrationInventoryService`、两个领域错误和单例 `workline_migration_inventory_service`。单例通过 Service 的默认依赖复用 `repository_wiring.workline_repository`；增加测试断言生产单例持有该同一 repository identity。不要加入 `_LAZY_SHIM_MAP`。

- [ ] **Step 6: 提交服务**

```bash
git add src/app/workline/services/migration_inventory_service.py src/app/workline/services/__init__.py tests/workline_runtime/test_workline_migration_inventory_service.py
git commit -m "feat(workline): 生成活动工作线迁移清单"
```

### Task 3: 增加只读 CLI 与机器退出码

**Files:**

- Create: `scripts/workline_migration_inventory.py`
- Create: `tests/deployment/test_workline_migration_inventory_cli.py`

- [ ] **Step 1: 写 CLI 失败测试**

测试通过 monkeypatch 注入 `build_report`，不连接数据库。覆盖：stdout 默认输出、`--output` UTF-8 文件、`foundation_ready × check_foundation` 四种组合（报告始终输出；只有 blocked + check 返回 3，其余返回 0）、参数/环境不匹配返回 2、运行异常返回 1、输出目录不存在时明确失败。
关键顺序断言：即使报告被阻断，CLI 也必须先完整写出 JSON，再在开启 `--check-foundation` 时返回 3。

- [ ] **Step 2: 运行并确认脚本模块不存在**

Run: `uv run pytest tests/deployment/test_workline_migration_inventory_cli.py -q`

Expected: import failure。

- [ ] **Step 3: 实现 CLI**

脚本保持三个边界：

| 边界 | 职责 |
|---|---|
| `build_report()` | 创建 `REPEATABLE READ` engine/session，在 60 秒总超时内开启一个事务，依次设置 `READ ONLY`、5 秒 statement timeout、15 秒 idle transaction timeout，调用 Service，并在 `finally` dispose engine |
| `run(argv)` | 解析参数、在连库前校验 expected environment、生成报告、输出 canonical JSON，再根据 `foundation_ready × --check-foundation` 返回 0 或 3 |
| `main(argv)` | 将超时、inventory 领域错误、I/O 和 SQLAlchemy 错误映射为脱敏 stderr/exit 1；未知编程错误原样抛出 |

参数：

- `--expected-environment {dev,test,prod}`：必填，只用于校验操作者意图，必须与 `settings.APP_ENV` 完全一致；报告环境始终取 `settings.APP_ENV`，禁止调用方自由改名。参数缺失、非法值或环境不匹配都由 argparse `parser.error()` 终止：直接单测断言 `SystemExit.code == 2`，子进程测试断言进程退出码 2。
- `--output PATH`：可选；父目录必须已存在，不在脚本内创建目录。私有 `write_report_atomically()` 必须在目标同目录创建临时文件，写入 UTF-8 后 flush + `os.fsync()`，最后通过 `os.replace()` 原子替换；任何异常都清理临时文件并保留既有目标文件。
- `--check-foundation`：启用当前事实盘点基础的退出码；不影响报告生成，也不表示最终 cutover readiness。脚本定义命名常量 `EXIT_OK=0`、`EXIT_RUNTIME_ERROR=1`、`EXIT_USAGE_ERROR=2`、`EXIT_FOUNDATION_BLOCKED=3`，禁止在分支中散落裸数字。

脚本定义 `INVENTORY_STATEMENT_TIMEOUT_SECONDS=5`、`INVENTORY_IDLE_TRANSACTION_TIMEOUT_SECONDS=15`、`INVENTORY_TOTAL_TIMEOUT_SECONDS=60` 三个命名常量，不提供临时放宽 CLI 参数。不得调用 `commit()`、不得打印连接串、SQL 文本或参数。`main()` 只捕获 `TimeoutError`、两个 inventory 领域错误、`OSError` 和 `SQLAlchemyError`，输出稳定且脱敏的 stderr 后返回 1；未知异常继续抛出，禁止 catch-all 吞掉程序缺陷。CLI 单元测试必须逐类验证已知错误返回 1、未知错误重新抛出、environment 匹配/不匹配、engine 使用 `REPEATABLE READ`，并在构建报告前依次设置 `READ ONLY`、5 秒 statement timeout、15 秒 idle transaction timeout；用可控阻塞 coroutine 验证总超时会取消报告并关闭 session/engine。不匹配时不得连接数据库或生成报告。文件测试必须覆盖首次写入、覆盖旧报告、write/flush/fsync/replace 失败时旧文件不变、临时文件被清理；这些条件缺一都不能声称生成了可信环境证据。

- [ ] **Step 4: 运行 CLI 测试并通过**

Run: `uv run pytest tests/deployment/test_workline_migration_inventory_cli.py -q`

Expected: 全部通过。

- [ ] **Step 5: 提交 CLI**

```bash
git add scripts/workline_migration_inventory.py tests/deployment/test_workline_migration_inventory_cli.py
git commit -m "feat(deployment): 增加工作线迁移清单命令"
```

### Task 4: 用 PostgreSQL 锁定真实引用统计合同

**Files:**

- Create: `tests/integration/test_workline_migration_inventory_postgresql.py`

- [ ] **Step 1: 写 PostgreSQL integration test**

使用仓库现有 PostgreSQL fixture，建立两个 WorkLine，并通过参数化 fixture 覆盖全部五类运行引用：

1. 一个 catalog 同版、无运行引用，断言 foundation_ready。
2. 一个 catalog 同版，分别关联 `WorklineSession`、`DeviceCommand`、`SystemOutbox`、`RuntimeInbox`、`RuntimeHold` 的 active 与 terminal 记录。每种类型必须至少覆盖 repository 当前采用的全部 active/terminal status 集合，逐项断言只有 active 记录进入对应计数、`total` 等于五类之和，并产生 `RUNTIME_REFERENCES_PRESENT`。
3. 为五类各建立单独场景，断言 sample 归一化后的 `type/reference/status`；再建立五类同时存在场景，锁定现有 sample 优先级 `SESSION -> COMMAND -> OUTBOX -> INBOX -> RUNTIME_HOLD`，防止 repository 查询顺序变化静默改变报告证据。

额外记录调用前后五类运行表和 WorkLine 的行数与目标字段，调用后断言不变，证明 inventory 无写入副作用。不要在 `tests/` 根目录或默认快速回归中放置此测试。复用参数化 builder，禁止为每种状态复制整段 ORM 构造代码。

再增加一个并发快照场景：inventory 事务读出 WorkLine 列表后暂停，第二个独立 session 提交一条关联 RuntimeInbox，再恢复 inventory；断言本次报告仍保持事务开始时的引用计数，下一次新事务生成的报告才观察到新 Inbox。该测试同时断言当前事务隔离级别为 `repeatable read`、事务为 read-only、`statement_timeout=5s`、`idle_in_transaction_session_timeout=15s`，并以测试自身的更短超时保护并发协调，禁止产生挂起测试。

最后在独立 read-only 事务中对 fixture WorkLine 执行一次 `SET line_name = line_name` 的 no-op UPDATE，断言 PostgreSQL 返回 read-only transaction 对应 SQLSTATE（不要匹配完整英文错误文本），随后 rollback，并在新 session 中确认目标行未改变。该测试直接证明数据库级写保护，而不只是检查 `SHOW transaction_read_only`。

- [ ] **Step 2: 显式运行重测试**

Run: `uv run pytest tests/integration/test_workline_migration_inventory_postgresql.py -q`

Expected: PostgreSQL 可用时全部通过；环境未准备好不能以 skip 冒充验收，需先按测试 fixture 指引启动测试数据库。

- [ ] **Step 3: 提交集成合同**

```bash
git add tests/integration/test_workline_migration_inventory_postgresql.py
git commit -m "test(workline): 验证迁移清单 PostgreSQL 合同"
```

### Task 5: 完成治理门禁与人工验收

**Files:**

- Verify only; 修复仅限本计划创建/修改的文件。

- [ ] **Step 1: 运行快速域测试**

```bash
uv run pytest tests/workline_runtime/test_workline_migration_inventory_models.py tests/workline_runtime/test_workline_migration_inventory_service.py tests/deployment/test_workline_migration_inventory_cli.py -q
```

Expected: 全部通过。

- [ ] **Step 2: 运行测试拓扑和收集门禁**

```bash
uv run pytest tests/architecture/test_test_suite_topology_guardrail.py -q
uv run pytest --collect-only -q -o addopts='' | tail -5
```

Expected: 拓扑 guardrail 通过；新增 integration test 不进入默认快速集合。

- [ ] **Step 3: 运行格式、静态和安全检查**

```bash
uv run ruff format --check src/app/workline/models/migration_inventory.py src/app/workline/services/migration_inventory_service.py scripts/workline_migration_inventory.py tests/workline_runtime/test_workline_migration_inventory_models.py tests/workline_runtime/test_workline_migration_inventory_service.py tests/deployment/test_workline_migration_inventory_cli.py tests/integration/test_workline_migration_inventory_postgresql.py
uv run ruff check src/app/workline/models/migration_inventory.py src/app/workline/services/migration_inventory_service.py scripts/workline_migration_inventory.py tests/workline_runtime/test_workline_migration_inventory_models.py tests/workline_runtime/test_workline_migration_inventory_service.py tests/deployment/test_workline_migration_inventory_cli.py tests/integration/test_workline_migration_inventory_postgresql.py
uv run bandit -r src/app/workline/services/migration_inventory_service.py scripts/workline_migration_inventory.py
./scripts/git-quality-gate.sh --profile quality
```

Expected: 全部退出 0。

- [ ] **Step 4: 在测试数据库执行人工验收**

```bash
APP_ENV=test uv run python scripts/workline_migration_inventory.py --expected-environment test --output /tmp/workline-migration-inventory.json --check-foundation
uv run python -m json.tool /tmp/workline-migration-inventory.json >/dev/null
```

Expected: JSON 合法；无 blocker 时第一条命令退出 0，有 blocker 时退出 3 且文件仍完整生成，参数/环境错误退出 2，运行失败退出 1。人工核对每个活动 WorkLine、catalog 版本和未完成引用均出现在报告中。

- [ ] **Step 5: 提交前运行 GitNexus 变更检测**

运行 `gitnexus_detect_changes()`，确认只影响新增 inventory 模型/服务/CLI 和显式 export；若出现 runtime ingest、orchestrator 或数据库写链路，停止并调查。

- [ ] **Step 6: 执行最终 diff 检查**

```bash
git status --short
git diff --check
git log --oneline develop..HEAD
```

Expected: 无空白错误；提交粒度与四个任务一致；不存在 migration、API route、兼容 shim 或与本计划无关的文件。

## Worktree parallelization strategy

Sequential implementation, no parallelization opportunity.

合同模型是 Service 的前置，Service 又是 CLI 和 PostgreSQL 合同测试的前置；九个文件虽跨 models/services/scripts/tests，但共享同一输出合同与 repository 语义。为避免多个 worktree 同时修改 facade export、错误码和 fixture 而造成合并漂移，按 Task 1 → 2 → 3 → 4 → 5 串行执行。

## Retrospective learning

近期 `v0.15.x`/`v0.16.0.0` 已经过多轮 RuntimeInbox 权威收敛、运行时值规范化与 WorkLine final cleanup；当前 `develop` 上还有“重放验真拒绝直接进入死信”修复。这些历史表明 runtime 状态语义和重放路径曾是高漂移区，因此本计划强制复用 `get_unfinished_workload_summary()`、禁止直改 HIGH 风险 `RuntimeInboxRepository`，并用真实 PostgreSQL active/terminal 矩阵锁定权威语义。

## Implementation Tasks

Synthesized from this review's findings. Each task derives from a specific finding above. Run with Claude Code or Codex; checkbox as you ship.

- [ ] **T1 (P1, human: ~1h / CC: ~10min)** — 合同模型 — 实现严格、冻结、可机器验证的 foundation report
  - Surfaced by: Code Quality Review — 私有严格基类、Enum 错误码、typed runtime sample、aware datetime 与 SHA-256 格式
  - Files: `src/app/workline/models/migration_inventory.py`、`src/app/workline/models/__init__.py`、`tests/workline_runtime/test_workline_migration_inventory_models.py`
  - Verify: `uv run pytest tests/workline_runtime/test_workline_migration_inventory_models.py -q`
- [ ] **T2 (P1, human: ~2h / CC: ~20min)** — 盘点服务 — 复用权威 repository 装配并生成确定性报告
  - Surfaced by: Architecture + Performance Review — 生产 repository identity、catalog 边界、summary invariant、稳定 digest 与 100 条硬上限
  - Files: `src/app/workline/services/migration_inventory_service.py`、`src/app/workline/services/__init__.py`、`tests/workline_runtime/test_workline_migration_inventory_service.py`
  - Verify: `uv run pytest tests/workline_runtime/test_workline_migration_inventory_service.py -q`
- [ ] **T3 (P1, human: ~2h / CC: ~20min)** — 运维 CLI — 实现环境护栏、只读快照、超时、原子输出和四类退出码
  - Surfaced by: Architecture + Code Quality + Performance Review — expected environment、`REPEATABLE READ/READ ONLY`、退出 0/1/2/3、原子文件与本地/总超时
  - Files: `scripts/workline_migration_inventory.py`、`tests/deployment/test_workline_migration_inventory_cli.py`
  - Verify: `uv run pytest tests/deployment/test_workline_migration_inventory_cli.py -q`
- [ ] **T4 (P1, human: ~4h / CC: ~45min)** — PostgreSQL 合同 — 锁定五类引用状态、MVCC 快照与数据库级拒写
  - Surfaced by: Test Review — 五类 active/terminal 矩阵、严格 summary、foundation check 矩阵、并发快照和 no-op UPDATE 拒绝
  - Files: `tests/integration/test_workline_migration_inventory_postgresql.py`
  - Verify: `uv run pytest tests/integration/test_workline_migration_inventory_postgresql.py -q`
- [ ] **T5 (P1, human: ~1h / CC: ~15min)** — 治理门禁 — 运行快速/重测试、拓扑、静态、安全与 GitNexus 验证
  - Surfaced by: Test Review + Repository Guidelines — 测试分层、未知错误重抛、质量门禁与提交前影响检测
  - Files: 仅验证本计划的 9 个实施文件；修复不得超出边界
  - Verify: `./scripts/git-quality-gate.sh --profile quality` 与 `gitnexus_detect_changes()`

## 完成定义

- 任意未删除 WorkLine 都被完整盘点；超过安全上限时 fail closed。
- capability 缺失、未知、错版及所有未完成运行引用均产生稳定 blocker。
- 同一数据库快照的内容、排序和 digest 确定；时间戳不污染 digest。
- PostgreSQL 并发集成测试证明一次报告只观察一个 `REPEATABLE READ` 快照，数据库拒绝该事务中的写操作。
- CLI 可供人读取，也可供后续计划和 CI 通过 foundation 专用退出码消费；不得作为最终 cutover 门禁。
- PostgreSQL 测试证明统计复用现有 repository 语义且整个过程无写入。
- 未引入数据库迁移、API、自动修复、旧合同兼容或未来迁移执行抽象。

## 后续计划入口（不在本计划实施）

清单基础通过验收后，再分别编写：

1. 粗分拣 capability/contract 业务规格与验收矩阵。
2. runtime session/intent 的 capability version pin 与审计证据计划。
3. 基于本报告的迁移执行、切流、回滚和生产门禁计划。

## GSTACK REVIEW REPORT

### Completion Summary

- Step 0: Scope Challenge — 保留 9 个文件；分层与测试边界成立，范围按推荐接受。
- Architecture Review: 6 issues found，全部折叠进事务、环境、catalog、repository 装配和文档流程合同。
- Code Quality Review: 6 issues found，全部折叠进严格模型、Enum、typed sample、digest、退出码和原子输出。
- Test Review: 已生成 31/31 分组路径覆盖图，5 gaps identified 并全部补齐。
- Performance Review: 2 issues found，落实 100 条硬上限与 PostgreSQL/客户端两层超时。
- NOT in scope: 已写入 7 项显式延后边界。
- What already exists: 已写入 7 项复用能力。
- TODOS.md updates: 提议并接受 1 项 P3 “WorkLine inventory 批量运行引用摘要”。
- Failure modes: 11 类路径均有测试/处理/可见性结论，0 critical gaps。
- Outside voice: skipped；Claude CLI 存在但未认证，用户选择继续收尾。
- Parallelization: 1 lane，0 parallel / 5 sequential。
- Lake Score: 21/21 已完成决策选择了推荐的完整方案；外部视角不计入覆盖评分。

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | 最近 7 天无有效记录；本计划是已锁定平台设计的 foundation 实施切片 |
| Codex Review | `/codex review` | Independent 2nd opinion | 1 | ISSUES FOUND (informational) | 2026-07-15 的早期 plan review 记录；当前评审的 19 项工程发现已全部折叠 |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 7 | CLEAR (PLAN) | 19 issues、0 unresolved、0 critical gaps；FULL_REVIEW |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | 后端只读 CLI，无 UI 范围 |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | 运维命令参数、退出码与失败可见性已在 Eng Review 锁定 |

- **VERDICT:** ENG CLEARED — 该 foundation 计划可进入实施；不代表最终 cutover readiness。

NO UNRESOLVED DECISIONS
