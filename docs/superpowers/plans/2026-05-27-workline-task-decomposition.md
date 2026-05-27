# Celery Workline 任务拆分 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `src/celery_app/tasks/workline.py` 从 3800+ 行多职责任务文件拆成可单测、可演进的 Workline 后台应用服务；未发布系统不保留历史测试/脚本导入兼容，只保留真实运行合同。

**Architecture:** Celery task 只保留任务入口、DB session 生命周期和 retry facade。业务逻辑下沉到 `src/app/workline/services/`，sys outbox 通用派发能力留在 `src/app/sys/services/`。公开核心服务收敛为 `InboxBatchProcessor`、`OrchestratorWriteBackService`、`OutboxDispatchService`、`DeviceCommandGateway`；单调用方 helper 作为所属 service 私有实现，不额外导出。

**Tech Stack:** Python 3.13, Celery, SQLModel/SQLAlchemy AsyncSession, httpx, pytest/pytest-asyncio, ruff, GitNexus.

---

## Problem Analysis

`src/celery_app/tasks/workline.py` 当前承担过多职责，且 GitNexus 对该文件持续报 scope extraction failure。已按仓库规则执行 `rtk npx gitnexus analyze` 刷新索引，但该文件仍超出工具稳定建模能力。因此实现阶段必须结合 GitNexus impact 与 `rg` 本地调用链交叉确认。

当前职责拆分目标：

| 职责 | 当前位置 | 目标边界 |
|---|---:|---|
| Celery task facade / DB session 生命周期 | `WorklineTask`, `process_inbox_batch`, `scan_timeouts_batch`, `scan_device_heartbeats_batch`, `process_signal` | 保留在 `src/celery_app/tasks/workline.py` |
| Inbox 批处理 | `ProcessInboxMessages._process_batch` | 迁到 `InboxBatchProcessor.process_batch()`；删除旧内部类合同 |
| session/workline/device/command 解析 | `_load_related_entities` 及相关 helper | 作为 `InboxBatchProcessor` 私有 helper，不导出公共 service |
| 编排结果写回 | `_apply_orchestrator_effects` 及 effect helper | 迁到 `OrchestratorWriteBackService.apply()` |
| 诊断/timeline 辅助 | `_record_diagnostic`, `_emit_timeline`, `_record_*_timeline` | 保留稳定 builder/service 复用；迁移到所属 service 私有 helper |
| WORKLINE outbox 派发 | `OutboxDispatcher._dispatch` | 迁到 `OutboxDispatchService.dispatch()`；删除旧内部类合同 |
| 设备命令通信 | `_dispatch_device_command`, `_reserve_sandbox_device_command`, governance helper | 迁到 `DeviceCommandGateway` |
| device busy repair | `_repair_orphaned_device_busy_dispatches`, `_repair_self_blocked_device_busy_dispatches` | 作为 `OutboxDispatchService` 私有 repair helper，不导出公共 service |
| external/internal outbox delivery | `SystemOutboxEngine` 与 `OutboxDispatcher` 双向 lazy import | 抽到 sys-owned delivery helper，避免循环依赖和重复规则 |

关键约束：

- 不保留 `ProcessInboxMessages`、`OutboxDispatcher`、`process_inbox_messages` 等历史内部 alias/wrapper。
- 保留真实运行合同：`process_inbox_batch`、`scan_timeouts_batch`、`scan_device_heartbeats_batch`、`process_signal` 的 Celery task name 不变。
- `dispatch_system_outbox_batch` 仍是唯一 outbox Celery 入口；`dispatch_outbox_batch` 不恢复。
- API 层不参与本次变更；不新增路由、不改迁移、不改外部业务 API 合同。
- 单个 batch 内保持顺序处理；不得用共享 `AsyncSession` 做 `asyncio.gather` 并发。

## Target Flow

