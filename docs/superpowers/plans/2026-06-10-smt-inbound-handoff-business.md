# SMT 入库 Handoff 业务 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> 对齐 SPEC：`docs/superpowers/specs/2026-06-10-smt-inbound-handoff-business-spec.md`
>
> 状态：已按 SPEC D2-D11 工程评审决策同步实施合同；已通过实施前工程复审，可进入代码实施。

**Goal:** 打通 SMT 粗分机单层货架释放到满箱交换、分拣 source item claim、插件首盘 `SORTING_SOURCE_PICK` 的后端业务闭环。

**Architecture:** 在 WorkLine domain 内新增 SMT inbound handoff 聚合账本，API 只调用 Service，Service 通过 Repository 持久化 demand/source item，并复用 handling、WorkLine Inbox、`SMT_SORTING_INBOUND` 插件和现有资源/Station 能力。满箱交换仍走 handling 的 `SINGLE_LAYER_FULL_BOX_EXCHANGE` + `CALLBACK_PLUS_RECONCILIATION`；分拣首盘只通过一等内部 Inbox kind/helper 创建 `SORTING_SOURCE_PICK_REQUESTED` 进入插件，插件只返回 command intent，由 runtime effect 创建 command/outbox 并写回 source item correlation，不允许 service 直接创建设备 command。

**Tech Stack:** Python 3.13, FastAPI, SQLModel/SQLAlchemy async, Alembic, Celery, pytest/pytest-asyncio, Docker/PostgreSQL integration tests, Ruff, GitNexus, uv。

---

## 计划约束

- 本计划是执行合同，不是代码实现；遵守 `AGENTS.md` 的规划文档可读性要求，不粘贴完整类、完整函数或大段测试代码。
- 基础分支：`develop`；推荐实现分支：`feature/smt-inbound-handoff-business`。
- 所有项目命令使用 `uv run ...`。
- 修改任何函数、类、方法前运行 GitNexus impact analysis；HIGH/CRITICAL 风险先向用户汇报。
- 新 Alembic migration 使用 `uv run alembic revision -m "add smt inbound handoff"` 生成 revision id 后再编辑。
- 后端分层必须保持 API -> Service -> Repository -> Database；API 层禁止直接 `select()` 或 `db.execute()`。
- 当前系统未发布，按破坏性优化执行；不保留旧 `smt_full_box_exchange` 插件、旧 candidate scan task 或兼容 alias。
- 不进入前端运营页面实现；本计划只落后端结构化查询、处置 API 合同和低基数指标边界。
- 本计划已吸收 SPEC D2-D11 合同；若后续复审发现新漂移，先修订文档再进入代码实施。

## 已确认实现决策

- 聚合归属：SMT inbound handoff 作为 WorkLine domain 内的业务聚合落地，不新建通用 workflow engine。
- 账本分层：`SmtInboundHandoffDemand` 记录一次 rack release；`SmtInboundHandoffSourceItem` 记录逐个可 claim source item；source item 不存为 JSON 热路径。
- Release fact：唯一正常 producer 接在 `MOVE_OUT_ACTIVE_RACK` 成功后的现有 `ROUGH_SORTER_RELEASE_FACT` / resource fact 应用链；Beat/projection replay 只能重放同一 fact。
- 目标 WorkLine：claim 阶段通过显式 route service 选择，采用“配置候选 + 运行态准入”两阶段；不从粗分机 release fact 推断，不在 handoff service 内硬编码。
- usage 口径：统一为 `0..1`；handoff 与现有 SMT rack/bin 调度共用同一 usage policy/helper。
- 满箱交换：复用 `HandlingOperationService.request_bin_operation(...)`，operation type 为 `SINGLE_LAYER_FULL_BOX_EXCHANGE`，completion policy 为 `CALLBACK_PLUS_RECONCILIATION`。
- 首盘分拣：通过 `WorklineInbox` 一等内部事件 kind/helper 创建 `SORTING_SOURCE_PICK_REQUESTED`；`SMT_SORTING_INBOUND` 插件 handler 只返回 `SORTING_SOURCE_PICK` command intent。
- Command correlation：runtime effect 创建 `DeviceCommand` 和 Outbox 后必须立即写回 `source_pick_command_id`、`source_pick_command_code`、`source_pick_dispatch_key` 或等价 evidence。
- Demand 聚合：所有 item 推进、callback、recovery、人工动作和重试后统一调用 `recalculate_demand_status(...)` 或等价单一入口。
- claim 后恢复：handoff item 必须记录 `source_pick_inbox_id`、claim attempt 和 command evidence，并能从 Inbox `FAILED`、`DEAD_LETTER`、stale `PROCESSING`、事件成功但未产生命令或未写回 command correlation 中恢复或转人工。
- 原因码：`SmtInboundHandoffReasonCode` 或等价 catalog 是 `failure_code`、`available_actions`、API filter 和测试断言的唯一来源。
- ECS 准入：设备命令下发前以 ECS realtime `IDLE` probe 为事实源；WES 本地状态只作为筛选和诊断 evidence。

## 文件结构

