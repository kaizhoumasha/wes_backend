# 系统级料箱低级操作域 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立一个系统级 **Bin Operation / Bin Task** 低级操作域，让工作线/插件只调用一个插件门面方法即可发起 CTU 料箱请求，并让 CTU 投料、退箱、满箱交换和分拣机入库都能复用同一套任务、派发、回调、投影和追踪能力。

**Architecture:** 低级能力按系统级建模：料箱任务描述“料箱从来源到目标”的物理执行事实，工作线只作为发起方、关联上下文和等待恢复上下文。插件层唯一入口是 `ctx.next.bin_operation_request(...)`，它只接收内部标准化 move specs；WMS/RCS endpoint、dispatch_key、outbox envelope、厂商 payload 和回调状态映射都由低级域内部封装。第一版落在现有 workline runtime/outbox 基础设施中，新增 `workline_bin_tasks`、`WorklineBinOperationService`、`WorklineBinTaskLifecycleService` 和 `WmsRcsBinOperationGateway`，复用 `EXTERNAL_HTTP` outbox 与 callback 入口；满箱交换、分拣机投料水位、退箱优先级等策略保持在上层工作线协调器或插件中。

**Tech Stack:** Python 3.13, SQLModel, SQLAlchemy/Alembic, FastAPI service layer, Celery outbox dispatcher, pytest, ruff, GitNexus.

---

## Scope Check

本计划只实现系统级低级料箱操作域，不实现满箱交换协调器、不实现 `smt_sorter_inbound` 插件、不实现 CTU 投料水位策略、不实现跨工作线 CTU 调度优化。

本计划产出的能力包括：

- 记录料箱级低级任务：`workline_bin_tasks`。
- 通过 `EXTERNAL_HTTP` outbox 派发 CTU/WMS/RCS 请求。
- 通过 `/api/v1/callback/external` 回调更新料箱任务状态。
- 派生 operation 状态：`PENDING`、`SUCCEEDED`、`FAILED`、`RECONCILING`。
- 将可信回调投影为料箱位置/挂载资源事实。
- 提供插件面向的唯一请求入口：`ctx.next.bin_operation_request(...)`。
- 让后粗分协调、满箱交换、分拣机投料在后续工作线业务层复用该系统级低级能力。

## Boundary Decisions

1. 能力归属是系统级。`Bin Operation / Bin Task` 不属于粗分机、分拣机或某一条工作线；它描述可复用的料箱物理操作。
2. 任务实例携带工作线上下文。`workline_id`、`workline_code`、`material_session_id` 用于追踪发起方、恢复等待 session 和审计，不代表低级能力归属于该工作线。
3. 第一版实现位置复用 workline 基础设施。现有 runtime、outbox、callback 和 trace 已在 workline 模块内闭环，因此表名和 service 暂沿用 `workline_*` 命名；文档和接口语义必须明确它是系统级低级能力。
4. 低级域命名使用 **Bin Operation / Bin Task**，不使用 CTU Operation。CTU 是执行载体，料箱是被操作对象。
5. `workline_bin_tasks.task_type` 第一版只定义 `MOVE_BIN`。投料、退箱、回架、交换由 `operation_type` 和 source/target 字段表达。
6. `bin_code` 允许为空。CTU 投料阶段可能只知道授权集合和 CTU 槽位，真实 `bin_id` 必须等 `TARGET_BIN_SCAN_COMPLETED` 后绑定。
7. `resource` 域只记录资源事实和当前投影。它不判断是否满箱交换、不决定是否进入分拣队列、不决定 CTU 投料数量。
8. 插件层只能调用 `ctx.next.bin_operation_request(...)` 发起 CTU 料箱操作，不允许直接创建 outbox、直接调用 WMS/RCS gateway、直接访问 repository 或拼接厂商 HTTP payload。
9. 插件传入的是内部语义：operation type、operation key、source/target、bin 或 placeholder、carrier 约束、timeout。低级域负责生成 dispatch_key、选择 WMS/RCS target、构造外部 request envelope。
10. 不复活旧 `smt_full_box_exchange` 插件。本计划只给未来协调器提供低级执行能力。

## File Structure

### 系统级低级任务账本（落在 WorkLine 基础设施）

- Create: `src/app/workline/models/bin_task.py`
  - 定义 `WorklineBinTaskType`、`WorklineBinTaskStatus`、`WorklineBinTaskBase`、`WorklineBinTask` 和 Schema。
  - 字段与 `rack_task.py` 对齐，但对象从 rack 换成 bin、source/target 支持 rack、line、buffer、ctu slot。
  - `workline_id` / `workline_code` 是发起和关联上下文，不是领域归属边界。
- Modify: `src/app/workline/models/__init__.py`
  - 导出新模型和枚举。
- Create: `src/app/workline/repositories/bin_task_repository.py`
  - 查询 `dispatch_key`、`operation_key`、活动 bin claim、operation sequence。
- Modify: `src/app/workline/repositories/__init__.py`
  - 导出 repository 实例。
- Create: `src/app/workline/services/bin_task_service.py`
  - 记录请求、解释回调状态、同步等待 session。
- Create: `src/app/workline/services/bin_operation_service.py`
  - 作为低级域内部门面，规范化 task specs、调用 WMS/RCS gateway、创建 outbox、派生 operation 状态。
- Create: `src/app/workline/services/bin_operation_gateway.py`
  - 将内部 `WorklineBinTaskSpec` 转换为 WMS/RCS `EXTERNAL_HTTP` envelope。
  - 将 WMS/RCS 回调 payload 归一化为内部 bin task 状态和资源事实候选。
- Modify: `src/app/workline/services/__init__.py`
  - 导出两个 service、gateway 和 `WorklineBinTaskSpec`。

### Runtime 集成