```text
Inbox worker
  process_inbox_batch (Celery task name unchanged)
      |
      v
  InboxBatchProcessor
      |-- private entity loading helpers
      |-- OrchestratorService.process_inbox(write_callback=...)
      |-- rollback-safe diagnostic snapshot helpers
      |
      v
  OrchestratorWriteBackService
      |-- RuntimeIntentEffectApplier
      |-- command/outbox/timeline/session projection
      '-- deferred SSE publish after commit

Outbox worker
  dispatch_system_outbox_batch (only outbox Celery entrypoint)
      |
      v
  SystemOutboxEngine
      |-- injected workline_domain_dispatcher
      |-- injected device_command_dispatcher
      '-- sys outbox delivery helper for non-WORKLINE external/internal
              |
              v
  OutboxDispatchService (WORKLINE domain)
      |-- private repair helpers
      |-- safety guards / claim / attempt ledger / fencing
      |-- DeviceCommandGateway
      '-- sys outbox delivery helper for WORKLINE external/internal
```

## Target File Structure

| 文件 | 操作 | 职责 |
|---|---|---|
| `src/celery_app/tasks/workline.py` | Modify | 只保留 Celery task facade、`WorklineTask`、timeout/heartbeat/signal 入口和必要纯 helper；删除旧内部类 facade |
| `src/app/sys/services/outbox_engine.py` | Modify | 增加 `device_command_dispatcher` 显式依赖注入；WORKLINE 域和设备命令委托都可替换 |
| `src/app/sys/services/outbox_delivery.py` | Create | sys-owned external HTTP / internal signal delivery helper，单源维护 endpoint allowlist 和 signal target 规则 |
| `src/app/sys/services/__init__.py` | Modify | 仅导出需要被其它模块复用的 sys outbox 类型/单例 |
| `src/app/workline/services/inbox_batch_processor.py` | Create | `InboxBatchProcessor.process_batch(db, limit)`；负责 inbox claim、payload gate、特殊事件分流、orchestrator 调用、write callback 和结果统计 |
| `src/app/workline/services/orchestrator_write_back_service.py` | Create | `OrchestratorWriteBackService.apply(...)`；负责 orchestrator result 到 session/command/outbox/timeline 的原子写回 |
| `src/app/workline/services/outbox_dispatch_service.py` | Create | `OutboxDispatchService.dispatch(db, limit)`；负责 WORKLINE outbox repair、safety guard、claim、attempt ledger、终态更新、诊断和 SSE |
| `src/app/workline/services/device_command_gateway.py` | Create | `DeviceCommandGateway.dispatch(db, outbox)` 与 `reserve_sandbox_command(db, outbox)`；负责设备治理、HTTP ACK、设备运行态投影 |
| `src/app/workline/services/__init__.py` | Modify | 只导出四个核心新 service 类与单例，避免单调用方 helper 公共化 |
| `tests/workline_runtime/test_inbox_batch_processor.py` | Create | Inbox 批处理服务测试，覆盖当前 `_process_batch` 核心分支 |
| `tests/workline_runtime/test_orchestrator_write_back_service.py` | Create | 编排写回服务测试，迁移 `test_runtime_intent_effects.py` 中和 task 文件耦合的断言 |
| `tests/workline_runtime/test_outbox_dispatch_service.py` | Create | WORKLINE outbox 派发服务测试，迁移旧 dispatcher 行为断言 |
| `tests/workline_runtime/test_device_command_gateway.py` | Create | 设备 HTTP/sandbox/governance/ACK 投影测试 |
| `tests/workline_runtime/test_celery_task_entrypoints.py` | Modify | 验证真实 Celery task name 不变、旧内部 facade 不再是合同 |
| `tests/sys/test_system_outbox_engine.py` | Modify | 验证 `SystemOutboxEngine` 的 WORKLINE 和 device dispatcher 显式注入 |
| `tests/sys/test_outbox_delivery.py` | Create | 验证 sys delivery helper 的 external/internal 成功与失败路径 |
| `docs/workline_runtime_workflow_guide.md` 或对应运行文档 | Modify if present | 说明新的后台流：Celery facade -> application service -> repository/gateway |

## What Already Exists

- `SystemOutboxEngine` 已有 `workline_domain_dispatcher` 注入；本次按同一模式补 `device_command_dispatcher`，不要再 patch `src.celery_app.tasks.workline` 私有路径。
- `SystemOutboxRepository.get_pending_messages()` 已支持 WORKLINE include/exclude filters 和设备 FIFO；复用它，不重写 claim selection。
- `EffectApplyContext` 和 `_build_effect_apply_context()` 已经把 write-back 状态集中在一个上下文形状里；迁移该形状，不引入更重执行框架。
- 现有 `tests/workline_runtime/test_outbox_dispatcher.py` 与 `test_runtime_intent_effects.py` 已覆盖大量历史行为；迁移断言到新 service seam，不删除行为覆盖。

