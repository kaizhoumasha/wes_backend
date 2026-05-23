# Sandbox Workline Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为沙箱测试页提供“按工作线清理沙箱数据”能力，清理后该工作线的待处理、历史、Runtime Hold 和重复 Event 幂等冲突都不再受旧沙箱数据影响。

**Architecture:** 新增独立 `SandboxCleanupService` 负责 dry-run 统计和事务内物理删除；现有 `workline/v1/operation.py` 暴露一个受权限保护的沙箱 cleanup 入口。前端在沙箱工作台顶部增加危险操作按钮，先 dry-run 预览，再二次确认执行，并刷新工作台数据。

**Tech Stack:** FastAPI, SQLModel/SQLAlchemy AsyncSession, Pydantic, Vue 3, Element Plus, Vitest, pytest, ruff, vue-tsc.

---

## Scope And Decisions

- 粒度：按 `workline_id` 清理整条工作线的沙箱运行时数据。
- 可见性：执行后沙箱待处理、沙箱历史、HOLD 处置都完全看不到旧记录。
- 删除策略：物理删除运行时沙箱链路数据；保留工作线、设备、货架、库位、插件配置等主数据。
- 安全边界：仅允许 `SIMULATION` 工作线；后端路由使用专门权限 `biz:workline:cleanup-sandbox`；前端必须 dry-run 后二次确认。
- 资源状态边界：第一版只清理可通过 sandbox session 明确归属的运行时数据和 `workline_bin_cell_reservations`。不删除无法唯一归属为沙箱链路的库存/货架主投影，避免误删真实配置或模拟主数据。
- 工作线状态：执行清理后将该工作线运行状态恢复为 `READY`，清空 `stopped_reason`；把被删除 sandbox command 占用的设备恢复为 `IDLE/current_command_id=None/error_code=None`。

## File Map

### Backend

- Create `src/app/workline/services/sandbox_cleanup_service.py`
  - 负责识别工作线 sandbox 运行时数据、dry-run 计数、清理引用字段、按依赖顺序物理删除。
- Modify `src/app/workline/models/operation.py`
  - 增加 `SandboxCleanupRequest` 和 `SandboxCleanupResponse`。
- Modify `src/app/workline/services/__init__.py`
  - 导出 `SandboxCleanupService` 和 `sandbox_cleanup_service`。
- Modify `src/app/workline/v1/operation.py`
  - 增加 `POST /sandbox/worklines/{workline_id}/cleanup`。
- Test `tests/workline_runtime/test_sandbox_cleanup_service.py`
  - 覆盖 dry-run、执行清理、非 SIMULATION 拒绝、非沙箱数据不清理。
- Test `tests/api/test_workline_operation_api.py`
  - 覆盖路由权限、dry-run 不提交删除、执行后返回计数。

### Frontend

- Modify `src/types/runtime.ts`
  - 增加 `SandboxCleanupRequest`、`SandboxCleanupResponse`、`SandboxCleanupCounts` 类型。
- Modify `src/api/modules/runtime.ts`
  - 增加 `sandboxCleanup(worklineId, payload)`。
- Modify `src/views/runtime/sandbox/SandboxWorkbenchPage.vue`
  - 增加“清理沙箱数据”按钮、dry-run 预览确认、执行后清空本地列表并刷新。
- Test `tests/unit/api/runtime.test.ts`
  - 覆盖 cleanup URL 和 payload。
- Create `tests/unit/views/runtime/sandboxWorkbenchCleanup.test.ts`
  - 覆盖按钮触发 dry-run、确认执行、刷新视图、取消不执行。

---

### Task 1: Backend Models And Service Contract

**Files:**
- Modify: `src/app/workline/models/operation.py`
- Create: `src/app/workline/services/sandbox_cleanup_service.py`
- Modify: `src/app/workline/services/__init__.py`
- Test: `tests/workline_runtime/test_sandbox_cleanup_service.py`

- [ ] **Step 1: Run impact checks before editing service exports**

Run GitNexus impact:

```text
impact(repo="wes_backend", target="WorklineOperationService", direction="upstream", maxDepth=2)
impact(repo="wes_backend", target="src/app/workline/services/__init__.py", direction="upstream", maxDepth=2)
```

