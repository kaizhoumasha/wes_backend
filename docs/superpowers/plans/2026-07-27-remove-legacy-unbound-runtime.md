# Legacy Unbound Runtime 消除实施计划

> **执行方式：** 使用 `subagent-driven-development` 在单一隔离 worktree 中串行实施。每个工作包由新 implementer 执行并接受独立任务审查，不并行派发实现者。

**目标：** 删除未绑定 Session 的 legacy 执行路径，使所有可执行 WorkLine Session 只能通过 immutable Plugin Binding、generated Plugin dispatcher 和 System Capability 推进。

**最终结果：** 粗分机和 SMT 入库分拣共享同一套 generated-only 平台合同；运行态记录必须固定完整 binding；设备回调只信任数据库中的 `DeviceCommand`；旧 processor、旧 orchestrator delegate、旧 compatibility 和旧写回类型全部删除。

**技术栈：** Python 3.13、FastAPI、SQLModel/SQLAlchemy、PostgreSQL、Pydantic v2、Celery、Pytest、Alembic、GitNexus、Ruff。

## 全局约束

- 所有沟通、文档、代码注释和 Commit Comment 使用中文。
- 严格遵守 `API → Service → Repository → Database`，API 不直接访问 Repository 或 Database。
- 系统尚未发布，不保留 backward compatibility、route alias、legacy fallback、旧 Session backfill 或旧数据迁移逻辑。
- 本机 Docker 是开发调试环境；schema 或 generated digest 改变后允许删除本机数据库 volume 并从零初始化。
- 严格遵循 DRY、KISS、SOLID、YAGNI；不新增缓存、批量框架、新 worker 或为未来插件预留抽象。
- 所有项目命令使用 `uv run ...`，Shell 命令经 `rtk` 执行。
- 修改函数、类或方法前逐符号运行 GitNexus upstream impact analysis；HIGH/CRITICAL 风险必须先向用户汇报并确认。
- Commit 前运行 GitNexus detect changes；只 stage 明确文件路径，禁止 `git add -A` 和目录级宽泛 stage。
- 当前工作区已有用户修改 `AGENTS.md`、`CLAUDE.md`、`.serena/project.yml`；不得覆盖或提交这些文件。
- 新测试遵守 `tests/README.md`；integration/load 等重测试显式运行。
- 新 Alembic revision 必须通过生成器创建，不手写 revision ID。
- 规划只描述接口、状态、边界、验收和验证命令，不内嵌完整实现。

## What already exists

| 现有能力 | 复用方式 |
| --- | --- |
| `WorklinePluginDefinition`、generated index、handler registry、dispatcher | 保留生成式注册与 dispatch 主干，只把 route facts builder 加入 `HandlerRegistration` 和 digest |
| `PluginDispatchRequest`、`PinnedPluginSnapshot`、`AttemptWriteSet` | 继续作为 Stage 1/2/3 immutable 边界，不创建第二套 request/write-set |
| `WorklinePluginBindingService` | 收紧为必填 binding 合同，不新增 parallel binding service |
| `RuntimeInboxProcessorBridge` 和 claim/commit 流程 | 保留 inbox 入口和事务边界，删除 unbound/legacy 分支 |
| `RuntimeIntentEffectApplier`、`DeviceCommand`、`SystemOutbox` | 继续负责 effect 持久化，不增加平台级 command-created hook |
| `SmtInboundHandoffService` 的 claim/recovery/record 方法 | 在现有恢复扫描中补齐 command correlation，而不是新建恢复服务 |
| `record_source_pick_command_correlation()`、`record_source_pick_success()` | 直接复用既有状态推进与幂等校验 |
| diagnostics codes/registry/builder/query mapping | 将 `PLUGIN_BINDING_REQUIRED` 纳入单一真源，不散落字符串 |
| PostgreSQL 临时库、runtime extension performance budget、测试 topology guardrail | 扩展现有测试基础，不创建第二套 harness |

## NOT in scope