- Modify: `src/workline_runtime/runtime_intent.py`
  - 增加 `BIN_OPERATION_REQUEST`。
  - 增加 `RuntimeIntent.bin_operation_request(...)`。
- Modify: `src/workline_runtime/plugin_next.py`
  - 在 `PluginNext` 增加 `bin_operation_request(...)` 便捷方法。
  - 插件只调用这个方法发起 CTU 料箱请求，不传 `dispatch_key`、`external_target_code` 或 WMS/RCS 原始 payload。
- Modify: `src/workline_runtime/runtime_intent_effects.py`
  - 增加 `_apply_bin_operation_request`。
  - 将 session 标记为 `WAITING_EXTERNAL`，`current_wait_type="BIN_OPERATION"`。
- Modify: `src/celery_app/tasks/workline.py`
  - 允许 `BIN_OPERATION_REQUEST` 触发 outbox 派发。
  - `wait_type` 处理中接受 `BIN_OPERATION`。
- Modify: `src/workline_runtime/session_resolver.py`
  - `EXTERNAL_HTTP` 回调优先按 `dispatch_key -> workline_bin_tasks -> operation_key` 找回等待 session。

### Callback 和资源投影

- Modify: `src/app/callback/services/callback_orchestration_service.py`
  - 回调写入 inbox 后，同步调用 `workline_bin_task_lifecycle_service.record_callback_from_external_http(...)`。
- Modify: `src/app/resource/models/resource.py`
  - 增加料箱位置投影模型 `BinPlacement` 和状态枚举 `BinPlacementStatus`。
  - 增加资源事实类型：`BIN_ARRIVED`、`BIN_DEPARTED`。
- Modify: `src/app/resource/models/__init__.py`
  - 导出 `BinPlacement`、`BinPlacementStatus`。
- Modify: `src/app/resource/repositories/resource_repository.py`
  - 增加 `BinPlacementRepository`。
- Modify: `src/app/resource/services/projection_service.py`
  - 支持 `BIN_ARRIVED`、`BIN_DEPARTED`、`BIN_MOUNTED`、`BIN_UNMOUNTED` 的统一入口。
  - `BIN_ARRIVED` 写入 `resource_bin_placements` active 投影。
  - `BIN_DEPARTED` 关闭对应 active 投影。
  - `BIN_MOUNTED` 和 `BIN_UNMOUNTED` 仍更新 `resource_rack_bin_mounts`。

### Migrations

- Create: `migrations/versions/<alembic_generated>_add_workline_bin_tasks_and_bin_placements.py`
  - 通过 `uv run alembic revision -m "add workline bin tasks and bin placements"` 生成，不手写 revision ID。
  - 新增 `wes_biz.workline_bin_tasks`。
  - 新增 `wes_biz.resource_bin_placements`。

### Tests

- Create: `tests/workline_runtime/test_workline_bin_operation_service.py`
- Create: `tests/workline_runtime/test_workline_bin_task_service.py`
- Modify: `tests/workline_runtime/test_runtime_intent.py`
- Modify: `tests/workline_runtime/test_runtime_intent_effects.py`
- Modify: `tests/api/test_callback_api.py`
- Modify: `tests/resource/test_resource_projection_service.py`
- Modify: `tests/workline_runtime/test_celery_task_entrypoints.py`

---

## Data Contract

### Plugin-Facing API Contract

插件层唯一入口：

```text
ctx.next.bin_operation_request(
    operation_type,
    operation_key,
    moves,
    carrier_type="CTU",
    carrier_code=None,
    timeout_seconds=None,
)
```

插件必须提供：

- `operation_type`: 上层业务语义，例如 `SORTER_FEED_BINS`、`SORTER_RETURN_BINS`、`FULL_BOX_EXCHANGE_BIN_MOVE`。
- `operation_key`: 上层幂等键。
- `moves`: 一个或多个内部 move specs，只包含 source/target、bin 或 placeholder、carrier 约束和 required 标记。
- `carrier_type`: 第一版固定为 `CTU`。
- `carrier_code`: 可为空，由 WMS/RCS 或调度侧分配时不要求插件传入。
- `timeout_seconds`: 本次外部等待超时。

插件不得提供：

- `dispatch_key`
- `external_target_code`
- `outbox_id`
- WMS/RCS 厂商字段
- HTTP header、URL、鉴权、重试参数

低级域负责把该请求转换为 `WorklineBinOperationRequest`、`WorklineBinTaskSpec`、WMS/RCS gateway envelope 和 `EXTERNAL_HTTP` outbox。

### `workline_bin_tasks`

核心字段：

| 字段 | 说明 |
|------|------|
| `task_key` | 任务幂等键，唯一 |
| `operation_key` | 上层 operation 幂等键 |
| `operation_type` | 上层业务动作，例如 `SORTER_FEED_BINS`、`SORTER_RETURN_BINS`、`FULL_BOX_EXCHANGE_BIN_MOVE` |
| `sequence_no` | 同一 operation 内任务序号 |
| `task_type` | 第一版固定枚举 `MOVE_BIN` |
| `task_status` | `PLANNED`、`REQUESTED`、`IN_PROGRESS`、`SUCCEEDED`、`FAILED`、`TIMEOUT`、`RECONCILING`、`CANCELLED` |
| `workline_id` / `workline_code` | 发起或关联工作线，用于等待恢复、trace 和审计；不是低级能力归属边界 |
| `material_session_id` | 关联 session，可为空；系统级任务可以只通过 operation/dispatch 追踪 |
| `bin_code` | 已知真实料箱编码；未知时为空 |
| `placeholder_key` | 未扫码前的流水线临时占位或 CTU 槽位标识 |
| `source_type` / `source_code` | 来源类型和来源位置 |
| `target_type` / `target_code` | 目标类型和目标位置 |
| `source_rack_code` / `source_rack_slot_code` | 来源为货架槽位时使用 |
| `target_rack_code` / `target_rack_slot_code` | 目标为货架槽位时使用 |
| `carrier_type` / `carrier_code` | 第一版 `carrier_type="CTU"` |
| `dispatch_key` | 外部派发幂等键，唯一 |
| `outbox_id` | 关联 `workline_outbox.id` |
| `external_target_code` | WMS/RCS/CTU HTTP endpoint；由 gateway 解析后写入，插件不得直接传入 |
| `request_json` / `actions_json` | 请求证据和低级动作参数 |
| `callback_json` / `result_json` | 回调证据和归一化结果 |
| `error_code` / `error_message` | 失败证据 |
| `requested_at` / `started_at` / `completed_at` | 生命周期时间 |