| 文件 | 职责 |
| --- | --- |
| `src/app/workline/models/smt_inbound_handoff.py` | 新增 demand/source item 表模型、状态枚举、原因码枚举、API 请求/响应 schema。 |
| `src/app/workline/repositories/smt_inbound_handoff_repository.py` | demand/source item 幂等查询、row-lock claim、scan selection、post-claim recovery selection。 |
| `src/app/workline/domain/services/smt_usage_policy.py` | 抽出现有 SMT usage 解析与阈值口径，供 handoff 和 rack/bin scheduling 共用。 |
| `src/app/workline/domain/services/smt_inbound_handoff_reason.py` | 单一原因码 catalog，定义原因、可恢复性、默认 message、`available_actions`。 |
| `src/app/workline/domain/services/smt_inbound_handoff_route_service.py` | 目标 `SMT_SORTING_INBOUND` WorkLine 候选过滤、稳定排序、冲突处理和 route 失败原因。 |
| `src/app/workline/services/smt_inbound_handoff_service.py` | release 幂等创建、满箱交换决策、callback 推进、source item claim、claim 后恢复、人工处置。 |
| `src/app/workline/v1/inbound_handoff.py` | handoff list/detail/action API；只调用 service，不直接访问数据库。 |
| `src/app/workline/models/inbox.py`、`src/app/workline/services/inbox_service.py` | 新增内部事件 Inbox kind/helper、source system、payload envelope 和 claim bucket 合同。 |
| `src/workline_runtime/session_resolver.py`、`src/app/workline/services/inbox_batch_processor.py` | 支持内部事件按既有 session/workline 归属解析与批处理，不落 `serial:unknown`。 |
| `src/workline_runtime/orchestrator.py`、`src/workline_runtime/plugin_sdk/normalizers/input_normalizer.py` | 将 `INTERNAL_EVENT` 映射为插件可路由事件，并保证 normalizer 输出 `SORTING_SOURCE_PICK_REQUESTED` canonical event。 |
| `src/app/workline/services/single_layer_rack_orchestration_service.py` | 在现有 `ROUGH_SORTER_RELEASE_FACT` / resource fact 应用链触发 handoff release producer。 |
| `src/workline_runtime/runtime_intent_effects.py` | `SORTING_SOURCE_PICK` command/outbox 创建后写回 handoff source item correlation evidence。 |
| `src/workline_plugins/smt_sorting_inbound/constants.py` | 新增 `EVENT_SOURCE_PICK_REQUESTED = "SORTING_SOURCE_PICK_REQUESTED"`。 |
| `src/workline_plugins/smt_sorting_inbound/plugin.py` | manifest 支持新事件；新增 `@on_event(EVENT_SOURCE_PICK_REQUESTED)` handler。 |
| `src/workline_plugins/smt_sorting_inbound/flow_service.py` | 从 source pick requested payload 构造首盘 `SORTING_SOURCE_PICK` command，并保证无 command 时返回可诊断异常。 |
| `src/celery_app/tasks/workline.py`、`src/celery_app/config.py` | 新增 `scan_smt_inbound_handoff_demands_batch` 兜底任务和 Beat 配置；不恢复旧 task。 |
| `migrations/versions/` | 新增 handoff demand/source item 表、唯一约束、部分索引和必要字段。 |
| `tests/workline_runtime/`、`tests/api/`、`tests/integration/workline_runtime/` | 覆盖 service、route、plugin、Beat、API、PostgreSQL 并发和 EXPLAIN 门禁。 |

导出要求：

- `src/app/workline/models/__init__.py` 导出所有 handoff model/schema/enum。
- `src/app/workline/repositories/__init__.py` 导出 repository 与实例。
- `src/app/workline/services/__init__.py` 导出 `SmtInboundHandoffService` 与 `smt_inbound_handoff_service`。
- `src/app/workline/domain/services/__init__.py` 导出 usage policy、reason catalog 和 route service。
- `src/app/workline/v1/__init__.py` include `inbound_handoff_router`，建议 prefix 为 `/inbound-handoff`。

## 数据模型合同

### `SmtInboundHandoffDemand`

必需字段：

- 幂等与追踪：`demand_key`、`rack_release_id`、`trace_id`。
- 来源：`source_workline_id`、`source_workline_code`、`single_layer_rack_code`、`release_reason_code`、`bin_snapshots_json`。
- 目标：`target_workline_id`、`target_workline_code`；只由 route service 写入。
- 业务决策：`decision_status`、`handling_operation_key`、`sorting_source_demand_key`。
- 状态：`status`、`failure_code`、`failure_message`、`next_attempt_at`。

索引/约束：

- `demand_key` unique。
- `rack_release_id` unique。
- hot scan partial index 覆盖 `status IN ('CREATED','EVALUATING','FULL_BOX_EXCHANGED','READY_FOR_SORTING')`、`next_attempt_at`、`updated_at`、`id`。
- route/filter 查询按 `status`、`target_workline_id`、`updated_at` 支持列表页摘要。

### `SmtInboundHandoffSourceItem`

必需字段：

- 归属与幂等：`handoff_demand_id`、`item_key`。
- source evidence：`bin_code`、`bin_cell_index`、`bin_cell_code`、`material_identity_key`、`pkg_code`、`reel_thickness_mm`。
- claim：`status`、`target_workline_id`、`target_workline_code`、`sorting_session_id`、`source_pick_inbox_id`、`claim_attempt_no`。
- command evidence：`source_pick_command_id`、`source_pick_command_code`、`source_pick_dispatch_key`。
- 失败与时间：`failure_code`、`failure_message`、`claimed_at`、`completed_at`、`next_attempt_at`。

索引/约束：

- unique `handoff_demand_id + item_key`。
- READY claim partial index 覆盖 `status='READY'`、`next_attempt_at`、`handoff_demand_id`、`id`。
- post-claim recovery partial index 覆盖 `status IN ('PICK_REQUESTED','CLAIMED_BY_SORTING')`、`source_pick_inbox_id`、`updated_at`、`id`。
- demand detail 使用 `handoff_demand_id`、`status`、`id` 的稳定排序。

### 内部 Inbox 存储合同

- `SORTING_SOURCE_PICK_REQUESTED` 使用 `INTERNAL_EVENT` 或等价一等 Inbox kind，不复用 `DEVICE_EVENT`、`EXTERNAL_HTTP` 或 `TIMER_TIMEOUT`。
- Alembic migration 必须同步 `InboxKind` SQL enum/check constraint，使 PostgreSQL 可写入新 kind。
- Inbox helper 必须写入系统内部 `source_system`、可路由事件名、`event_id`、`causation_id`、`trace_id`、`session_id`、`workline_id` 和 handoff source item correlation。
- `claim_bucket_key` 必须来自 `session:{id}` 或 `workline:{id}` 等明确归属；测试必须证明内部事件不会进入 `serial:unknown` 热队列。

## 状态与恢复合同

### Demand 主状态流

```text
CREATED
  -> EVALUATING
  -> WAITING_FULL_BOX_EXCHANGE
       -> RECONCILING
       -> FULL_BOX_EXCHANGED
  -> READY_FOR_SORTING
  -> CLAIMED_BY_SORTING
  -> SORTING_IN_PROGRESS
  -> COMPLETED

不可自动恢复事实缺口
  -> MANUAL_HOLD
```

