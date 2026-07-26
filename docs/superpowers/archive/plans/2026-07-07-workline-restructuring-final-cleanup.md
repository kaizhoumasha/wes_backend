# WorkLine Restructuring Final Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `docs/architecture/workline-and-plugin-restructuring.md` 推进到“全部重构完成”：旧 `BinTransitMembership/BinTransitQueue` 生产路径清零，`WorkLine.runtime_status` 物理字段删除，Phase5/quality gate 能持续证明这两个残留不会回流。

**Architecture:** 旧 handling 队列模型不再作为状态源，handling lifecycle 只把外部回调 evidence 转交 runtime/orchestration 的 `ConveyorQueueMembershipWriterService`。WorkLine 配置表不再保存运行态字段，runtime/orchestration 新增 WorkLine 运行状态投影表与 repository/service，原 `WorkLineRuntimeStatusProjectionService` 改为 runtime 原生读写 facade。所有 destructive cleanup 都先写 guardrail，再做最小迁移和删除。

**Tech Stack:** Python 3.13、FastAPI、SQLModel/SQLAlchemy、Alembic、pytest、ruff、bandit、GitNexus、uv。

---

## Scope Check

本计划只处理本轮验证发现的两个阻塞项：

1. `src/app/handling/models/bin_transit_membership.py`、repository、service、默认测试和 `bin_transit_memberships` 物理表仍存在。
2. `src/app/workline/models/workline.py` 仍包含 `runtime_status` 字段，相关写入仍通过兼容投影服务修改 WorkLine 实例。

不处理 RCS/AGV/CTU 直连 provider adapter，不新增业务能力，不重写 Phase4 runtime capability。

## File Structure

### 删除或停止导出的旧 handling 队列文件

- Delete: `src/app/handling/models/bin_transit_membership.py`
- Delete: `src/app/handling/repositories/bin_transit_membership_repository.py`
- Delete: `src/app/handling/services/bin_transit_membership_service.py`
- Delete: `tests/handling/test_bin_transit_membership.py`
- Modify: `src/app/handling/models/__init__.py`
- Modify: `src/app/handling/repositories/__init__.py`
- Modify: `src/app/handling/services/__init__.py`
- Modify: `src/app/handling/services/lifecycle_service.py`
- Modify: `tests/handling/test_handling_operation_lifecycle.py`

### 新增 runtime 原生 WorkLine 状态投影

- Create: `src/app/runtime/orchestration/workline_runtime_status_projection.py`
- Create: `src/app/runtime/orchestration/repositories/workline_runtime_status_projection_repository.py`
- Modify: `src/app/runtime/orchestration/services/workline_runtime_status_projection_service.py`
- Modify: `src/app/runtime/orchestration/__init__.py`
- Modify: `src/app/runtime/orchestration/repositories/__init__.py`
- Modify: `src/app/runtime/orchestration/services/__init__.py`
- Modify: `src/app/workline/models/workline.py`
- Modify: `src/app/workline/models/safety.py`

### 迁移、脚本、文档和 guardrail

- Create: generated Alembic revision under `migrations/versions/` using `uv run alembic revision -m "drop legacy workline runtime residuals"`
- Modify: `scripts/architecture-guardrails.allowlist`
- Modify: `tests/architecture/test_phase2_runtime_status_owner_guardrail.py`
- Modify: `tests/architecture/test_phase5_legacy_absence_guardrail.py`
- Modify: `tests/architecture/test_phase5_business_legacy_absence_guardrail.py`
- Modify: `scripts/check_phase5_readiness_gate.py`
- Modify: `scripts/check_phase5_business_destructive_cleanup_gate.py`
- Modify: `scripts/generate_legacy_matrix.py`
- Modify: `docs/architecture/legacy-cleanup-matrix.md`
- Modify: `docs/architecture/legacy-cleanup-matrix.csv`
- Modify: `docs/architecture/legacy-cleanup-execution-plan.md`
- Modify: `docs/architecture/phase5-business-destructive-cleanup-ledger.md`
- Modify: `docs/architecture/workline-and-plugin-restructuring.md`
- Modify: `docs/architecture/file_index.md`
- Modify: `CHANGELOG.md`

## Task 0: 预检、分支和影响分析

**Files:**
- Read: `docs/architecture/workline-and-plugin-restructuring.md:493`
- Read: `src/app/handling/models/bin_transit_membership.py:20`
- Read: `src/app/handling/services/lifecycle_service.py:242`
- Read: `src/app/workline/models/workline.py:133`
- Read: `src/app/runtime/orchestration/services/workline_runtime_status_projection_service.py:29`

- [ ] **Step 1: 确认工作区干净**

Run:

```bash
rtk git status --short --branch
```

Expected: only current branch line, no modified files.

- [ ] **Step 2: 创建执行分支**

Run:

```bash
rtk git switch -c feature/workline-final-cleanup develop
```

Expected: branch switched to `feature/workline-final-cleanup`.

- [ ] **Step 3: 刷新 GitNexus 索引**

Run:

```bash
rtk bash -lc 'if [ -f .gitnexus/run.cjs ]; then node .gitnexus/run.cjs analyze; else npx gitnexus analyze; fi'
```

Expected: repository indexed successfully. If `AGENTS.md` or `CLAUDE.md` are auto-mutated by analyzer, inspect diff and restore analyzer-only edits before coding.

- [ ] **Step 4: 运行必需影响分析**

Use GitNexus MCP impact before edits:

```text
impact({target: "BinTransitMembership", direction: "upstream", repo: "wes_backend"})
impact({target: "BinTransitMembershipService", direction: "upstream", repo: "wes_backend"})
impact({target: "HandlingOperationLifecycleService", direction: "upstream", repo: "wes_backend"})
impact({target: "WorkLine", direction: "upstream", repo: "wes_backend"})
impact({target: "WorkLineRuntimeStatusProjectionService", direction: "upstream", repo: "wes_backend"})
```

Expected: record direct callers and risk. If any result is HIGH or CRITICAL, pause and report before edits.

- [ ] **Step 5: 建立当前失败假设**

Run:

```bash
rtk rg -n "BinTransitMembership|BinTransitQueue|bin_transit_membership|bin_transit_memberships|WorkLine\\.runtime_status|runtime_status: WorkLineRuntimeStatus" src tests migrations scripts docs/architecture -S
```

Expected: current output still shows handling BinTransit production files and WorkLine model field. These are the residuals this plan removes.

## Task 1: Guardrails First - 让残留变成可检测失败

**Files:**
- Modify: `tests/architecture/test_phase5_legacy_absence_guardrail.py`
- Modify: `tests/architecture/test_phase5_business_legacy_absence_guardrail.py`
- Modify: `tests/architecture/test_phase2_runtime_status_owner_guardrail.py`
- Modify: `scripts/check_phase5_readiness_gate.py`

- [ ] **Step 1: 扩展 Phase5 absence guardrail 的 forbidden modules**

在 `tests/architecture/test_phase5_legacy_absence_guardrail.py` 中将 forbidden 集合扩展为：

```python
FORBIDDEN_MODULES = (
    "src.app.workline.plugins",
    "src.workline_plugin_registry",
    "src.workline_plugins",
    "src.app.handling.models.bin_transit_membership",
    "src.app.handling.repositories.bin_transit_membership_repository",
    "src.app.handling.services.bin_transit_membership_service",
)
FORBIDDEN_IMPORT_TEXT = FORBIDDEN_MODULES + (
    "BinTransitMembership",
    "BinTransitQueue",
    "bin_transit_memberships",
)
```

Keep the existing archived-doc allowance by scanning only `src/`.

- [ ] **Step 2: 扩展 business absence guardrail**

在 `tests/architecture/test_phase5_business_legacy_absence_guardrail.py` 中同步加入：

```python
TECHNICAL_LANE_FORBIDDEN_MODULES = (
    "src.app.workline.plugins",
    "src.workline_plugin_registry",
    "src.workline_plugins",
    "src.app.handling.models.bin_transit_membership",
    "src.app.handling.repositories.bin_transit_membership_repository",
    "src.app.handling.services.bin_transit_membership_service",
)
```

新增一个测试函数，扫描 `src/` 禁止 `BinTransitMembership`、`BinTransitQueue`、`bin_transit_memberships`。

- [ ] **Step 3: 把 runtime_status guardrail 从“兼容投影”改为“物理字段禁止”**

在 `tests/architecture/test_phase2_runtime_status_owner_guardrail.py` 增加三类断言：

