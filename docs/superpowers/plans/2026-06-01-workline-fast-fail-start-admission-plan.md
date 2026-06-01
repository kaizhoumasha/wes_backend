# Workline Fast-Fail START Admission Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现工作线 `STOPPED -> START admission -> READY`、生产事件同步 fast-fail、command 前实时设备 status 校验、dev mock 真实 HTTP 调试链路，以及前端 STOPPED/START 运行态合同。

**Architecture:** 后端严格保持 `API Layer -> Service Layer -> Repository Layer -> Database`。Callback API 只负责 HTTP 协议状态包装，业务准入、拒收、diagnostic 和状态变更全部落在 Service 层；START 准入使用两阶段 CAS，禁止持有 DB 行锁等待 ECS HTTP。前端只消费 runtime summary/detail 稳定字段，不追 callback logs。

**Tech Stack:** Python 3.13, FastAPI, SQLModel/SQLAlchemy AsyncSession, Alembic, httpx, pytest, Ruff, Vue 3 + TypeScript in `../wes_frontend`.

---

## Scope Check

本 SPEC 横跨后端 runtime、callback ingress、device dispatch、dev mock、前端 runtime UI 和 `TODOS.md` follow-up。用户已选择单一总 PLAN，因为这些变更需要同批交付才能避免后端语义切换后前端仍显示“稳定/已恢复接收”。

执行时可以分 lane，但不要把功能拆成多个不一致的 PR：后端 `STOPPED/START/409/status` 合同和前端 STOPPED/START 展示必须一起验收。

## Implementation Guardrails

- 修改任何函数、类或方法前，先按项目要求运行 GitNexus impact，例如 `gitnexus_impact({target: "WorkLineSafetyService", direction: "upstream"})`；如果 HIGH/CRITICAL，先向用户确认。
- API 层不得直接访问 DB 或 repository；callback route 只设置 HTTP status / JSONResponse，并委托 service。
- 新 service 必须在对应 `__init__.py` 导出。
- 时间字段遵守项目 timezone 规则：DB 写入使用 `timezone.now_for_db()`，API aware ISO 由既有响应模型处理。
- Alembic migration 必须用 `uv run alembic revision -m "<message>"` 生成 revision ID 后再编辑，不手写模板 revision。
- 文档和提交信息使用中文；计划文档不粘贴完整类、完整函数或大段测试代码。

## File Structure

### Backend Runtime State

- Modify `src/app/workline/models/safety.py`: 增加 `WorkLineRuntimeStatus.STOPPED`。
- Modify `src/app/workline/models/workline.py`: runtime 默认值改为 `STOPPED`，增加 START admission 投影字段。
- Modify `src/app/workline/models/runtime.py`: runtime summary/detail response 增加 START admission 字段。
- Modify `src/app/workline/services/safety_service.py`: clear-estop 后返回 `STOPPED`。
- Modify `src/app/workline/services/runtime_hold_release_service.py`: resolve/release 后最终状态回 `STOPPED`。
- Modify `src/app/workline/services/runtime_reconciliation_service.py` if needed: 确认 reconciliation release 不会直接写 `READY`。
- Create Alembic migration under `migrations/versions/`: 增加 STOPPED CHECK/enum value 与 START admission columns。

### Event Classification And Callback Ingress

- Modify `src/workline_runtime/runtime_events.py`: 平台控制/安全/生产事件分类 helpers。
- Modify `src/app/callback/models/ingress_response.py`: 如果现有响应模型不足，补充 service 到 route 的 business body + http status 表达。
- Modify `src/app/callback/services/callback_ingress_service.py`: START、ESTOP、生产事件分流；生产事件非 READY 时拒收。
- Modify `src/app/callback/services/callback_orchestration_service.py`: 确认只有 accepted production event 才创建 `workline_inbox`。
- Modify `src/app/callback/v1/callback.py`: 根据 service 决策返回真实 HTTP 409。

### START Admission

- Create `src/app/workline/services/start_admission_service.py`: START 准入主服务，包含两阶段 CAS、shared precheck、ECS batch status、diagnostic snapshot。
- Modify `src/app/workline/services/__init__.py`: 导出 `WorkLineStartAdmissionService` 和 singleton。
- Modify `src/app/workline/services/workline_service.py`: `configuration_status()` 补 host/port/path 通信配置检查，并供 START admission 复用。
- Modify `src/app/workline/services/runtime_query_service.py`: runtime summary/detail 暴露 START admission projection。