### Demand 聚合状态不变量

- `SmtInboundHandoffSourceItem` 是逐项进度真源，`SmtInboundHandoffDemand.status` 只是聚合摘要。
- `SmtInboundHandoffService.recalculate_demand_status(...)` 或等价单一入口负责按 item 计数、满箱交换状态、人工 hold 和对账状态重算 demand。
- API、Beat、人工处置和 `available_actions` 不允许各自解析 raw JSON 推断 demand 状态。
- 任何推进 source item、处理 handling callback、claim 后恢复、人工释放 hold、重试 release 或 command result 的路径，完成后都必须调用聚合入口。

### Claim 后恢复矩阵

| 观察事实 | Item 状态 | Demand 状态 | 处理 |
| --- | --- | --- | --- |
| Inbox `PROCESSED` 且已生成并写回 `SORTING_SOURCE_PICK` command evidence | `PICK_REQUESTED` | `CLAIMED_BY_SORTING` | 保持等待 command result；记录 command/outbox evidence。 |
| Inbox `PROCESSED` 但未生成 command 或未写回 command correlation | `MANUAL_HOLD` | `MANUAL_HOLD` | 原因码 `SOURCE_PICK_COMMAND_NOT_CREATED`；允许人工释放后递增 claim attempt 重试。 |
| Inbox `FAILED` 且 attempt 未耗尽 | `READY` 或 `PICK_REQUESTED` with retry | `READY_FOR_SORTING` | 写 `next_attempt_at`，重新进入可重试 claim；保留 `source_pick_inbox_id` evidence。 |
| Inbox `DEAD_LETTER` | `MANUAL_HOLD` | `MANUAL_HOLD` | 原因码 `SOURCE_PICK_INBOX_DEAD_LETTER`；API detail 返回 dead-letter evidence。 |
| Inbox stale `PROCESSING` | `PICK_REQUESTED` | `CLAIMED_BY_SORTING` | 由 WorkLine Inbox reclaim/dead-letter 机制处理；handoff scan 同步 evidence，不直接绕过 token fencing。 |
| route/payload/plugin contract error | `MANUAL_HOLD` | `MANUAL_HOLD` | 不自动重试；由 reason catalog 暴露可处置动作。 |

## 目标 WorkLine 路由合同

配置候选来源：

- `WorkLine.plugin_key == SMT_SORTING_INBOUND_PLUGIN_KEY`。
- manifest 支持 `SORTING_SOURCE_PICK_REQUESTED` event 和 `SORTING_SOURCE_PICK` command。
- manifest 声明 required device roles、single-layer boundary 和 station lease 能力。
- route config 或 runtime_config 明确允许承接当前 source rack/bin/cell 边界。

运行态准入：

- 配置候选存在后，再检查 `WorkLine.runtime_status == READY`。
- station lease 可用。
- 当前 session 没有未关闭的 `current_material`。
- 设备命令下发前 ECS realtime `IDLE` probe 通过。

稳定排序：

1. route priority。
2. `workline_code` ascending。
3. `id` ascending。

冲突处理：

- 没有候选：demand 进入 `MANUAL_HOLD`，原因码 `ROUTE_NOT_FOUND`。
- 候选存在但未 READY：保持可重试，原因码 `TARGET_WORKLINE_NOT_READY`，写 `next_attempt_at`。
- station lease busy：保持可重试，原因码 `SOURCE_STATION_BUSY`，写 `next_attempt_at`。
- 当前 session `current_material` 未关闭：保持可重试，原因码 `TARGET_SESSION_BUSY`，写 `next_attempt_at`。
- ECS realtime 非 `IDLE`：保持可重试，原因码 `ECS_DEVICE_NOT_IDLE`，写 `next_attempt_at`。
- 多候选可用：按稳定排序选第一条，并在 demand/source item 写入 route evidence。

## API 合同

Base prefix：`/workline/inbound-handoff`。

权限：

- list：`biz:workline:list`。
- detail：`biz:workline:detail`。
- actions：`biz:workline:update`；非生产 mock/scan 还必须限制 `settings.APP_ENV in NON_PROD_ENVS`。

Endpoints：

- `POST /demands/query`：列表摘要，支持 `status`、`failure_code`、`target_workline_code`、时间范围和分页。
- `GET /demands/{id}`：详情，包含 source item 明细、release snapshot、handling trace、claim recovery evidence。
- `POST /demands/{id}/reevaluate`：重新评估可自动推进 demand。
- `POST /demands/{id}/retry-exchange`：重试满箱交换 handling operation。
- `POST /demands/{id}/convert-to-sorting`：将 WMS/RCS 拒绝或失败的 demand 转分拣。
- `POST /demands/{id}/release-hold`：释放 `MANUAL_HOLD`。
- `POST /demands/{id}/reconcile`：补充 `RECONCILING` 人工对账结果。
- `POST /debug/mock-callback`、`POST /debug/scan`：仅非生产环境。

禁止 API：

- 不提供直接创建 `SORTING_SOURCE_PICK` 的接口。
- 不提供直接编辑 resource projection 或 outbox payload 的接口。
- 不让前端通过 raw JSON 推断 `available_actions`。

## Implementation Tasks

### Task 0: 实施前安全检查

**Files:**
- Inspect: `git status --short`
- Inspect: GitNexus impact analysis
- Inspect: current tests around WorkLine/handling/resource

- [ ] **Step 1: 确认工作区**
  - Run: `git status --short`
  - Expected: 明确记录已有用户变更；不得回滚 `AGENTS.md`、`CLAUDE.md` 或 SPEC 变更。
- [ ] **Step 2: 检查 GitNexus**
  - Run: `npx gitnexus status`
  - Expected: repo `wes_backend` 已索引；若 stale，先 `npx gitnexus analyze`。
- [ ] **Step 3: 对首批会修改符号运行 impact analysis**
  - Minimum targets: `InboxKind`、`WorklineInboxRepository`、`WorklineInboxService`、`InboxBatchProcessor`、session resolver、`OrchestratorService._resolve_inbox_type`、runtime input normalizer、`RuntimeIntentEffectApplier._apply_command`、`SingleLayerRackOrchestrationService.plan_single_layer_rack_dispatch`、`SmtSortingInboundPlugin`、`SmtSortingInboundFlowService`、`HandlingOperationService`、`RuntimeQueryService`、`WorkLineService`、`SmtRackBinSchedulingService`。
  - Expected: HIGH/CRITICAL 风险先汇报；记录 blast radius 到交付说明。

