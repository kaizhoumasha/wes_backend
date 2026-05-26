# Rack Domain 与 System Outbox Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 WES 出站调用统一到系统级 `SystemOutboxEngine`，并将 Rack 操作从 Workline 子域提升为系统级 Rack 域。

**Architecture:** `src/app/sys` 提供唯一出站消息底座，统一处理 Workline、Rack、Handling 面向外部硬件系统的派发、重试、阻塞和对账。`src/app/rack` 提供系统级货架 operation/task；Workline 只作为可选上下文接入。Rack 和 Handling 用 `OperationCompletionPolicy` 显式表达完成确认策略。

**Tech Stack:** Python 3.13, SQLModel, SQLAlchemy async, Alembic, Celery, pytest, ruff, GitNexus.

---

## Scope Check

本计划覆盖三个强相关子系统，必须一起实施才能形成可运行闭环：

1. `SystemOutboxEngine` 统一出站消息底座。
2. `RackOperation/RackTask` 系统级 Rack 域。
3. `OperationCompletionPolicy` 统一完成策略。

本计划不实现通用库位容量主数据，不实现后台巡检任务，不保留旧 import 或旧表名兼容。

## File Structure

### 新增系统出站底座

- Create: `src/app/sys/models/outbox.py`
  - 系统级 outbox 模型和枚举。
- Create: `src/app/sys/models/operation_completion.py`
  - `OperationCompletionPolicy`。
- Modify: `src/app/sys/models/__init__.py`
  - 导出 outbox 和 completion policy。
- Create: `src/app/sys/repositories/outbox_repository.py`
  - 合并原 Workline outbox 和 Handling system outbox repository 能力。
- Modify: `src/app/sys/repositories/__init__.py`
  - 导出 `SystemOutboxRepository` 和单例。
- Create: `src/app/sys/services/outbox_engine.py`
  - 统一派发、租约、重试、阻塞、状态对账。
- Modify: `src/app/sys/services/__init__.py`
  - 导出 `SystemOutboxEngine` 和单例。
- Create: `src/celery_app/tasks/sys.py`
  - 唯一 outbox dispatch Celery task。
- Modify: `src/celery_app/config.py`
  - 删除 Workline/Handling 双 outbox beat，保留系统级 beat。

### 新增系统 Rack 域

- Create: `src/app/rack/models/operation.py`
  - `RackOperation`、`RackTask`、枚举和 schema。
- Create: `src/app/rack/models/__init__.py`
  - Rack 模型导出。
- Create: `src/app/rack/repositories/operation_repository.py`
  - Rack operation/task 数据访问。
- Create: `src/app/rack/repositories/__init__.py`
  - Rack repository 导出。
- Create: `src/app/rack/services/gateway.py`
  - Rack WMS/RCS payload gateway。
- Create: `src/app/rack/services/completion_policy.py`
  - Rack completion policy 执行器。
- Create: `src/app/rack/services/operation_service.py`
  - Rack operation 创建、容量校验、outbox 创建。
- Create: `src/app/rack/services/task_lifecycle_service.py`
  - Rack task 回调和 operation/session 同步。
- Create: `src/app/rack/services/__init__.py`
  - Rack service 导出。
- Create: `src/app/rack/__init__.py`
  - Rack 模块顶层导出。

### 删除或迁移旧实现

- Delete: `src/app/workline/models/outbox.py`
- Delete: `src/app/workline/repositories/outbox_repository.py`
- Delete: `src/app/workline/models/rack_task.py`
- Delete: `src/app/workline/repositories/rack_task_repository.py`
- Delete: `src/app/workline/services/rack_operation_service.py`
- Delete: `src/app/workline/services/rack_task_service.py`
- Delete: `src/app/workline/services/rack_gateway.py`
- Delete: `src/app/handling/models/outbox.py`
- Delete: `src/app/handling/repositories/outbox_repository.py`
- Delete: `src/app/handling/services/outbox_dispatcher.py`

### 主要调用方迁移