Expected: Review direct callers. If risk is HIGH/CRITICAL, report before continuing.

- [ ] **Step 2: Add response/request schema tests first**

Add tests in `tests/workline_runtime/test_sandbox_cleanup_service.py`:

```python
def test_sandbox_cleanup_response_shape_accepts_counts() -> None:
    response = SandboxCleanupResponse(
        workline_id=45,
        dry_run=True,
        deleted=False,
        counts={"sessions": 1, "inboxes": 2, "outboxes": 1},
        affected_session_ids=[93],
        message="dry-run only",
    )
    assert response.counts["sessions"] == 1
    assert response.deleted is False
```

Run:

```bash
rtk uv run pytest tests/workline_runtime/test_sandbox_cleanup_service.py::test_sandbox_cleanup_response_shape_accepts_counts -q
```

Expected: FAIL because `SandboxCleanupResponse` is not defined.

- [ ] **Step 3: Add operation schemas**

In `src/app/workline/models/operation.py`, add these models near other sandbox request/response schemas:

```python
class SandboxCleanupRequest(BaseModel):
    dry_run: bool = Field(default=True, description="true 仅返回影响范围；false 执行清理")
    confirmation: str | None = Field(default=None, max_length=200, description="执行清理时必须等于工作线编码")


class SandboxCleanupResponse(BaseModel):
    workline_id: int
    dry_run: bool
    deleted: bool
    counts: dict[str, int] = Field(default_factory=dict)
    affected_session_ids: list[int] = Field(default_factory=list)
    message: str
```

Update `__all__` to include both names and keep it sorted.

- [ ] **Step 4: Create service skeleton**

Create `src/app/workline/services/sandbox_cleanup_service.py` with:

```python
class SandboxCleanupService:
    async def preview_cleanup(self, db: AsyncSession, *, workline_id: int) -> SandboxCleanupResponse:
        raise NotImplementedError

    async def cleanup_workline(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        confirmation: str | None,
    ) -> SandboxCleanupResponse:
        raise NotImplementedError
```

Also define `sandbox_cleanup_service = SandboxCleanupService()` and `__all__`.

- [ ] **Step 5: Export service**

Modify `src/app/workline/services/__init__.py`:

```python
from .sandbox_cleanup_service import SandboxCleanupService, sandbox_cleanup_service
```

Add both symbols to `__all__`.

- [ ] **Step 6: Run model test**

Run:

```bash
rtk uv run pytest tests/workline_runtime/test_sandbox_cleanup_service.py::test_sandbox_cleanup_response_shape_accepts_counts -q
```

Expected: PASS.

- [ ] **Step 7: Commit checkpoint**

```bash
rtk git add src/app/workline/models/operation.py src/app/workline/services/sandbox_cleanup_service.py src/app/workline/services/__init__.py tests/workline_runtime/test_sandbox_cleanup_service.py
rtk git commit -m "feat(workline): add sandbox cleanup contract"
```

---

### Task 2: Backend Dry-Run Discovery

**Files:**
- Modify: `src/app/workline/services/sandbox_cleanup_service.py`
- Test: `tests/workline_runtime/test_sandbox_cleanup_service.py`

- [ ] **Step 1: Write failing dry-run test**

Add a test that creates:

- one `SIMULATION` workline with one sandbox session
- one sandbox inbox linked to the session
- one sandbox outbox linked to the session
- one device command linked to the session
- one runtime hold linked to the session
- one rack task linked to the session
- one `AUTO` workline with similar non-sandbox data that must not be counted

Use these assertions:

```python
result = await sandbox_cleanup_service.preview_cleanup(db_session, workline_id=simulation_workline.id)
assert result.dry_run is True
assert result.deleted is False
assert result.affected_session_ids == [sandbox_session.id]
assert result.counts["sessions"] == 1
assert result.counts["inboxes"] >= 1
assert result.counts["outboxes"] >= 1
assert result.counts["commands"] >= 1
assert result.counts["runtime_holds"] >= 1
assert result.counts["rack_tasks"] >= 1
```

Run:

```bash
rtk uv run pytest tests/workline_runtime/test_sandbox_cleanup_service.py::test_preview_cleanup_counts_only_simulation_workline_sandbox_data -q
```