### Task 1: Handoff 模型、Inbox 迁移与原因码 catalog

**Files:**
- Create: `src/app/workline/models/smt_inbound_handoff.py`
- Create: `src/app/workline/domain/services/smt_inbound_handoff_reason.py`
- Modify: `src/app/workline/models/inbox.py`
- Modify: `src/app/workline/services/inbox_service.py`
- Modify: `src/workline_runtime/session_resolver.py`
- Modify: `src/app/workline/services/inbox_batch_processor.py`
- Modify: `src/workline_runtime/orchestrator.py`
- Modify: `src/workline_runtime/plugin_sdk/normalizers/input_normalizer.py`
- Modify: `src/app/workline/models/__init__.py`
- Migration: generated Alembic revision under `migrations/versions/`
- Test: `tests/workline_runtime/test_smt_inbound_handoff_models.py`
- Test: `tests/workline_runtime/test_smt_inbound_handoff_reason_catalog.py`
- Test: `tests/workline_runtime/test_workline_inbox_internal_event.py`
- Test: `tests/workline_runtime/test_runtime_config_and_normalization.py`
- Test: `tests/workline_runtime/test_workline_orchestrator_internal_event.py`

- [ ] **Step 1: 写失败测试**
  - Assert demand/source item enum values match SPEC.
  - Assert source item metadata contains `claim_attempt_no` and command evidence fields.
  - Assert `SmtInboundHandoffReasonCode` catalog returns stable `failure_code` and `available_actions`.
  - Assert generated metadata contains unique constraints and partial indexes named for idempotency, READY claim, demand scan, post-claim recovery.
  - Assert `INTERNAL_EVENT` or equivalent kind/helper creates a routable Inbox envelope with session/workline binding and non-`serial:unknown` claim bucket.
  - Assert runtime orchestrator maps `INTERNAL_EVENT` to plugin event routing instead of defaulting to `DEVICE_EVENT`.
  - Assert input normalizer preserves `SORTING_SOURCE_PICK_REQUESTED` as canonical event type and rejects malformed internal payloads with diagnostic errors.
- [ ] **Step 2: 运行 RED**
  - Run: `uv run pytest tests/workline_runtime/test_smt_inbound_handoff_models.py tests/workline_runtime/test_smt_inbound_handoff_reason_catalog.py tests/workline_runtime/test_workline_inbox_internal_event.py tests/workline_runtime/test_runtime_config_and_normalization.py tests/workline_runtime/test_workline_orchestrator_internal_event.py -q`
  - Expected: FAIL because model/catalog/internal Inbox helper/orchestrator routing do not exist.
- [ ] **Step 3: 实现模型与 catalog**
  - Model 使用 `BaseMixin + DataTableMixin`，不要重复继承 `EnterpriseMixin` 与 `AuditMixin`。
  - `bin_snapshots_json` 使用 JSON column，只作 release evidence，不作 claim 热路径。
  - Source item 包含 `claim_attempt_no`、`source_pick_command_id`、`source_pick_command_code`、`source_pick_dispatch_key`。
  - Catalog 至少覆盖 SPEC 中的 release fact、usage、WMS/RCS、reconcile、route、target busy、ECS、claim/plugin 分类。
- [ ] **Step 4: 生成并编辑 migration**
  - Run: `uv run alembic revision -m "add smt inbound handoff"`
  - Migration 必须包含 handoff 表、唯一约束、partial indexes、source item command evidence 字段，以及 Inbox kind enum/check constraint 更新；revision id 由 Alembic 生成。
- [ ] **Step 5: 实现内部 Inbox helper**
  - 新 helper 使用系统内部 source system、标准 envelope、session/workline binding、稳定 `event_id` / idempotency key 和非 `serial:unknown` claim bucket。
  - Session resolver 与 batch processor 识别内部事件，并按既有 session/workline 归属处理。
  - Orchestrator 的 Inbox kind/type 解析必须把 `INTERNAL_EVENT` 交给插件事件 handler；normalizer 必须输出可被 manifest `supported_events` 匹配的 canonical event。
- [ ] **Step 6: 运行 GREEN**
  - Run: `uv run pytest tests/workline_runtime/test_smt_inbound_handoff_models.py tests/workline_runtime/test_smt_inbound_handoff_reason_catalog.py tests/workline_runtime/test_workline_inbox_internal_event.py tests/workline_runtime/test_runtime_config_and_normalization.py tests/workline_runtime/test_workline_orchestrator_internal_event.py -q`
  - Expected: PASS。

### Task 2: Usage policy 与 release fact 幂等入口

**Files:**
- Create: `src/app/workline/domain/services/smt_usage_policy.py`
- Modify: `src/app/resource/services/smt_rack_bin_scheduling_service.py`
- Modify: `src/app/workline/services/single_layer_rack_orchestration_service.py`
- Create: `src/app/workline/services/smt_inbound_handoff_service.py`
- Create: `src/app/workline/repositories/smt_inbound_handoff_repository.py`
- Modify: `src/app/workline/domain/services/__init__.py`
- Modify: `src/app/workline/repositories/__init__.py`
- Modify: `src/app/workline/services/__init__.py`
- Test: `tests/workline_runtime/test_smt_usage_policy.py`
- Test: `tests/workline_runtime/test_smt_inbound_handoff_service_release.py`
- Test: `tests/workline_runtime/test_smt_inbound_handoff_release_producer.py`

- [ ] **Step 1: 写失败测试**
  - usage policy 覆盖 `0`、`0.5`、`0.8`、`1`、缺失、非法值和旧字段兼容。
  - `create_or_get_from_release(...)` 同一 `rack_release_id` 幂等返回同一 demand。
  - 缺 `rack_release_id`、`single_layer_rack_code` 或有效快照时进入 `MANUAL_HOLD`，原因来自 catalog。
  - `ROUGH_SORTER_RELEASE_FACT` / resource fact 应用链触发 handoff release producer；普通 API、Beat、projection 查询不会成为第二 producer。