- Modify: `src/app/workline/services/__init__.py`
- Modify: `src/app/workline/models/__init__.py`
- Modify: `src/app/workline/repositories/__init__.py`
- Modify: `src/app/handling/models/__init__.py`
- Modify: `src/app/handling/repositories/__init__.py`
- Modify: `src/app/handling/services/operation_service.py`
- Modify: `src/app/handling/services/lifecycle_service.py`
- Modify: `src/app/callback/services/callback_orchestration_service.py`
- Modify: `src/workline_runtime/runtime_intent_effects.py`
- Modify: `src/workline_runtime/session_resolver.py`
- Modify: `src/app/workline/services/runtime_reconciliation_service.py`
- Modify: `src/app/workline/services/runtime_hold_release_service.py`
- Modify: `src/app/workline/services/safety_service.py`
- Modify: `src/app/workline/repositories/sandbox_cleanup_repository.py`
- Modify: `src/celery_app/tasks/workline.py`
- Modify: `src/celery_app/tasks/handling.py`

## Task 1: 变更前影响分析

**Files:**
- Read: `src/app/workline/models/outbox.py`
- Read: `src/app/workline/models/rack_task.py`
- Read: `src/app/handling/models/outbox.py`
- Read: `src/app/workline/services/rack_operation_service.py`
- Read: `src/app/handling/services/lifecycle_service.py`

- [ ] **Step 1: 运行 GitNexus impact**

Run:

```text
mcp__gitnexus__.impact(repo="wes_backend", target="WorklineOutbox", file_path="src/app/workline/models/outbox.py", direction="upstream", includeTests=true)
mcp__gitnexus__.impact(repo="wes_backend", target="WorklineRackTask", file_path="src/app/workline/models/rack_task.py", direction="upstream", includeTests=true)
mcp__gitnexus__.impact(repo="wes_backend", target="SystemOutbox", file_path="src/app/handling/models/outbox.py", direction="upstream", includeTests=true)
```

Expected:

- `WorklineOutbox` 风险至少 MEDIUM。
- `WorklineRackTask` 风险至少 MEDIUM。
- 输出中列出的 d=1 文件必须纳入后续迁移。

- [ ] **Step 2: 建立迁移清单**

Run:

```bash
rg -n "WorklineOutbox|WorklineOutboxRepository|WorklineRackTask|WorklineRackTaskRepository|workline_outbox|workline_rack_tasks|SystemOutbox" src tests migrations
```

Expected:

- 输出用于确认所有 import、测试、迁移和 Celery 任务引用点。
- 不修改文件。

## Task 2: 写失败测试锁定目标行为

**Files:**
- Create: `tests/sys/test_system_outbox_engine.py`
- Create: `tests/rack/test_rack_operation_core.py`
- Create: `tests/rack/test_rack_operation_lifecycle.py`
- Modify: `tests/handling/test_handling_operation_core.py`
- Modify: `tests/handling/test_handling_operation_lifecycle.py`
- Modify: `tests/workline_runtime/test_runtime_intent_effects.py`

- [ ] **Step 1: 新增 SystemOutboxEngine 测试场景**

Test scenarios:

- `test_system_outbox_engine_dispatches_external_http_and_marks_sent`
- `test_system_outbox_engine_applies_exponential_backoff_on_failure`
- `test_system_outbox_engine_preserves_device_fifo`
- `test_system_outbox_engine_releases_blocked_resource_messages`
- `test_system_outbox_can_reference_workline_rack_and_handling_domains`

- [ ] **Step 2: 新增 Rack operation 核心测试场景**

Test scenarios:

- `test_request_rack_operation_without_workline_context_creates_operation_task_and_system_outbox`
- `test_request_rack_operation_with_workline_context_persists_optional_context`
- `test_request_rack_operation_rejects_external_protocol_fields`
- `test_request_rack_operation_reuses_existing_operation_by_operation_key`

- [ ] **Step 3: 新增 completion policy 测试场景**

Test scenarios:

- `test_callback_trusted_policy_succeeds_without_resource_projection`
- `test_projection_required_policy_reconciles_until_projection_matches`
- `test_workline_rack_operation_uses_projection_required_by_default`
- `test_handling_operation_uses_callback_trusted_by_default`

- [ ] **Step 4: 验证测试先失败**

Run:

```bash
uv run pytest tests/sys/test_system_outbox_engine.py tests/rack tests/handling/test_handling_operation_core.py tests/handling/test_handling_operation_lifecycle.py tests/workline_runtime/test_runtime_intent_effects.py -q
```

Expected:

- 新增 `src.app.sys.models.outbox`、`src.app.rack` 等 import 不存在导致失败。
- 不允许直接跳过失败测试进入实现。

## Task 3: 建立 `src/app/sys` System Outbox Engine