```python
def test_workline_model_no_longer_declares_runtime_status_column() -> None:
    source = _source(Path("src/app/workline/models/workline.py"))
    assert "runtime_status:" not in source
    assert "WorkLineRuntimeStatus" not in source

def test_runtime_status_projection_service_no_longer_writes_workline_field() -> None:
    source = _source(PROJECTION_SERVICE)
    assert ".runtime_status =" not in source
    assert 'getattr(workline, "runtime_status"' not in source

def test_latest_migration_mentions_final_cleanup_targets() -> None:
    migration_text = _latest_migration_text()
    assert "workline_runtime_status_projections" in migration_text
    assert "bin_transit_memberships" in migration_text
    assert "runtime_status" in migration_text
```

This static test is only a cheap anti-omission check. The authoritative destructive cleanup proof is the database schema smoke in Task 8 after `alembic upgrade head`.

- [ ] **Step 4: 扩展 Phase5 readiness gate 的静态扫描**

在 `scripts/check_phase5_readiness_gate.py` 中加入 final cleanup scan：

```python
FINAL_FORBIDDEN_TEXT = (
    "src.app.handling.models.bin_transit_membership",
    "src.app.handling.repositories.bin_transit_membership_repository",
    "src.app.handling.services.bin_transit_membership_service",
    "BinTransitMembership",
    "BinTransitQueue",
    "bin_transit_memberships",
)
```

扫描范围只包含 `src/`、`tests/architecture/`、`tests/handling/`，排除 migration 历史文件和 archived docs。检测到即返回 failing gate message `PHASE5_FINAL_LEGACY_RESIDUALS_FOUND`。

- [ ] **Step 5: 运行新增 guardrails，确认先失败**

Run:

```bash
rtk uv run pytest tests/architecture/test_phase5_legacy_absence_guardrail.py tests/architecture/test_phase5_business_legacy_absence_guardrail.py tests/architecture/test_phase2_runtime_status_owner_guardrail.py -q
```

Expected: FAIL, with offenders pointing to existing `src/app/handling/...bin_transit...` and `src/app/workline/models/workline.py`.

- [ ] **Step 6: Keep guardrail red in working tree only**

Do not create a standalone commit containing intentionally failing tests. Keep the red guardrails in the working tree, then commit them together with the implementation that makes them pass. The local history should remain quality-gate friendly and bisectable.

## Task 2: 补齐 runtime queue writer terminal/reconciling adapter

**Files:**
- Modify: `src/app/runtime/orchestration/services/conveyor_queue_membership_writer_service.py`
- Modify: `src/app/runtime/orchestration/repositories/conveyor_queue_membership_repository.py`
- Test: `tests/runtime/orchestration/test_conveyor_queue_membership_writer_service.py`

- [ ] **Step 1: 写失败测试 - close active membership**

Add tests in `tests/runtime/orchestration/test_conveyor_queue_membership_writer_service.py`:

```python
async def test_close_active_marks_membership_left_and_is_idempotent(...):
    result = await service.write_active(db, workline_id=1, conveyor_code="LEGACY", queue_code="Q1", queue_role="LEGACY_CALLBACK", bin_code="BIN-1", strict=False, source_event_id="evt-enter-1")
    closed = await service.close_active(db, workline_id=1, bin_code="BIN-1", reason_code="QUEUE_LEFT", source_event_id="evt-1")
    replay = await service.close_active(db, workline_id=1, bin_code="BIN-1", reason_code="QUEUE_LEFT", source_event_id="evt-1", ignore_missing=True)
    assert closed.membership_status == "LEFT"
    assert replay is None
```

This is a small test sketch; keep the file’s existing fixture style and imports.
Also assert that a single `ObjectTransitionEvent` is recorded for the close with runtime-owned semantics:

- `domain=HANDLING`
- `object_type="CONVEYOR_QUEUE_MEMBERSHIP"`
- `projection_type="QUEUE_MEMBERSHIP"`
- `to_state="LEFT"`
- repeated idempotent close with `ignore_missing=True` does not create a duplicate transition.

Add a regression test for `write_active(...)` transition evidence too: create/switch writes exactly one `ObjectTransitionEvent` with `to_state=<queue_code>`, and an idempotent replay against the same active membership does not duplicate the event.

- [ ] **Step 2: 写失败测试 - mark reconciling by identity**

Add:

```python
async def test_mark_reconciling_for_identity_updates_active_membership(...):
    await service.write_active(db, workline_id=1, conveyor_code="LEGACY", queue_code="Q1", queue_role="LEGACY_CALLBACK", placeholder_key="ph-1", strict=False, source_event_id="evt-enter-2")
    marked = await service.mark_reconciling_for_identity(db, workline_id=1, placeholder_key="ph-1", reason_code="CALLBACK_RECONCILING", source_event_id="evt-2")
    assert marked.membership_status == "RECONCILING"
    assert marked.evidence_json["reason_code"] == "CALLBACK_RECONCILING"
```

Also assert that reconciling writes a transition event with `to_state="RECONCILING"` and the same `source_event_id` from evidence.
Add one negative test for each new writer method: missing/blank `source_event_id` raises a clear validation error before any membership or transition write.

- [ ] **Step 3: Run tests to verify fail**

Run:

```bash
rtk uv run pytest tests/runtime/orchestration/test_conveyor_queue_membership_writer_service.py -q
```

Expected: FAIL with missing `close_active` / `mark_reconciling_for_identity`.

- [ ] **Step 4: Add repository helper**

In `ConveyorQueueMembershipRepository`, add one helper using existing active identity select:

```python
async def get_one_active_by_identity(self, db, *, workline_id: int, bin_code: str | None = None, placeholder_key: str | None = None, for_update: bool = False) -> ConveyorQueueMembership | None:
    memberships = await self.list_active_by_identity(db, workline_id=workline_id, bin_code=bin_code, placeholder_key=placeholder_key, for_update=for_update)
    return memberships[0] if memberships else None
```

- [ ] **Step 5: Add service methods**

In `ConveyorQueueMembershipWriterService`, inject `ObjectTransitionEventService` and add:

```python
async def close_active(..., ignore_missing: bool = False) -> ConveyorQueueMembership | None:
    membership = await self.repo.get_one_active_by_identity(..., for_update=True)
    if membership is None:
        if ignore_missing:
            return None
        raise ValueError("bin_code 或 placeholder_key 没有 ACTIVE conveyor queue membership")
    membership.membership_status = "LEFT"
    membership.left_at = _now_ms()
    membership.evidence_json = _merge_evidence(membership, {"reason_code": reason_code, **dict(evidence_json or {})})
    db.add(membership)
    await db.flush()
    await self._finish(db, membership, auto_commit=auto_commit)
    return membership
```

And:

```python
async def mark_reconciling_for_identity(..., ignore_missing: bool = False) -> ConveyorQueueMembership | None:
    membership = await self.repo.get_one_active_by_identity(..., for_update=True)
    if membership is None:
        if ignore_missing:
            return None
        raise ValueError("bin_code 或 placeholder_key 没有 ACTIVE conveyor queue membership")
    membership.membership_status = "RECONCILING"
    membership.evidence_json = _merge_evidence(membership, {"reason_code": reason_code, **dict(evidence_json or {})})
    db.add(membership)
    await db.flush()
    await self._finish(db, membership, auto_commit=auto_commit)
    return membership
```

Keep signatures explicit: `workline_id`, `bin_code`, `placeholder_key`, `reason_code`, `source_event_id`, `evidence_json`, `auto_commit`, `ignore_missing`. `source_event_id` is required for transition idempotency and must not be hidden only inside `evidence_json`.

Record queue transition evidence for every non-idempotent write path:

- `write_active`: `from_state=None` for create, existing `queue_code` for switch/resolve.
- `close_active`: `from_state=<active.queue_code>`, `to_state="LEFT"`.
- `mark_reconciling_for_identity`: `from_state=<active.queue_code>`, `to_state="RECONCILING"`.

Use `ObjectTransitionEventService.record_transition(...)` with `domain=ObjectTransitionDomain.HANDLING`, `object_type="CONVEYOR_QUEUE_MEMBERSHIP"`, `projection_type="QUEUE_MEMBERSHIP"`, and source refs for `workline_id`, `membership_id`, `bin_code`, `placeholder_key`, `conveyor_code`, and `queue_role`. Do not keep emitting old `object_type="BIN_TRANSIT"` from production code.

- [ ] **Step 6: Run writer tests**

Run:

```bash
rtk uv run pytest tests/runtime/orchestration/test_conveyor_queue_membership_writer_service.py -q
```

Expected: PASS.