### Source/Target Type

第一版支持这些字符串：

- `RACK_SLOT`
- `LINE_INPUT`
- `LINE_SCAN`
- `LINE_WORK`
- `LINE_OUTPUT`
- `CTU_SLOT`
- `BUFFER`
- `UNKNOWN`

`UNKNOWN` 只允许出现在回调证据或异常投影中，不允许业务层主动创建目标为 `UNKNOWN` 的移动任务。

### Resource Facts

| fact_type | 用途 |
|----------|------|
| `BIN_ARRIVED` | 料箱到达流水线、CTU 槽位、缓存位、工作位、退箱位等非 rack 位置 |
| `BIN_DEPARTED` | 料箱离开非 rack 位置 |
| `BIN_MOUNTED` | 料箱挂载到货架槽位 |
| `BIN_UNMOUNTED` | 料箱离开货架槽位 |

---

## Task 1: 建立模型和 Alembic 迁移

**Files:**
- Create: `src/app/workline/models/bin_task.py`
- Modify: `src/app/workline/models/__init__.py`
- Modify: `src/app/resource/models/resource.py`
- Modify: `src/app/resource/models/__init__.py`
- Create: `migrations/versions/<alembic_generated>_add_workline_bin_tasks_and_bin_placements.py`
- Test: `tests/workline_runtime/test_workline_bin_task_model.py`

- [ ] **Step 1: 变更前做影响分析**

Run:

```bash
rtk git status --short
```

Expected: 只确认工作区状态，不回退用户已有改动。

GitNexus impact:

```text
impact target=WorklineRackTask direction=upstream relationTypes=IMPORTS,HAS_PROPERTY,HAS_METHOD,ACCESSES
impact target=ResourceStateEventType direction=upstream relationTypes=IMPORTS,HAS_PROPERTY,ACCESSES
```

Expected: 如果返回 HIGH 或 CRITICAL，先在实现记录中说明直接影响面，再继续。

- [ ] **Step 2: 写失败测试**

Add `tests/workline_runtime/test_workline_bin_task_model.py` covering:

- `WorklineBinTask` declares the low-level MOVE_BIN task contract.
- `WorklineBinTask` exposes task key, dispatch key, and operation sequence uniqueness.
- `BinPlacement` declares the active bin projection table and ARRIVED status.
- `BinPlacement` exposes active uniqueness for known `bin_code`.

Use table metadata inspection for index assertions; keep the test file focused on model contracts.

- [ ] **Step 3: 运行测试确认失败**

Run:

```bash
uv run pytest -q tests/workline_runtime/test_workline_bin_task_model.py
```

Expected: FAIL because `WorklineBinTask` and `BinPlacement` do not exist.

- [ ] **Step 4: 实现模型**

Create `src/app/workline/models/bin_task.py` using these decisions:

- Inherit `WorklineBinTaskBase` from `BaseMixin`.
- Inherit table model from `DataTableMixin` only, not `EnterpriseMixin`。
- `WorklineBinTaskType` contains only `MOVE_BIN`。
- `WorklineBinTaskStatus` mirrors `WorklineRackTaskStatus`。
- Use JSON columns for `request_json`、`actions_json`、`callback_json`、`result_json`。
- Add unique indexes:
  - `ux_workline_bin_tasks_key`
  - `ux_workline_bin_tasks_dispatch_key`
  - `ux_workline_bin_tasks_operation_sequence`
  - `ux_workline_bin_tasks_active_known_bin_claim` where `bin_code IS NOT NULL` and status is active.
- Add query indexes:
  - `ix_workline_bin_tasks_operation_status`
  - `ix_workline_bin_tasks_session_operation`
  - `ix_workline_bin_tasks_placeholder_status`
  - `ix_workline_bin_tasks_target_status`

Modify `src/app/resource/models/resource.py`:

- Add `BIN_ARRIVED` and `BIN_DEPARTED` to `ResourceStateEventType`。
- Add `BinPlacementStatus` enum with `ARRIVED`、`DEPARTED`、`IN_TRANSIT`、`UNKNOWN`。
- Add `BinPlacement` table:
  - `bin_code`
  - `placeholder_key`
  - `position_type`
  - `position_code`
  - `workline_id`
  - `workline_code`
  - `placement_status`
  - `source_system`
  - `source_event_id`
  - `source_version`
  - `trace_id`
  - `session_id`
  - `started_at`
  - `ended_at`
  - `metadata_json`
- Add unique active index on `bin_code` when `bin_code IS NOT NULL AND ended_at IS NULL`。
- Add unique active index on `placeholder_key` when `placeholder_key IS NOT NULL AND ended_at IS NULL`。

- [ ] **Step 5: 生成并编辑迁移**

Run:

```bash
uv run alembic revision -m "add workline bin tasks and bin placements"
```

Expected: Alembic creates one migration file under `migrations/versions/` with generated revision ID.

Edit the generated file to create:

- `wes_biz.workline_bin_tasks`
- `wes_biz.resource_bin_placements`
- enum check constraints generated by SQLAlchemy native_enum=False where applicable
- the indexes listed in Step 4

- [ ] **Step 6: 运行模型测试**

Run:

```bash
uv run pytest -q tests/workline_runtime/test_workline_bin_task_model.py
```

Expected: PASS.

- [ ] **Step 7: 运行迁移语法校验**

Run:

```bash
uv run alembic upgrade head
```

Expected: migration applies successfully in the local test database configured by `.env`。

- [ ] **Step 8: Commit**

```bash
git add src/app/workline/models/bin_task.py src/app/workline/models/__init__.py src/app/resource/models/resource.py src/app/resource/models/__init__.py migrations/versions tests/workline_runtime/test_workline_bin_task_model.py
git commit -m "feat(workline): 新增料箱低级任务模型"
```

---

## Task 2: Repository 和任务生命周期服务

**Files:**
- Create: `src/app/workline/repositories/bin_task_repository.py`
- Modify: `src/app/workline/repositories/__init__.py`
- Create: `src/app/workline/services/bin_task_service.py`
- Modify: `src/app/workline/services/__init__.py`
- Test: `tests/workline_runtime/test_workline_bin_task_service.py`

- [ ] **Step 1: 变更前做影响分析**

GitNexus impact:

```text
impact target=WorklineRackTaskLifecycleService direction=upstream relationTypes=CALLS,IMPORTS,HAS_METHOD
impact target=WorklineRackTaskRepository direction=upstream relationTypes=CALLS,IMPORTS,HAS_METHOD
```

Expected: 用现有 rack task 服务作为参考，确认不会修改它的行为。

- [ ] **Step 2: 写失败测试**

Add tests for:

- `record_requested_task` creates a `REQUESTED` bin task with normalized `MOVE_BIN`。
- duplicate `task_key` with same identity returns existing task。
- duplicate `task_key` with different operation raises `ValueError("task_key 已绑定不同 bin task")`。
- callback `CTU_BIN_MOVE_PROGRESS` maps to `IN_PROGRESS`。
- callback `CTU_BIN_MOVE_COMPLETED` maps to `SUCCEEDED`。
- callback `CTU_BIN_MOVE_FAILED` maps to `FAILED` and records error evidence。
- terminal task ignores late duplicate callback and keeps terminal status。

- [ ] **Step 3: 运行测试确认失败**

Run:

```bash
uv run pytest -q tests/workline_runtime/test_workline_bin_task_service.py
```

Expected: FAIL because repository and service do not exist.

- [ ] **Step 4: 实现 repository**

Create repository methods:

- `get_by_task_key(db, task_key)`
- `get_by_dispatch_key(db, dispatch_key)`
- `get_by_operation_sequence(db, operation_key, sequence_no)`
- `list_by_operation_key(db, operation_key)`
- `list_active_by_known_bin(db, bin_code)`
- `list_active_by_placeholder_key(db, placeholder_key)`

Follow `src/app/workline/repositories/rack_task_repository.py` style and use repository layer only.

- [ ] **Step 5: 实现 lifecycle service**

Create service methods:

- `record_requested_task(...)`
- `record_callback_from_external_http(db, payload_json, trace_id=None, **_)`

Status mapping:

| callback_type/status | task_status |
|----------------------|-------------|
| `CTU_BIN_MOVE_PROGRESS` 或 `ACCEPTED`、`QUEUED`、`IN_PROGRESS` | `IN_PROGRESS` |
| `CTU_BIN_MOVE_COMPLETED` 或 `SUCCESS`、`SUCCEEDED`、`COMPLETED` | `SUCCEEDED` |
| `CTU_BIN_MOVE_RECONCILING` 或 `RESOURCE_UNCONFIRMED` | `RECONCILING` |
| `CTU_BIN_MOVE_FAILED` 或 `FAILED`、`REJECTED`、`ERROR` | `FAILED` |
| `TIMEOUT`、`TIMED_OUT` | `TIMEOUT` |
| `CANCELLED`、`CANCELED` | `CANCELLED` |

Do not update resource projection in this service. It only updates task state and waiting session context.

- [ ] **Step 6: Export**

Modify `src/app/workline/repositories/__init__.py` and `src/app/workline/services/__init__.py` to export:

- `WorklineBinTaskRepository`
- `workline_bin_task_repository`
- `WorklineBinTaskLifecycleService`
- `workline_bin_task_lifecycle_service`

- [ ] **Step 7: 运行测试**

Run:

```bash
uv run pytest -q tests/workline_runtime/test_workline_bin_task_service.py
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/app/workline/repositories/bin_task_repository.py src/app/workline/repositories/__init__.py src/app/workline/services/bin_task_service.py src/app/workline/services/__init__.py tests/workline_runtime/test_workline_bin_task_service.py
git commit -m "feat(workline): 新增料箱任务生命周期服务"
```

---

## Task 3: BinOperationService 创建 outbox 并派生 operation 状态

**Files:**
- Create: `src/app/workline/services/bin_operation_service.py`
- Create: `src/app/workline/services/bin_operation_gateway.py`
- Modify: `src/app/workline/services/__init__.py`
- Test: `tests/workline_runtime/test_workline_bin_operation_service.py`

- [ ] **Step 1: 变更前做影响分析**

GitNexus impact:

```text
impact target=WorklineRackOperationService direction=upstream relationTypes=CALLS,IMPORTS,HAS_METHOD
```

Expected: 只借鉴 rack operation 模式，不修改 rack operation 行为。

- [ ] **Step 2: 写失败测试**

Add tests for:

- `request_ctu_bin_operation` creates `WorklineOutbox` with `DispatchType.EXTERNAL_HTTP`。
- `request_ctu_bin_operation` accepts internal move specs and does not require caller-provided WMS/RCS raw payload。
- `WmsRcsBinOperationGateway` maps internal source/target/carrier fields into the external HTTP envelope。
- generated `dispatch_key` format is `bin-operation:{operation_key}:{sequence_no}:MOVE_BIN`。
- repeated request with same operation returns existing tasks。
- same known `bin_code` cannot have two active operations。
- same active `placeholder_key` cannot have two active operations。
- `derive_operation_status` returns `PENDING` while any required task is requested/in progress。
- `derive_operation_status` returns `FAILED` when any required task failed。
- `derive_operation_status` returns `RECONCILING` when required tasks succeeded but resource projection is not confirmed。
- `derive_operation_status` returns `SUCCEEDED` after task success and resource confirmation。

- [ ] **Step 3: 运行测试确认失败**

Run:

```bash
uv run pytest -q tests/workline_runtime/test_workline_bin_operation_service.py
```

Expected: FAIL because operation service does not exist.

- [ ] **Step 4: 实现 service contract**

Create:

- `DEFAULT_BIN_OPERATION_TIMEOUT_SECONDS = 300`
- `WorklineBinOperationStatus`
- `WorklineBinTaskSpec`
- `WorklineBinOperationRequest`
- `WmsRcsBinOperationGateway`
- `WorklineBinOperationService`

`WorklineBinOperationRequest` fields:

- `operation_type`
- `operation_key`
- `carrier_type`
- `carrier_code`
- `workline_id`
- `workline_code`
- `material_session_id`
- `moves`
- `trace_id`
- `timeout_seconds`

`WorklineBinTaskSpec` fields:

- `sequence_no`
- `task_type`
- `bin_code`
- `placeholder_key`
- `source_type`
- `source_code`
- `target_type`
- `target_code`
- `source_rack_code`
- `source_rack_slot_code`
- `target_rack_code`
- `target_rack_slot_code`
- `carrier_type`
- `carrier_code`
- `dispatch_key`
- `request_json`
- `actions_json`
- `required`

- [ ] **Step 5: 实现 WMS/RCS gateway**

Create `WmsRcsBinOperationGateway` with these responsibilities:

- Resolve the external HTTP target code from operation context and workline configuration.
- Build the external request envelope from internal `WorklineBinOperationRequest` and `WorklineBinTaskSpec` values.
- Generate deterministic `dispatch_key` values.
- Keep WMS/RCS vendor field names inside the gateway; service and plugin code use internal field names only.
- Expose callback normalization helpers used by lifecycle service when mapping WMS/RCS statuses into internal task status.

- [ ] **Step 6: 实现 request_ctu_bin_operation**

Rules:

- This is the only service method the runtime effect should call for CTU bin requests.
- Require `operation_key`、`operation_type`、`carrier_type="CTU"`、`moves`、`trace_id`。
- Require `workline.line_code` for runtime-originated operations in v1 because the waiting session and trace live in workline runtime; treat it as correlation context, not as ownership of the low-level capability.
- Normalize move specs sorted by `sequence_no`。
- Ensure `sequence_no` is unique inside one operation。
- Ensure `task_type == "MOVE_BIN"`。
- Ask `WmsRcsBinOperationGateway` for `dispatch_key`、`target_code` and external HTTP envelope。
- Create or reuse outbox by gateway-provided `dispatch_key`。
- Outbox uses:
  - `dispatch_type=EXTERNAL_HTTP`
  - `target_type=HTTP_ENDPOINT`
  - `target_code` from gateway resolution
  - payload from gateway envelope
- Create task via `workline_bin_task_lifecycle_service.record_requested_task(...)`。

- [ ] **Step 7: 实现状态派生**

`derive_operation_status(db, operation_key=...)` rules:

- No required tasks: `PENDING`
- Any required task failed, timed out, or cancelled: `FAILED`
- Any required task reconciling: `RECONCILING`
- Any required task planned, requested, or in progress: `PENDING`
- All required tasks succeeded but resource projection missing expected result: `RECONCILING`
- All required tasks succeeded and projection confirms result: `SUCCEEDED`

Projection confirmation v1:

- If target is `RACK_SLOT`, confirm active `RackBinMount` for `target_rack_code + target_rack_slot_code + bin_code`。
- If target is line/buffer/ctu position and `bin_code` is known, confirm active `BinPlacement` for `bin_code + target_type + target_code`。
- If `bin_code` is unknown and `placeholder_key` is known, confirm active `BinPlacement` by `placeholder_key + target_type + target_code`。

- [ ] **Step 8: 运行测试**

Run:

```bash
uv run pytest -q tests/workline_runtime/test_workline_bin_operation_service.py
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/app/workline/services/bin_operation_service.py src/app/workline/services/bin_operation_gateway.py src/app/workline/services/__init__.py tests/workline_runtime/test_workline_bin_operation_service.py
git commit -m "feat(workline): 新增料箱操作编排服务"
```

---

## Task 4: RuntimeIntent 接入 BIN_OPERATION_REQUEST

**Files:**
- Modify: `src/workline_runtime/runtime_intent.py`
- Modify: `src/workline_runtime/plugin_next.py`
- Modify: `src/workline_runtime/runtime_intent_effects.py`
- Modify: `src/celery_app/tasks/workline.py`
- Test: `tests/workline_runtime/test_runtime_intent.py`
- Test: `tests/workline_runtime/test_runtime_intent_effects.py`

- [ ] **Step 1: 变更前做影响分析**

GitNexus impact:

```text
impact target=RuntimeIntentKind direction=upstream relationTypes=IMPORTS,ACCESSES
impact target=RuntimeIntentEffectApplier direction=upstream relationTypes=CALLS,IMPORTS,HAS_METHOD
```