- 旧版本、旧 Session、旧 binding 或旧业务数据迁移、回填与兼容读取。
- `PICK_AND_PUT_RESULT`、旧 SMT plugin identity 或 legacy diagnostic 字符串的 alias。
- 已执行的历史 Alembic revision 和归档文档改写。
- 粗分机格位预约、补架、`PUT_TO_BIN` 等未进入当前执行切片的业务扩展。
- SMT 目标机械臂、扫码平台、目标投格等未来能力。
- 新 API、前端页面、外部协议、部署制品或发布流水线。
- 新缓存层、批量预取框架、专用恢复 worker 或可配置 plugin lifecycle 平台。
- repository-wide “migration/legacy” 命名清理。

## 已锁定架构决策

1. 实施收敛为 4 个内聚工作包，保留完整目标行为。
2. `WorklineSession`、`ExecutionSession`、`ExecutionWorkItem` 的 plugin identity 和 binding pins 在 ORM 与 PostgreSQL 中均为必填。
3. schema/digest 变化后删除本机 Docker PostgreSQL volume，从最新 migration 和 seed 重新初始化；不扩展 `reset_runtime_data.py`。
4. facts builder 归属 route-level `HandlerRegistration`，不进入 `WorklinePluginDefinition`。
5. facts builder 的稳定 import identity 进入 generated index digest；lambda、局部函数和不稳定 callable 拒绝注册。
6. 共享输入模型放入现有 `workline_plugins/contracts.py`；不新增 `facts.py`、`common_inputs.py`。
7. SMT 插件固定为 `__init__.py`、`contracts.py`、`handlers.py`、`definition.py` 四个文件。
8. `EffectApplyState` 是 `write_back_service.py` 内最小 typed dataclass；删除 `OrchestratorResult`、`SimpleNamespace` 和 `OrchestratorWriteBackService`。
9. `COMMAND_RESULT` 必须带有效 `command_id`；动作类型只来自 `DeviceCommand.task_type`。
10. SMT handoff recovery 从 bound Session/correlation 和 demand/item/attempt/inbox evidence 唯一解析 source-pick command，再调用现有 correlation 记录方法。
11. command recovery 每个 item 最多一次基于索引的 `limit(2)` 候选查询；不做批量预取。
12. `PLUGIN_BINDING_REQUIRED` 进入 diagnostics 单一真源。

## 最终架构

```text
SMT handoff claim
        │
        ▼
active immutable Plugin Binding
        │ pin
        ├──────────────┬───────────────────┐
        ▼              ▼                   ▼
WorklineSession   ExecutionSession   ExecutionWorkItem
  NOT NULL pins     NOT NULL pins      NOT NULL pins
        │
        ▼
RuntimeInbox claim
        │
        ▼
HandlerRegistration
  ├─ handler
  ├─ facts_model
  └─ facts_builder ── stable identity ──► generated index digest
        │
        ▼
PluginDispatchRequest
        │
        ▼
generated dispatcher
        │
        ▼
PluginDecision → AttemptWriteSet
        │
        ▼
RuntimeIntentEffectApplier
        ├─ DeviceCommand
        └─ SystemOutbox
        │
        ▼
COMMAND_RESULT(command_id required)
        │
        ▼
authoritative DeviceCommand validation
        │
        ▼
SMT handoff recovery correlation → PICKED
```

任何 binding 缺失、digest 不一致、facts builder 不稳定、command 不权威或 correlation 不唯一的情况都必须 fail closed，且不得产生 DeviceCommand、Outbox、timeline 或状态推进副作用。

## 状态与权威数据约定

### Plugin identity

- `plugin_key`: `smt_sorting_inbound`
- `contract_version`: `smt_sorting_inbound.v1`
- 不保留旧 identity `SMT_SORTING_INBOUND@2026-06-21.p1`。

### Logical routes

- 粗分机和 SMT 的设备终态统一使用 `COMMAND_RESULT`。
- 删除 `PICK_AND_PUT_RESULT`，不保留 alias。
- SMT 当前只声明：
  - `SORTING_SOURCE_PICK_REQUESTED`
  - `COMMAND_RESULT`
  - `CAPABILITY_EFFECT_RESULT`