- [ ] **Step 7: Run import smoke for runtime writer exports**

Run:

```bash
rtk uv run python - <<'PY'
from src.app.runtime.orchestration.services.conveyor_queue_membership_writer_service import conveyor_queue_membership_writer_service
assert hasattr(conveyor_queue_membership_writer_service, "close_active")
assert hasattr(conveyor_queue_membership_writer_service, "mark_reconciling_for_identity")
PY
```

Expected: PASS.

- [ ] **Step 8: Commit runtime writer adapter**

Run:

```bash
rtk git add src/app/runtime/orchestration/repositories/conveyor_queue_membership_repository.py src/app/runtime/orchestration/services/conveyor_queue_membership_writer_service.py tests/runtime/orchestration/test_conveyor_queue_membership_writer_service.py
rtk git commit -m "feat(runtime): support conveyor queue terminal updates"
```

Expected: commit succeeds.

## Task 3: 用 runtime 队列 writer 替换 handling lifecycle 的旧 membership 依赖

**Files:**
- Modify: `src/app/handling/services/lifecycle_service.py`
- Modify: `tests/handling/test_handling_operation_lifecycle.py`

- [ ] **Step 1: 修改 lifecycle 测试替身为 runtime writer 语义**

在 `tests/handling/test_handling_operation_lifecycle.py` 中用 `FakeConveyorQueueWriterService` 替换 `FakeMembershipService`。测试只断言 lifecycle 传出 runtime writer 所需字段：

```python
class FakeConveyorQueueWriterService:
    def __init__(self) -> None:
        self.write_active_calls = []
        self.reconciling_calls = []
        self.close_calls = []

    async def write_active(self, _db, **kwargs):
        self.write_active_calls.append(kwargs)
        return SimpleNamespace(diagnostics=SimpleNamespace(reconciliation_required=False))

    async def mark_reconciling_for_identity(self, _db, **kwargs):
        self.reconciling_calls.append(kwargs)
        return None

    async def close_active(self, _db, **kwargs):
        self.close_calls.append(kwargs)
        return None
```

Update assertions:

```python
assert writer.write_active_calls[0]["queue_code"] == "WORKSTATION_WAIT_QUEUE"
assert writer.write_active_calls[0]["queue_role"] == "LEGACY_CALLBACK"
assert writer.write_active_calls[0]["strict"] is False
```

Use `strict=False` because old handling callbacks may carry legacy queue names while Phase4 manifest routing is not always available in these tests.

Add a regression test for callbacks whose `step.operation_key` no longer resolves to a `HandlingOperation`: step/move status updates should still complete, but queue projection should be skipped with a warning/diagnostic instead of calling the runtime writer without `workline_id`.

- [ ] **Step 2: 运行 lifecycle 测试确认失败**

Run:

```bash
rtk uv run pytest tests/handling/test_handling_operation_lifecycle.py -q
```

Expected: FAIL because `HandlingOperationLifecycleService.__init__` still accepts `membership_service`, not `queue_writer_service`.

- [ ] **Step 3: 修改 lifecycle service imports and constructor**

In `src/app/handling/services/lifecycle_service.py`:

- remove import of `BinTransitQueue` and `BinTransitMembershipService`
- import `ConveyorQueueMembershipWriterService` and singleton from `src.app.runtime.orchestration.services`
- constructor argument becomes `queue_writer_service`
- keep `HandlingMoveStatus`, `HandlingOperationStatus`, `HandlingStepStatus` imports from handling models

Minimal constructor shape:

```python
def __init__(..., queue_writer_service: ConveyorQueueMembershipWriterService = conveyor_queue_membership_writer_service) -> None:
    ...
    self.queue_writer_service = queue_writer_service
```

- [ ] **Step 4: Replace queue switch/leave/reconciling calls**

In `record_callback_from_external_http`, replace:

- `membership_service.switch_queue(...)` with `queue_writer_service.write_active(...)`
- `membership_service.mark_reconciling(...)` with `queue_writer_service.mark_reconciling_for_identity(...)`
- `membership_service.leave_queue(...)` with `queue_writer_service.close_active(...)`

Before any runtime writer call, resolve a small queue projection context:

- `workline_id`: prefer `operation.workline_id`; fallback only to an explicitly trusted current waiting session or payload field if the existing lifecycle flow already treats it as authoritative.
- `workline_session_id`: prefer `operation.material_session_id`; fallback to the waiting session id when available.
- missing `workline_id`: do not call `ConveyorQueueMembershipWriterService`; log/diagnose `HANDLING_QUEUE_PROJECTION_CONTEXT_MISSING` and keep the callback lifecycle path non-fatal.

Required field mapping:

| lifecycle source | runtime writer field |
| --- | --- |
| resolved queue projection context | `workline_id` |
| payload/move queue | `queue_code` |
| literal | `queue_role="LEGACY_CALLBACK"` |
| payload `conveyor_code` or `"LEGACY"` | `conveyor_code` |
| payload/move bin | `bin_code` |
| payload placeholder | `placeholder_key` |
| dispatch key + status | `source_event_id` |
| trace | `correlation_id` only if it is already a runtime correlation; otherwise put trace in `evidence_json` |

- [ ] **Step 5: Replace helper return type**

Change helpers:

```python
def _callback_target_queue(payload_json: Mapping[str, Any]) -> str | None:
    ...
    return raw_queue

def _move_target_queue(move: Any) -> str | None:
    ...
    return raw_queue
```

No `BinTransitQueue(raw_queue)` conversion remains. Unknown queue values are no longer silently dropped by enum parsing; runtime writer policy decides with manifest/strict settings.

- [ ] **Step 6: Run lifecycle tests**

Run:

```bash
rtk uv run pytest tests/handling/test_handling_operation_lifecycle.py -q
```

Expected: PASS.

- [ ] **Step 7: Run legacy absence guardrail**

Run:

```bash
rtk uv run pytest tests/architecture/test_phase5_legacy_absence_guardrail.py -q
```

Expected: still FAIL, but offenders should no longer include `src/app/handling/services/lifecycle_service.py`.

- [ ] **Step 8: Commit lifecycle migration**

Run:

```bash
rtk git add src/app/handling/services/lifecycle_service.py tests/handling/test_handling_operation_lifecycle.py
rtk git commit -m "refactor(handling): route queue callbacks to runtime projection"
```

Expected: commit succeeds.

## Task 4: 删除旧 BinTransit production surface

**Files:**
- Delete: `src/app/handling/models/bin_transit_membership.py`
- Delete: `src/app/handling/repositories/bin_transit_membership_repository.py`
- Delete: `src/app/handling/services/bin_transit_membership_service.py`
- Delete: `tests/handling/test_bin_transit_membership.py`
- Modify: `src/app/handling/models/__init__.py`
- Modify: `src/app/handling/repositories/__init__.py`
- Modify: `src/app/handling/services/__init__.py`
- Modify: `scripts/architecture-guardrails.allowlist`
- Modify: `scripts/generate_legacy_matrix.py`
- Modify: `docs/architecture/legacy-cleanup-matrix.csv`
- Modify: `docs/architecture/legacy-cleanup-matrix.md`

- [ ] **Step 1: Remove exports**

Remove all `BinTransit...` imports and `__all__` entries from:

```text
src/app/handling/models/__init__.py
src/app/handling/repositories/__init__.py
src/app/handling/services/__init__.py
```

Expected: handling package exports only operation, lifecycle, gateway, completion policy.

- [ ] **Step 2: Delete old files**

Run:

```bash
rtk rm src/app/handling/models/bin_transit_membership.py
rtk rm src/app/handling/repositories/bin_transit_membership_repository.py
rtk rm src/app/handling/services/bin_transit_membership_service.py
rtk rm tests/handling/test_bin_transit_membership.py
```

Expected: files removed from worktree.

- [ ] **Step 3: Remove architecture allowlist entries**

Delete lines mentioning:

```text
src/app/handling/models/bin_transit_membership.py
src/app/handling/services/bin_transit_membership_service.py
```

from `scripts/architecture-guardrails.allowlist`.

- [ ] **Step 4: Update matrix generator**

Remove fixed legacy seed entries for deleted BinTransit files from `scripts/generate_legacy_matrix.py`.

Expected: generator no longer emits rows for deleted paths.

- [ ] **Step 5: Regenerate cleanup matrix**

Run:

```bash
rtk uv run python scripts/generate_legacy_matrix.py
```

Expected: `legacy-cleanup-matrix.csv/md` regenerate with no rows for `src/app/handling/*bin_transit*`.

- [ ] **Step 6: Run absence search**