## Implementation Tasks

### Task 1: 建立 clean-v1 测试保护网

**Files:**

- Modify: `tests/workline_runtime/test_celery_task_entrypoints.py`
- Create: `tests/workline_runtime/test_inbox_batch_processor.py`
- Create: `tests/workline_runtime/test_outbox_dispatch_service.py`
- Create: `tests/workline_runtime/test_device_command_gateway.py`
- Create: `tests/sys/test_outbox_delivery.py`
- Modify: `tests/sys/test_system_outbox_engine.py`

- [ ] **Step 1: 补 Celery facade 合同测试**
  - 验证 `process_inbox_batch` 仍注册为 `src.celery_app.tasks.workline.process_inbox_batch`。
  - 验证 `dispatch_system_outbox_batch` 仍是唯一 outbox batch task。
  - 验证 `dispatch_outbox_batch` 不存在。
  - 验证旧 `ProcessInboxMessages` / `OutboxDispatcher` 不再是测试或运行合同。

- [ ] **Step 2: 补核心 service 目标契约测试**
  - `InboxBatchProcessor.process_batch(limit=0)` 返回空统计，不触发 repository。
  - `OutboxDispatchService.dispatch(limit=0)` 返回空统计，不触发 repair 或 repository。
  - `DeviceCommandGateway.dispatch()` 对缺失设备通信配置返回失败，不写 sent。
  - `SystemOutboxEngine` 可注入 `workline_domain_dispatcher` 和 `device_command_dispatcher`。

- [ ] **Step 3: 补 sys delivery helper 测试**
  - external HTTP endpoint resolve 成功/失败。
  - HTTP sender 返回失败。
  - internal signal target allowlist 通过/拒绝。
  - `celery_app.send_task` 抛异常时返回失败并记录日志。

- [ ] **Step 4: 运行基线测试**
  - Run: `uv run pytest tests/workline_runtime/test_celery_task_entrypoints.py tests/sys/test_system_outbox_engine.py -q`
  - Expected: 当前代码可能因新 clean-v1 断言失败；记录失败点，后续任务逐步修正。

- [ ] **Step 5: 提交**
  - Commit: `test(workline): 增加任务拆分 clean v1 保护网`

### Task 2: 抽 sys outbox delivery helper 与显式注入边界

**Files:**

- Create: `src/app/sys/services/outbox_delivery.py`
- Modify: `src/app/sys/services/outbox_engine.py`
- Modify: `src/app/sys/services/__init__.py`
- Modify: `tests/sys/test_outbox_delivery.py`
- Modify: `tests/sys/test_system_outbox_engine.py`

- [ ] **Step 1: 执行 GitNexus impact**
  - Run: `gitnexus_impact(target="SystemOutboxEngine", direction="upstream", file_path="src/app/sys/services/outbox_engine.py", includeTests=true)`
  - Expected: 记录 direct callers、affected modules、risk；若 HIGH/CRITICAL，先暂停复核调用方。

- [ ] **Step 2: 提取 delivery helper**
  - 将 `dispatch_external_http` 和 `dispatch_internal_signal` 的发送规则移到 sys-owned helper。
  - `SystemOutboxEngine` 和后续 `OutboxDispatchService` 都依赖 helper，不互相导入。
  - endpoint registry、allowed internal signals、payload dict 规则保持单源。

- [ ] **Step 3: 增加 device command dispatcher 注入**
  - `SystemOutboxEngine.__init__` 增加 `device_command_dispatcher` 参数。
  - `dispatch_device_command()` 调用注入依赖，默认指向 `DeviceCommandGateway.dispatch`。
  - 测试必须用 fake dispatcher 注入，不再 patch `src.celery_app.tasks.workline.OutboxDispatcher`。

- [ ] **Step 4: 验证**
  - Run: `uv run pytest tests/sys/test_outbox_delivery.py tests/sys/test_system_outbox_engine.py -q`
  - Run: `uv run ruff check src/app/sys/services/outbox_delivery.py src/app/sys/services/outbox_engine.py tests/sys`

- [ ] **Step 5: 提交**
  - Commit: `refactor(sys): 抽出 outbox 通用派发 helper`