- [ ] **Step 2: 运行 RED**
  - Run: `uv run pytest tests/workline_runtime/test_smt_usage_policy.py tests/workline_runtime/test_smt_inbound_handoff_service_release.py tests/workline_runtime/test_smt_inbound_handoff_release_producer.py -q`
  - Expected: FAIL because usage helper/service/repository/producer wiring do not exist.
- [ ] **Step 3: 实现 usage policy**
  - 从 `SmtRackBinSchedulingService` 抽出共享 helper，保持现有调度测试通过。
  - usage 输出为 `0..1` float；非法值返回明确 invalid result，不静默归零。
- [ ] **Step 4: 实现 release fact 入口**
  - Service 创建 demand 和 source items；Repository 负责唯一约束下的 get/create。
  - 现有 `SingleLayerRackOrchestrationService` 的 `ROUGH_SORTER_RELEASE_FACT` / resource fact 应用链调用 `create_or_get_from_release(...)`。
  - Beat 和 projection replay 只能重放同一 release fact 并返回同一 demand；不新增前端按钮、列表页操作或资源投影查询作为 release 事实源。
- [ ] **Step 5: 运行 GREEN**
  - Run: `uv run pytest tests/workline_runtime/test_smt_usage_policy.py tests/workline_runtime/test_smt_inbound_handoff_service_release.py tests/workline_runtime/test_smt_inbound_handoff_release_producer.py tests/workline_runtime/test_smt_rack_bin_scheduling_service.py tests/workline_runtime/test_single_layer_rack_orchestration_service.py -q`
  - Expected: PASS。

### Task 3: 满箱交换决策与 callback 推进

**Files:**
- Modify: `src/app/workline/services/smt_inbound_handoff_service.py`
- Reuse: `src/app/handling/services/operation_service.py`
- Reuse: `src/app/handling/services/lifecycle_service.py`
- Test: `tests/workline_runtime/test_smt_inbound_handoff_exchange.py`
- Test: `tests/handling/test_handling_operation_core.py`
- Test: `tests/handling/test_handling_operation_lifecycle.py`

- [ ] **Step 1: 写失败测试**
  - `usage < 0.5` skips exchange and creates READY source items.
  - `usage >= 0.8` creates `SINGLE_LAYER_FULL_BOX_EXCHANGE` handling operation with idempotent key.
  - `0.5 <= usage < 0.8` supports preferred exchange and fallback to sorting after reject/failure.
  - `BUSINESS_COMPLETED` maps exchanged/remaining items; `PHYSICAL_COMPLETED` without `post_exchange_relations` stays `RECONCILING`; `rack_release_id` mismatch enters `MANUAL_HOLD`.
  - Callback、manual reconcile、retry exchange 完成后都调用 demand 聚合入口。
- [ ] **Step 2: 运行 RED**
  - Run: `uv run pytest tests/workline_runtime/test_smt_inbound_handoff_exchange.py -q`
  - Expected: FAIL because exchange branch is not implemented.
- [ ] **Step 3: 实现 exchange decision**
  - Handoff service only decides and calls handling service; it must not construct WMS/RCS payload directly.
  - Store `handling_operation_key` and handling trace evidence on demand.
- [ ] **Step 4: 实现 callback/reconcile 推进**
  - Callback result updates demand/source item through service method.
  - `RECONCILING` only moves forward after new reconciliation evidence or manual action.
  - 所有推进路径最后经由 `recalculate_demand_status(...)` 或等价单一入口重算 demand 摘要。
- [ ] **Step 5: 运行 GREEN**
  - Run: `uv run pytest tests/workline_runtime/test_smt_inbound_handoff_exchange.py tests/handling/test_handling_operation_core.py tests/handling/test_handling_operation_lifecycle.py -q`
  - Expected: PASS。

### Task 4: 目标 WorkLine 路由与 source item claim

**Files:**
- Create: `src/app/workline/domain/services/smt_inbound_handoff_route_service.py`
- Modify: `src/app/workline/services/smt_inbound_handoff_service.py`
- Modify: `src/app/workline/repositories/smt_inbound_handoff_repository.py`
- Reuse: `src/app/workline/services/station_lease_service.py`
- Reuse: `src/app/workline/services/start_admission_service.py`
- Test: `tests/workline_runtime/test_smt_inbound_handoff_route_service.py`
- Test: `tests/workline_runtime/test_smt_inbound_handoff_claim.py`
- Integration: `tests/integration/workline_runtime/test_smt_inbound_handoff_claim_postgres.py`

- [ ] **Step 1: 写失败测试**
  - route missing -> `MANUAL_HOLD` with `ROUTE_NOT_FOUND`。
  - multi-candidate route uses priority, `workline_code`, `id` stable order。
  - Config candidate exists but WorkLine not READY, station lease busy, session `current_material` open, ECS non-IDLE -> retry with `next_attempt_at`。
  - Internal Inbox created with first-class kind/helper, stable idempotency key, session/workline binding and non-`serial:unknown` claim bucket.
  - concurrent claim under PostgreSQL does not duplicate source item.
- [ ] **Step 2: 运行 RED**
  - Run: `uv run pytest tests/workline_runtime/test_smt_inbound_handoff_route_service.py tests/workline_runtime/test_smt_inbound_handoff_claim.py -q`
  - Expected: FAIL because route/claim are missing.
- [ ] **Step 3: 实现 route service**
  - 配置候选只使用 WorkLine `plugin_key`、manifest `supported_events`/`supported_commands`、required roles、single-layer boundary 和 route config/runtime_config。
  - 运行态准入再检查 `runtime_status`、station lease、session `current_material` 和 ECS realtime `IDLE`。
  - No hardcoded target line in handoff service.
- [ ] **Step 4: 实现 row-lock claim**
  - Repository claim 使用 DB transaction、row-level lock、stable ordering。
  - Service 同一事务内更新 demand、item、session，并通过内部 Inbox helper 创建或复用 `SORTING_SOURCE_PICK_REQUESTED`。
  - `event_id` / idempotency key 使用 `handoff_source_item_id + claim_attempt_no` 或等价稳定代次；dead-letter 人工释放后递增 attempt。