Run:

```bash
rtk rg -n "BinTransitMembership|BinTransitQueue|bin_transit_membership|bin_transit_memberships" src tests scripts -S
```

Expected: no matches in `src/`; only migration history may remain if command includes `migrations`.

- [ ] **Step 7: Run targeted tests**

Run:

```bash
rtk uv run pytest tests/architecture/test_phase5_legacy_absence_guardrail.py tests/architecture/test_phase5_business_legacy_absence_guardrail.py tests/handling/test_handling_operation_lifecycle.py tests/runtime/orchestration/test_conveyor_queue_membership_writer_service.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit legacy BinTransit deletion**

Run:

```bash
rtk git add src/app/handling/models/__init__.py src/app/handling/repositories/__init__.py src/app/handling/services/__init__.py src/app/handling/services/lifecycle_service.py src/app/runtime/orchestration/repositories/conveyor_queue_membership_repository.py src/app/runtime/orchestration/services/conveyor_queue_membership_writer_service.py tests/handling/test_handling_operation_lifecycle.py tests/runtime/orchestration/test_conveyor_queue_membership_writer_service.py tests/architecture/test_phase5_legacy_absence_guardrail.py tests/architecture/test_phase5_business_legacy_absence_guardrail.py scripts/architecture-guardrails.allowlist scripts/generate_legacy_matrix.py docs/architecture/legacy-cleanup-matrix.csv docs/architecture/legacy-cleanup-matrix.md
rtk git add -u src/app/handling/models/bin_transit_membership.py src/app/handling/repositories/bin_transit_membership_repository.py src/app/handling/services/bin_transit_membership_service.py tests/handling/test_bin_transit_membership.py
rtk git commit -m "refactor(handling): remove legacy bin transit projection"
```

Expected: commit succeeds.

## Task 5: 新增 WorkLine runtime 状态原生投影

**Files:**
- Create: `src/app/runtime/orchestration/workline_runtime_status_projection.py`
- Create: `src/app/runtime/orchestration/repositories/workline_runtime_status_projection_repository.py`
- Modify: `src/app/runtime/orchestration/services/workline_runtime_status_projection_service.py`
- Modify: `src/app/runtime/orchestration/__init__.py`
- Modify: `src/app/runtime/orchestration/repositories/__init__.py`
- Modify: `src/app/runtime/orchestration/services/__init__.py`
- Test: `tests/workline_runtime/test_workline_runtime_status_projection_service.py`

- [ ] **Step 1: Rewrite tests around runtime native projection**

Update `tests/workline_runtime/test_workline_runtime_status_projection_service.py` so tests no longer assert mutation of `workline.runtime_status`. New assertions:

```python
snapshot = await projection.runtime_status_snapshot(db, workline_id=45)
assert snapshot.runtime_status == "READY"
assert not hasattr(workline, "runtime_status")
```

Keep pure unit tests by using a fake repository object with methods `get_by_workline_id`, `upsert_status`, and `ensure_default`.
Add a test where `get_by_workline_id` returns `None`: `runtime_status_snapshot(...)` must return an explicit default/diagnostic snapshot without calling `ensure_default` or `upsert_status`.

- [ ] **Step 2: Run tests to verify fail**

Run:

```bash
rtk uv run pytest tests/workline_runtime/test_workline_runtime_status_projection_service.py -q
```

Expected: FAIL because service is still synchronous and WorkLine-field based.

- [ ] **Step 3: Add model**

Create `src/app/runtime/orchestration/workline_runtime_status_projection.py` with one SQLModel table:

```text
table: wes_runtime.workline_runtime_status_projections
fields: id, workline_id, runtime_status, source, stopped_at, stopped_reason, resumed_at, active_safety_incident_id, evidence_json
indexes: unique workline_id, runtime_status, active_safety_incident_id
check: runtime_status in READY/STOPPED/STARTING/ESTOPPED/RECONCILING
```

Use `RUNTIME_SCHEMA` from `execution_session.py`, `BaseMixin`, JSON column, and `timezone.now_for_db` only where timestamp defaults are needed.

- [ ] **Step 4: Add repository**

Create repository with explicit methods:

```python
async def get_by_workline_id(self, db, workline_id: int, *, for_update: bool = False) -> WorklineRuntimeStatusProjection | None
async def list_by_workline_ids(self, db, workline_ids: Sequence[int]) -> dict[int, WorklineRuntimeStatusProjection]
async def ensure_default(self, db, workline_id: int) -> WorklineRuntimeStatusProjection
async def upsert_status(self, db, *, workline_id: int, runtime_status: str, stopped_at=None, stopped_reason=None, resumed_at=None, active_safety_incident_id=None, evidence_json=None) -> WorklineRuntimeStatusProjection
```

Do not reference `WorkLine` model here.
`ensure_default(...)` is for migrations, repair jobs, and explicit projection writes only; ordinary read paths must not call it implicitly.

- [ ] **Step 5: Rewrite projection service as async runtime facade**

Change `WorkLineRuntimeStatusProjectionService` methods to accept `db` and `workline_id`:

```python
async def runtime_status_snapshot(self, db, *, workline_id: int) -> WorkLineRuntimeStatusSnapshot
async def runtime_status_snapshot_map(self, db, *, workline_ids: Sequence[int]) -> dict[int, WorkLineRuntimeStatusSnapshot]
async def is_ready(self, db, *, workline_id: int) -> bool
async def is_estopped(self, db, *, workline_id: int) -> bool
async def assert_accepting_runtime_work(self, db, *, workline_id: int, blocked_error=RuntimeError) -> None
async def project_ready_after_start(self, db, *, workline_id: int, occurred_at=None) -> WorklineRuntimeStatusProjection
async def project_stopped_waiting_start(self, db, *, workline_id: int) -> WorklineRuntimeStatusProjection
async def project_reconciling(self, db, *, workline_id: int, occurred_at=None, reason: str) -> bool
async def project_estopped_active_hold(self, db, *, workline_id: int, reason: str | None) -> WorklineRuntimeStatusProjection
```

Existing callers must be updated in Task 6. Do not keep a synchronous compatibility overload.
`runtime_status_snapshot(...)`, `is_ready(...)`, `is_estopped(...)`, and `assert_accepting_runtime_work(...)` are read-only facade methods. If no projection row exists, they return/evaluate an explicit missing-projection snapshot policy; they do not insert rows. Only `project_*` methods write.
`runtime_status_snapshot_map(...)` must use one repository query for all requested `workline_ids`, return the same explicit missing-projection snapshot policy for absent rows, and preserve the input key boundary without inserting defaults.

- [ ] **Step 6: Export model and repository**

Add imports and `__all__` entries in:

```text
src/app/runtime/orchestration/__init__.py
src/app/runtime/orchestration/repositories/__init__.py
src/app/runtime/orchestration/services/__init__.py
```

- [ ] **Step 7: Run unit tests**

Run:

```bash
rtk uv run pytest tests/workline_runtime/test_workline_runtime_status_projection_service.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit runtime status projection model**

Run:

```bash
rtk git add src/app/runtime/orchestration/workline_runtime_status_projection.py src/app/runtime/orchestration/repositories/workline_runtime_status_projection_repository.py src/app/runtime/orchestration/services/workline_runtime_status_projection_service.py src/app/runtime/orchestration/__init__.py src/app/runtime/orchestration/repositories/__init__.py src/app/runtime/orchestration/services/__init__.py tests/workline_runtime/test_workline_runtime_status_projection_service.py
rtk git commit -m "feat(runtime): add workline runtime status projection"
```

Expected: commit succeeds.

## Task 6: 迁移所有 runtime_status 调用方到 async projection API

**Files:**
- Modify: `src/app/callback/services/callback_ingress_service.py`
- Modify: `src/app/workline/services/safety_service.py`
- Modify: `src/app/runtime/capabilities/phase4/start_admission_service.py`
- Modify: `src/app/runtime/capabilities/phase4/single_layer_rack_orchestration_service.py`
- Modify: `src/app/runtime/capabilities/phase4/smt_inbound_handoff_route_service.py`
- Modify: `src/app/runtime/orchestration/services/hold/runtime_hold_creation_service.py`
- Modify: `src/app/runtime/orchestration/services/hold/runtime_hold_release_service.py`
- Modify: `src/app/runtime/orchestration/services/reconciliation/runtime_reconciliation_service_impl.py`
- Modify: `src/app/runtime/orchestration/services/query/runtime_query_service.py`
- Modify: `src/app/runtime/orchestration/services/trace/trace_query_service.py`
- Modify: related tests under `tests/workline_runtime/`, `tests/api/`, `tests/contracts/`