### Task 3: 抽 OrchestratorWriteBackService

**Files:**

- Create: `src/app/workline/services/orchestrator_write_back_service.py`
- Modify: `src/app/workline/services/__init__.py`
- Modify: `src/celery_app/tasks/workline.py`
- Create/Modify: `tests/workline_runtime/test_orchestrator_write_back_service.py`
- Modify: `tests/workline_runtime/test_runtime_intent_effects.py`

- [ ] **Step 1: 锁定写回语义**
  - 覆盖 context patch、trace fields、`RuntimeIntentEffectApplier` 调用、timeline sequence、command/outbox 投影、failure/cancel/complete/wait/fallback 状态转换。
  - 覆盖 stale session snapshot 拒绝写入。

- [ ] **Step 2: 执行 GitNexus impact**
  - Run: `gitnexus_impact(target="_apply_orchestrator_effects", direction="upstream", file_path="src/celery_app/tasks/workline.py", includeTests=true)`
  - Run: `gitnexus_impact(target="_emit_timeline", direction="upstream", file_path="src/celery_app/tasks/workline.py", includeTests=true)`
  - Expected: 如果 GitNexus 仍返回 LOW/0 callers，必须用 `rg "_apply_orchestrator_effects|_emit_timeline"` 交叉确认。

- [ ] **Step 3: 创建 write-back service**
  - 公共方法：`apply(db, session, workline, inbox, devices_by_role, source_device, orch_result)`。
  - 迁移 `EffectApplyContext`、`_build_effect_apply_context` 和相关 effect helper。
  - 删除旧 `_apply_orchestrator_effects` 公共 wrapper 依赖；若 task 内仍需私有函数，命名为模块私有且不导出。

- [ ] **Step 4: 导出核心 service**
  - `src/app/workline/services/__init__.py` 导出 `OrchestratorWriteBackService` 和 `orchestrator_write_back_service`。

- [ ] **Step 5: 验证**
  - Run: `uv run pytest tests/workline_runtime/test_orchestrator_write_back_service.py tests/workline_runtime/test_runtime_intent_effects.py -q`
  - Run: `uv run ruff check src/app/workline/services/orchestrator_write_back_service.py src/celery_app/tasks/workline.py tests/workline_runtime/test_orchestrator_write_back_service.py`

- [ ] **Step 6: 提交**
  - Commit: `refactor(workline): 拆分编排写回服务`

### Task 4: 抽 InboxBatchProcessor

**Files:**

- Create: `src/app/workline/services/inbox_batch_processor.py`
- Modify: `src/app/workline/services/__init__.py`
- Modify: `src/celery_app/tasks/workline.py`
- Modify: `tests/workline_runtime/test_inbox_batch_processor.py`
- Modify: `tests/integration/workline_runtime/test_process_inbox_real_entry_integration.py`

- [ ] **Step 1: 补核心分支测试**
  - 覆盖：空 `SCAN_COMPLETED` payload 失败、`ESTOP_PRESSED` 分流、`TIMER_TIMEOUT` 分流、session/workline 缺失诊断、重复入口归档、迟到命令结果归档、orchestrator success 写回、orchestrator failure 诊断。
  - 覆盖：`SessionResolveError`、`WorkLineSafetyBlocked`、`TimeoutError`、generic `Exception` rollback 后诊断快照与失败补记。
  - 覆盖：write callback stale guard、deferred SSE publish。

- [ ] **Step 2: 执行 GitNexus impact**
  - Run: `gitnexus_impact(target="_process_batch", direction="upstream", file_path="src/celery_app/tasks/workline.py", includeTests=true)`
  - Expected: 记录 direct callers、affected processes、risk；若工具结果为空，用 `rg "_process_batch|ProcessInboxMessages"` 交叉确认。

- [ ] **Step 3: 创建 `InboxBatchProcessor`**
  - 公共方法：`process_batch(db, limit=10) -> ProcessResult`。
  - 构造依赖：`write_back_service` 和必要 service/repository；entity loading 与 diagnostic snapshot 保持为私有 helper。
  - `process_inbox_batch` Celery task 直接调用 `inbox_batch_processor.process_batch`。
  - 删除旧 `ProcessInboxMessages` 类、`process_inbox_messages` alias 和相关测试导入合同。