### Device Status Before Command

- Modify `src/app/workline/services/device_command_gateway.py`: DB projection 后、command POST 前做 single-device realtime status GET。
- Add small helper in same file or a focused new service only if needed: timeout clamp、status response parsing、operation-scoped client request helpers。
- Do not add global HTTP client pool in this PR。

### Dev Mock And Seed Data

- Modify `tests/mock/ecs_mock_server.py`: `/api/v1/device/status` 支持带 `device_code` 单设备和不带 `device_code` 批量返回。
- Modify `tests/mock/ecs_mock_catalog.py` if status catalog currently lacks mode/status/current command shape.
- Modify `scripts/data/sync_test_workline_devices.py`: rough sorter dev 工作线 `run_mode=AUTO`、设备 host 指向 `mock_ecs:8010`、初始状态 `STOPPED`。
- Modify `tests/mock/test_ecs_mock_server.py` and `tests/scripts/test_sync_test_workline_devices.py`。

### Frontend Contract In `../wes_frontend`

- Modify `../wes_frontend/src/constants/runtime-safety.ts`: 增加 `STOPPED_RUNTIME_STATUS` 和 platform control event filtering constant。
- Modify `../wes_frontend/src/types/runtime.ts`: 增加 START admission 字段。
- Modify generated OpenAPI metadata only through the repo’s existing generation path if required; do not hand-edit generated files unless that repo’s workflow expects it.
- Modify `../wes_frontend/src/utils/runtime-display.ts`: STOPPED risk label/tone。
- Modify `../wes_frontend/src/utils/runtime-safety.ts`: STOPPED verdict、blocked reason、non-safety locked semantics。
- Modify `../wes_frontend/src/components/runtime/devices/DecisionStrip.vue`: STOPPED 主信号、START admission 状态、dev/mock START action slot/entry。
- Modify `../wes_frontend/src/views/runtime/worklines/WorklineMonitorPage.vue`: clear-estop 文案和 STOPPED detail refresh。
- Modify `../wes_frontend/src/views/runtime/sandbox/SandboxWorkbenchPage.vue`: clear-estop 文案、dev/mock START 按钮入口、composer disabled reason。
- Modify `../wes_frontend/src/components/runtime/sandbox/SandboxEventComposer.vue`: 过滤 `WORKLINE_START_REQUESTED`，非 READY 禁用生产事件且显示可见原因。

### Follow-up Documentation

- Modify `TODOS.md`: 更新现有 P2 “Workline worker 吞吐 benchmark 与队列/连接策略调优”，加入 ECS batch status、bounded START probe concurrency、operation-scoped client 和 future global client pool comparison。

## Task 0: Baseline And Impact Reports

**Files:**
- Read only: `docs/superpowers/specs/2026-06-01-workline-fast-fail-start-admission-spec.md`
- Read only: files listed in File Structure

- [ ] Step 0.1: Confirm worktree state.

Run:

```bash
git status --short
```

Expected: existing user changes may include `TODOS.md` and the SPEC; do not revert them.

- [ ] Step 0.2: Run GitNexus impact before touching backend symbols.

Run impact checks for at least these targets before code edits:

```text
WorkLineRuntimeStatus
WorkLine
WorkLineSafetyService
RuntimeHoldReleaseService
WorkLineService
DeviceCommandGateway
CallbackIngressService
callback_event
```

Expected: record risk level and direct callers in the implementation notes. HIGH/CRITICAL requires user confirmation before edits.

- [ ] Step 0.3: Run targeted baseline tests to know current behavior.

Run:

```bash
uv run pytest tests/api/test_callback_api.py tests/workline_runtime/test_device_command_gateway.py tests/mock/test_ecs_mock_server.py -q
```

Expected: current baseline result captured. If unrelated failures exist, note them before starting TDD.

## Task 1: WorkLine STOPPED State, Migration, And Runtime Summary