- [ ] **Step 1: Search all call sites**

Run:

```bash
rtk rg -n "runtime_status_snapshot|assert_accepting_runtime_work|project_ready_after_start|project_stopped_waiting_start|project_reconciling|project_estopped_active_hold|is_ready\\(|is_estopped\\(|getattr\\([^\\n]*(runtime_status|active_safety_incident_id|stopped_at|stopped_reason|resumed_at)|\\.(runtime_status|active_safety_incident_id|stopped_at|stopped_reason|resumed_at)" src/app tests scripts -S
```

Expected: all call sites known before editing.

- [ ] **Step 2: Update safety service tests first**

In API/runtime tests that construct `SimpleNamespace(runtime_status=...)`, replace with fake projection service that returns `WorkLineRuntimeStatusSnapshot`.

Small fake shape:

```python
class FakeWorklineStatusProjectionService:
    async def runtime_status_snapshot(self, _db, *, workline_id):
        return WorkLineRuntimeStatusSnapshot(runtime_status="READY", source="runtime/orchestration", stopped_at=None, stopped_reason=None, resumed_at=None, active_safety_incident_id=None)
```

- [ ] **Step 3: Update safety service**

Update calls from:

```python
self.workline_status_projection_service.assert_accepting_runtime_work(workline, workline_id=workline.id)
```

to:

```python
await self.workline_status_projection_service.assert_accepting_runtime_work(db, workline_id=workline.id)
```

Apply equivalent async changes for snapshot and project methods.

Also move all safety runtime-state writes off the `WorkLine` object:

- `active_safety_incident_id` is written through `project_estopped_active_hold(...)` / release projection updates.
- `stopped_at`, `stopped_reason`, and `resumed_at` come from the runtime projection snapshot, not `workline` attributes.
- `WorkLine` remains the configuration model; the runtime projection is the single source for operational safety state.

- [ ] **Step 4: Update Phase4 capability services**

Replace `is_ready(workline)` with:

```python
await workline_runtime_status_projection_service.is_ready(db, workline_id=workline_id)
```

Where only `workline` object is available, use `getattr(workline, "id", None)` and fail fast if missing.
Replace `workline.active_safety_incident_id`, `workline.stopped_at`, `workline.stopped_reason`, and `workline.resumed_at` reads with runtime projection snapshot fields. `StartAdmissionService._guard_startable` must check `runtime_snapshot.active_safety_incident_id`, not the WorkLine model.

- [ ] **Step 5: Update runtime hold/reconciliation/query/trace services**

Replace all snapshot calls with:

```python
runtime_snapshot = await workline_runtime_status_projection_service.runtime_status_snapshot(db, workline_id=workline_id)
```

Keep outward response fields named `runtime_status` or `workline_runtime_status` if API contracts require them; only the storage source changes.

Performance boundary:

- `RuntimeQueryService.list_worklines(...)` must batch load all workline runtime snapshots once via `runtime_status_snapshot_map(...)`, just like it already batches devices and sessions.
- `_build_workline_summary(...)` and `_runtime_workline_readiness(...)` should accept an already-loaded `WorkLineRuntimeStatusSnapshot`; they must not perform DB reads internally.
- `get_workline_detail(...)`, `get_workline_monitor_projection(...)`, and `_build_workline_runtime_boundary(...)` should load a single snapshot once per workline request and pass it through builders that need status/readiness.
- `TraceQueryService._load_workline_projection(...)` should perform at most one projection lookup for the resolved workline id.
- Add regression tests around the query/trace fakes to assert list/overview paths do not issue one projection lookup per row.

- [ ] **Step 5.5: Update callback ingress production-event guard**

In `src/app/callback/services/callback_ingress_service.py`, replace direct reads of `workline.runtime_status` with an async runtime projection lookup by `workline_id`.

Required behavior:

- READY/unknown-by-policy accepts production events only when the chosen policy is explicit in code and tests.
- ESTOPPED / RECONCILING / STOPPED reject production events with the existing guard response shape and runtime status in diagnostics.
- Missing `workline_id` or projection lookup failure must not silently become “accept”; choose an explicit safe response and test it.
- Do not add a cross-request cache for safety state. Use a request-local resolver/snapshot variable so each callback request performs at most one projection lookup for the guard, and non-production events do not perform projection lookup at all.
- Add callback tests that count fake projection-service calls: production guard lookup is `1`, rejected production diagnostics reuse the same snapshot, and non-production callback paths stay at `0`.

- [ ] **Step 6: Run targeted tests**

Run:

```bash
rtk uv run pytest tests/workline_runtime/test_workline_runtime_status_projection_service.py tests/workline_runtime/test_runtime_reconciliation_idempotency.py tests/workline_runtime/test_runtime_query_workline_status_projection.py tests/api/test_workline_runtime_sse.py tests/api/test_workline_safety_operation_api.py tests/api/test_callback_event_api.py -q
```

Expected: PASS.

- [ ] **Step 7: Run runtime_status guardrail**

Run:

```bash
rtk uv run pytest tests/architecture/test_phase2_runtime_status_owner_guardrail.py -q
```

Expected: still FAIL only because model/migration fields remain, not because call sites read/write WorkLine field.

- [ ] **Step 8: Commit call site migration**

Run:

```bash
rtk git add src/app/callback/services/callback_ingress_service.py src/app/workline/services/safety_service.py src/app/runtime/capabilities/phase4/start_admission_service.py src/app/runtime/capabilities/phase4/single_layer_rack_orchestration_service.py src/app/runtime/capabilities/phase4/smt_inbound_handoff_route_service.py src/app/runtime/orchestration/services/hold src/app/runtime/orchestration/services/reconciliation src/app/runtime/orchestration/services/query src/app/runtime/orchestration/services/trace tests/workline_runtime tests/api tests/contracts
rtk git commit -m "refactor(runtime): read workline status from runtime projection"
```

Expected: commit succeeds.

## Task 7: 删除 WorkLine 运行态字段和 enum 依赖

**Files:**
- Modify: `src/app/workline/models/workline.py`
- Modify: `src/app/workline/models/safety.py`
- Modify: `src/app/workline/models/__init__.py`
- Modify: `tests/architecture/test_workline_service_shim_contract.py`
- Modify: seed/reset scripts under `scripts/data/`

- [ ] **Step 1: Remove runtime-state fields from WorkLine model**

Delete the following runtime-owned Field blocks from `WorkLine` in `src/app/workline/models/workline.py`:

- `runtime_status`
- `active_safety_incident_id`
- `stopped_at`
- `stopped_reason`
- `resumed_at`

Also remove unused import:

```python
from src.app.workline.models.safety import WorkLineRuntimeStatus
```

- [ ] **Step 2: Move enum ownership or keep only if still needed**

If `WorkLineRuntimeStatus` remains used by runtime projection, move the enum to a runtime-owned module:

```text
src/app/runtime/orchestration/workline_runtime_status_projection.py
```

Then remove `WorkLineRuntimeStatus` from `src/app/workline/models/safety.py` and `src/app/workline/models/__init__.py`.

- [ ] **Step 3: Update scripts/data**

Replace direct `work_lines.runtime_status`, safety incident, stopped/resumed SQL and seed object fields in:

```text
scripts/data/init_production_base_data.sql
scripts/data/reset_runtime_data.py
scripts/data/seed_runtime_monitor_smoke.py
scripts/data/sync_test_workline_devices.py
```

New SQL target is `wes_runtime.workline_runtime_status_projections`.

Example reset statement:

```sql
INSERT INTO wes_runtime.workline_runtime_status_projections (
    workline_id, runtime_status, source, active_safety_incident_id, stopped_at, stopped_reason, resumed_at
)
SELECT id, 'STOPPED', 'reset_runtime_data', NULL, NULL, 'RESET_RUNTIME_DATA', NULL
FROM wes_biz.work_lines
ON CONFLICT (workline_id) DO UPDATE
SET runtime_status = 'STOPPED',
    active_safety_incident_id = NULL,
    stopped_at = NULL,
    stopped_reason = 'RESET_RUNTIME_DATA',
    resumed_at = NULL;
```

- [ ] **Step 4: Update architecture shim test**

In `tests/architecture/test_workline_service_shim_contract.py`, remove comments/assertions saying `safety.py` must keep `WorkLineRuntimeStatus` because it is a WorkLine model field. Replace with assertion that no `WorkLineRuntimeStatus` import remains under `src/app/workline`.

- [ ] **Step 5: Run runtime_status search**