Expected: 如果 `RuntimeIntentEffectApplier` 返回 HIGH，记录涉及 command/outbox/session 等共享路径，并保持新增分支独立。

- [ ] **Step 2: 写失败测试**

Add tests:

- `RuntimeIntent.bin_operation_request(...)` validates operation type, operation key, carrier type, move specs, trace context, timeout。
- `PluginNext.bin_operation_request(...)` returns kind `BIN_OPERATION_REQUEST` without exposing WMS/RCS target, dispatch key, outbox, repository, or raw vendor payload。
- Effect applier creates bin tasks and marks session waiting for `BIN_OPERATION`。
- Multiple command-producing intents still rejected when `BIN_OPERATION_REQUEST` is combined with `COMMAND`。

- [ ] **Step 3: 运行测试确认失败**

Run:

```bash
uv run pytest -q tests/workline_runtime/test_runtime_intent.py tests/workline_runtime/test_runtime_intent_effects.py
```

Expected: FAIL because `BIN_OPERATION_REQUEST` does not exist.

- [ ] **Step 4: 修改 RuntimeIntent**

Add:

- `RuntimeIntentKind.BIN_OPERATION_REQUEST = "BIN_OPERATION_REQUEST"`
- `RuntimeIntent.bin_operation_request(operation_type, operation_key, moves, carrier_type="CTU", carrier_code=None, timeout_seconds=None)`
- validation requiring:
  - action/operation type
  - idempotency key/operation key
  - carrier type
  - non-empty move specs
  - positive timeout seconds

- [ ] **Step 5: 修改 PluginNext**

Add `bin_operation_request(...)` with parameters:

- `operation_type`
- `operation_key`
- `moves`
- `carrier_type`
- `carrier_code`
- `timeout_seconds`

It must call `RuntimeIntent.bin_operation_request(...)` and not directly create outbox, call WMS/RCS, import repositories, build `dispatch_key`, or expose external target code.

- [ ] **Step 6: 修改 effect applier**

Add `_apply_bin_operation_request`:

- Resolve `workline_bin_operation_service` lazily from `src.app.workline.services`。
- Require `trace_id` from runtime trace or intent context。
- Require intent move specs as non-empty list。
- Call `request_ctu_bin_operation(...)`。
- Write session context:
  - `waiting_bin_operation_key`
  - `bin_operation.operation_key`
  - `bin_operation.operation_type`
  - `bin_operation.status="PENDING"`
  - `bin_operation.task_count`
  - `bin_operation.task_dispatch_keys`
- Set session:
  - `status="WAITING_EXTERNAL"`
  - `current_wait_type="BIN_OPERATION"`
  - `awaiting_command_id=None`
  - `deadline_at=now + timeout_seconds`
- Emit timeline wait event with `wait_type="BIN_OPERATION"` and actor `WMS_RCS`。

- [ ] **Step 7: 修改 Celery wait handling**

Update `src/celery_app/tasks/workline.py` so wait type helpers accept `BIN_OPERATION` wherever `RACK_OPERATION` is accepted.

- [ ] **Step 8: 运行测试**

Run:

```bash
uv run pytest -q tests/workline_runtime/test_runtime_intent.py tests/workline_runtime/test_runtime_intent_effects.py
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/workline_runtime/runtime_intent.py src/workline_runtime/plugin_next.py src/workline_runtime/runtime_intent_effects.py src/celery_app/tasks/workline.py tests/workline_runtime/test_runtime_intent.py tests/workline_runtime/test_runtime_intent_effects.py
git commit -m "feat(runtime): 接入料箱操作意图"
```

---

## Task 5: Callback 与 SessionResolver 接入料箱任务

**Files:**
- Modify: `src/app/callback/services/callback_orchestration_service.py`
- Modify: `src/workline_runtime/session_resolver.py`
- Test: `tests/api/test_callback_api.py`
- Test: `tests/workline_runtime/test_session_resolver.py`

- [ ] **Step 1: 变更前做影响分析**

GitNexus impact:

```text
impact target=CallbackOrchestrationService direction=upstream relationTypes=CALLS,IMPORTS,HAS_METHOD
impact target=SessionResolver direction=upstream relationTypes=CALLS,IMPORTS,HAS_METHOD
```

Expected: 这两个是共享入口，HIGH 风险时先列出回归测试范围，再修改。

- [ ] **Step 2: 写失败测试**

Callback tests:

- External callback with `dispatch_key` matching a bin task calls `workline_bin_task_lifecycle_service.record_callback_from_external_http(...)`。
- Duplicate callback does not call lifecycle service again。
- Lifecycle-only bin callback marks inbox processed and does not enqueue workline processing。

Session resolver tests:

- `EXTERNAL_HTTP` with bin task `dispatch_key` resolves waiting session by `waiting_bin_operation_key`。
- If bin task is missing, resolver falls back to outbox/session/trace path。

- [ ] **Step 3: 运行测试确认失败**

Run:

```bash
uv run pytest -q tests/api/test_callback_api.py tests/workline_runtime/test_session_resolver.py
```

Expected: FAIL on new assertions.

- [ ] **Step 4: 修改 callback orchestration**

Add a resolver method `_resolve_bin_task_service()` importing `workline_bin_task_lifecycle_service`。

In external callback path:

- Always create external inbox first, preserving current idempotency behavior。
- If not duplicate, call rack task lifecycle service as today。
- If not duplicate, call bin task lifecycle service with same `payload_json` and `trace_id`。
- Treat these callback types as lifecycle-only:
  - `CTU_BIN_MOVE_PROGRESS`
  - `CTU_BIN_TASK_PROGRESS`