**Files:**
- Create: `src/app/sys/models/outbox.py`
- Create: `src/app/sys/models/operation_completion.py`
- Modify: `src/app/sys/models/__init__.py`
- Create: `src/app/sys/repositories/outbox_repository.py`
- Modify: `src/app/sys/repositories/__init__.py`
- Create: `src/app/sys/services/outbox_engine.py`
- Modify: `src/app/sys/services/__init__.py`
- Test: `tests/sys/test_system_outbox_engine.py`

- [ ] **Step 1: 新增系统级模型**

Model contract:

- `SystemOutboxStatus`: `NEW`、`DISPATCHING`、`SENT`、`BLOCKED_RESOURCE`、`FAILED`、`CANCELLED`
- `SystemOutboxDispatchType`: `DEVICE_COMMAND`、`EXTERNAL_HTTP`、`INTERNAL_SIGNAL`
- `SystemOutboxTargetType`: `DEVICE`、`HTTP_ENDPOINT`、`INTERNAL_SERVICE`
- `SystemOutbox` table: `system_outbox`
- `OperationCompletionPolicy`: `CALLBACK_TRUSTED`、`RESOURCE_PROJECTION_REQUIRED`、`CALLBACK_PLUS_RECONCILIATION`

- [ ] **Step 2: 新增 repository 行为**

Repository must support:

- `get_by_dispatch_key`
- `get_pending_messages`
- `get_dispatching_device_messages`
- `get_blocked_device_busy_messages`
- `get_sandbox_pending_messages`
- `get_sandbox_completed_messages`
- `mark_as_dispatching`
- `mark_as_sent`
- `mark_as_failed`
- `cancel_active_by_session`
- `cancel_active_by_workline`
- `release_blocked_by_device`
- `release_blocked_by_reconciliation_session`
- `block_by_runtime_hold`
- `mark_as_blocked_by_workline_state`
- `mark_as_blocked_by_workline_estop`
- `mark_as_blocked_by_device_busy`

- [ ] **Step 3: 新增 engine 行为**

Engine must:

- claim message with dispatch lease before I/O
- commit claim before external I/O when DB session supports commit
- dispatch `EXTERNAL_HTTP`
- dispatch `INTERNAL_SIGNAL`
- dispatch `DEVICE_COMMAND` using the existing device command sender path from workline dispatcher
- mark success as `SENT`
- mark failure with exponential retry
- keep same-device FIFO

- [ ] **Step 4: 跑 SystemOutboxEngine 测试**

Run:

```bash
uv run pytest tests/sys/test_system_outbox_engine.py -q
```

Expected:

- All tests pass.

## Task 4: 迁移 Workline/Handling 到 SystemOutbox

**Files:**
- Modify: `src/app/handling/services/operation_service.py`
- Modify: `src/app/workline/services/runtime_reconciliation_service.py`
- Modify: `src/app/workline/services/runtime_hold_release_service.py`
- Modify: `src/app/workline/services/safety_service.py`
- Modify: `src/workline_runtime/session_resolver.py`
- Modify: `src/celery_app/tasks/workline.py`
- Modify: `src/celery_app/tasks/handling.py`
- Modify: `src/celery_app/config.py`

- [ ] **Step 1: Handling 创建 outbox 改用 `src.app.sys`**

Required behavior:

- Handling operation 创建 outbox 时写入 `operation_domain="HANDLING"`。
- `operation_key` 使用 Handling operation key。
- `session_id/workline_id/trace_id` 保持原语义。

- [ ] **Step 2: Workline outbox 查询与阻塞改用 `SystemOutboxRepository`**

Required behavior:

- Workline device command 和 external request 不再依赖 `WorklineOutboxRepository`。
- Runtime hold、safety、reconciliation 的 blocked/release 语义保持不变。
- Sandbox 查询仍按 `workline_id/session_id/device_id` 过滤。

- [ ] **Step 3: Celery 只保留系统级 outbox task**

Required behavior:

- 新任务名：`src.celery_app.tasks.sys.dispatch_system_outbox_batch`
- Beat 配置只注册一个 outbox dispatch。
- Workline orchestrator 需要派发时 enqueue 系统级 outbox task。

- [ ] **Step 4: 跑 Workline/Handling outbox 相关测试**

Run:

```bash
uv run pytest tests/sys/test_system_outbox_engine.py tests/handling/test_handling_operation_core.py tests/workline_runtime/test_outbox_repository.py tests/workline_runtime/test_outbox_dispatcher.py -q
```