- [ ] **Step 4: 导出核心 service**
  - `src/app/workline/services/__init__.py` 导出 `InboxBatchProcessor` 和 `inbox_batch_processor`。
  - 不导出 `RuntimeEntityLoader` / `RuntimeDiagnosticWriter`。

- [ ] **Step 5: 验证**
  - Run: `uv run pytest tests/workline_runtime/test_inbox_batch_processor.py tests/workline_runtime/test_celery_task_entrypoints.py tests/integration/workline_runtime/test_process_inbox_real_entry_integration.py -q`
  - Run: `uv run ruff check src/app/workline/services/inbox_batch_processor.py src/celery_app/tasks/workline.py tests/workline_runtime/test_inbox_batch_processor.py`

- [ ] **Step 6: 提交**
  - Commit: `refactor(workline): 拆分 inbox 批处理服务`

### Task 5: 抽 DeviceCommandGateway

**Files:**

- Create: `src/app/workline/services/device_command_gateway.py`
- Modify: `src/app/workline/services/__init__.py`
- Modify: `src/celery_app/tasks/workline.py`
- Modify: `src/app/sys/services/outbox_engine.py`
- Modify: `tests/workline_runtime/test_device_command_gateway.py`
- Modify: `tests/sys/test_system_outbox_engine.py`

- [ ] **Step 1: 迁移设备命令测试**
  - 从旧 dispatcher 测试迁移 HTTP 200 ACK、HTTP error body 日志、timeout 抛 `OUTBOX_ACK_TIMEOUT`、callback path、default path、maintenance reject、unsupported command type、busy device、same running command、sandbox reserve、sandbox duplicate sent。

- [ ] **Step 2: 执行 GitNexus impact**
  - Run: `gitnexus_impact(target="_dispatch_device_command", direction="upstream", file_path="src/celery_app/tasks/workline.py", includeTests=true)`
  - Run: `gitnexus_impact(target="_enforce_device_command_governance", direction="upstream", file_path="src/celery_app/tasks/workline.py", includeTests=true)`
  - Expected: GitNexus 结果必须与 `rg "_dispatch_device_command|_enforce_device_command_governance"` 交叉确认。

- [ ] **Step 3: 创建 gateway**
  - 公共方法：`dispatch(db, outbox) -> bool`。
  - 公共方法：`reserve_sandbox_command(db, outbox) -> bool`。
  - 迁移 payload 构造、设备治理、URL 解析、httpx ACK、DeviceCommand/Device runtime 投影。
  - 不保留 `OutboxDispatcher._dispatch_device_command` wrapper。

- [ ] **Step 4: 接入 `SystemOutboxEngine`**
  - 默认 `device_command_dispatcher` 指向 `device_command_gateway.dispatch`。
  - sys 测试通过 fake dispatcher 验证委托，不 patch workline task 内部类。

- [ ] **Step 5: 导出核心 service**
  - `src/app/workline/services/__init__.py` 导出 `DeviceCommandGateway` 和 `device_command_gateway`。

- [ ] **Step 6: 验证**
  - Run: `uv run pytest tests/workline_runtime/test_device_command_gateway.py tests/sys/test_system_outbox_engine.py -q`
  - Run: `uv run ruff check src/app/workline/services/device_command_gateway.py src/app/sys/services/outbox_engine.py src/celery_app/tasks/workline.py tests/workline_runtime/test_device_command_gateway.py`

- [ ] **Step 7: 提交**
  - Commit: `refactor(workline): 拆分设备命令网关`

### Task 6: 抽 OutboxDispatchService

**Files:**

- Create: `src/app/workline/services/outbox_dispatch_service.py`
- Modify: `src/app/workline/services/__init__.py`
- Modify: `src/celery_app/tasks/workline.py`
- Modify: `src/app/sys/services/outbox_engine.py`
- Modify: `tests/workline_runtime/test_outbox_dispatch_service.py`
- Modify: `tests/workline_runtime/test_device_command_gateway.py`
- Modify: `tests/sys/test_system_outbox_engine.py`

- [ ] **Step 1: 迁移 WORKLINE outbox 主流程测试**
  - 覆盖 operation_domain filter、无消息、claim fencing、workline safety 三段 guard、attempt ledger success/failure、physical success 但 mark sent fenced、physical failure/exception 后诊断、max retries 后 reconciliation、blocked device busy、SSE deferred publish。
  - 覆盖 private repair helper：orphaned device busy、self-blocked device busy、repository 不支持 getter 时安全返回 0。