- TIMER 继续由平台 reconciliation owner 处理。

### Command authority

```text
RuntimeInbox.command_id
        │ required
        ▼
DeviceCommand
  ├─ command_code
  ├─ task_type
  ├─ correlation_id
  └─ params evidence
        │
        ▼
与 Session wait + handoff demand/item/attempt/inbox 逐项匹配
```

- callback payload 不拥有 `command_type`。
- command 缺失、类型不支持或 evidence 不一致时返回稳定诊断，事务中零 effect。
- correlation 恢复仅接受唯一候选；0 条和 2 条及以上都进入受控失败，不猜测。

## Task 1：建立 route-level facts 合同并新增 SMT generated Plugin

**目标：** 消除 runtime bridge 对 `RoughSorterFacts` 的硬编码，让粗分机与 SMT 使用同一个 generated dispatcher 合同。

**核心文件：**

- 修改 `src/app/runtime/workline_plugins/dispatcher.py`
- 修改 `src/app/runtime/workline_plugins/handler_registry.py`
- 修改 `src/app/runtime/workline_plugins/index_builder.py`
- 修改 `src/app/runtime/workline_plugins/contracts.py`
- 修改 `src/app/runtime/workline_plugins/rough_sorter/definition.py`
- 修改 `src/app/runtime/workline_plugins/rough_sorter/handlers.py`
- 修改 `src/app/runtime/workline_plugins/rough_sorter/inputs.py`
- 修改 `src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_orchestrator_bridge.py`
- 新增 `src/app/runtime/workline_plugins/smt_sorting_inbound/__init__.py`
- 新增 `src/app/runtime/workline_plugins/smt_sorting_inbound/contracts.py`
- 新增 `src/app/runtime/workline_plugins/smt_sorting_inbound/handlers.py`
- 新增 `src/app/runtime/workline_plugins/smt_sorting_inbound/definition.py`
- 生成更新 `src/app/runtime/workline_plugins/generated_index.py`

**合同：**

- `PluginAttemptFactSource` 放在 `dispatcher.py`，只携带 immutable、ORM-free 的通用原料。
- `HandlerRegistration` 固定包含 `handler`、`facts_model`、`facts_builder`。
- dispatcher 先调用 builder，再用对应 `facts_model` 校验输出。
- `CommandResultInput`、`CapabilityEffectResultInput` 放入现有 `contracts.py`。
- SMT `contracts.py` 只包含 config/state/source-pick input/facts。
- SMT `handlers.py` 只包含 pure facts builder 和 pure decisions。
- SMT `definition.py` 只包含 schema、routes 和 registrations。

**TDD 步骤：**

- [ ] 对 `HandlerRegistration`、`workline_plugin_handler_identities`、`_build_plugin_dispatch_request`、rough sorter handler/definition 执行 GitNexus upstream impact。
- [ ] 先写 generated index 合同失败测试：builder identity 改变必须改变 digest；lambda/局部函数拒绝；缺失/重复 registration fail closed；builder 输出必须匹配 `facts_model`。
- [ ] 先写 route 合同失败测试：`COMMAND_RESULT` 缺 `command_id`、command 不存在、task type 不支持、command code/wait 不匹配时返回稳定诊断且零 effect。
- [ ] 把 bridge 中 rough sorter facts 构建迁入 rough sorter registration；bridge 不再 import `RoughSorterFacts` 或 `RoughSorterBindingSnapshot`。
- [ ] 新增 SMT 四文件插件，覆盖 source-pick request、success、failure、correlation mismatch、capability reject。
- [ ] 运行生成器并验证 generated 文件无手工编辑。

**验收：**

```text
runtime_inbox_orchestrator_bridge.py 不包含 RoughSorterFacts/RoughSorterBindingSnapshot
WorklinePluginDefinition 不包含 facts_resolver
所有 route registration 都有稳定 facts_builder
generated index 同时包含 rough_sorter 与 smt_sorting_inbound
PICK_AND_PUT_RESULT 与旧 SMT identity 在运行时代码中为 0 命中
```

**聚焦验证：**