**Files:**
- Modify: `src/app/workline/models/safety.py`
- Modify: `src/app/workline/models/workline.py`
- Modify: `src/app/workline/models/runtime.py`
- Modify: `src/app/workline/services/runtime_query_service.py`
- Create: `migrations/versions/<generated>_add_workline_stopped_start_admission.py`
- Test: `tests/workline_runtime/test_enums.py`
- Test: `tests/workline_runtime/test_master_data_runtime_properties.py`
- Test: `tests/api/test_workline_runtime_api.py`

- [ ] Step 1.1: Write failing enum/model/schema tests.

Cover:

- `WorkLineRuntimeStatus.STOPPED.value == "STOPPED"`。
- New `WorkLine()` default runtime status is `STOPPED`。
- Runtime summary/detail includes `start_admission_status`, `start_admission_message`, `start_admission_failed_device_code`, `start_admission_checked_at`, `last_start_request_id`, `last_start_trace_id`。
- Alembic migration keeps SQLAlchemy enum style compatible with `native_enum=False` and creates DB constraint/columns.

- [ ] Step 1.2: Run tests and verify failure.

Run:

```bash
uv run pytest tests/workline_runtime/test_enums.py tests/workline_runtime/test_master_data_runtime_properties.py tests/api/test_workline_runtime_api.py -q
```

Expected: failures point to missing `STOPPED` and missing START admission fields.

- [ ] Step 1.3: Generate migration.

Run:

```bash
uv run alembic revision -m "add workline stopped start admission"
```

Expected: Alembic creates one new file in `migrations/versions/` with generated revision ID.

- [ ] Step 1.4: Implement minimal model and schema changes.

Implementation boundaries:

- Add `STOPPED` to runtime status enum.
- Set WorkLine DB default/Python default to STOPPED.
- Add nullable START admission projection columns on WorkLine.
- Expose the same names through runtime summary/detail models.
- Use `timezone.now_for_db()` for DB timestamp writes in later tasks.

- [ ] Step 1.5: Edit generated migration.

Migration must:

- Add START admission columns as nullable.
- Update runtime status constraint/allowed values to include `STOPPED` while preserving existing values.
- For unpublished/dev data, update existing `READY` rows to `STOPPED` only if needed by the final schema/default decision from SPEC.

- [ ] Step 1.6: Run targeted tests.

Run:

```bash
uv run pytest tests/workline_runtime/test_enums.py tests/workline_runtime/test_master_data_runtime_properties.py tests/api/test_workline_runtime_api.py -q
```

Expected: PASS.

- [ ] Step 1.7: Commit.

Run:

```bash
git add src/app/workline/models/safety.py src/app/workline/models/workline.py src/app/workline/models/runtime.py src/app/workline/services/runtime_query_service.py migrations/versions/*.py tests/workline_runtime/test_enums.py tests/workline_runtime/test_master_data_runtime_properties.py tests/api/test_workline_runtime_api.py
git commit -m "feat(workline): add stopped runtime admission state"
```

Expected: commit includes only Task 1 files.

## Task 2: Event Taxonomy And Callback HTTP 409

**Files:**
- Modify: `src/workline_runtime/runtime_events.py`
- Modify: `src/app/callback/services/callback_ingress_service.py`
- Modify: `src/app/callback/services/callback_orchestration_service.py`
- Modify: `src/app/callback/v1/callback.py`
- Modify if needed: `src/app/callback/models/ingress_response.py`
- Test: `tests/workline_runtime/test_reserved_runtime_events.py`
- Test: `tests/api/test_callback_api.py`

- [ ] Step 2.1: Write failing event taxonomy tests.

Cover:

- `WORKLINE_START_REQUESTED` is platform control.
- `ESTOP_PRESSED` is platform safety/reserved.
- Production events are neither platform control nor safety.
- Plugin `supported_events` does not gate START/ESTOP.

- [ ] Step 2.2: Write failing callback route tests.

Cover route-level behavior:

- Production event on `STOPPED`, `RECONCILING`, and `ESTOPPED` returns HTTP 409.
- Response body top-level code is project numeric conflict code, with `data.reason_code="WORKLINE_NOT_ACCEPTING_WORK"`。
- No `workline_inbox` is created and no inbox processing is enqueued.
- `callback_logs.response_status=409`, `ingress_outcome=REJECTED`, `failure_stage=WORKLINE_GUARD`。
- `ESTOP_PRESSED` still enters safety flow when workline is not READY.
- `WORKLINE_START_REQUESTED` bypasses production capability path.