Expected:

- 所有 outbox 测试通过。
- 测试 import 不再引用 `src.app.workline.models.outbox`。

## Task 5: 建立系统级 Rack 域

**Files:**
- Create: `src/app/rack/models/operation.py`
- Create: `src/app/rack/models/__init__.py`
- Create: `src/app/rack/repositories/operation_repository.py`
- Create: `src/app/rack/repositories/__init__.py`
- Create: `src/app/rack/services/gateway.py`
- Create: `src/app/rack/services/operation_service.py`
- Create: `src/app/rack/services/task_lifecycle_service.py`
- Create: `src/app/rack/services/completion_policy.py`
- Create: `src/app/rack/services/__init__.py`
- Create: `src/app/rack/__init__.py`
- Test: `tests/rack/test_rack_operation_core.py`

- [ ] **Step 1: 新增 Rack 模型**

Model contract:

- `RackOperation.__tablename__ == "rack_operations"`
- `RackTask.__tablename__ == "rack_tasks"`
- `RackOperation.workline_id` is optional
- `RackTask.workline_id` is optional
- `RackOperation.completion_policy` defaults to `RESOURCE_PROJECTION_REQUIRED`
- `RackTask.outbox_id` references `wes_biz.system_outbox.id`

- [ ] **Step 2: 新增 Rack repository**

Repository must support:

- operation by `operation_key`
- task by `task_key`
- task by `dispatch_key`
- task by operation sequence
- list tasks by operation key
- list active target position tasks
- list active source rack claims
- cancel active tasks by material session

- [ ] **Step 3: 新增 Rack gateway**

Gateway must:

- derive `dispatch_key` as `rack-operation:{operation_key}:{sequence_no}:{task_type}`
- generate WMS/RCS payload internally
- reject caller-owned protocol fields before payload generation
- preserve `target_code` fallback from env or explicit service argument

- [ ] **Step 4: 新增 Rack operation service**

Service must:

- create `RackOperation`
- create one or more `RackTask`
- create `SystemOutbox` with `operation_domain="RACK"`
- allow `workline=None`
- use Workline rack position capacity only when `workline` exists
- keep source rack claim and target task conflict checks

- [ ] **Step 5: 跑 Rack core 测试**

Run:

```bash
uv run pytest tests/rack/test_rack_operation_core.py -q
```

Expected:

- 无 Workline context 的 Rack operation 测试通过。
- Workline context 仍写入可选上下文。

## Task 6: 实现 completion policy

**Files:**
- Modify: `src/app/rack/services/completion_policy.py`
- Modify: `src/app/rack/services/task_lifecycle_service.py`
- Modify: `src/app/handling/models/operation.py`
- Modify: `src/app/handling/services/lifecycle_service.py`
- Test: `tests/rack/test_rack_operation_lifecycle.py`
- Test: `tests/handling/test_handling_operation_lifecycle.py`

- [ ] **Step 1: Rack completion policy**

Required behavior:

- Failed/timeout/cancelled required task makes operation failed.
- Pending required task keeps operation pending.
- `CALLBACK_TRUSTED` succeeds when all required tasks succeeded.
- `RESOURCE_PROJECTION_REQUIRED` succeeds only after rack placement projection matches.
- Projection mismatch returns `RECONCILING`.
- `CALLBACK_PLUS_RECONCILIATION` succeeds and records `reconciliation_expected=true`.

- [ ] **Step 2: Handling completion policy**

Required behavior:

- `HandlingOperation.completion_policy` defaults to `CALLBACK_TRUSTED`.
- Existing callback status mapping remains unchanged.
- Existing full-box-exchange missing `post_exchange_relations` reconciliation remains unchanged.

- [ ] **Step 3: 跑 lifecycle 测试**

Run:

```bash
uv run pytest tests/rack/test_rack_operation_lifecycle.py tests/handling/test_handling_operation_lifecycle.py -q
```

Expected:

- Rack 和 Handling completion policy 测试通过。

## Task 7: 更新 Workline/Callback 调用链