Expected: FAIL because `preview_cleanup` is not implemented.

- [ ] **Step 2: Implement sandbox candidate discovery**

In `SandboxCleanupService`, implement private discovery helpers:

- `_load_workline(db, workline_id)`
- `_require_simulation_workline(workline)`
- `_collect_session_ids(db, workline_id)`
- `_collect_runtime_ids(db, workline_id, session_ids)`

Candidate rules:

```text
sessions: WorklineSession.workline_id == workline_id AND run_mode == SIMULATION
inboxes: workline_id == workline_id AND (session_id in sessions OR sandbox marker fields)
outboxes: workline_id == workline_id AND session_id in sessions
commands: workline_id == workline_id AND session_id_int in sessions
holds: workline_id == workline_id AND session_id in sessions
rack_tasks: workline_id == workline_id AND material_session_id in sessions
bin_cell_reservations: workline_id == workline_id AND session_id in sessions
safety_incidents: workline_id == workline_id AND trigger_payload_json.source == "sandbox"
```

Sandbox marker fields for inbox fallback:

```text
payload_json["sandbox_mode"] == true
source_message_id starts with "sandbox:"
event_id starts with "sandbox:"
trace_id starts with "sandbox:"
```

Use `select()` for ID discovery and keep API layer free of DB access.

- [ ] **Step 3: Implement `preview_cleanup` counts**

Return `SandboxCleanupResponse` with stable count keys:

```python
counts = {
    "sessions": len(session_ids),
    "inboxes": len(inbox_ids),
    "outboxes": len(outbox_ids),
    "commands": len(command_ids),
    "runtime_holds": len(hold_ids),
    "ng_return_items": len(ng_item_ids),
    "rack_tasks": len(rack_task_ids),
    "bin_cell_reservations": len(reservation_ids),
    "timelines": len(timeline_ids),
    "diagnostics": len(diagnostic_ids),
    "dispatch_attempts": len(dispatch_attempt_ids),
    "safety_incidents": len(safety_incident_ids),
}
```

Message for dry-run:

```text
已预览工作线沙箱清理范围，未删除数据
```

- [ ] **Step 4: Run dry-run tests**

Run:

```bash
rtk uv run pytest tests/workline_runtime/test_sandbox_cleanup_service.py::test_preview_cleanup_counts_only_simulation_workline_sandbox_data -q
```

Expected: PASS.

- [ ] **Step 5: Add and pass non-simulation rejection test**

Test:

```python
with pytest.raises(ValueError, match="仅允许 SIMULATION 工作线"):
    await sandbox_cleanup_service.preview_cleanup(db_session, workline_id=auto_workline.id)
```

Run:

```bash
rtk uv run pytest tests/workline_runtime/test_sandbox_cleanup_service.py::test_preview_cleanup_rejects_non_simulation_workline -q
```

Expected: PASS.

- [ ] **Step 6: Commit checkpoint**

```bash
rtk git add src/app/workline/services/sandbox_cleanup_service.py tests/workline_runtime/test_sandbox_cleanup_service.py
rtk git commit -m "feat(workline): preview sandbox cleanup scope"
```

---

### Task 3: Backend Cleanup Execution

**Files:**
- Modify: `src/app/workline/services/sandbox_cleanup_service.py`
- Test: `tests/workline_runtime/test_sandbox_cleanup_service.py`

- [ ] **Step 1: Write failing execution test**

Add a test that:

- builds the same sandbox graph as Task 2
- calls `cleanup_workline(..., confirmation=workline.line_code)`
- verifies all sandbox rows are gone
- verifies non-sandbox rows remain
- verifies workline is `READY`
- verifies devices with deleted `current_command_id` are reset

Core assertions:

```python
result = await sandbox_cleanup_service.cleanup_workline(
    db_session,
    workline_id=simulation_workline.id,
    confirmation=simulation_workline.line_code,
)
assert result.deleted is True
assert await db_session.get(WorklineSession, sandbox_session.id) is None
assert await db_session.get(WorklineInbox, sandbox_inbox.id) is None
assert await db_session.get(WorklineOutbox, sandbox_outbox.id) is None
assert await db_session.get(DeviceCommand, sandbox_command.id) is None
await db_session.refresh(simulation_workline)
assert simulation_workline.runtime_status == WorkLineRuntimeStatus.READY
assert simulation_workline.stopped_reason is None
```