- [ ] Step 2.3: Run tests and verify failure.

Run:

```bash
uv run pytest tests/workline_runtime/test_reserved_runtime_events.py tests/api/test_callback_api.py -q
```

Expected: failures show missing taxonomy helpers and route still returns 200 for rejected production events.

- [ ] Step 2.4: Implement taxonomy helpers.

Implementation boundaries:

- Keep event constants in `src/workline_runtime/runtime_events.py` so callback, inbox, plugin tests share one source of truth.
- Preserve existing `RESERVED_RUNTIME_EVENTS` behavior and extend it instead of duplicating string checks.

- [ ] Step 2.5: Implement callback service decision shape.

Implementation boundaries:

- Service returns a structured decision with body and `http_status`.
- Accepted production events still call orchestration and return existing accepted semantics.
- Rejected production events stop before orchestration and before inbox creation.
- API route sets real HTTP status; service does not import FastAPI response classes unless an existing local pattern already does.

- [ ] Step 2.6: Run targeted tests.

Run:

```bash
uv run pytest tests/workline_runtime/test_reserved_runtime_events.py tests/api/test_callback_api.py -q
```

Expected: PASS.

- [ ] Step 2.7: Commit.

Run:

```bash
git add src/workline_runtime/runtime_events.py src/app/callback/services src/app/callback/v1/callback.py src/app/callback/models tests/workline_runtime/test_reserved_runtime_events.py tests/api/test_callback_api.py
git commit -m "feat(callback): fast fail production events by runtime state"
```

Expected: commit contains callback/event taxonomy only.

## Task 3: START Admission Service

**Files:**
- Create: `src/app/workline/services/start_admission_service.py`
- Modify: `src/app/workline/services/__init__.py`
- Modify: `src/app/workline/services/workline_service.py`
- Modify: `src/app/callback/services/callback_ingress_service.py`
- Modify if needed: `src/app/workline/services/runtime_query_service.py`
- Test: `tests/workline_runtime/test_start_admission_service.py`
- Test: `tests/test_workline_service_plugin_validation.py`
- Test: `tests/api/test_callback_api.py`

- [ ] Step 3.1: Write failing configuration/status tests.

Cover:

- `configuration_status()` reports incomplete host/port/path for command target devices.
- START admission reuses the same communication config check result.
- Missing communication config names the affected device.

- [ ] Step 3.2: Write failing START admission tests.

Cover:

- Happy path: WorkLine `STOPPED`, no incident/hold/reconciliation, required devices present, ECS batch status all `AUTO/IDLE/current_command_id=null`, final state `READY`.
- Failure path: one required device returns non-IDLE; WorkLine remains `STOPPED`, HTTP 409, diagnostic names device.
- ECS timeout/non-2xx/bad JSON/missing `device_code` all fail admission without writing READY.
- Timeout config default 2s and clamp 1s-5s.
- Batch concurrency default 4 and clamp 1-8.
- Final CAS drift: WorkLine changes to `ESTOPPED` or `RECONCILING` after status probe; service refuses READY.

- [ ] Step 3.3: Run tests and verify failure.

Run:

```bash
uv run pytest tests/workline_runtime/test_start_admission_service.py tests/test_workline_service_plugin_validation.py tests/api/test_callback_api.py -q
```

Expected: failures show missing service and missing shared config/status behavior.

- [ ] Step 3.4: Implement `WorkLineStartAdmissionService`.

Implementation boundaries:

- Resolve workline from callback `device_code` through existing device context/binding services; do not let API layer do lookup.
- Use short DB transaction to lock/read STOPPED snapshot.
- Release DB lock before ECS HTTP status probes.
- Group devices by `scheme/host/port/status_path`; call batch `GET /api/v1/device/status` without `device_code` for START.
- Parse response by `device_code`; missing/duplicate/malformed data fails admission.
- Re-lock WorkLine and recheck STOPPED/no active incident/no hold/no pending reconciliation before writing READY.
- Write START admission projection fields on success and failure.
- Export service singleton in `src/app/workline/services/__init__.py`。