**Files:**
- Modify: `src/workline_runtime/runtime_intent_effects.py`
- Modify: `src/workline_runtime/session_resolver.py`
- Modify: `src/app/callback/services/callback_orchestration_service.py`
- Modify: `src/app/workline/services/runtime_reconciliation_service.py`
- Modify: `src/app/workline/repositories/sandbox_cleanup_repository.py`
- Modify: `src/app/workline/services/__init__.py`
- Modify: `src/app/workline/models/__init__.py`
- Modify: `src/app/workline/repositories/__init__.py`
- Test: `tests/workline_runtime/test_runtime_intent_effects.py`
- Test: `tests/workline_runtime/test_workline_rack_task_service.py`
- Test: `tests/workline_runtime/test_sandbox_cleanup_service.py`
- Test: `tests/workline_runtime/test_runtime_reconciliation_service.py`

- [ ] **Step 1: Runtime intent 调用 Rack 域**

Required behavior:

- `RACK_OPERATION_REQUEST` 调用 `src.app.rack.services.rack_operation_service`。
- Workline 场景传入 `completion_policy=RESOURCE_PROJECTION_REQUIRED`。
- Session wait context 仍使用 `waiting_rack_operation_key`。

- [ ] **Step 2: Callback orchestration 调用 Rack lifecycle**

Required behavior:

- Rack 回调按 `dispatch_key` 进入 `RackTaskLifecycleService.record_callback_from_external_http`。
- Handling 回调仍进入 `HandlingOperationLifecycleService`。
- Callback orchestration 不直接访问数据库模型。

- [ ] **Step 3: Workline cleanup/reconciliation 使用新 repository**

Required behavior:

- Sandbox cleanup 只清理带当前 `workline_id/session_id` 的 `SystemOutbox/RackOperation/RackTask`。
- 非 Workline Rack operation 不被 Workline sandbox cleanup 删除。
- Runtime reconciliation blocked/release 仍作用于系统 outbox。

- [ ] **Step 4: 跑 Workline runtime 测试**

Run:

```bash
uv run pytest tests/workline_runtime/test_runtime_intent_effects.py tests/workline_runtime/test_workline_rack_task_service.py tests/workline_runtime/test_sandbox_cleanup_service.py tests/workline_runtime/test_runtime_reconciliation_service.py -q
```

Expected:

- Workline runtime 相关测试通过。
- 测试不再 import `WorklineRackTask`。

## Task 8: 更新未发布迁移

**Files:**
- Modify: `migrations/versions/20260317_0930_8f8180e751c3_create_workline_session_timeline_inbox_.py`
- Modify: `migrations/versions/20260520_1453_083e85d1bf93_add_workline_rack_tasks.py`
- Modify: `migrations/versions/20260521_1513_97dbf218ed9f_add_rack_operation_task_metadata.py`
- Modify: `migrations/versions/20260522_0024_c0ff648f8718_add_rack_task_source_claim_guard.py`
- Modify: `migrations/versions/20260522_1449_745068e173c2_add_handling_core.py`

- [ ] **Step 1: 删除旧表定义**

Required changes:

- `workline_outbox` 不再创建。
- `workline_rack_tasks` 不再创建。
- Handling revision 不再创建 Handling 专属 `SystemOutbox` 模型含义。

- [ ] **Step 2: 创建新表定义**

Required tables:

- `system_outbox`
- `rack_operations`
- `rack_tasks`

Required index names:

- `ux_system_outbox_dispatch_key`
- `ix_system_outbox_status_retry`
- `ix_system_outbox_domain_operation`
- `ux_rack_operations_key`
- `ux_rack_tasks_key`
- `ux_rack_tasks_dispatch_key`
- `ux_rack_tasks_operation_sequence`
- `ux_rack_tasks_move_source_claim`
- `ix_rack_tasks_operation_status`
- `ix_rack_tasks_context_status`
- `ix_rack_tasks_target_status`

- [ ] **Step 3: 验证迁移引用**

Run:

```bash
rg -n "workline_outbox|workline_rack_tasks|WorklineOutbox|WorklineRackTask" migrations
```

Expected:

- 命令无输出。

## Task 9: 删除旧代码和死引用

**Files:**
- Delete old Workline outbox/rack files listed in File Structure.
- Delete old Handling outbox files listed in File Structure.
- Modify all affected tests.

- [ ] **Step 1: 删除旧文件**

Delete:

```text
src/app/workline/models/outbox.py
src/app/workline/repositories/outbox_repository.py
src/app/workline/models/rack_task.py
src/app/workline/repositories/rack_task_repository.py
src/app/workline/services/rack_operation_service.py
src/app/workline/services/rack_task_service.py
src/app/workline/services/rack_gateway.py
src/app/handling/models/outbox.py
src/app/handling/repositories/outbox_repository.py
src/app/handling/services/outbox_dispatcher.py
```