- [ ] **Step 5: 运行 GREEN**
  - Run: `uv run pytest tests/workline_runtime/test_smt_inbound_handoff_route_service.py tests/workline_runtime/test_smt_inbound_handoff_claim.py -q`
  - Expected: PASS。
- [ ] **Step 6: PostgreSQL 并发门禁**
  - Run: `RUN_WORKLINE_INTEGRATION=1 INTEGRATION_DATABASE_URL=<postgres-url> uv run pytest tests/integration/workline_runtime/test_smt_inbound_handoff_claim_postgres.py -q`
  - Expected: PASS；若本机无 PostgreSQL，交付说明必须标明未运行原因，不能视为最终通过。

### Task 5: 插件事件与 command 生成

**Files:**
- Modify: `src/workline_plugins/smt_sorting_inbound/constants.py`
- Modify: `src/workline_plugins/smt_sorting_inbound/plugin.py`
- Modify: `src/workline_plugins/smt_sorting_inbound/flow_service.py`
- Modify: `src/workline_runtime/runtime_intent_effects.py`
- Modify: `src/app/workline/services/smt_inbound_handoff_service.py`
- Test: `tests/workline_runtime/test_smt_sorting_inbound_plugin.py`
- Test: `tests/workline_runtime/test_smt_inbound_handoff_command_correlation.py`
- Integration: `tests/integration/workline_runtime/test_smt_sorting_inbound_p0_integration.py`

- [ ] **Step 1: 写失败测试**
  - manifest declares `SORTING_SOURCE_PICK_REQUESTED` in supported events.
  - handler returns one `SORTING_SOURCE_PICK` command intent with source rack/bin/cell payload and handoff source item correlation.
  - Runtime effect creates `DeviceCommand`/Outbox then writes `source_pick_command_id`、`source_pick_command_code`、`source_pick_dispatch_key` back to source item.
  - invalid payload returns plugin contract failure, not silent no-op.
  - existing `SORTING_SOURCE_PICK` success callback still handles `MATERIAL_UNMOUNTED` and `current_material`。
- [ ] **Step 2: 运行 RED**
  - Run: `uv run pytest tests/workline_runtime/test_smt_sorting_inbound_plugin.py tests/workline_runtime/test_smt_inbound_handoff_command_correlation.py -q`
  - Expected: FAIL on missing event support/handler and runtime effect command correlation writeback.
- [ ] **Step 3: 实现 plugin event**
  - Add event constant and `@on_event(EVENT_SOURCE_PICK_REQUESTED)` handler.
  - Handler delegates command construction to flow service.
  - Handler 不直接写 handoff 表，只返回 command intent 和 correlation payload。
- [ ] **Step 4: 实现 runtime effect correlation 写回**
  - `_apply_command` 创建 command/outbox 后，识别 handoff source item correlation 并写回 command evidence。
  - 未写回视为 `SOURCE_PICK_COMMAND_NOT_CREATED`，由 recovery 兜底转人工或重试。
- [ ] **Step 5: 运行 GREEN**
  - Run: `uv run pytest tests/workline_runtime/test_smt_sorting_inbound_plugin.py tests/workline_runtime/test_smt_inbound_handoff_command_correlation.py tests/integration/workline_runtime/test_smt_sorting_inbound_p0_integration.py -q`
  - Expected: PASS。

### Task 6: Claim 后恢复与 Beat 兜底

**Files:**
- Modify: `src/app/workline/services/smt_inbound_handoff_service.py`
- Modify: `src/app/workline/repositories/smt_inbound_handoff_repository.py`
- Modify: `src/celery_app/tasks/workline.py`
- Modify: `src/celery_app/config.py`
- Test: `tests/workline_runtime/test_smt_inbound_handoff_recovery.py`
- Test: `tests/workline_runtime/test_smt_inbound_handoff_celery.py`
- Test: `tests/workline_runtime/test_celery_task_entrypoints.py`
- Integration: `tests/integration/workline_runtime/test_smt_inbound_handoff_recovery_postgres.py`

- [ ] **Step 1: 写失败测试**
  - Inbox `FAILED`/retryable does not leave item forever in `PICK_REQUESTED`。
  - Inbox `DEAD_LETTER` moves item/demand to `MANUAL_HOLD` with evidence.
  - stale `PROCESSING` is observed without bypassing WorkLine Inbox token fencing.
  - `PROCESSED` without created command or without command correlation writeback becomes `SOURCE_PICK_COMMAND_NOT_CREATED`。
  - Command result exists but source item state did not advance is detected and repaired or moved to `MANUAL_HOLD` with evidence.
  - New Beat task scans due demand and stuck source item with batch limit and stable ordering.
  - Old `scan_smt_full_box_exchange_candidates_batch` remains unavailable.
- [ ] **Step 2: 运行 RED**
  - Run: `uv run pytest tests/workline_runtime/test_smt_inbound_handoff_recovery.py tests/workline_runtime/test_smt_inbound_handoff_celery.py tests/workline_runtime/test_celery_task_entrypoints.py -q`
  - Expected: FAIL because recovery scanner/task are missing.
- [ ] **Step 3: 实现 recovery service**
  - Scan demand statuses from SPEC with `next_attempt_at` due.
  - Scan stuck source items by `status`、`source_pick_inbox_id`、`updated_at` using repository hot path.
  - Map each recovery outcome through reason catalog.
  - Recovery outcome must call demand aggregation entry instead of mutating demand status ad hoc.
- [ ] **Step 4: 实现 Celery task**
  - Task name: `src.celery_app.tasks.workline.scan_smt_inbound_handoff_demands_batch`。
  - Return summary includes scanned, advanced, retry_scheduled, manual_hold, recovery_errors.
- [ ] **Step 5: 运行 GREEN**
  - Run: `uv run pytest tests/workline_runtime/test_smt_inbound_handoff_recovery.py tests/workline_runtime/test_smt_inbound_handoff_celery.py tests/workline_runtime/test_celery_task_entrypoints.py -q`
  - Expected: PASS。