```bash
rtk uv run pytest tests/workline_plugins/rough_sorter -q
rtk uv run pytest tests/workline_plugins/smt_sorting_inbound -q
rtk uv run pytest tests/workline_runtime/extensions/test_plugin_runtime_inbox_routing.py -q
rtk uv run pytest tests/workline_runtime/extensions/test_runtime_extension_index_generation.py -q
```

## Task 2：把 Plugin Binding 变成不可绕过的数据库不变量

**目标：** Session 与 execution 记录从创建时起必须携带完整 binding pins，不允许 Optional 或 conditional coherence。

**核心文件：**

- 修改 `src/app/runtime/orchestration/models/session.py`
- 修改 `src/app/runtime/orchestration/execution_session.py`
- 修改 `src/app/runtime/orchestration/execution_work_item.py`
- 修改 `src/app/workline/services/plugin_binding_service.py`
- 修改 `src/app/runtime/orchestration/services/session/session_resolver.py`
- 修改 `src/app/runtime/orchestration/services/intent/smt_inbound_handoff_service.py`
- 修改相关 repository projection、factory、fixture 和 seed
- 新增 Alembic 生成 revision：`migrations/versions/<generated>_enforce_runtime_plugin_binding.py`

**必填字段：**

```text
plugin_key
contract/manifest_version
plugin_binding_id
plugin_binding_version
plugin_config_hash
plugin_index_digest
```

- `WorklineSession`、`ExecutionSession`、`ExecutionWorkItem` 的上述运行态字段为非 Optional。
- PostgreSQL 列为 `NOT NULL`，binding ID 保持 FK，版本/摘要保持现有校验。
- `resolve_new_session_binding()` 和 `pin_new_runtime_session()` 返回必填 binding snapshot，不返回 `None`。
- SMT handoff claim 必须先解析 active binding，再在同一事务中创建并固定 Session/Execution/WorkItem。
- 失败时统一使用 `PLUGIN_BINDING_REQUIRED`，不使用散落字符串。
- migration 不包含旧数据 backfill；开发数据通过 volume 重建处理。

**TDD 步骤：**

- [ ] 对三个 model、binding service、session resolver、handoff claim 创建路径执行 GitNexus upstream impact。
- [ ] 先写 model/service 失败测试：任何缺 pin 创建都失败；完整且一致的 pin 成功；创建中途失败必须 rollback，不残留半套 Session/Execution/WorkItem。
- [ ] 用 Alembic 生成 revision，再编辑为直接 `NOT NULL`/FK 目标 schema，不编写数据转换。
- [ ] 更新所有 production factory、fixture、seed 和测试构造器，不保留默认 `None`。
- [ ] 在本机开发 Docker 中执行一次明确的数据清理和从零初始化：

```bash
rtk docker compose down -v
rtk docker compose up -d
rtk ./scripts/init-env.sh dev
rtk ./scripts/migrate.sh upgrade
```

执行前再次确认 compose project 和 volume 仅属于当前本机开发环境。

**PostgreSQL 验收：**

- `information_schema.columns` 证明三张表的 binding pins 均 `NOT NULL`。
- 每张表逐项漏传 pin 都触发数据库拒绝。
- 错误 binding FK 被数据库拒绝。
- 完整一致记录可写入。
- fresh database 激活新 generated digest 后可成功执行一次 dispatch。
- 不运行旧数据升级、回填或兼容读取测试。

**聚焦验证：**

```bash
rtk uv run pytest tests/workline_runtime/extensions/test_plugin_binding_service.py -q
rtk uv run pytest tests/workline_runtime/extensions/test_plugin_binding_runtime_wiring.py -q
rtk uv run pytest tests/integration/test_workline_migration_inventory_postgresql.py -q -o addopts=''
rtk uv run pytest tests/integration/workline_capabilities/test_smt_sorting_inbound_plugin_attempt_postgresql.py -q -o addopts=''
```

## Task 3：删除双轨并闭合 SMT command correlation

**目标：** 只保留 generated attempt 主线，删除 legacy/unbound 执行者，并使 SMT handoff 从 command 创建到 `PICKED` 可恢复、可证明、可幂等。