- [ ] Step 3.5: Wire callback START path.

Implementation boundaries:

- `WORKLINE_START_REQUESTED` calls START admission service.
- START success returns HTTP 200 accepted/success body.
- START failure returns HTTP 409 numeric conflict body with diagnostic data.
- START does not require plugin `supported_events` or device event capability.

- [ ] Step 3.6: Run targeted tests.

Run:

```bash
uv run pytest tests/workline_runtime/test_start_admission_service.py tests/test_workline_service_plugin_validation.py tests/api/test_callback_api.py -q
```

Expected: PASS.

- [ ] Step 3.7: Commit.

Run:

```bash
git add src/app/workline/services/start_admission_service.py src/app/workline/services/__init__.py src/app/workline/services/workline_service.py src/app/callback/services/callback_ingress_service.py tests/workline_runtime/test_start_admission_service.py tests/test_workline_service_plugin_validation.py tests/api/test_callback_api.py
git commit -m "feat(workline): admit start events with device status checks"
```

Expected: commit contains START admission service and tests.

## Task 4: Recovery Transitions Return STOPPED

**Files:**
- Modify: `src/app/workline/services/safety_service.py`
- Modify: `src/app/workline/services/runtime_hold_release_service.py`
- Modify if needed: `src/app/workline/services/runtime_reconciliation_service.py`
- Test: `tests/workline_runtime/test_workline_safety_service.py`
- Test: `tests/workline_runtime/test_runtime_hold_release_service.py`
- Test: `tests/api/test_workline_safety_operation_api.py`
- Test: `tests/api/test_runtime_hold_api.py`

- [ ] Step 4.1: Write failing recovery tests.

Cover:

- `clear_estop` writes/returns `STOPPED`, not `READY`.
- Runtime hold resolve/release writes/returns `STOPPED`, not `READY`.
- Parked outbox release behavior remains unchanged except line is not auto-READY.
- API success messages/data allow frontend to show “已解除冻结，等待现场 START”。

- [ ] Step 4.2: Run tests and verify failure.

Run:

```bash
uv run pytest tests/workline_runtime/test_workline_safety_service.py tests/workline_runtime/test_runtime_hold_release_service.py tests/api/test_workline_safety_operation_api.py tests/api/test_runtime_hold_api.py -q
```

Expected: existing assertions expecting READY fail.

- [ ] Step 4.3: Implement transition changes.

Implementation boundaries:

- Preserve valuable comments in safety and hold release services; update comments that currently say “restore READY”。
- Do not change incident/hold resolution semantics beyond final runtime status.
- Keep audit/diagnostic behavior intact.

- [ ] Step 4.4: Run targeted tests.

Run:

```bash
uv run pytest tests/workline_runtime/test_workline_safety_service.py tests/workline_runtime/test_runtime_hold_release_service.py tests/api/test_workline_safety_operation_api.py tests/api/test_runtime_hold_api.py -q
```

Expected: PASS.

- [ ] Step 4.5: Commit.

Run:

```bash
git add src/app/workline/services/safety_service.py src/app/workline/services/runtime_hold_release_service.py src/app/workline/services/runtime_reconciliation_service.py tests/workline_runtime/test_workline_safety_service.py tests/workline_runtime/test_runtime_hold_release_service.py tests/api/test_workline_safety_operation_api.py tests/api/test_runtime_hold_api.py
git commit -m "fix(workline): return stopped after recovery"
```

Expected: commit scoped to recovery transition semantics.

## Task 5: Realtime Device Status Before Command POST

**Files:**
- Modify: `src/app/workline/services/device_command_gateway.py`
- Test: `tests/workline_runtime/test_device_command_gateway.py`
- Test if outbox retry behavior is covered there: `tests/workline_runtime/test_outbox_dispatch_service.py`

- [ ] Step 5.1: Write failing device gateway tests.

Cover:

- Status GET timeout never POSTs `/api/v1/device/command` and returns dispatch retry path.
- Status GET non-2xx never POSTs.
- Status GET bad JSON never POSTs.
- `mode != AUTO` never POSTs.
- `status != IDLE` never POSTs.
- `current_command_id != null` never POSTs.
- Status OK then command POST works.
- Command POST timeout after status OK still raises/uses existing ACK timeout semantics.
- Status timeout uses START/status short timeout, not command ACK timeout.