Run:

```bash
rtk uv run pytest tests/workline_runtime/test_sandbox_cleanup_service.py::test_cleanup_workline_deletes_sandbox_runtime_graph_and_resets_runtime_state -q
```

Expected: FAIL because execution is not implemented.

- [ ] **Step 2: Implement confirmation guard**

In `cleanup_workline`, reject execution unless:

```text
confirmation == workline.line_code
```

Error message:

```text
清理确认失败：confirmation 必须等于工作线编码
```

- [ ] **Step 3: Clear cyclic references before deleting**

Before physical delete, update these references to avoid FK cycles:

```text
WorklineOutbox.blocked_by_runtime_hold_id = NULL for collected outboxes/workline
RuntimeHold.reopened_from_hold_id = NULL for collected holds
WorklineSession.awaiting_command_id = NULL for collected sessions
Device.current_command_id = NULL for collected commands
Device.device_status = IDLE and error_code = NULL when current_command_id was collected
```

Flush after reference cleanup.

- [ ] **Step 4: Delete in dependency order**

Use `delete(Model).where(Model.id.in_(ids))` in this order:

```text
WorklineTimeline
WorklineDiagnostic
WorklineDispatchAttempt
WorklineBinCellReservation
WorklineRackTask
NgReturnItem
WorklineSafetyIncident
RuntimeHold
WorklineOutbox
WorklineInbox
DeviceCommand
WorklineSession
```

Then set the workline runtime state:

```text
runtime_status = READY
stopped_reason = NULL
resumed_at = timezone.now_for_db()
```

Do not commit inside the service. Let the API route commit after service success.

- [ ] **Step 5: Run execution test**

Run:

```bash
rtk uv run pytest tests/workline_runtime/test_sandbox_cleanup_service.py::test_cleanup_workline_deletes_sandbox_runtime_graph_and_resets_runtime_state -q
```

Expected: PASS.

- [ ] **Step 6: Run service test file**

Run:

```bash
rtk uv run pytest tests/workline_runtime/test_sandbox_cleanup_service.py -q
```

Expected: all tests PASS.

- [ ] **Step 7: Commit checkpoint**

```bash
rtk git add src/app/workline/services/sandbox_cleanup_service.py tests/workline_runtime/test_sandbox_cleanup_service.py
rtk git commit -m "feat(workline): cleanup sandbox runtime data"
```

---

### Task 4: Backend API Route And Permission

**Files:**
- Modify: `src/app/workline/v1/operation.py`
- Test: `tests/api/test_workline_operation_api.py`

- [ ] **Step 1: Run API impact check**

Run GitNexus API impact:

```text
api_impact(repo="wes_backend", file="src/app/workline/v1/operation.py")
```

Expected: Review current consumers before adding the route.

- [ ] **Step 2: Add failing route permission test**

In `tests/api/test_workline_operation_api.py`, add:

```python
def test_sandbox_cleanup_route_uses_dedicated_permission() -> None:
    route = _get_route("/sandbox/worklines/{workline_id}/cleanup", "POST")
    permissions = [getattr(dep.dependency, "permission_required", "") for dep in route.dependencies]
    assert permissions == ["biz:workline:cleanup-sandbox"]
```

Run:

```bash
rtk uv run pytest tests/api/test_workline_operation_api.py::test_sandbox_cleanup_route_uses_dedicated_permission -q
```

Expected: FAIL because the route does not exist.

- [ ] **Step 3: Add route**

In `src/app/workline/v1/operation.py`:

- import `SandboxCleanupRequest`, `SandboxCleanupResponse`
- import `sandbox_cleanup_service`
- add route:

```python
@router.post(
    "/sandbox/worklines/{workline_id}/cleanup",
    summary="[biz:workline:cleanup-sandbox] 清理工作线沙箱运行时数据",
    response_model=ResponseSchemaModel[SandboxCleanupResponse],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:cleanup-sandbox"))],
)
```

Route behavior:

```text
if payload.dry_run:
    call preview_cleanup and do not commit
else:
    call cleanup_workline, await db.commit(), publish_deferred_sse_events(db)
ValueError maps through _operation_error_response
```

- [ ] **Step 4: Add API behavior tests**

Add tests:

```python
async def test_sandbox_cleanup_dry_run_does_not_commit(monkeypatch, db_session) -> None:
    ...
    response = await operation_api.cleanup_sandbox_workline(workline_id, SandboxCleanupRequest(dry_run=True), db_session)
    assert response["data"].deleted is False
```

```python
async def test_sandbox_cleanup_execute_commits_and_publishes(monkeypatch, db_session) -> None:
    ...
    payload = SandboxCleanupRequest(dry_run=False, confirmation="WL-SIM")
    response = await operation_api.cleanup_sandbox_workline(workline_id, payload, db_session)
    assert response["data"].deleted is True
```

Use `AsyncMock` for `publish_deferred_sse_events`.

- [ ] **Step 5: Run API tests**

Run:

```bash
rtk uv run pytest tests/api/test_workline_operation_api.py -q
```

Expected: PASS.

- [ ] **Step 6: Sync and verify permissions**

Run:

```bash
rtk uv run python scripts/data/sync_permissions.py --dry-run --permissions-only
```

Expected: output includes `biz:workline:cleanup-sandbox` in scanned permissions.

- [ ] **Step 7: Commit checkpoint**

```bash
rtk git add src/app/workline/v1/operation.py tests/api/test_workline_operation_api.py
rtk git commit -m "feat(workline): expose sandbox cleanup api"
```

---

### Task 5: Frontend API Method And Types

**Files:**
- Modify: `src/types/runtime.ts`
- Modify: `src/api/modules/runtime.ts`
- Test: `tests/unit/api/runtime.test.ts`

- [ ] **Step 1: Add failing API wrapper test**

In `tests/unit/api/runtime.test.ts`, add:

```ts
it('submits sandbox cleanup through direct workline operations endpoint', async () => {
  const { runtimeApiMethods } = await import('@/api/modules/runtime')

  await runtimeApiMethods
    .sandboxCleanup(45, { dry_run: false, confirmation: 'WL-SMT-SIM' })
    .send()

  expect(mocks.apiClientPost).toHaveBeenCalledWith(
    '/api/v1/workline/operations/sandbox/worklines/45/cleanup',
    { dry_run: false, confirmation: 'WL-SMT-SIM' }
  )
})
```

Run:

```bash
rtk pnpm test tests/unit/api/runtime.test.ts
```

Expected: FAIL because `sandboxCleanup` is not defined.

- [ ] **Step 2: Add frontend types**

In `src/types/runtime.ts`, add:

```ts
export interface SandboxCleanupRequest {
  dry_run: boolean
  confirmation?: string | null
}

export type SandboxCleanupCounts = Record<string, number>

export interface SandboxCleanupResponse {
  workline_id: number
  dry_run: boolean
  deleted: boolean
  counts: SandboxCleanupCounts
  affected_session_ids: number[]
  message: string
}
```

- [ ] **Step 3: Add runtime API method**

In `src/api/modules/runtime.ts`, import the new types and add:

```ts
sandboxCleanup(worklineId: number, payload: SandboxCleanupRequest) {
  return adaptRuntimeMethod<SandboxCleanupResponse>(
    apiClient.Post(`/api/v1/workline/operations/sandbox/worklines/${worklineId}/cleanup`, payload)
  )
}
```

- [ ] **Step 4: Run API wrapper test**

Run:

```bash
rtk pnpm test tests/unit/api/runtime.test.ts
```

Expected: PASS.

- [ ] **Step 5: Commit checkpoint**

```bash
rtk git add src/types/runtime.ts src/api/modules/runtime.ts tests/unit/api/runtime.test.ts
rtk git commit -m "feat(runtime): add sandbox cleanup client"
```

---

### Task 6: Frontend Workbench Cleanup UI

**Files:**
- Modify: `src/views/runtime/sandbox/SandboxWorkbenchPage.vue`
- Create: `tests/unit/views/runtime/sandboxWorkbenchCleanup.test.ts`

- [ ] **Step 1: Generate frontend permissions after backend route exists**