- [ ] **Step 2: 全仓搜索旧符号**

Run:

```bash
rg -n "WorklineOutbox|WorklineOutboxRepository|WorklineRackTask|WorklineRackTaskRepository|WorklineRackOperationService|WorklineRackTaskLifecycleService|workline_outbox|workline_rack_tasks" src tests migrations
```

Expected:

- 命令无输出。

- [ ] **Step 3: 运行导入检查**

Run:

```bash
uv run python -m py_compile \
  src/app/sys/models/outbox.py \
  src/app/sys/repositories/outbox_repository.py \
  src/app/sys/services/outbox_engine.py \
  src/app/rack/models/operation.py \
  src/app/rack/services/operation_service.py \
  src/app/rack/services/task_lifecycle_service.py
```

Expected:

- 命令成功，无输出。

## Task 10: 最终验证

**Files:**
- All changed files.

- [ ] **Step 1: 运行定向测试**

Run:

```bash
uv run pytest tests/sys tests/rack tests/handling tests/workline_runtime -q
```

Expected:

- 相关测试通过。

- [ ] **Step 2: 运行质量检查**

Run:

```bash
uv run ruff format .
uv run ruff check .
```

Expected:

- formatter 无异常。
- linter 无错误。

- [ ] **Step 3: 运行完整测试**

Run:

```bash
uv run pytest tests/ -q
```

Expected:

- 全量测试通过。

- [ ] **Step 4: 运行架构规则检查**

Run:

```bash
grep -r "from sqlalchemy import select" src/app/*/v1/ || true
grep -r "db.execute(" src/app/*/v1/ || true
rg -n "WorklineOutbox|WorklineRackTask|workline_outbox|workline_rack_tasks" src tests migrations
```

Expected:

- 前两个命令不出现 API 层直接访问数据库的新违规。
- 最后一个命令无输出。

- [ ] **Step 5: 运行 GitNexus 变更检测**

Run:

```text
mcp__gitnexus__.detect_changes(repo="wes_backend", scope="all")
```

Expected:

- 风险摘要与本计划范围一致。
- 若出现 HIGH 或 CRITICAL，暂停提交并复查对应流程。

## Public Interface Changes

Removed:

- `src.app.workline.models.WorklineOutbox`
- `src.app.workline.repositories.WorklineOutboxRepository`
- `src.app.workline.models.WorklineRackTask`
- `src.app.workline.repositories.WorklineRackTaskRepository`
- `src.app.workline.services.WorklineRackOperationService`
- `src.app.workline.services.WorklineRackTaskLifecycleService`
- `src.app.handling.models.SystemOutbox`
- `src.app.handling.repositories.SystemOutboxRepository`

Added:

- `src.app.sys.models.SystemOutbox`
- `src.app.sys.repositories.SystemOutboxRepository`
- `src.app.sys.services.SystemOutboxEngine`
- `src.app.sys.models.OperationCompletionPolicy`
- `src.app.rack.models.RackOperation`
- `src.app.rack.models.RackTask`
- `src.app.rack.services.RackOperationService`
- `src.app.rack.services.RackTaskLifecycleService`

Changed:

- 所有外部硬件系统出站调用统一写入 `system_outbox`。
- Rack operation 不再要求 Workline context。
- Handling/Rack operation 都持久化 `completion_policy`。
- Celery outbox dispatch 只有系统级一个入口。

## Commit Plan

建议分 5 个提交：

1. `test(sys,rack): cover system outbox and rack domain contracts`
2. `feat(sys): introduce unified system outbox engine`
3. `feat(rack): add system-level rack operation domain`
4. `refactor(workline,handling): migrate outbound calls to system outbox`
5. `refactor(rack,handling): model operation completion policy`

提交前必须确认：

```bash
git diff --check
uv run ruff check .
uv run pytest tests/ -q
```

## Assumptions

- 系统未发布，允许破坏性删除旧表、旧类型、旧任务名和旧 import。
- 不保留兼容别名。
- `SystemOutboxEngine` 是 WES 唯一出站消息底座。
- `CALLBACK_PLUS_RECONCILIATION` 本轮只完成字段、状态入口和结果标记。
- 非 Workline Rack operation 本轮不引入通用库位容量模型。