- Do not mark `CTU_BIN_MOVE_COMPLETED` lifecycle-only because plugin/resource projection may need the same inbox.

- [ ] **Step 5: 修改 SessionResolver**

In `_resolve_external_http`:

- Keep rack task lookup first to avoid changing existing behavior。
- Add bin task lookup after rack task and before outbox fallback。
- Resolve waiting session by `workline_id + operation_key` where session context has `waiting_bin_operation_key`。
- Set `inbox.session_id` and `inbox.workline_id` when found。

- [ ] **Step 6: 运行测试**

Run:

```bash
uv run pytest -q tests/api/test_callback_api.py tests/workline_runtime/test_session_resolver.py
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/app/callback/services/callback_orchestration_service.py src/workline_runtime/session_resolver.py tests/api/test_callback_api.py tests/workline_runtime/test_session_resolver.py
git commit -m "feat(callback): 接入料箱任务回调解析"
```

---

## Task 6: 资源投影支持料箱位置和挂载变化

**Files:**
- Modify: `src/app/resource/repositories/resource_repository.py`
- Modify: `src/app/resource/services/projection_service.py`
- Test: `tests/resource/test_resource_projection_service.py`

- [ ] **Step 1: 变更前做影响分析**

GitNexus impact:

```text
impact target=ResourceProjectionService direction=upstream relationTypes=CALLS,IMPORTS,HAS_METHOD
impact target=RackBinMountRepository direction=upstream relationTypes=CALLS,IMPORTS,HAS_METHOD
```

Expected: 资源投影是共享路径，修改必须覆盖幂等和冲突测试。

- [ ] **Step 2: 写失败测试**

Add tests:

- `BIN_ARRIVED` creates active `BinPlacement` for known `bin_code`。
- repeated `BIN_ARRIVED` with same idempotency key is idempotent。
- `BIN_DEPARTED` closes active `BinPlacement` by `bin_code`。
- unknown bin with `placeholder_key` creates active placeholder placement。
- `BIN_MOUNTED` creates active `RackBinMount` and closes matching `BinPlacement` when source placement is present。
- `BIN_UNMOUNTED` closes active `RackBinMount` and can create `BinPlacement` at CTU slot when payload includes target position。
- conflicting active known-bin placement returns `ResourceProjectionResult` with reason `BIN_ACTIVE_PLACEMENT_CONFLICT`。

- [ ] **Step 3: 运行测试确认失败**

Run:

```bash
uv run pytest -q tests/resource/test_resource_projection_service.py
```

Expected: FAIL on new bin placement assertions.

- [ ] **Step 4: 实现 repository**

Add `BinPlacementRepository` methods:

- `get_active_by_bin_code(db, bin_code)`
- `get_active_by_placeholder_key(db, placeholder_key)`
- `list_active_by_workline_position(db, workline_code, position_type, position_code)`
- `create(db, data)`
- `close_active_by_bin_code(db, bin_code, ended_at, source_event_id)`
- `close_active_by_placeholder_key(db, placeholder_key, ended_at, source_event_id)`

- [ ] **Step 5: 实现 projection service routes**

Extend `record_resource_fact(...)` routing:

- `BIN_ARRIVED` -> `record_bin_arrived_at_position(...)`
- `BIN_DEPARTED` -> `record_bin_departed_from_position(...)`
- `BIN_MOUNTED` keeps existing rack-bin behavior and closes prior `BinPlacement` when payload says the bin moved from line/ctu/buffer。
- `BIN_UNMOUNTED` closes `RackBinMount`; if target position exists, creates `BinPlacement`。

Required payload fields:

`BIN_ARRIVED`:

- `position_type`
- `position_code`
- one of `bin_code` or `placeholder_key`
- `source_event_id`

`BIN_DEPARTED`:

- one of `bin_code` or `placeholder_key`
- `source_event_id`

`BIN_UNMOUNTED`:

- `rack_code`
- `rack_slot_code`
- `bin_code`
- `source_event_id`

- [ ] **Step 6: 运行测试**

Run:

```bash
uv run pytest -q tests/resource/test_resource_projection_service.py
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/app/resource/repositories/resource_repository.py src/app/resource/services/projection_service.py tests/resource/test_resource_projection_service.py
git commit -m "feat(resource): 增加料箱位置资源投影"
```

---

## Task 7: Trace、诊断和最小可观测性

**Files:**
- Modify: `src/app/workline/services/trace_query_service.py`
- Modify: `src/app/workline/services/trace_response_builder.py`
- Modify: `src/app/workline/models/runtime.py`
- Test: `tests/workline_runtime/test_trace_query_service.py`

- [ ] **Step 1: 变更前做影响分析**

GitNexus impact:

```text
impact target=TraceQueryService direction=upstream relationTypes=CALLS,IMPORTS,HAS_METHOD
impact target=TraceDetailResponse direction=upstream relationTypes=IMPORTS,HAS_PROPERTY
```

Expected: Trace response 可能影响 API 响应，若已有快照测试需要同步更新。

- [ ] **Step 2: 写失败测试**

Add tests:

- Trace detail includes bin tasks linked by `session_id`。
- Trace detail includes bin placement resource evidence for `trace_id`。
- Response preserves existing rack task and rack placement evidence。

- [ ] **Step 3: 运行测试确认失败**

Run:

```bash
uv run pytest -q tests/workline_runtime/test_trace_query_service.py
```

Expected: FAIL because bin task evidence is absent.

- [ ] **Step 4: 扩展 trace 查询**

Add fields:

- `bin_tasks`
- `bin_placements`

Query rules:

- Load `workline_bin_tasks` by `session_id` and `trace_id`。
- Load `resource_bin_placements` by `trace_id`。
- Return evidence dictionaries consistent with existing resource evidence format。

- [ ] **Step 5: 运行测试**