- [ ] **Step 6: PostgreSQL recovery/EXPLAIN 门禁**
  - Run: `RUN_WORKLINE_INTEGRATION=1 INTEGRATION_DATABASE_URL=<postgres-url> uv run pytest tests/integration/workline_runtime/test_smt_inbound_handoff_recovery_postgres.py -q`
  - Expected: PASS and EXPLAIN proves demand scan/source claim/post-claim recovery use index-friendly access paths.

### Task 7: API、权限与结构化可观测

**Files:**
- Create: `src/app/workline/v1/inbound_handoff.py`
- Modify: `src/app/workline/v1/__init__.py`
- Modify: `src/app/workline/models/smt_inbound_handoff.py`
- Modify: `src/app/workline/services/smt_inbound_handoff_service.py`
- Test: `tests/api/test_smt_inbound_handoff_api.py`
- Test: `tests/test_api_application_routes.py`

- [ ] **Step 1: 写失败测试**
  - list returns summaries: demand status, decision, failure code, item status counts, handling trace summary, claim stuck/dead-letter summary, available actions.
  - detail returns source item rows, `source_pick_inbox_id` recovery evidence and command/outbox correlation evidence.
  - action endpoints enforce permission and state machine.
  - available actions come from reason catalog and demand aggregation state, not raw JSON inference.
  - nonprod debug endpoints are hidden or rejected in prod env.
  - API route is registered under WorkLine router.
- [ ] **Step 2: 运行 RED**
  - Run: `uv run pytest tests/api/test_smt_inbound_handoff_api.py tests/test_api_application_routes.py -q`
  - Expected: FAIL because route is not registered.
- [ ] **Step 3: 实现 API**
  - API only calls service methods.
  - `available_actions` must come from reason catalog/state machine, not frontend raw JSON inference.
  - 状态摘要来自 `recalculate_demand_status(...)` 或等价聚合结果，不在 API 层重新推断。
  - Use existing `response_builder` and `ResponseSchemaModel` pattern.
- [ ] **Step 4: 运行 GREEN**
  - Run: `uv run pytest tests/api/test_smt_inbound_handoff_api.py tests/test_api_application_routes.py -q`
  - Expected: PASS。

### Task 8: Integration smoke、文档守护与质量门禁

**Files:**
- Create or modify: `tests/integration/workline_runtime/test_smt_inbound_handoff_e2e.py`
- Modify if needed: `tests/docs/test_wes_resource_boundary_docs.py`
- Modify: `docs/superpowers/specs/2026-06-10-smt-inbound-handoff-business-spec.md`
- Modify: this plan file for acceptance audit

- [ ] **Step 1: 写集成 smoke 测试**
  - Mock `ROUGH_SORTER_RELEASE_FACT` -> optional exchange -> source item claim -> internal `SORTING_SOURCE_PICK_REQUESTED` Inbox -> plugin command intent -> runtime effect command/outbox correlation writeback。
  - Repeat release/callback/scan are idempotent.
  - Device busy only waits matching resource, not entire WorkLine.
- [ ] **Step 2: 文档守护**
  - Ensure docs still state WES does not lock five-layer empty boxes, does not bypass WMS/RCS, does not restore old plugin/task.
- [ ] **Step 3: focused verification**
  - Run: `uv run pytest tests/workline_runtime/test_smt_inbound_handoff_models.py tests/workline_runtime/test_smt_inbound_handoff_reason_catalog.py tests/workline_runtime/test_workline_inbox_internal_event.py tests/workline_runtime/test_runtime_config_and_normalization.py tests/workline_runtime/test_workline_orchestrator_internal_event.py tests/workline_runtime/test_smt_usage_policy.py tests/workline_runtime/test_smt_inbound_handoff_service_release.py tests/workline_runtime/test_smt_inbound_handoff_release_producer.py tests/workline_runtime/test_smt_inbound_handoff_exchange.py tests/workline_runtime/test_smt_inbound_handoff_route_service.py tests/workline_runtime/test_smt_inbound_handoff_claim.py tests/workline_runtime/test_smt_inbound_handoff_command_correlation.py tests/workline_runtime/test_smt_inbound_handoff_recovery.py tests/workline_runtime/test_smt_inbound_handoff_celery.py tests/workline_runtime/test_smt_sorting_inbound_plugin.py tests/api/test_smt_inbound_handoff_api.py -q`
  - Expected: PASS。
- [ ] **Step 4: PostgreSQL verification**
  - Run: `RUN_WORKLINE_INTEGRATION=1 INTEGRATION_DATABASE_URL=<postgres-url> uv run pytest tests/integration/workline_runtime/test_smt_inbound_handoff_claim_postgres.py tests/integration/workline_runtime/test_smt_inbound_handoff_recovery_postgres.py tests/integration/workline_runtime/test_smt_inbound_handoff_e2e.py -q`
  - Expected: PASS。
- [ ] **Step 5: quality gate**
  - Run: `uv run ruff format . && uv run ruff check .`
  - Run: `uv run pytest tests/`
  - Run: `./scripts/git-quality-gate.sh --profile quality`
  - Expected: PASS or documented pre-existing failures unrelated to this plan.
- [ ] **Step 6: GitNexus detect changes**
  - Run: `gitnexus_detect_changes()` before commit.
  - Expected: affected symbols and flows match WorkLine handoff, handling reuse, plugin event, API and Celery task only.

## Planned Test Coverage Diagram