Run from frontend repo:

```bash
rtk pnpm generate:permissions
```

Expected: generated permissions include `BIZ_PERMISSIONS.workline.cleanupSandbox`.

- [ ] **Step 2: Write failing UI test**

Create `tests/unit/views/runtime/sandboxWorkbenchCleanup.test.ts` that mounts `SandboxWorkbenchPage.vue` with stubs for heavy child components. Mock:

- `runtimeApiMethods.sandboxCleanup`
- `ElMessageBox.confirm`
- `ElMessage.success`
- `usePermission().hasPermission`
- `useWorklineRuntimeStore()`

Core behavior:

```ts
expect(wrapper.text()).toContain('清理沙箱数据')
await wrapper.get('[data-test="sandbox-cleanup"]').trigger('click')
expect(mocks.sandboxCleanup).toHaveBeenNthCalledWith(1, 45, { dry_run: true })
expect(mocks.confirm).toHaveBeenCalled()
expect(mocks.sandboxCleanup).toHaveBeenNthCalledWith(2, 45, {
  dry_run: false,
  confirmation: 'WL-SMT-SIM'
})
```

Run:

```bash
rtk pnpm test tests/unit/views/runtime/sandboxWorkbenchCleanup.test.ts
```

Expected: FAIL because the button and handler do not exist.

- [ ] **Step 3: Add button and state**

In `SandboxWorkbenchPage.vue`:

- import `ElMessageBox`
- import `usePermission`
- import `BIZ_PERMISSIONS`
- add `cleanupLoading`
- add `canCleanupSandbox = computed(() => hasPermission(BIZ_PERMISSIONS.workline.cleanupSandbox))`
- add button in top bar:

```vue
<el-button
  v-if="canCleanupSandbox"
  type="danger"
  size="small"
  plain
  data-test="sandbox-cleanup"
  :loading="cleanupLoading"
  @click="requestSandboxCleanup"
>
  清理沙箱数据
</el-button>
```

- [ ] **Step 4: Add cleanup handler**

Add handler behavior:

```text
1. call sandboxCleanup(worklineId, { dry_run: true })
2. show confirm dialog with worklineCode and count summary
3. on confirm call sandboxCleanup(worklineId, { dry_run: false, confirmation: worklineCode })
4. clear pendingItems, completedItems, submitted result sets, selectedOutbox
5. call refreshAll()
6. show success message
```

Confirmation copy must include:

```text
将清理当前工作线全部沙箱待处理、历史、Runtime Hold 与相关运行时记录，清理后旧历史不可恢复。
```

- [ ] **Step 5: Add cancellation test**

Add a test where `ElMessageBox.confirm` rejects with `"cancel"` and assert the second cleanup call is not made:

```ts
expect(mocks.sandboxCleanup).toHaveBeenCalledTimes(1)
```

- [ ] **Step 6: Run UI tests**

Run:

```bash
rtk pnpm test tests/unit/views/runtime/sandboxWorkbenchCleanup.test.ts
```

Expected: PASS.

- [ ] **Step 7: Commit checkpoint**

```bash
rtk git add src/views/runtime/sandbox/SandboxWorkbenchPage.vue tests/unit/views/runtime/sandboxWorkbenchCleanup.test.ts src/api/generated/permissions .permission-sync-record.json
rtk git commit -m "feat(runtime): add sandbox cleanup action"
```

---

### Task 7: End-To-End Verification

**Files:**
- No new files.
- Verify backend and frontend touched files.

- [ ] **Step 1: Backend targeted tests**

Run:

```bash
rtk uv run pytest tests/workline_runtime/test_sandbox_cleanup_service.py tests/api/test_workline_operation_api.py -q
```

Expected: PASS.

- [ ] **Step 2: Backend formatting and lint**

Run:

```bash
rtk uv run ruff format --check src/app/workline/models/operation.py src/app/workline/services/sandbox_cleanup_service.py src/app/workline/services/__init__.py src/app/workline/v1/operation.py tests/workline_runtime/test_sandbox_cleanup_service.py tests/api/test_workline_operation_api.py
rtk uv run ruff check src/app/workline/models/operation.py src/app/workline/services/sandbox_cleanup_service.py src/app/workline/services/__init__.py src/app/workline/v1/operation.py tests/workline_runtime/test_sandbox_cleanup_service.py tests/api/test_workline_operation_api.py
```