**核心文件：**

- 修改 `src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_context_loader.py`
- 修改 `src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_orchestrator_bridge.py`
- 修改 `src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_writeback_service.py`
- 修改 `src/app/runtime/orchestration/services/runtime_inbox/__init__.py`
- 修改 `src/app/runtime/orchestration/services/intent/smt_inbound_handoff_service.py`
- 修改 `src/app/runtime/orchestration/repositories/smt_inbound_handoff_repository.py`
- 修改 `src/app/device/repositories/command_repository.py`
- 修改 `src/app/workline/services/write_back_service.py`
- 修改 `src/app/runtime/orchestration/runtime_intent_effects.py`
- 修改 `src/app/runtime/orchestration/diagnostics/codes.py`
- 修改 `src/app/runtime/orchestration/diagnostics/registry.py`
- 修改 `src/app/runtime/orchestration/diagnostics/builder.py`
- 修改 diagnostics domain/query mappings
- 删除 `src/app/runtime/workline_plugins/legacy_compatibility.py`
- 删除 `src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_processor_service.py`
- 删除 `src/app/runtime/orchestration/orchestrator_bridge.py`
- 删除 `tests/runtime/orchestration/test_legacy_unbound_session_processor.py`

**generated-only 处理链：**

```text
claim inbox
  → load mandatory binding context
  → build route facts
  → generated dispatch
  → prepare AttemptWriteSet
  → lock/revalidate binding
  → apply effects with EffectApplyState
  → commit once
```

**EffectApplyState：**

- 定义在 `src/app/workline/services/write_back_service.py`。
- 只保留当前 effect applier 实际读写的 typed 字段。
- context key 从 `orch_result` 改为 `effect_state`。
- 不复用 `effect_state_contract.py` 的 ledger reducer state。
- 删除旧 write-back callback、`OrchestratorWriteBackService`、`OrchestratorResult` 和 `SimpleNamespace`。

**SMT correlation recovery：**

- Inbox 已 `PROCESSED` 且 source item 尚无 command correlation 时，从绑定 Session/execution correlation 查询 `SORTING_SOURCE_PICK` 候选。
- Repository 查询必须使用已有索引入口并 `limit(2)`。
- 候选必须同时匹配 sorting session、awaiting command code、task type、handoff demand ID、source item ID、claim attempt number、source inbox evidence。
- 恰好一条时调用现有 `record_source_pick_command_correlation()`。
- 同一轮扫描可继续检查终态并调用现有 `record_source_pick_success()`。
- 0 条、多条、不匹配、失败终态都进入稳定诊断/人工处置，不猜测关联。
- 重复 callback、重复 scan 和事务重试保持幂等。

**TDD 步骤：**

- [ ] 对 bridge/context loader/writeback、effect applier、handoff recovery、command repository 和 diagnostics 符号执行 GitNexus upstream impact。
- [ ] 先写 `EffectApplyState` 回归测试：no-op/command、block/wait、complete/continue、effect exception rollback。
- [ ] 先写 handoff recovery 矩阵：唯一、0 条、多条、payload mismatch、成功、失败、重复 scan。
- [ ] 增加 `PLUGIN_BINDING_REQUIRED` 到 codes、domain mapping、registry、builder defaults、query mapping 和测试；所有 service 只引用 enum。
- [ ] 删除 legacy 文件和 imports；删除 legacy-only tests，不把断言迁成兼容测试。
- [ ] 新增静态 guardrail，禁止被删模块、`legacy_compatibility`、`OrchestratorResult`、`orch_result` 和 `SimpleNamespace` 回流。

**聚焦验证：**

```bash
rtk uv run pytest tests/workline_runtime -q
rtk uv run pytest tests/workline_plugins -q
rtk uv run pytest tests/architecture/test_no_legacy_unbound_runtime.py -q
```

## Task 4：完成数据库闭环、性能门禁、文档和最终审计

**目标：** 用真实 PostgreSQL 证明完整链路、幂等性和查询/延迟预算，并更新当前文档。

**测试文件：**