Run:

```bash
uv run pytest -q tests/workline_runtime/test_trace_query_service.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/app/workline/services/trace_query_service.py src/app/workline/services/trace_response_builder.py src/app/workline/models/runtime.py tests/workline_runtime/test_trace_query_service.py
git commit -m "feat(trace): 展示料箱操作证据"
```

---

## Task 8: 回归测试、文档和质量门

**Files:**
- Modify: `docs/business/smt_sorter_inbound_workflow_guide.md`
- Modify: `docs/superpowers/specs/2026-05-20-rack-operation-service-design.md`
- Test: multiple test suites listed below

- [ ] **Step 1: 更新业务文档**

Update `docs/business/smt_sorter_inbound_workflow_guide.md`:

- 在 CTU 搬运章节说明 `workline_bin_tasks` 是系统级低级执行账本，第一版复用 workline runtime/outbox 基础设施。
- 明确 CTU 投料阶段真实 `bin_id` 未知时使用 `placeholder_key`。
- 明确 `TARGET_BIN_SCAN_COMPLETED` 绑定真实 `bin_id` 后，后续业务层可把 placeholder 和 bin 做对账。
- 明确工作线插件只决定业务策略：为什么搬、何时搬、搬到哪里、优先级是什么；低级域只负责执行账本、派发、回调、投影和追踪。
- 明确插件发起 CTU 料箱请求只允许调用 `ctx.next.bin_operation_request(...)`；不得直接调用 WMS/RCS、创建 outbox、生成 dispatch key 或拼接厂商 payload。

Update `docs/superpowers/specs/2026-05-20-rack-operation-service-design.md`:

- 在“满箱交换和分拣机预留边界”补充：货架操作由 `workline_rack_tasks` 承载，料箱操作由 `workline_bin_tasks` 承载；二者都是低级执行能力，上层工作线协调器负责策略。
- 保持“resource 域不承载策略”的边界。

- [ ] **Step 2: 运行目标测试**

Run:

```bash
uv run pytest -q \
  tests/workline_runtime/test_workline_bin_task_model.py \
  tests/workline_runtime/test_workline_bin_task_service.py \
  tests/workline_runtime/test_workline_bin_operation_service.py \
  tests/workline_runtime/test_runtime_intent.py \
  tests/workline_runtime/test_runtime_intent_effects.py \
  tests/api/test_callback_api.py \
  tests/resource/test_resource_projection_service.py
```

Expected: PASS.

- [ ] **Step 3: 运行相关回归测试**

Run:

```bash
uv run pytest -q \
  tests/workline_runtime/test_workline_rack_operation_service.py \
  tests/workline_runtime/test_workline_rack_task_service.py \
  tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py
```

Expected: PASS. Existing rack operation and smt classifier behavior unchanged.

- [ ] **Step 4: 运行格式和 lint**

Run:

```bash
uv run ruff format src/app/workline src/workline_runtime src/app/resource tests/workline_runtime tests/resource tests/api
uv run ruff check src/app/workline src/workline_runtime src/app/resource tests/workline_runtime tests/resource tests/api
```

Expected: PASS.

- [ ] **Step 5: GitNexus detect changes**

Run GitNexus:

```text
detect_changes scope=all repo=wes_backend
```

Expected: Changed symbols are limited to bin task, runtime intent, callback, session resolver, resource projection, trace, and docs. Unexpected changes to `smt_classifier` or rack operation behavior must be investigated before commit.

- [ ] **Step 6: Commit**

```bash
git add docs/business/smt_sorter_inbound_workflow_guide.md docs/superpowers/specs/2026-05-20-rack-operation-service-design.md
git commit -m "docs(workline): 记录料箱操作域边界"
```

---

## Final Acceptance Criteria

- The bin operation capability is documented as system-level; `workline_id`、`workline_code`、`material_session_id` are correlation context, not the ownership boundary.
- `workline_bin_tasks` can record CTU/bin movement requests independently of any plugin while still linking back to a waiting workline session when one exists.
- Workline plugins can complete a CTU bin request by calling only `ctx.next.bin_operation_request(...)` with internal move specs.
- Plugins do not pass WMS/RCS endpoint, dispatch key, outbox id, HTTP settings, or vendor-specific payload fields.
- `WmsRcsBinOperationGateway` owns internal move spec to external HTTP envelope conversion.
- Duplicate bin operation requests do not create duplicate tasks or outboxes.
- External callback by `dispatch_key` updates the correct bin task.
- Waiting session can be recovered by `waiting_bin_operation_key`.
- Runtime plugin authors can emit `BIN_OPERATION_REQUEST` without directly creating outbox, touching repositories, or knowing WMS/RCS protocol details.
- Resource projection can express:
  - bin arrived at non-rack position
  - bin departed from non-rack position
  - bin mounted to rack slot
  - bin unmounted from rack slot
- Existing rack operation tests continue to pass.
- Existing `smt_classifier` behavior remains unchanged.
- Docs clearly state that business strategy belongs above the system-level low-level bin operation domain.

## Self-Review

- Spec coverage: The plan covers model, repository, service, WMS/RCS gateway, single plugin-facing runtime intent, callback, session resolver, resource projection, trace, docs, and verification.
- Placeholder scan: No unresolved placeholder text is used; generated Alembic revision is explicitly delegated to Alembic per repository rule.
- Type consistency: `operation_key`、`dispatch_key`、`task_type`、`task_status`、`bin_code`、`placeholder_key`、`source_type`、`target_type`、`carrier_type` are named consistently across model, service, callback, and projection tasks.
- Scope check: 满箱交换协调器、`smt_sorter_inbound` 插件 and cross-workline CTU dispatch optimization are intentionally outside this implementation plan; they consume this system-level low-level domain in separate plans.