Expected: both commands PASS.

- [ ] **Step 3: Frontend targeted tests**

Run:

```bash
rtk pnpm test tests/unit/api/runtime.test.ts tests/unit/views/runtime/sandboxWorkbenchCleanup.test.ts
```

Expected: PASS.

- [ ] **Step 4: Frontend type and style checks**

Run:

```bash
rtk pnpm run type:check
rtk pnpm exec eslint --max-warnings 0 src/types/runtime.ts src/api/modules/runtime.ts src/views/runtime/sandbox/SandboxWorkbenchPage.vue tests/unit/api/runtime.test.ts tests/unit/views/runtime/sandboxWorkbenchCleanup.test.ts
rtk pnpm exec prettier --ignore-unknown --check src/types/runtime.ts src/api/modules/runtime.ts src/views/runtime/sandbox/SandboxWorkbenchPage.vue tests/unit/api/runtime.test.ts tests/unit/views/runtime/sandboxWorkbenchCleanup.test.ts
rtk pnpm exec stylelint src/views/runtime/sandbox/SandboxWorkbenchPage.vue --cache --cache-location node_modules/.cache/stylelint/
```

Expected: all commands PASS.

- [ ] **Step 5: Manual DB verification for `e1167e54de47c5eb` scenario**

Before cleanup, verify current data exists with read-only SQL through `uv run python`; print sessions, holds, rack tasks, inboxes and outboxes for `workline_id=45` and `business_key=e1167e54de47c5eb`.

Execute dry-run through API or service and confirm counts include the current session and hold.

Execute cleanup with `confirmation=<line_code>`.

After cleanup, verify:

```text
workline_sessions: no SIMULATION rows for workline_id=45
runtime_holds: no rows for deleted sandbox session ids
workline_rack_tasks: no rows for deleted sandbox session ids
workline_outbox / workline_inbox / device_commands: no sandbox-linked rows for deleted session ids
workline.runtime_status == READY
```

- [ ] **Step 6: Frontend manual verification**

Open the sandbox workbench for the same workline:

```text
沙箱测试页 → 右侧SMT粗分线 → 清理沙箱数据
```

Expected:

```text
dry-run dialog shows non-zero counts
confirming cleanup removes pending actions
history list no longer shows old Event or commands
HOLD 处置 no longer shows old runtime hold for that workline
resending the same Event no longer fails with DUPLICATE_ENTRY_ARCHIVED due to old sandbox rows
```

- [ ] **Step 7: Detect change scope**

Run GitNexus change detection:

```text
detect_changes(repo="wes_backend", scope="all")
```

Expected: changed backend symbols are limited to sandbox cleanup service, operation API, operation schemas, and tests plus existing unrelated dirty files already present in the worktree.

- [ ] **Step 8: Final commit**

```bash
rtk git status --short
rtk git add src/app/workline/models/operation.py src/app/workline/services/sandbox_cleanup_service.py src/app/workline/services/__init__.py src/app/workline/v1/operation.py tests/workline_runtime/test_sandbox_cleanup_service.py tests/api/test_workline_operation_api.py
rtk git add src/types/runtime.ts src/api/modules/runtime.ts src/views/runtime/sandbox/SandboxWorkbenchPage.vue tests/unit/api/runtime.test.ts tests/unit/views/runtime/sandboxWorkbenchCleanup.test.ts src/api/generated/permissions .permission-sync-record.json
rtk git commit -m "feat(runtime): add workline sandbox cleanup"
```

---

## Self-Review

- Spec coverage: 工作线粒度、完全隐藏旧历史、dry-run、二次确认、专门权限、只清沙箱数据、恢复可复测状态均有任务覆盖。
- Placeholder scan: 文档没有未决占位；每个任务给出明确文件、命令和通过标准。
- Type consistency: 后端 `SandboxCleanupRequest/SandboxCleanupResponse` 与前端同名类型字段一致；API 路径在后端、前端和测试中一致。
- Project planning rule: 计划只给关键接口、字段、短示例和验证步骤，未粘贴完整类/完整函数实现。