- 新增 `tests/workline_plugins/smt_sorting_inbound/` 下 config/state、handler、conformance 测试。
- 新增 `tests/workline_runtime/extensions/test_runtime_plugin_binding_required.py`。
- 新增 `tests/integration/workline_capabilities/test_smt_sorting_inbound_plugin_attempt_postgresql.py`。
- 新增或扩展 `tests/integration/test_workline_migration_inventory_postgresql.py` 的真实 DDL 合同。
- 新增 `tests/architecture/test_no_legacy_unbound_runtime.py`。
- 扩展 `tests/workline_runtime/extensions/test_runtime_extension_performance_budget.py`。
- 扩展 `tests/integration/workline_capabilities/test_runtime_extension_performance_budget_postgresql.py`。
- 更新受影响的 rough sorter、binding、diagnostics、handoff 和 conformance 测试。

**PostgreSQL 关键闭环：**

```text
SMT handoff request
  → bound Session/Execution/WorkItem
  → RuntimeInbox
  → generated SMT decision
  → DeviceCommand + SystemOutbox
  → callback RuntimeInbox(command_id)
  → authoritative command validation
  → recovery correlation
  → source item PICKED
```

- 闭环测试必须覆盖成功、设备失败、重复 callback、重复 recovery scan 和事务重试。
- 每次失败断言数据库无多余 command/outbox/timeline/state advance。
- 测试只连接当前 worktree 的本机 Docker PostgreSQL，不回退到共享、远程或生产数据库。

**性能门禁：**

- recovery batch 100 项，每项新增 command recovery 查询不超过 1 次，候选不超过 2 条。
- 为 SMT source-pick generated attempt 增加 warmup + 5 次样本的 PostgreSQL median budget。
- 保留 generated index cold import budget。
- 预算沿用现有测试 harness；不为测试创建生产缓存。

**文档：**

- 更新 `docs/architecture/file_index.md` 和当前 runtime/plugin 架构文档。
- 删除“双轨、迁移期、未绑定 fallback、旧 orchestrator”描述。
- 保留 archive 原文。
- 在 handoff recovery service 的多阶段状态流附近维护简短 ASCII 注释；模型字段本身保持字段级说明，不复制整张架构图。

**最终验证：**

```bash
rtk uv run pytest tests/architecture/test_test_suite_topology_guardrail.py -q
rtk uv run pytest tests/workline_plugins tests/workline_runtime tests/database -q
rtk uv run pytest tests/integration/test_workline_migration_inventory_postgresql.py -q -o addopts=''
rtk uv run pytest tests/integration/workline_capabilities/test_smt_sorting_inbound_plugin_attempt_postgresql.py -q -o addopts=''
rtk uv run pytest tests/integration/workline_capabilities/test_runtime_extension_performance_budget_postgresql.py -q -o addopts=''
rtk uv run pytest --collect-only -q -o addopts='' | tail -5
rtk uv run pytest tests/ -q
rtk uv run ruff format --check .
rtk uv run ruff check .
rtk uv run bandit -r src/
rtk ./scripts/git-quality-gate.sh --profile quality
```

## Test Coverage Diagram

```text
CURRENTLY COVERED                                           NEW REQUIRED COVERAGE
[23 paths]
├─ generated index / dispatcher
│  ├─ stable handler + facts model identity                 [GAP-01] facts builder identity changes digest
│  ├─ duplicate handler rejection                          [GAP-02] lambda/local builder rejected
│  └─ rough sorter config/input/facts validation            [GAP-03] builder output model validation
├─ binding service
│  ├─ activation, pin and digest admission                  [GAP-04] creation rollback leaves no partial records
│  └─ mismatch/revocation fail closed                       [GAP-05] real PostgreSQL NOT NULL/FK rejection
├─ RuntimeInbox / rough sorter
│  ├─ scan, command, callback, replay and write-set          [GAP-06] COMMAND_RESULT missing/invalid command_id
│  └─ command/outbox idempotency                             [GAP-07] fresh DB new digest activation + dispatch
└─ SMT handoff existing recovery
   ├─ RECEIVED/PROCESSING/FAILED/DEAD_LETTER                [GAP-08] unique command candidate
   └─ known command success/failure                         [GAP-09] zero candidates
                                                            [GAP-10] multiple candidates
                                                            [GAP-11] evidence mismatch
                                                            [GAP-12] success/failure full closure
                                                            [GAP-13] repeated callback/scan idempotency
                                                            [GAP-14] four EffectApplyState parity regressions
                                                            [GAP-15] query and SMT latency budgets

TOTAL: 23 existing + 15 required = 38/38 planned
QUALITY TARGET: every required path asserts behavior + edge case + failure side effects
[→INTEGRATION]: DDL, generated dispatch, command/outbox/callback/recovery/PICKED
```