- [ ] **Step 2: 执行 GitNexus impact**
  - Run: `gitnexus_impact(target="_dispatch", direction="upstream", file_path="src/celery_app/tasks/workline.py", includeTests=true)`
  - Run: `gitnexus_impact(target="SystemOutboxEngine.dispatch", direction="upstream", file_path="src/app/sys/services/outbox_engine.py", includeTests=true)`
  - Expected: `SystemOutboxEngine` impact 已知为 MEDIUM 级导入扩散；实现前记录直接影响文件。

- [ ] **Step 3: 创建 dispatch service**
  - 公共方法：`dispatch(db, limit=50) -> DispatchResult`。
  - 构造依赖：`outbox_repository`、`device_gateway`、`attempt_service`、`safety_service`、`delivery_helper`。
  - repair helper 留在该 service 内部，不导出 `RuntimeRepairService`。
  - external/internal dispatch 调用 sys-owned delivery helper，不导入 `system_outbox_engine`。
  - 单 batch 内顺序处理；不得引入 `asyncio.gather`。

- [ ] **Step 4: 修改委托链**
  - `SystemOutboxEngine._dispatch_workline_domain` 默认调用 `outbox_dispatch_service.dispatch(db, limit)`。
  - 删除旧 `OutboxDispatcher` 类及其 wrapper。

- [ ] **Step 5: 导出核心 service**
  - `src/app/workline/services/__init__.py` 导出 `OutboxDispatchService` 和 `outbox_dispatch_service`。

- [ ] **Step 6: 验证**
  - Run: `uv run pytest tests/workline_runtime/test_outbox_dispatch_service.py tests/workline_runtime/test_device_command_gateway.py tests/sys/test_system_outbox_engine.py -q`
  - Run: `uv run ruff check src/app/workline/services/outbox_dispatch_service.py src/app/sys/services/outbox_engine.py src/celery_app/tasks/workline.py tests/workline_runtime/test_outbox_dispatch_service.py`

- [ ] **Step 7: 提交**
  - Commit: `refactor(workline): 拆分 outbox 派发服务`

### Task 7: 清理 task 文件、文档和后续 TODO

**Files:**

- Modify: `src/celery_app/tasks/workline.py`
- Modify: `src/app/workline/services/__init__.py`
- Modify: `tests/workline_runtime/test_celery_task_entrypoints.py`
- Modify: `docs/workline_runtime_workflow_guide.md` 或相关 Workline 运行文档（若存在对应章节）
- Modify: `TODOS.md`

- [ ] **Step 1: 删除已迁移的大块实现**
  - `workline.py` 保留：module docstring、imports、`WorklineTask`、`process_inbox_batch`、`scan_timeouts_batch`、`scan_device_heartbeats_batch`、`process_signal`、必要常量和仍被多处复用的纯 helper。
  - 删除 `ProcessInboxMessages`、`OutboxDispatcher`、`process_inbox_messages` 等旧内部合同。
  - 删除已迁出且没有真实调用方的 helper，避免双源维护。

- [ ] **Step 2: 更新运行文档**
  - 说明 Workline 后台流：Celery facade -> application service -> repository/gateway。
  - 明确 `dispatch_system_outbox_batch` 是唯一 outbox Celery 入口。
  - 说明 batch 内顺序处理策略和禁止共享 `AsyncSession` 并发处理的原因。

- [ ] **Step 3: 记录后续 TODO**
  - 在 `TODOS.md` 增加 P2：Workline worker 吞吐 benchmark 与队列/连接策略调优。
  - Context 必须写明：本次选择顺序 batch 保护事务/fencing；吞吐优化需在拆分落地后基于真实数据评估 worker concurrency、队列隔离、batch limit、HTTP client strategy。

- [ ] **Step 4: 架构检查**
  - Run: `grep -r "from sqlalchemy import select" src/app/*/v1/`
  - Run: `grep -r "db.execute(" src/app/*/v1/`
  - Expected: 不因本次任务新增 API 层 DB 访问。