- [ ] Step 5.2: Run tests and verify failure.

Run:

```bash
uv run pytest tests/workline_runtime/test_device_command_gateway.py tests/workline_runtime/test_outbox_dispatch_service.py -q
```

Expected: failures show command POST currently happens without realtime status GET.

- [ ] Step 5.3: Implement status GET before POST.

Implementation boundaries:

- Keep existing DB projection governance first.
- Build status URL from same device scheme/host/port and standard `/api/v1/device/status?device_code=...`。
- Use one operation-scoped `httpx.AsyncClient` inside `dispatch()`.
- Use per-request timeout: short status timeout for GET, existing ACK timeout for command POST.
- Status failure happens before physical action; it must not be treated as `OUTBOX_ACK_TIMEOUT` unless POST was already issued.
- Preserve command ACK success, terminal status refresh, and runtime deadline activation behavior.

- [ ] Step 5.4: Run targeted tests.

Run:

```bash
uv run pytest tests/workline_runtime/test_device_command_gateway.py tests/workline_runtime/test_outbox_dispatch_service.py -q
```

Expected: PASS.

- [ ] Step 5.5: Commit.

Run:

```bash
git add src/app/workline/services/device_command_gateway.py tests/workline_runtime/test_device_command_gateway.py tests/workline_runtime/test_outbox_dispatch_service.py
git commit -m "feat(workline): check device status before command dispatch"
```

Expected: commit contains device dispatch status changes only.

## Task 6: Dev Mock Batch Status And Real START Flow

**Files:**
- Modify: `tests/mock/ecs_mock_server.py`
- Modify: `tests/mock/ecs_mock_catalog.py`
- Modify: `tests/mock/test_ecs_mock_server.py`
- Modify: `scripts/data/sync_test_workline_devices.py`
- Modify: `tests/scripts/test_sync_test_workline_devices.py`
- Test if available: `tests/integration/workline_runtime/test_rough_sorter_physical_flow_integration.py`

- [ ] Step 6.1: Write failing mock status tests.

Cover:

- `GET /api/v1/device/status?device_code=...` returns one device state with `state.mode/status/current_command_id`。
- `GET /api/v1/device/status` without `device_code` returns all ECS device states.
- Command lifecycle updates `current_command_id` consistently for status and command history.
- Unknown `device_code` returns a clear non-2xx or contract error matching START admission parser expectations.

- [ ] Step 6.2: Write failing dev seed tests.

Cover:

- Rough sorter dev workline uses `run_mode=AUTO`。
- Mock devices point to `mock_ecs:8010`。
- Initial workline state is `STOPPED`。
- Standard status/command paths are configured.

- [ ] Step 6.3: Run tests and verify failure.

Run:

```bash
uv run pytest tests/mock/test_ecs_mock_server.py tests/scripts/test_sync_test_workline_devices.py -q
```

Expected: failures show missing batch status contract and current READY/dev seed assumptions.

- [ ] Step 6.4: Implement mock and seed changes.

Implementation boundaries:

- Mock ECS should model the ECS contract, not bypass WES device dispatch.
- Do not restore old `preset` mock event format.
- Keep command history endpoint behavior stable for Swagger/mock verification.

- [ ] Step 6.5: Run targeted tests and integration smoke.

Run:

```bash
uv run pytest tests/mock/test_ecs_mock_server.py tests/scripts/test_sync_test_workline_devices.py -q
uv run pytest tests/integration/workline_runtime/test_rough_sorter_physical_flow_integration.py -q
```

Expected: targeted tests PASS. If integration test requires services not running, record exact blocker and run the targeted unit tests at minimum.

- [ ] Step 6.6: Commit.

Run:

```bash
git add tests/mock/ecs_mock_server.py tests/mock/ecs_mock_catalog.py tests/mock/test_ecs_mock_server.py scripts/data/sync_test_workline_devices.py tests/scripts/test_sync_test_workline_devices.py tests/integration/workline_runtime/test_rough_sorter_physical_flow_integration.py
git commit -m "feat(mock): support workline start admission flow"
```