```text
Release fact
  ├── missing rack_release_id/snapshot/rack_code       unit: MANUAL_HOLD + reason catalog
  ├── duplicate rack_release_id                        unit+db: same demand, no duplicate exchange
  ├── valid release                                    unit: demand + source items created
  └── producer callsite                                unit: ROUGH_SORTER_RELEASE_FACT chain only

Usage decision
  ├── usage < 0.5                                      unit: direct READY_FOR_SORTING
  ├── 0.5 <= usage < 0.8                               unit: preferred exchange, reject fallback
  ├── usage >= 0.8                                     unit: handling full-box exchange
  └── missing/invalid usage                            unit: MANUAL_HOLD

Full-box exchange callback
  ├── BUSINESS_COMPLETED                               unit: item EXCHANGED or READY
  ├── PHYSICAL_COMPLETED without relations             unit: RECONCILING visible
  ├── FAILED/REJECTED/TIMEOUT                          unit: MANUAL_HOLD + actions
  └── rack_release_id mismatch                         unit: MANUAL_HOLD, no auto recovery

Route + claim
  ├── route missing                                    unit: ROUTE_NOT_FOUND
  ├── multi-candidate                                  unit: stable sort
  ├── two-phase route                                  unit: config candidate vs runtime admission
  ├── target not READY / station/session/ECS busy      unit: retry + next_attempt_at
  ├── concurrent READY claim                           PostgreSQL: no duplicate item
  ├── internal inbox created                           unit+db: kind/envelope/bucket recorded
  └── internal event runtime routing                   unit: orchestrator + normalizer dispatch as plugin event

Plugin source pick
  ├── SORTING_SOURCE_PICK_REQUESTED valid              unit/integration: command intent generated
  ├── runtime effect writeback                         unit: command/outbox evidence on source item
  ├── invalid payload                                  unit: plugin contract error
  └── source pick result                               regression: existing current_material flow

Recovery
  ├── Inbox FAILED                                     unit: retry or MANUAL_HOLD by attempts
  ├── Inbox DEAD_LETTER                                unit: demand/item MANUAL_HOLD
  ├── stale PROCESSING                                 unit/integration: observed via WorkLine Inbox
  ├── PROCESSED but no command/correlation             unit: SOURCE_PICK_COMMAND_NOT_CREATED
  └── Beat fallback                                    unit+integration: stable scan and batch limit

Demand aggregation
  ├── item progress                                    unit: single recalculate entry updates summary
  ├── manual hold/release                              unit: no API-side raw JSON inference
  └── callback/recovery/retry                          unit: all paths call aggregation entry

API
  ├── list                                             api: summaries, filters, permissions
  ├── detail                                           api: source items + recovery evidence
  ├── actions                                          api: state-machine allowed actions
  └── debug                                            api: nonprod only
```

## Failure Modes

| Failure mode | Handling | Required test |
| --- | --- | --- |
| Handoff service hardcodes target WorkLine | Route service owns candidates and stable sort; tests create two candidates | `test_route_uses_stable_sort_without_hardcoded_line` |
| Route service filters READY in config candidate stage | Two-phase route keeps config candidate and returns retry for runtime busy | `test_route_candidate_not_ready_is_retry_not_route_missing` |
| Source item stored only in demand JSON | Source item model has row-level claim path and unique key | model metadata + PostgreSQL claim tests |
| Internal event disguised as `DEVICE_EVENT` / `EXTERNAL_HTTP` | Dedicated internal Inbox kind/helper and migration contract tests | `test_source_pick_request_uses_internal_event_kind` |
| Internal Inbox kind falls through to default runtime dispatch | Orchestrator kind/type mapping and normalizer contract make it a plugin event | `test_internal_event_routes_to_plugin_handler` |
| Release demand created by Beat/projection query | Producer tests assert only `ROUGH_SORTER_RELEASE_FACT` chain creates normal demand | `test_release_producer_is_single_workline_fact_entry` |
| `SORTING_SOURCE_PICK_REQUESTED` Inbox processed but no command evidence exists | Recovery moves item/demand to `MANUAL_HOLD` with evidence | `test_processed_source_pick_inbox_without_command_enters_manual_hold` |
| Runtime effect creates command but does not write source item correlation | Runtime effect unit test asserts command/outbox evidence is persisted | `test_runtime_effect_writes_source_pick_command_correlation` |
| Inbox dead-letter invisible to API | Detail returns Inbox status/error/dead-letter evidence | `test_detail_includes_source_pick_inbox_dead_letter_evidence` |
| Demand status differs between API, Beat and manual actions | Single aggregation entry drives summary status and item counts | `test_recalculate_demand_status_is_single_source_of_truth` |
| Usage helper drifts from existing rack/bin scheduling | Shared usage policy is imported by both paths | `test_handoff_and_rack_bin_scheduling_share_usage_policy` |
| API action strings drift from service reasons | Reason catalog drives `available_actions` and tests | `test_available_actions_are_catalog_driven` |
| SQLite claim test passes but PostgreSQL duplicates | PostgreSQL integration claim uses real row locks | `test_concurrent_claim_does_not_duplicate_source_item` |
| Internal Inbox falls into `serial:unknown` hot queue | Helper binds session/workline bucket and PostgreSQL test checks access path | `test_internal_source_pick_inbox_claim_bucket_is_not_serial_unknown` |
| Beat scans full table | Repository exposes EXPLAIN-friendly selectors and integration checks access path | PostgreSQL EXPLAIN gate |

## NOT in scope

- 不实现完整 SMT Handoff 前端运营页面。
- 不建设完整告警阈值、Runbook 或生产看板；首版只输出低基数指标和结构化 API evidence。
- 不新增通用跨 WorkLine workflow engine。
- 不复活旧 `smt_full_box_exchange` 插件或 `scan_smt_full_box_exchange_candidates_batch`。
- 不让 service 直接创建设备 command，不绕过 `SMT_SORTING_INBOUND` 插件。
- 不让 WES 本地判断 CTU 路径、锁定五层空箱或替代 WMS/RCS 资源权威。

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | 范围与策略 | 0 | 未运行 | 本计划保持 SPEC 已定后端范围，未扩展前端运营面 |
| Codex Review | `/codex review` | 独立二次意见 | 0 | 未运行 | 未运行 |
| Eng Review | `/plan-eng-review` + `$investigate` | 架构与测试，必需 | 2 | CLEAR | 本轮实施前复审补齐 runtime orchestrator / normalizer 的 INTERNAL_EVENT 分发合同；0 个 open issue |
| Design Review | `/plan-design-review` | UI/UX 缺口 | 0 | 未运行 | 前端运营页面不在本轮范围内 |
| DX Review | `/plan-devex-review` | 开发者体验缺口 | 0 | 未运行 | 未运行 |

- **UNRESOLVED:** 0 个交互决策未决；0 个实施门禁项。
- **VERDICT:** ENG CLEARED FOR IMPLEMENTATION；可按本文进入代码实施，但实施阶段必须先跑 GitNexus impact analysis、按 RED/GREEN 顺序推进，并在提交前运行 detect changes。