- [ ] **Step 5: GitNexus 变更检测**
  - Run: `gitnexus_detect_changes(scope="all")`
  - Expected: 受影响符号集中在 Workline Celery facade、Workline services、SystemOutboxEngine/sys delivery 和对应 tests；若出现 API 层或无关域，停止复核。

- [ ] **Step 6: 最终验证**
  - Run: `uv run pytest tests/workline_runtime/ tests/sys/test_system_outbox_engine.py tests/sys/test_outbox_delivery.py tests/integration/workline_runtime/ -q`
  - Run: `./scripts/git-quality-gate.sh --profile quality`
  - Expected: PASS；若存在历史无关失败，记录失败用例、原因和是否与本次 diff 有关。

- [ ] **Step 7: 提交**
  - Commit: `refactor(workline): 收敛 celery task facade`

## Acceptance Criteria

- `src/celery_app/tasks/workline.py` 只保留 task facade、运行入口和必要纯 helper；业务主流程不再落在 Celery task 文件中。
- `ProcessInboxMessages`、`OutboxDispatcher`、`process_inbox_messages` 不再作为合同存在；测试不再 patch 这些旧路径。
- `process_inbox_batch`、`scan_timeouts_batch`、`scan_device_heartbeats_batch`、`process_signal` 的 Celery task name 不变。
- `dispatch_system_outbox_batch` 仍是唯一 outbox Celery 入口；`dispatch_outbox_batch` 不恢复。
- `SystemOutboxEngine` 可显式注入 `workline_domain_dispatcher` 和 `device_command_dispatcher`。
- external HTTP / internal signal delivery 由 sys-owned helper 单源维护。
- WORKLINE 设备命令 ACK、sandbox reserve、device busy block、reconciliation exhausted、diagnostic、deferred SSE 行为与迁移前一致。
- 新 service 只导出四个核心服务；单调用方 helper 不进入 `src/app/workline/services/__init__.py` 公共面。
- 单 batch 内顺序处理；不得使用共享 `AsyncSession` 并发处理 inbox/outbox 消息。

## Verification Matrix

| 验证 | 命令 | 通过标准 |
|---|---|---|
| Celery entrypoints | `uv run pytest tests/workline_runtime/test_celery_task_entrypoints.py tests/integration/workline_runtime/test_celery_eager_registration_smoke_integration.py -q` | task name 不变，旧 outbox task 不恢复 |
| Sys outbox boundary | `uv run pytest tests/sys/test_system_outbox_engine.py tests/sys/test_outbox_delivery.py -q` | 注入边界和 delivery helper PASS |
| Inbox service | `uv run pytest tests/workline_runtime/test_inbox_batch_processor.py -q` | PASS |
| Write-back service | `uv run pytest tests/workline_runtime/test_orchestrator_write_back_service.py tests/workline_runtime/test_runtime_intent_effects.py -q` | PASS |
| Device gateway | `uv run pytest tests/workline_runtime/test_device_command_gateway.py -q` | PASS |
| Outbox service | `uv run pytest tests/workline_runtime/test_outbox_dispatch_service.py -q` | PASS |
| Integration smoke | `uv run pytest tests/integration/workline_runtime/ -q` | PASS |
| Full Workline regression | `uv run pytest tests/workline_runtime/ tests/sys/test_system_outbox_engine.py tests/sys/test_outbox_delivery.py tests/integration/workline_runtime/ -q` | PASS |
| Quality gate | `./scripts/git-quality-gate.sh --profile quality` | PASS |
| GitNexus scope | `gitnexus_detect_changes(scope="all")` | 只影响预期 Workline/Sys outbox 符号和测试 |

## NOT in Scope

- 不修改数据库 schema，不新增 Alembic migration。
- 不重写 OrchestratorService 或 RuntimeIntentEffectApplier。
- 不改变 callback API、设备协议、outbox payload shape、dispatch_key/idempotency 规则。
- 不把 sys 非 WORKLINE outbox 派发逻辑迁入 workline 域。
- 不做 batch-internal `asyncio.gather`、per-message independent session redesign、HTTP connection-pool redesign、Celery queue tuning。
- 不做 worker throughput benchmark/tuning；只在 `TODOS.md` 记录后续项。

## Failure Modes