Expected: commit contains dev mock and seed changes only.

## Task 7: Frontend STOPPED And START Contract

**Files:**
- Modify: `../wes_frontend/src/constants/runtime-safety.ts`
- Modify: `../wes_frontend/src/types/runtime.ts`
- Modify: `../wes_frontend/src/utils/runtime-display.ts`
- Modify: `../wes_frontend/src/utils/runtime-safety.ts`
- Modify: `../wes_frontend/src/components/runtime/devices/DecisionStrip.vue`
- Modify: `../wes_frontend/src/views/runtime/worklines/WorklineMonitorPage.vue`
- Modify: `../wes_frontend/src/views/runtime/sandbox/SandboxWorkbenchPage.vue`
- Modify: `../wes_frontend/src/components/runtime/sandbox/SandboxEventComposer.vue`
- Test: frontend unit/component tests in `../wes_frontend` matching existing test framework.

- [ ] Step 7.1: Detect frontend test commands.

Run in frontend repo:

```bash
cd ../wes_frontend
ls package.json vitest.config.* playwright.config.* 2>/dev/null
cat package.json
```

Expected: identify exact unit and smoke commands before editing. Use repo-defined scripts rather than inventing commands.

- [ ] Step 7.2: Write failing runtime utility tests.

Cover:

- `STOPPED` risk tone is `warning`。
- `STOPPED` label is “等待现场 START” or the exact copy from SPEC.
- `getWorklineRuntimeVerdict()` returns blocked production state but not safety clearable state.
- `WORKLINE_START_REQUESTED` is excluded from normal production templates.

- [ ] Step 7.3: Write failing component/view tests.

Cover:

- Workline directory badge and `DecisionStrip` show STOPPED as warning primary verdict.
- clear-estop success copy says “已解除冻结，等待现场 START”，not “已恢复接收新流程”。
- Production Event composer is disabled when runtime status is not READY and shows visible reason text.
- START status `CHECKING` exposes loading/announcement behavior.
- START `FAILED` shows failed device/message/request/trace.
- Dev/mock START button appears only in STOPPED sandbox/dev context.
- Narrow viewport keeps STOPPED/START verdict before topology/session content.
- Touch target for dev/mock START action is at least 44px.

- [ ] Step 7.4: Run frontend tests and verify failure.

Run the commands discovered in Step 7.1, scoped to runtime/sandbox tests.

Expected: failures show STOPPED not recognized and current copy still says “恢复接收”。

- [ ] Step 7.5: Implement frontend contract.

Implementation boundaries:

- Follow `../wes_frontend/DESIGN.md`: dark industrial control UI, warning yellow for STOPPED, green for READY, red for ESTOP/incident.
- Reuse `RuntimeStatusBadge`, `DecisionStrip`, existing runtime utility structure.
- Do not introduce decorative cards or long explanatory copy.
- Disabled reason must be visible without hover.
- Use `aria-live="polite"` or equivalent for START status change region.
- `WORKLINE_START_REQUESTED` must not appear in ordinary production Event composer templates.

- [ ] Step 7.6: Run frontend tests.

Run scoped unit/component tests and one smoke command if the frontend repo has it.

Expected: PASS.

- [ ] Step 7.7: Commit frontend changes.

Run from backend repo root or frontend repo according to actual git layout:

```bash
git status --short ../wes_frontend
```

If `../wes_frontend` is a separate repo, commit there with:

```bash
cd ../wes_frontend
git add src/constants/runtime-safety.ts src/types/runtime.ts src/utils/runtime-display.ts src/utils/runtime-safety.ts src/components/runtime/devices/DecisionStrip.vue src/views/runtime/worklines/WorklineMonitorPage.vue src/views/runtime/sandbox/SandboxWorkbenchPage.vue src/components/runtime/sandbox/SandboxEventComposer.vue
git commit -m "feat(runtime): show stopped worklines waiting for start"
```

Expected: frontend commit is separate if frontend is a separate repo.

## Task 8: TODO Follow-up And Final Backend Verification

**Files:**
- Modify: `TODOS.md`
- Read/verify: all modified backend/frontend files