Run:

```bash
rtk rg -n "WorkLineRuntimeStatus|runtime_status: WorkLineRuntimeStatus|work_lines\\.(runtime_status|active_safety_incident_id|stopped_at|stopped_reason|resumed_at)|workline\\.(runtime_status|active_safety_incident_id|stopped_at|stopped_reason|resumed_at)" src/app/workline scripts/data tests/architecture -S
```

Expected: no WorkLine-owned runtime-state matches. Runtime-owned enum and projection field matches are allowed only under `src/app/runtime/orchestration/`.

- [ ] **Step 6: Run tests**

Run:

```bash
rtk uv run pytest tests/architecture/test_phase2_runtime_status_owner_guardrail.py tests/architecture/test_workline_service_shim_contract.py tests/workline_runtime/test_workline_runtime_status_projection_service.py tests/scripts/test_seed_runtime_monitor_smoke.py tests/scripts/test_sync_test_workline_devices.py -q
```

Expected: model/source assertions pass; migration assertion still fails until Task 8.

- [ ] **Step 7: Commit WorkLine model cleanup**

Run:

```bash
rtk git add src/app/workline/models/workline.py src/app/workline/models/safety.py src/app/workline/models/__init__.py scripts/data/init_production_base_data.sql scripts/data/reset_runtime_data.py scripts/data/seed_runtime_monitor_smoke.py scripts/data/sync_test_workline_devices.py tests/architecture/test_workline_service_shim_contract.py tests/architecture/test_phase2_runtime_status_owner_guardrail.py
rtk git commit -m "refactor(workline): remove runtime status from config model"
```

Expected: commit succeeds.

## Task 8: Alembic destructive cleanup migration

**Files:**
- Create: generated file under `migrations/versions/`
- Test: `tests/database/` and `tests/architecture/test_phase2_runtime_status_owner_guardrail.py`

- [ ] **Step 1: Generate migration**

Run:

```bash
rtk uv run alembic revision -m "drop legacy workline runtime residuals"
```

Expected: Alembic creates a new revision file with random revision ID. Do not hand-write the revision ID.

- [ ] **Step 2: Edit generated migration**

Migration upgrade must:

1. Create `wes_runtime.workline_runtime_status_projections`.
2. Backfill from existing `wes_biz.work_lines.runtime_status`, `stopped_at`, `stopped_reason`, `resumed_at`, `active_safety_incident_id`.
3. Drop indexes for `runtime_status`, `active_safety_incident_id`, `stopped_at`, and `resumed_at` if present.
4. Drop enum/check constraint for `work_lines.runtime_status` if present.
5. Drop columns `wes_biz.work_lines.runtime_status`, `active_safety_incident_id`, `stopped_at`, `stopped_reason`, and `resumed_at`.
6. Drop table `wes_biz.bin_transit_memberships`.

Migration downgrade may recreate schema columns/tables but must state data boundary in comments: dropped `bin_transit_memberships` data is not recoverable without DB snapshot.

- [ ] **Step 2.5: Add destructive migration operational guardrails**

In the generated migration, set conservative session-local timeouts before destructive statements and keep them scoped to the migration transaction:

```sql
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';
```

Before running this migration outside a disposable local DB, record:

- `SELECT count(*) FROM wes_biz.work_lines;`
- `SELECT count(*) FROM wes_biz.bin_transit_memberships;`
- whether callback/runtime workers are stopped or production traffic is otherwise quiesced for the maintenance window
- start/end timestamps for `alembic upgrade head`

If `lock_timeout` or `statement_timeout` trips, do not raise the timeout blindly. Stop, report the row counts and blocker, then decide whether to rerun in a larger maintenance window or split the migration.

- [ ] **Step 3: Add migration smoke assertions**

Add or update architecture text checks only as anti-omission smoke:

```python
assert "workline_runtime_status_projections" in migration_text
assert "bin_transit_memberships" in migration_text
assert "runtime_status" in migration_text
```

Add a database migration smoke under `tests/database/` or the repo’s existing migration-test pattern that runs against an upgraded test database and verifies:

- `wes_runtime.workline_runtime_status_projections` exists.
- `wes_biz.bin_transit_memberships` does not exist.
- `wes_biz.work_lines` no longer has `runtime_status`, `active_safety_incident_id`, `stopped_at`, `stopped_reason`, or `resumed_at`.
- `workline_runtime_status_projections.workline_id` has a unique constraint or unique index.
- Backfill preserves existing WorkLine runtime values: a pre-upgrade `READY/ESTOPPED/RECONCILING` row, active incident id, stopped/resumed timestamps, and stopped reason appear on the new projection row after upgrade.

- [ ] **Step 4: Run migration upgrade/downgrade locally**

Run:

```bash
rtk uv run alembic upgrade head
rtk uv run alembic downgrade -1
rtk uv run alembic upgrade head
```

Expected: all commands complete. If local DB is unavailable, stop and report blocker rather than claiming migration verified.

The implementation note or PR verification must include the preflight row counts, migration elapsed time, and whether timeouts were hit.

- [ ] **Step 5: Run database and architecture tests**

Run:

```bash
rtk uv run pytest tests/architecture/test_phase2_runtime_status_owner_guardrail.py tests/architecture/test_phase5_legacy_absence_guardrail.py tests/architecture/test_phase5_business_legacy_absence_guardrail.py tests/database/test_workline_runtime_status_projection_migration.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit migration**

Run:

```bash
rtk git add migrations/versions tests/architecture/test_phase2_runtime_status_owner_guardrail.py
rtk git commit -m "db(runtime): drop legacy workline runtime residuals"
```

Expected: commit succeeds.

## Task 9: 更新 Phase5 gates、矩阵和主计划状态

**Files:**
- Modify: `scripts/check_phase5_readiness_gate.py`
- Modify: `scripts/check_phase5_business_destructive_cleanup_gate.py`
- Modify: `docs/architecture/workline-and-plugin-restructuring.md`
- Modify: `docs/architecture/legacy-cleanup-execution-plan.md`
- Modify: `docs/architecture/legacy-cleanup-matrix.md`
- Modify: `docs/architecture/legacy-cleanup-matrix.csv`
- Modify: `docs/architecture/phase5-business-destructive-cleanup-ledger.md`
- Modify: `docs/architecture/file_index.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Tighten Phase5 gates**

Ensure `scripts/check_phase5_readiness_gate.py --lane technical` and `--lane business` both fail on:

```text
BinTransitMembership
BinTransitQueue
bin_transit_memberships
WorkLine.runtime_status
work_lines.active_safety_incident_id / stopped_at / stopped_reason / resumed_at
runtime_status compatibility projection
```

Allow these names only when they refer to runtime-owned `workline_runtime_status_projections`, response schemas, diagnostics, migration history, or explicit historical documentation.

- [ ] **Step 2: Tighten business destructive cleanup gate**

In `scripts/check_phase5_business_destructive_cleanup_gate.py`, remove special-case language that excludes `WorkLine.runtime_status` from final cleanup. Final mode should require it closed.

- [ ] **Step 3: Update docs**

In `docs/architecture/workline-and-plugin-restructuring.md`:

- update frontmatter status to final cleanup completed
- remove “`WorkLine.runtime_status` 物理字段删除仍需独立 schema/data cleanup”
- add verification record for new migration and gates
- state old `BinTransitMembership/BinTransitQueue` production surface is absent

In `legacy-cleanup-execution-plan.md` and `legacy-cleanup-matrix.md`, mark final cleanup complete and no remaining schema/data cleanup.

- [ ] **Step 4: Regenerate matrix if needed**

Run:

```bash
rtk uv run python scripts/generate_legacy_matrix.py
```

Expected: matrix has no pending BinTransit/runtime_status cleanup rows.

- [ ] **Step 5: Run gate commands**

Run:

```bash
rtk uv run python scripts/check_phase5_readiness_gate.py --lane technical
rtk uv run python scripts/check_phase5_readiness_gate.py --lane business --phase3-p0-e2e-artifact reports/phase3/phase3-p0-e2e.json --phase3-benchmark-artifact reports/phase3/phase3-production-benchmark.json --phase4-evidence-artifact reports/phase4/runtime-evidence-production.json
rtk uv run python scripts/check_phase5_business_destructive_cleanup_gate.py --mode final
```

Expected: all pass.

- [ ] **Step 6: Commit docs and gates**

Run:

```bash
rtk git add scripts/check_phase5_readiness_gate.py scripts/check_phase5_business_destructive_cleanup_gate.py docs/architecture/workline-and-plugin-restructuring.md docs/architecture/legacy-cleanup-execution-plan.md docs/architecture/legacy-cleanup-matrix.md docs/architecture/legacy-cleanup-matrix.csv docs/architecture/phase5-business-destructive-cleanup-ledger.md docs/architecture/file_index.md CHANGELOG.md
rtk git commit -m "docs(workline): mark restructuring final cleanup complete"
```

Expected: commit succeeds.

## Task 10: Final Verification

**Files:**
- Verify only; no source edits expected.

- [ ] **Step 1: Run exact residual searches**

Run:

```bash
rtk rg -n "BinTransitMembership|BinTransitQueue|bin_transit_membership|bin_transit_memberships" src tests scripts -S
rtk rg -n "WorkLine\\.runtime_status|runtime_status: WorkLineRuntimeStatus|work_lines\\.runtime_status|compatibility projection" src tests scripts docs/architecture -S
```

Expected: first command has no matches in `src`, `tests`, `scripts`. Second command has no WorkLine-owned runtime status matches; docs may mention historical cleanup only in changelog/archive wording if clearly marked as past.

- [ ] **Step 2: Run targeted suites**

Run:

```bash
rtk uv run pytest tests/architecture/test_phase2_runtime_status_owner_guardrail.py tests/architecture/test_phase5_legacy_absence_guardrail.py tests/architecture/test_phase5_business_legacy_absence_guardrail.py tests/database/test_workline_runtime_status_projection_migration.py tests/workline_runtime/test_workline_runtime_status_projection_service.py tests/workline_runtime/test_runtime_reconciliation_idempotency.py tests/workline_runtime/test_runtime_query_workline_status_projection.py tests/runtime/orchestration/test_conveyor_queue_membership_writer_service.py tests/handling/test_handling_operation_lifecycle.py tests/api/test_callback_event_api.py tests/api/test_workline_runtime_sse.py tests/api/test_workline_safety_operation_api.py tests/scripts/test_seed_runtime_monitor_smoke.py tests/scripts/test_sync_test_workline_devices.py -q
```

Expected: PASS.

- [ ] **Step 3: Run Phase3/4/5 gates**

Run:

```bash
rtk uv run python scripts/check_phase3_closure_gate.py --closure-profile production --p0-e2e-artifact reports/phase3/phase3-p0-e2e.json --benchmark-artifact reports/phase3/phase3-production-benchmark.json
rtk uv run python scripts/check_phase4_runtime_readiness_gate.py --readiness-profile production --phase4-runtime-evidence-artifact reports/phase4/runtime-evidence-production.json --p0-e2e-artifact reports/phase3/phase3-p0-e2e.json --benchmark-artifact reports/phase3/phase3-production-benchmark.json
rtk uv run python scripts/check_phase5_readiness_gate.py --lane technical
rtk uv run python scripts/check_phase5_readiness_gate.py --lane business --phase3-p0-e2e-artifact reports/phase3/phase3-p0-e2e.json --phase3-benchmark-artifact reports/phase3/phase3-production-benchmark.json --phase4-evidence-artifact reports/phase4/runtime-evidence-production.json
rtk uv run python scripts/check_phase5_business_destructive_cleanup_gate.py --mode final
```

Expected: all pass.

- [ ] **Step 4: Run project quality gate**

Run:

```bash
rtk ./scripts/git-quality-gate.sh --profile quality
```

Expected: profile passed. The architecture guardrail output must no longer list allowlisted C2 entries for deleted `bin_transit_membership`.

- [ ] **Step 5: Run collect-only governance**

Run:

```bash
rtk bash -lc 'uv run pytest --collect-only -q -o addopts="" | tail -5'
```

Expected: collection succeeds and reports total tests. No `tests/` root `test_*.py` files are created.

- [ ] **Step 6: Run GitNexus detect changes**

Use GitNexus MCP:

```text
detect_changes({scope: "compare", base_ref: "develop", repo: "wes_backend"})
```

Expected: affected symbols match final cleanup scope: handling queue projection removal, runtime status projection migration, migration/docs/gates. No unrelated modules.

- [ ] **Step 7: Final commit if needed**

If verification fixes were needed:

```bash
rtk git add <intentional files>
rtk git commit -m "test(workline): verify final restructuring cleanup"
```

Expected: no uncommitted intentional changes remain.

## Self-Review

- **Spec coverage:** Covers main plan §3.7 old `BinTransitMembership/BinTransitQueue` deletion, §4.4 dynamic `ConveyorQueueMembership`, §10.0/§10.6 `WorkLine.runtime_status` independent schema/data cleanup, Phase5 readiness/business gates, and quality profile.
- **Placeholder scan:** No task contains unresolved placeholder language. Code snippets are intentionally small because repository planning rules forbid full function/class implementations in planning docs.
- **Type consistency:** Runtime status projection uses `workline_id` as the service boundary throughout; queue cleanup uses `ConveyorQueueMembershipWriterService` and string `queue_code`, not `BinTransitQueue`.

## Plan Eng Review Required Outputs

### NOT in scope

- Frontend/runtime monitor UI redesign: this cleanup keeps API response fields compatible where required and does not introduce UI changes.
- Multi-release expand/migrate/contract rollout: accepted as final cleanup work; operational migration guardrails are added instead of splitting the release.
- Preserving `BinTransitMembership` table data as a production table: runtime queue membership plus `ObjectTransitionEvent` keeps the transition trail; deleted table data still requires DB snapshot to recover.
- Cross-request caching for WorkLine safety/runtime status: rejected because safety state must stay fresh; only request-local reuse is allowed.
- New parallel runtime subsystem: plan reuses existing runtime/orchestration services and does not create a second runtime model.
- Broad plugin framework redesign: this plan only closes the WorkLine/plugin restructuring residuals listed in the main architecture plan.

### What already exists

- `ConveyorQueueMembershipWriterService` already owns runtime queue membership writes; the plan extends it with terminal/reconciling methods instead of rebuilding handling membership logic.
- `ObjectTransitionEventService` already records object state transitions; the plan reuses it to preserve queue movement evidence after deleting `BinTransit`.
- `WorkLineRuntimeStatusProjectionService` already centralizes the compatibility projection; the plan turns it into the runtime-native async facade rather than scattering status reads.
- `RuntimeQueryService.list_worklines(...)` already batches devices and sessions by workline id; the plan applies the same pattern to runtime status snapshots.
- Phase5 technical/business gates already scan legacy residuals; the plan tightens their allowlists instead of adding a separate gate family.
- Existing Alembic workflow and database test directories already support schema cleanup verification; the plan uses generated revisions and `tests/database/` smoke coverage.

### Data flow diagram

```text
External handling callback
        |
        v
HandlingLifecycleService
        |
        | resolve queue_projection_context(workline_id, queue_code, identity)
        v
ConveyorQueueMembershipWriterService
        |
        +--> ConveyorQueueMembership runtime projection
        |
        +--> ObjectTransitionEvent(domain=HANDLING, object_type=CONVEYOR_QUEUE_MEMBERSHIP)

WorkLine runtime/safety state
        |
        v
WorkLineRuntimeStatusProjectionService
        |
        +--> single snapshot for detail/callback
        +--> snapshot map for list/overview
        |
        v
API response fields keep runtime_status/workline_runtime_status names
```

Inline ASCII diagrams should be considered during implementation in:

- `src/app/runtime/orchestration/services/conveyor_queue_membership_writer_service.py`: queue membership state transitions `ACTIVE -> LEFT/RECONCILING`.
- `src/app/runtime/orchestration/services/workline_runtime_status_projection_service.py`: read-only snapshot path versus write-only `project_*` path.
- `src/app/runtime/orchestration/services/query/runtime_query_service.py`: batched workline summary assembly.
- `tests/runtime/orchestration/test_conveyor_queue_membership_writer_service.py`: idempotent transition-event setup.

### Failure modes