| Failure mode | Plan coverage |
|---|---|
| rollback 后 ORM entity expire，诊断读取触发 MissingGreenlet | `InboxBatchProcessor` 必须保留 snapshot-before-rollback 测试 |
| write callback 执行前 session 状态已变 | `OrchestratorWriteBackService` 必须拒绝 stale snapshot，不覆盖新状态 |
| 设备 ACK 返回前已收到完成回调 | `DeviceCommandGateway` 必须刷新 command 并避免把终态覆盖回 RUNNING |
| outbox 物理派发成功但 mark sent 被 fencing 拒绝 | `OutboxDispatchService` 必须计入 skipped 并保留安全终态 |
| device busy blocked/outbox repair 漏处理 | `OutboxDispatchService` private repair helper 必须覆盖 orphaned/self-blocked 两类 |
| 共享 `AsyncSession` 并发处理消息 | 明确禁止 batch 内 `asyncio.gather`，吞吐优化另开 TODO |

Critical silent gaps: none accepted; above failure modes都必须有测试或显式错误处理。

## Worktree Parallelization

| Step | Modules touched | Depends on |
|------|----------------|------------|
| Sys outbox boundary + delivery helper | `src/app/sys/services`, `tests/sys` | — |
| Write-back extraction | `src/celery_app/tasks`, `src/app/workline/services`, `tests/workline_runtime` | Task 1 |
| Inbox extraction | `src/celery_app/tasks`, `src/app/workline/services`, `tests/workline_runtime`, integration tests | Task 3 |
| Device gateway extraction | `src/celery_app/tasks`, `src/app/workline/services`, `src/app/sys/services`, `tests/workline_runtime`, `tests/sys` | Task 2 |
| Outbox dispatch extraction | `src/celery_app/tasks`, `src/app/workline/services`, `src/app/sys/services`, `tests/workline_runtime`, `tests/sys` | Task 2, Task 5 |
| Final cleanup/regression | task/docs/TODO/tests | All extraction tasks |

Parallel lanes:

- Lane A: Sys outbox boundary + delivery helper，可优先独立实施。
- Lane B: Write-back -> Inbox，顺序实施，因为共享 `workline.py` inbox/write callback 上下文。
- Lane C: Device gateway -> Outbox dispatch，Task 2 完成后实施；如与 Lane B 同时改 `workline.py`，需要 worktree merge 协调。
- Final lane: cleanup, docs, TODO, full regression。

Recommended order: Task 1 -> Task 2；之后 Lane B 与 Lane C 可在独立 worktree 并行，最终 Task 7 收口。

## Implementation Tasks Summary

- [ ] **T1 (P1, human: ~1h / CC: ~20min)** — scope — 改为 clean v1，不保留旧 facade 兼容。
- [ ] **T2 (P1, human: ~30min / CC: ~10min)** — sys outbox — 为 `SystemOutboxEngine` 增加 device command dispatcher 注入。
- [ ] **T3 (P1, human: ~45min / CC: ~15min)** — sys delivery — 提取 sys-owned external/internal delivery helper。
- [ ] **T4 (P2, human: ~1h / CC: ~20min)** — workline services — 折回单调用方 helper，避免 service 爆炸。
- [ ] **T5 (P1, human: ~1-2h / CC: ~20-40min)** — tests — 升级 clean-v1 测试和完整 Workline 回归。
- [ ] **T6 (P1, human: ~10min / CC: ~3min)** — concurrency — 记录 batch 内顺序处理策略。
- [ ] **T7 (P3, human: ~10min / CC: ~3min)** — follow-up — 记录 Workline worker 吞吐 benchmark/tuning TODO。

## Self-Review

- Spec coverage: 已覆盖评审后 clean v1、四个核心 service、sys delivery helper、注入边界、完整回归、后续性能 TODO。
- Placeholder scan: 无 TBD/TODO 占位；所有任务有文件、步骤、验证命令和提交说明。
- Type/name consistency: 服务名、文件名、单例名在 Target File Structure、Implementation Tasks、Acceptance Criteria 中一致。
- Repo rules: 计划不粘贴完整类/函数/测试实现；新增 service 导出范围收敛；每个被迁移符号都有 GitNexus impact 或交叉确认要求；提交说明使用中文 Conventional Commit subject。

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | — |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR | 33 issues/gaps, 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

- **UNRESOLVED:** 0
- **VERDICT:** ENG CLEARED — ready to implement.