- [ ] Step 8.1: Update existing TODO.

In `TODOS.md`, update existing P2 “Workline worker 吞吐 benchmark 与队列/连接策略调优” to include:

- START admission ECS batch status latency.
- Bounded START status probe concurrency.
- Operation-scoped client vs future global client pool comparison.
- Dual HTTP path benchmark: status GET + command POST.

- [ ] Step 8.2: Run backend targeted suite.

Run:

```bash
uv run pytest tests/api/test_callback_api.py tests/api/test_workline_runtime_api.py tests/workline_runtime/test_start_admission_service.py tests/workline_runtime/test_device_command_gateway.py tests/workline_runtime/test_runtime_hold_release_service.py tests/workline_runtime/test_workline_safety_service.py tests/mock/test_ecs_mock_server.py tests/scripts/test_sync_test_workline_devices.py -q
```

Expected: PASS.

- [ ] Step 8.3: Run backend quality checks.

Run:

```bash
uv run ruff format .
uv run ruff check .
```

Expected: no formatting diff after format, ruff check PASS.

- [ ] Step 8.4: Run migration smoke.

Run:

```bash
./scripts/migrate.sh upgrade
```

Expected: migration applies cleanly in the active dev/test database environment. If DB is unavailable, record exact environment blocker.

- [ ] Step 8.5: Run GitNexus changed-scope check before final commit.

Run:

```text
gitnexus_detect_changes(scope="all", repo="wes_backend")
```

Expected: affected flows match callback ingress, workline runtime, device dispatch, mock/dev seed, and docs/TODO only.

- [ ] Step 8.6: Final commit.

Run:

```bash
git status --short
git add TODOS.md docs/superpowers/specs/2026-06-01-workline-fast-fail-start-admission-spec.md docs/superpowers/plans/2026-06-01-workline-fast-fail-start-admission-plan.md
git add src/app/callback src/app/workline src/workline_runtime src/app/device tests scripts/data migrations/versions
git commit -m "feat(workline): add fast fail start admission flow"
```

Expected: commit contains backend implementation, tests, migration, docs, and TODO update. Do not include unrelated user changes.

## Final Acceptance Checklist

- [ ] `STOPPED` is the default runtime state for new/dev worklines.
- [ ] Production events on non-READY return real HTTP 409 and do not create inbox rows.
- [ ] START event succeeds only after shared config precheck and ECS status admission.
- [ ] START admission does not hold DB lock while waiting for ECS HTTP.
- [ ] START batch status uses ECS endpoint grouping and bounded concurrency.
- [ ] Command dispatch performs realtime single-device status before POST.
- [ ] Status GET failures never create physical action uncertainty.
- [ ] clear-estop and runtime hold resolve return `STOPPED`, not `READY`.
- [ ] Dev mock validates `START -> READY -> SCAN_COMPLETED -> command history`.
- [ ] Frontend STOPPED is warning, visible, accessible, and not confused with fault or stable READY.
- [ ] `WORKLINE_START_REQUESTED` is absent from normal production Event templates.
- [ ] `TODOS.md` captures deferred benchmark/client-pool follow-up.
- [ ] `gitnexus_detect_changes()` scope matches expected modules.

## Self-Review

Spec coverage:

- Runtime state and recovery semantics are covered by Tasks 1 and 4.
- Callback fast-fail and HTTP 409 are covered by Task 2.
- START admission, batch status, timeout clamp, and CAS are covered by Task 3.
- Command preflight realtime status is covered by Task 5.
- Dev mock and seed flow are covered by Task 6.
- Frontend STOPPED/START contract is covered by Task 7.
- Deferred performance benchmark TODO is covered by Task 8.

Placeholder scan:

- No task uses unresolved placeholder wording or vague test delegation.
- Every task lists exact files, test intent, commands, and pass/fail expectation.

Type consistency:

- START admission fields use the SPEC names consistently: `start_admission_status`, `start_admission_message`, `start_admission_failed_device_code`, `start_admission_checked_at`, `last_start_request_id`, `last_start_trace_id`.
- Runtime statuses use `STOPPED`, `READY`, `RECONCILING`, `ESTOPPED`.
- Platform control event name is consistently `WORKLINE_START_REQUESTED`.