| Code path | Realistic production failure | Test coverage | Error handling / user visibility |
| --- | --- | --- | --- |
| Runtime queue writer `close_active` / `mark_reconciling_for_identity` | Callback references an identity with no active queue membership | Negative writer tests plus lifecycle unresolved-operation regression | Explicit `ValueError` for strict writer use; lifecycle path skips projection non-fatally with diagnostic |
| Queue transition audit | Idempotent callback replay duplicates `ObjectTransitionEvent` rows | Writer tests assert no duplicate transition on replay | Duplicate prevention is visible in transition-event assertions |
| Handling lifecycle context resolution | `operation_key` no longer resolves to a `HandlingOperation`, so `workline_id` is missing | Lifecycle regression keeps step/move status update while writer is not called | Diagnostic `HANDLING_QUEUE_PROJECTION_CONTEXT_MISSING`, not silent acceptance |
| Runtime status snapshot reads | Projection row is missing after migration or repair drift | Projection-service fake repository test covers no implicit writes | Explicit missing-projection snapshot policy; ordinary reads do not create rows |
| Callback production-event guard | Projection lookup fails or workline id is missing | Callback tests cover safe response and call counts | Production event is rejected with existing guard response shape and diagnostics |
| Runtime list/overview | Per-workline async lookup creates N+1 query pattern | Query fake tests count one batch lookup | Performance regression is caught before merge |
| Destructive migration | Backfill/drop locks longer than expected | Migration smoke plus operational preflight evidence | `lock_timeout` / `statement_timeout` stop the migration; row counts and blocker must be reported |
| Final gates/docs | Legacy words remain in source or docs claim completion too early | Phase5 gates, residual `rg`, collect-only governance | Gate failure blocks final verification |

Critical silent gaps after review: none. All identified silent-risk paths now require either regression tests, explicit diagnostics, or operational stop conditions.

### Worktree parallelization strategy

| Step | Modules touched | Depends on |
| --- | --- | --- |
| Guardrails and failing tests | `tests/architecture/`, `scripts/` | - |
| Runtime queue writer adapter | `src/app/runtime/orchestration/`, `tests/runtime/` | Guardrails |
| Handling lifecycle migration and BinTransit deletion | `src/app/handling/`, `tests/handling/` | Runtime queue writer adapter |
| Runtime status projection model/service | `src/app/runtime/orchestration/`, `tests/workline_runtime/` | Guardrails |
| Runtime status call-site migration and WorkLine model cleanup | `src/app/runtime/`, `src/app/workline/`, `src/app/callback/`, `tests/api/` | Runtime status projection model/service |
| Destructive migration | `migrations/`, `tests/database/` | Runtime status projection model/service, WorkLine model cleanup |
| Docs, gates, final verification | `docs/architecture/`, `scripts/`, `CHANGELOG.md` | All implementation lanes |

Parallel lanes:

- Lane A: Runtime queue writer adapter -> handling lifecycle migration -> BinTransit deletion. Sequential because the lifecycle step depends on writer methods.
- Lane B: Runtime status projection model/service -> call-site migration -> WorkLine model cleanup -> destructive migration. Sequential because callers and schema depend on the projection facade.
- Lane C: Docs/gates/final verification. Starts only after Lane A and Lane B merge.

Execution order: run Guardrails first, then Lane A and Lane B can proceed in parallel worktrees with coordination around `src/app/runtime/orchestration/`. Merge both, run Lane C in the integration branch.

Conflict flags:

- Lane A and Lane B both touch `src/app/runtime/orchestration/` and `tests/workline_runtime/` adjacent surfaces. Keep branches small and merge after Task 2/Task 5 checkpoints to reduce conflict size.
- Migration and WorkLine model cleanup must not be merged before all production call sites stop reading WorkLine runtime-state columns.

### TODOS.md updates

No separate TODO item is recommended. Every review finding is now part of this implementation plan, and deferring any of them would leave the restructuring incomplete.

### Implementation Tasks

Synthesized from this review's findings. Each task derives from a specific finding above. Run with Claude Code or Codex; checkbox as you ship.

- [ ] **T1 (P1, human: ~2h / CC: ~15min)** - Runtime queue projection - Add terminal/reconciling writer semantics with transition-event idempotency.
  - Surfaced by: Architecture/Tests - deleting `BinTransitMembership` must preserve queue movement evidence and replay safety.
  - Files: `src/app/runtime/orchestration/services/conveyor_queue_membership_writer_service.py`, `src/app/runtime/orchestration/repositories/conveyor_queue_membership_repository.py`, `tests/runtime/orchestration/test_conveyor_queue_membership_writer_service.py`.
  - Verify: `rtk uv run pytest tests/runtime/orchestration/test_conveyor_queue_membership_writer_service.py -q`.
- [ ] **T2 (P1, human: ~2h / CC: ~20min)** - Handling lifecycle - Route legacy queue callbacks to runtime writer with explicit `workline_id` context.
  - Surfaced by: Architecture - lifecycle could update step/move state while lacking enough context to write runtime queue projection safely.
  - Files: `src/app/handling/services/lifecycle_service.py`, `tests/handling/test_handling_operation_lifecycle.py`.
  - Verify: `rtk uv run pytest tests/handling/test_handling_operation_lifecycle.py -q`.
- [ ] **T3 (P1, human: ~3h / CC: ~25min)** - WorkLine runtime status projection - Make runtime projection the only storage source for status and safety fields.
  - Surfaced by: Architecture/Code Quality - `runtime_status`, `active_safety_incident_id`, `stopped_at`, `stopped_reason`, and `resumed_at` must leave the WorkLine config table together.
  - Files: `src/app/runtime/orchestration/workline_runtime_status_projection.py`, `src/app/runtime/orchestration/services/workline_runtime_status_projection_service.py`, `src/app/workline/models/workline.py`, `tests/workline_runtime/test_workline_runtime_status_projection_service.py`.
  - Verify: `rtk uv run pytest tests/workline_runtime/test_workline_runtime_status_projection_service.py tests/architecture/test_phase2_runtime_status_owner_guardrail.py -q`.
- [ ] **T4 (P1, human: ~2h / CC: ~20min)** - Runtime callers and callback guard - Migrate status reads to async projection with batch/request-local reuse.
  - Surfaced by: Architecture/Performance - callback guard and runtime overview must not read dropped WorkLine fields or introduce N+1 DB lookups.
  - Files: `src/app/callback/services/callback_ingress_service.py`, `src/app/runtime/orchestration/services/query/runtime_query_service.py`, `src/app/runtime/orchestration/services/trace/trace_query_service.py`, related `tests/api/` and `tests/workline_runtime/`.
  - Verify: `rtk uv run pytest tests/workline_runtime/test_runtime_query_workline_status_projection.py tests/api/test_callback_event_api.py tests/api/test_workline_runtime_sse.py -q`.
- [ ] **T5 (P1, human: ~2h / CC: ~15min)** - Destructive migration - Backfill projection and drop legacy schema with row-count and timeout evidence.
  - Surfaced by: Tests/Performance - static migration text checks are insufficient for destructive cleanup and lock behavior.
  - Files: `migrations/versions/`, `tests/database/test_workline_runtime_status_projection_migration.py`.
  - Verify: `rtk uv run alembic upgrade head && rtk uv run alembic downgrade -1 && rtk uv run alembic upgrade head && rtk uv run pytest tests/database/test_workline_runtime_status_projection_migration.py -q`.
- [ ] **T6 (P2, human: ~1h / CC: ~10min)** - Final gates/docs - Mark restructuring complete only after residual scans, gates, and quality profile pass.
  - Surfaced by: Code Quality - final docs and gate allowlists must not keep stale exceptions after cleanup.
  - Files: `scripts/check_phase5_readiness_gate.py`, `scripts/check_phase5_business_destructive_cleanup_gate.py`, `docs/architecture/`, `CHANGELOG.md`.
  - Verify: `rtk uv run python scripts/check_phase5_business_destructive_cleanup_gate.py --mode final && rtk ./scripts/git-quality-gate.sh --profile quality`.

### Review completion summary

- Step 0: Scope Challenge - scope accepted as-is after complexity warning.
- Architecture Review: 5 issues found, all folded into the plan.
- Code Quality Review: 4 issues found, all folded into the plan.
- Test Review: diagram produced, 6 gaps identified, regression requirements added.
- Performance Review: 2 issues found, both folded into the plan.
- NOT in scope: written.
- What already exists: written.
- TODOS.md updates: 0 items proposed because no finding should be deferred.
- Failure modes: 0 critical silent gaps remain after plan updates.
- Outside voice: skipped; this is backend cleanup with no UI/product-scope expansion, and eng findings are already incorporated.
- Parallelization: 3 lanes, 2 parallel implementation lanes plus 1 sequential integration lane.
- Lake Score: 15/15 recommendations chose complete option.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 2 | clean | Final review clean; prior scope review accepted 3/3 proposals |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | - | No fresh codex-plan-review entry in the 7-day dashboard window |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 8 | clean | 17 issues, 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | - | Not applicable for backend cleanup |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | - | Not required for this cleanup plan |

- **VERDICT:** CEO + ENG CLEARED - ready to implement.

NO UNRESOLVED DECISIONS