## 失败模式

| 失败场景 | 处理方式 | 测试 | 可见结果 |
| --- | --- | --- | --- |
| Session 缺任一 binding pin | ORM 与 PostgreSQL 双重拒绝 | unit + PostgreSQL | 稳定 `PLUGIN_BINDING_REQUIRED` |
| binding digest 与 generated index 不一致 | dispatch 前 fail closed | unit + fresh DB smoke | diagnostic/hold，无 effect |
| facts builder 身份不稳定 | index generation 拒绝 | unit | 构建失败，不生成 index |
| facts builder 返回错误模型 | dispatch 前校验失败 | unit | contract diagnostic，无 effect |
| `COMMAND_RESULT` 缺/错 `command_id` | 不读取 payload fallback | unit + integration | stable diagnostic，无 effect |
| source-pick command 候选为 0 | 受控 manual hold | unit | 明确原因，不猜测 |
| source-pick command 候选超过 1 | 受控 manual hold | unit | ambiguity diagnostic |
| command evidence 与 item/attempt/inbox 不符 | 拒绝 correlation | unit | mismatch diagnostic |
| callback 或 recovery scan 重复 | 幂等复用既有 evidence | integration | 不重复 command/outbox/推进 |
| effect 应用中抛异常 | 整个 attempt rollback | regression | 无半写入 |
| 本机仍使用旧 DB volume | fresh smoke 的 digest/DDL 断言失败 | PostgreSQL | 阻止错误环境继续开发 |
| recovery 查询退化 | query budget 失败 | PostgreSQL performance | CI/本地 heavy gate 明确失败 |

所有新路径都至少有测试和显式错误处理；审查后无“无测试 + 无处理 + 静默失败”的 critical gap。

## 顺序与依赖

| 工作包 | 模块 | 依赖 |
| --- | --- | --- |
| 1. route facts + SMT plugin | `runtime/workline_plugins/`、RuntimeInbox bridge | — |
| 2. mandatory binding | orchestration models/session、workline binding、migration | 工作包 1 的新 plugin identity/digest |
| 3. generated-only cutover + recovery | RuntimeInbox、effects、handoff、device repository、diagnostics | 工作包 1、2 |
| 4. PostgreSQL/performance/docs/audit | tests、migrations verification、docs | 工作包 1、2、3 |

Sequential implementation, no parallelization opportunity。四个工作包共享 generated runtime、binding 和 handoff 边界，拆到多个 worktree 会增加生成文件、migration head 和测试 fixture 冲突。

## 分支、提交与 staging 纪律

- 从 `develop` 创建隔离 worktree 分支：`refactor/remove-legacy-unbound-runtime`。
- 每个工作包完成并通过聚焦测试后形成一个逻辑 commit；不提交未通过测试的中间状态。
- 新增/修改/删除文件均按精确路径 stage。
- 每次 commit 前：

```text
git diff --cached --name-only
gitnexus_detect_changes(scope="staged")
```

- stage 列表中出现 `AGENTS.md`、`CLAUDE.md` 或 `.serena/project.yml` 时立即停止并移除。
- generated index 必须由生成器产出，migration revision ID 必须由 Alembic 生成。

## Implementation Tasks

Synthesized from this review's findings. Each task derives from a specific finding above.

- [ ] **T1 (P1, human: ~2d / Codex: ~3h)** — Plugin runtime — 建立 route-level facts builder 合同并新增四文件 SMT generated Plugin
  - Surfaced by: Architecture/Code Quality — bridge 硬编码 rough sorter facts，原计划把 resolver 放错到 Definition 并拆出过多小文件
  - Files: `dispatcher.py`、`handler_registry.py`、`index_builder.py`、`contracts.py`、rough sorter、SMT plugin、RuntimeInbox bridge、generated index
  - Verify: rough sorter、SMT plugin、routing、index generation 聚焦测试

- [ ] **T2 (P1, human: ~2d / Codex: ~3h)** — Binding/Data model — 把三类运行态记录的 plugin binding pins 改为 ORM 与 PostgreSQL 必填
  - Surfaced by: Architecture/Tests — Optional pins 允许 unbound runtime 继续存在，原 conditional constraint 不能形成硬不变量
  - Files: session/execution models、binding service、session resolver、handoff claim、Alembic revision、fixtures
  - Verify: unit binding tests、真实 PostgreSQL `NOT NULL`/FK rejection、fresh activation/dispatch

- [ ] **T3 (P1, human: ~3d / Codex: ~4h)** — Runtime cutover — 删除 legacy/unbound 执行者，使用 typed effect state 并闭合 SMT command correlation
  - Surfaced by: Architecture/Code Quality/Tests — 双轨、`OrchestratorResult` 耦合、SMT command correlation 无 production caller、非权威 callback fallback
  - Files: RuntimeInbox services、write-back/effects、handoff service/repository、device command repository、diagnostics、legacy deletions
  - Verify: effect parity、recovery matrix、zero-effect failure、architecture guardrail

- [ ] **T4 (P2, human: ~2d / Codex: ~3h)** — Verification/Docs — 完成 PostgreSQL 端到端闭环、性能预算、文档和全量质量门禁
  - Surfaced by: Tests/Performance — 缺少 request→command/outbox→callback→recovery→PICKED 闭环及 SMT 独立预算
  - Files: integration/performance/architecture tests、当前 architecture docs
  - Verify: heavy PostgreSQL suites、38/38 coverage、full pytest、Ruff、Bandit、quality gate、GitNexus detect

## 验收标准

- 运行时代码不存在 `legacy_compatibility`、unbound processor、备用 orchestrator delegate 或旧 write-back callback。
- 三类运行态模型和 PostgreSQL schema 均不允许缺失 plugin identity/binding pins。
- bridge 不再认识具体插件 facts 类型。
- rough sorter 与 SMT 都只通过 generated dispatcher 执行。
- `COMMAND_RESULT` 只信任 `RuntimeInbox.command_id → DeviceCommand`。
- SMT source-pick 从 request 到 `PICKED` 的 PostgreSQL 闭环成功，失败和重复路径无副作用。
- `PLUGIN_BINDING_REQUIRED` 只有一个诊断定义来源。
- 本机 Docker 数据库从空 volume 初始化后 migration、activation、dispatch 全部通过。
- 38/38 计划路径有行为、边界或失败测试。
- 性能预算、完整测试、lint、安全扫描、quality gate 和 GitNexus detect 全部通过。
- `AGENTS.md`、`CLAUDE.md`、`.serena/project.yml` 未被本计划提交。

## 回退原则

本计划不提供运行时 feature flag、legacy fallback、旧 route alias、旧数据回填或双轨回退。

若实施失败：

1. 停止合并未完成分支。
2. 修复当前 generated-only 目标实现，不恢复被删除的 legacy 路径。
3. 本机开发数据库可再次删除 volume 并从零初始化。
4. 若已产生逻辑 commit，只 revert 当前功能分支 commit；不得在代码中加入永久兼容分支。

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 1 | CLEAR | 6 proposals, 6 accepted, 0 deferred |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | 本次 Claude Outside Voice 已启动但超时，无结果被采纳 |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 3 | CLEAR | 25 issues, 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | 后端运行时重构，无 UI 范围 |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | 未运行 |

**VERDICT:** CEO + ENG CLEARED — ready to implement

NO UNRESOLVED DECISIONS
