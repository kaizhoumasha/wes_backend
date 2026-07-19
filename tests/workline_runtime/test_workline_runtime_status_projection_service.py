from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql, sqlite

from src.app.runtime.orchestration.repositories.workline_runtime_status_projection_repository import (
    WorklineRuntimeStatusProjectionRepository,
)
from src.app.runtime.orchestration.services.hold.runtime_hold_creation_service import RuntimeHoldCreationService
from src.app.runtime.orchestration.services.workline_runtime_status_projection_service import (
    WorkLineRuntimeStatusProjectionService,
)
from src.app.runtime.orchestration.workline_runtime_status_projection import (
    WorkLineRuntimeStatus,
    WorklineRuntimeStatusProjection,
)
from src.utils.timezone import timezone


class _RuntimeHoldRepository:
    async def create_open_hold(self, *_args, **_kwargs):
        return SimpleNamespace(id=101)


class _WorklineRepository:
    def __init__(self, workline):
        self.workline = workline

    async def get_for_update(self, *_args, **_kwargs):
        return self.workline


class _ProjectionSpy:
    def __init__(self):
        self.calls = []

    async def project_reconciling(self, _db, *, workline_id, occurred_at, reason):
        self.calls.append((workline_id, occurred_at, reason))


class _RuntimeStatusProjectionSpy:
    def __init__(self):
        self.estop_calls = []
        self.reconciling_calls = []
        self.accepting_calls = []

    async def runtime_status_snapshot(self, _db, *, workline_id):
        return SimpleNamespace(
            runtime_status=WorkLineRuntimeStatus.READY.value,
            stopped_at=None,
            stopped_reason=None,
            resumed_at=None,
            active_safety_incident_id=None,
        )

    async def assert_accepting_runtime_work(self, _db, *, workline_id, blocked_error=RuntimeError):
        self.accepting_calls.append((workline_id, blocked_error))

    async def project_estopped_active_hold(self, _db, *, workline_id, reason, **kwargs):
        self.estop_calls.append((workline_id, reason, kwargs))

    async def project_reconciling(self, _db, *, workline_id, occurred_at, reason):
        self.reconciling_calls.append((workline_id, occurred_at, reason))
        return True


class _ProjectionRepository:
    def __init__(self, rows=None):
        self.rows = dict(rows or {})
        self.get_calls = []
        self.list_calls = []
        self.ensure_calls = []
        self.upsert_calls = []

    async def get_by_workline_id(self, _db, workline_id, *, for_update=False):
        self.get_calls.append((workline_id, for_update))
        return self.rows.get(workline_id)

    async def list_by_workline_ids(self, _db, workline_ids):
        self.list_calls.append(tuple(workline_ids))
        return {workline_id: self.rows[workline_id] for workline_id in workline_ids if workline_id in self.rows}

    async def ensure_default(self, _db, workline_id):
        self.ensure_calls.append(workline_id)
        row = SimpleNamespace(workline_id=workline_id, runtime_status=WorkLineRuntimeStatus.STOPPED.value)
        self.rows[workline_id] = row
        return row

    async def upsert_status(self, _db, **kwargs):
        self.upsert_calls.append(kwargs)
        row = SimpleNamespace(**kwargs)
        self.rows[kwargs["workline_id"]] = row
        return row


def test_projection_foreign_ids_use_sql_compatible_bigint():
    from sqlalchemy import BigInteger

    columns = WorklineRuntimeStatusProjection.__table__.c

    assert isinstance(columns.workline_id.type, BigInteger)
    assert isinstance(columns.active_safety_incident_id.type, BigInteger)
    assert columns.workline_id.type.compile(dialect=postgresql.dialect()).upper() == "BIGINT"
    assert columns.workline_id.type.compile(dialect=sqlite.dialect()).upper() == "INTEGER"
    assert columns.active_safety_incident_id.type.compile(dialect=postgresql.dialect()).upper() == "BIGINT"
    assert columns.active_safety_incident_id.type.compile(dialect=sqlite.dialect()).upper() == "INTEGER"


@pytest.mark.asyncio
async def test_projection_service_ensure_default_delegates_repository():
    repository = _ProjectionRepository()
    projection = WorkLineRuntimeStatusProjectionService(repository=repository)

    row = await projection.ensure_default(object(), workline_id=9007199254740993)

    assert row.runtime_status == WorkLineRuntimeStatus.STOPPED.value
    assert repository.ensure_calls == [9007199254740993]


@pytest.mark.asyncio
async def test_projection_repository_ensure_default_does_not_overwrite_conflicting_existing_status(db_session):
    repository = WorklineRuntimeStatusProjectionRepository()
    existing = WorklineRuntimeStatusProjection(
        workline_id=9007199254740993,
        runtime_status=WorkLineRuntimeStatus.ESTOPPED.value,
        source="runtime/orchestration",
        stopped_reason="ESTOP_PRESSED",
        active_safety_incident_id=9007199254740995,
    )
    db_session.add(existing)
    await db_session.flush()

    real_get = repository.get_by_workline_id
    calls = 0

    async def get_by_workline_id(_db, workline_id, *, for_update=False):
        nonlocal calls
        calls += 1
        if calls == 1:
            return None
        return await real_get(_db, workline_id, for_update=for_update)

    repository.get_by_workline_id = get_by_workline_id

    row = await repository.ensure_default(db_session, 9007199254740993)
    await db_session.refresh(existing)

    assert row.runtime_status == WorkLineRuntimeStatus.ESTOPPED.value
    assert existing.runtime_status == WorkLineRuntimeStatus.ESTOPPED.value
    assert existing.stopped_reason == "ESTOP_PRESSED"
    assert existing.active_safety_incident_id == 9007199254740995


@pytest.mark.asyncio
async def test_projection_ready_after_start_sets_ready_snapshot_and_resume_time():
    resumed_at = timezone.now_for_db()
    repository = _ProjectionRepository(
        {
            45: SimpleNamespace(
                workline_id=45,
                runtime_status=WorkLineRuntimeStatus.STOPPED.value,
                stopped_at="old-stop",
                stopped_reason="RECOVERY_CLEARED_WAITING_START",
                resumed_at=None,
                active_safety_incident_id=None,
                source="runtime/orchestration",
            )
        }
    )
    projection = WorkLineRuntimeStatusProjectionService(repository=repository)

    row = await projection.project_ready_after_start(object(), workline_id=45, occurred_at=resumed_at)
    snapshot = await projection.runtime_status_snapshot(object(), workline_id=45)

    assert row.runtime_status == WorkLineRuntimeStatus.READY.value
    assert row.stopped_reason is None
    assert row.resumed_at == resumed_at
    assert snapshot.runtime_status == WorkLineRuntimeStatus.READY.value
    assert snapshot.source == "runtime/orchestration"
    assert snapshot.resumed_at == resumed_at
    assert await projection.is_ready(object(), workline_id=45) is True


@pytest.mark.asyncio
async def test_projection_snapshot_missing_row_is_explicit_and_read_only():
    repository = _ProjectionRepository()
    projection = WorkLineRuntimeStatusProjectionService(repository=repository)

    snapshot = await projection.runtime_status_snapshot(object(), workline_id=45)

    assert snapshot.runtime_status is None
    assert snapshot.source == "runtime/orchestration:missing"
    assert snapshot.active_safety_incident_id is None
    assert repository.ensure_calls == []
    assert repository.upsert_calls == []
    with pytest.raises(RuntimeError, match="WORKLINE_UNKNOWN"):
        await projection.assert_accepting_runtime_work(object(), workline_id=45)


@pytest.mark.asyncio
async def test_projection_snapshot_map_batches_and_marks_missing_rows():
    repository = _ProjectionRepository(
        {
            45: SimpleNamespace(
                workline_id=45,
                runtime_status=WorkLineRuntimeStatus.READY.value,
                source="runtime/orchestration",
                stopped_at=None,
                stopped_reason=None,
                resumed_at="resume",
                active_safety_incident_id=None,
            )
        }
    )
    projection = WorkLineRuntimeStatusProjectionService(repository=repository)

    snapshots = await projection.runtime_status_snapshot_map(object(), workline_ids=[45, 46, 45])

    assert repository.list_calls == [(45, 46)]
    assert snapshots[45].runtime_status == WorkLineRuntimeStatus.READY.value
    assert snapshots[46].runtime_status is None
    assert repository.ensure_calls == []
    assert repository.upsert_calls == []


@pytest.mark.asyncio
async def test_projection_reconciling_preserves_estopped_projection():
    repository = _ProjectionRepository(
        {
            45: SimpleNamespace(
                workline_id=45,
                runtime_status=WorkLineRuntimeStatus.ESTOPPED.value,
                stopped_at="existing-stop",
                stopped_reason="ESTOP_PRESSED",
                resumed_at=None,
                active_safety_incident_id=9901,
            )
        }
    )
    projection = WorkLineRuntimeStatusProjectionService(repository=repository)

    projected = await projection.project_reconciling(
        object(),
        workline_id=45,
        occurred_at="now",
        reason="RESOURCE_CONFLICT",
    )

    assert projected is False
    assert repository.upsert_calls == []


@pytest.mark.asyncio
async def test_projection_reconciling_sets_runtime_hold_reason_and_clears_resume_time():
    stopped_at = timezone.now_for_db()
    repository = _ProjectionRepository(
        {
            45: SimpleNamespace(
                workline_id=45,
                runtime_status=WorkLineRuntimeStatus.READY.value,
                stopped_at=None,
                stopped_reason=None,
                resumed_at="old-resume",
                active_safety_incident_id=None,
            )
        }
    )
    projection = WorkLineRuntimeStatusProjectionService(repository=repository)

    projected = await projection.project_reconciling(
        object(),
        workline_id=45,
        occurred_at=stopped_at,
        reason="RESOURCE_CONFLICT",
    )
    snapshot = await projection.runtime_status_snapshot(object(), workline_id=45)

    assert projected is True
    assert snapshot.runtime_status == WorkLineRuntimeStatus.RECONCILING.value
    assert snapshot.stopped_at == stopped_at
    assert snapshot.stopped_reason == "RESOURCE_CONFLICT"
    assert snapshot.resumed_at is None


@pytest.mark.asyncio
async def test_projection_stopped_waiting_start_clears_resume_projection():
    repository = _ProjectionRepository(
        {
            45: SimpleNamespace(
                workline_id=45,
                runtime_status=WorkLineRuntimeStatus.RECONCILING.value,
                stopped_at="cleared-at",
                resumed_at="old-resume",
                stopped_reason="CALLBACK_DEADLINE_EXPIRED",
                active_safety_incident_id=None,
            )
        }
    )
    projection = WorkLineRuntimeStatusProjectionService(repository=repository)

    await projection.project_stopped_waiting_start(object(), workline_id=45)
    snapshot = await projection.runtime_status_snapshot(object(), workline_id=45)

    assert snapshot.runtime_status == WorkLineRuntimeStatus.STOPPED.value
    assert snapshot.stopped_reason == "RECOVERY_CLEARED_WAITING_START"
    assert snapshot.resumed_at is None


@pytest.mark.asyncio
async def test_projection_estopped_active_hold_sets_reason_and_blocks_runtime_work():
    repository = _ProjectionRepository(
        {
            45: SimpleNamespace(
                workline_id=45,
                runtime_status=WorkLineRuntimeStatus.READY.value,
                stopped_at=None,
                stopped_reason=None,
                resumed_at="old-resume",
                active_safety_incident_id=None,
            )
        }
    )
    projection = WorkLineRuntimeStatusProjectionService(repository=repository)

    await projection.project_estopped_active_hold(
        object(),
        workline_id=45,
        reason="ESTOP_PRESSED",
        active_safety_incident_id=9901,
    )
    snapshot = await projection.runtime_status_snapshot(object(), workline_id=45)

    assert snapshot.runtime_status == WorkLineRuntimeStatus.ESTOPPED.value
    assert snapshot.active_safety_incident_id == 9901
    with pytest.raises(RuntimeError, match="WORKLINE_ESTOPPED"):
        await projection.assert_accepting_runtime_work(object(), workline_id=45)


@pytest.mark.asyncio
async def test_projection_assert_accepting_runtime_work_accepts_ready_only():
    repository = _ProjectionRepository(
        {
            45: SimpleNamespace(
                workline_id=45,
                runtime_status=WorkLineRuntimeStatus.READY.value,
                source="runtime/orchestration",
            )
        }
    )
    projection = WorkLineRuntimeStatusProjectionService(repository=repository)

    await projection.assert_accepting_runtime_work(object(), workline_id=45)


@pytest.mark.asyncio
async def test_resource_reconciliation_uses_compat_projection_service():
    workline = SimpleNamespace(
        runtime_status=WorkLineRuntimeStatus.READY,
        stopped_at=None,
        stopped_reason=None,
    )
    projection = _ProjectionSpy()
    service = RuntimeHoldCreationService(
        repository=_RuntimeHoldRepository(),
        workline_repository=_WorklineRepository(workline),
        workline_status_projection_service=projection,
    )

    hold = await service.create_for_resource_reconciliation(
        object(),
        workline_id=7,
        source_reason="RESOURCE_CONFLICT",
        source_event_id="evt-1",
        evidence={"bin_code": "BIN-01"},
    )

    assert hold.id == 101
    assert len(projection.calls) == 1
    assert projection.calls[0][0] == 7
    assert projection.calls[0][2] == "RESOURCE_CONFLICT"


@pytest.mark.asyncio
async def test_safety_estop_uses_compat_projection_service():
    from src.app.workline.services.safety_service import WorkLineSafetyService

    class _Db:
        def add(self, item):
            item.id = 9901

        async def flush(self):
            pass

        async def commit(self):
            pass

    workline = SimpleNamespace(
        runtime_status=WorkLineRuntimeStatus.READY,
        active_safety_incident_id=None,
        stopped_at=None,
        stopped_reason=None,
    )
    projection = _RuntimeStatusProjectionSpy()
    service = WorkLineSafetyService(
        workline_repository=SimpleNamespace(get_for_update=AsyncMock(return_value=workline)),
        incident_repository=SimpleNamespace(get_active_for_workline=AsyncMock(return_value=None)),
        session_repository=SimpleNamespace(fail_open_by_workline=AsyncMock(return_value=0)),
        system_outbox_repository=SimpleNamespace(cancel_active_by_workline=AsyncMock(return_value=0)),
        command_repository=SimpleNamespace(cancel_active_by_workline=AsyncMock(return_value=0)),
        device_service=SimpleNamespace(mark_workline_safety_error=AsyncMock(return_value=0)),
        runtime_hold_creation_service=SimpleNamespace(
            create_for_safety_estop=AsyncMock(return_value=SimpleNamespace())
        ),
        workline_status_projection_service=projection,
    )

    _ = await service.handle_estop(_Db(), workline_id=45)

    assert len(projection.estop_calls) == 1
    assert projection.estop_calls[0][0] == 45
    assert projection.estop_calls[0][1] == "ESTOP_PRESSED"
    assert projection.estop_calls[0][2]["active_safety_incident_id"] == 9901


@pytest.mark.asyncio
async def test_repeated_safety_estop_reuses_active_incident():
    """重复 ESTOP 必须复用 active incident，不重复创建安全事件。"""
    from src.app.workline.services.safety_service import WorkLineSafetyService

    class _Db:
        def __init__(self):
            self.add_count = 0

        def add(self, item):
            _ = item
            self.add_count += 1

        async def flush(self):
            pass

        async def commit(self):
            pass

    incident = SimpleNamespace(id=9901, drain_status="PENDING", drain_error_json={}, evidence_json={})
    incident_repository = SimpleNamespace(get_active_for_workline=AsyncMock(return_value=incident))
    service = WorkLineSafetyService(
        workline_repository=SimpleNamespace(get_for_update=AsyncMock(return_value=SimpleNamespace(id=45))),
        incident_repository=incident_repository,
        session_repository=SimpleNamespace(fail_open_by_workline=AsyncMock(return_value=0)),
        system_outbox_repository=SimpleNamespace(cancel_active_by_workline=AsyncMock(return_value=0)),
        command_repository=SimpleNamespace(cancel_active_by_workline=AsyncMock(return_value=0)),
        device_service=SimpleNamespace(mark_workline_safety_error=AsyncMock(return_value=0)),
        runtime_hold_creation_service=SimpleNamespace(
            create_for_safety_estop=AsyncMock(return_value=SimpleNamespace())
        ),
        workline_status_projection_service=_RuntimeStatusProjectionSpy(),
    )
    db = _Db()

    first = await service.handle_estop(db, workline_id=45)
    second = await service.handle_estop(db, workline_id=45)

    assert first is incident
    assert second is incident
    assert db.add_count == 0
    assert incident_repository.get_active_for_workline.await_count == 2


@pytest.mark.asyncio
async def test_safety_assert_accepting_work_delegates_runtime_projection_service():
    from src.app.workline.services.safety_service import WorkLineSafetyService

    lock_order: list[str] = []
    workline = SimpleNamespace(id=45, runtime_status=WorkLineRuntimeStatus.READY)

    class Repository:
        async def acquire_plugin_pin_shared(self, _db, workline_id: int):
            assert workline_id == 45
            lock_order.append("shared")

        async def get_for_update(self, _db, workline_id: int):
            assert workline_id == 45
            lock_order.append("row")
            return workline

    class Projection(_RuntimeStatusProjectionSpy):
        async def assert_accepting_runtime_work(self, _db, *, workline_id, blocked_error=RuntimeError):
            lock_order.append("projection")
            await super().assert_accepting_runtime_work(
                _db,
                workline_id=workline_id,
                blocked_error=blocked_error,
            )

    projection = Projection()
    service = WorkLineSafetyService(
        workline_repository=Repository(),
        workline_status_projection_service=projection,
    )

    await service.assert_accepting_work(object(), workline_id=45)

    assert len(projection.accepting_calls) == 1
    assert projection.accepting_calls[0][0] == 45
    assert projection.accepting_calls[0][1].__name__ == "WorkLineSafetyBlocked"
    assert lock_order == ["shared", "row", "projection"]


@pytest.mark.asyncio
async def test_dispatch_ack_exhausted_uses_projection_without_overwriting_estop():
    from src.app.device.models.command import CommandStatus
    from src.app.runtime.orchestration.models.session import (
        RuntimeReconciliationReason,
        RuntimeReconciliationSourceKind,
        RuntimeReconciliationState,
        SessionStatus,
    )
    from src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl import (
        WorklineRuntimeReconciliationService,
    )
    from src.app.sys.models import SystemOutboxStatus

    now = timezone.now_for_db()
    old_stop = now
    command = SimpleNamespace(
        id=881,
        command_code="CMD-ACK-EXHAUSTED",
        workline_id=45,
        device_id=7,
        correlation_id="corr-runtime-reconciliation-dispatch",
        status=CommandStatus.SENT,
        completed_at=None,
        error_detail=None,
    )
    outbox = SimpleNamespace(
        id=862,
        session_id=553,
        workline_id=45,
        target_code="CONVEYOR01",
        dispatch_key="device-command:CMD-ACK-EXHAUSTED",
        status=SystemOutboxStatus.SENT,
        last_error=None,
        next_retry_at=now,
        finished_at=None,
        blocked_by_runtime_hold_id=None,
        blocked_by_reconciliation_session_id=None,
        blocked_device_id=7,
        blocked_workline_id=45,
        blocked_reason="DEVICE_BUSY",
        payload_json={"command_code": "CMD-ACK-EXHAUSTED"},
    )
    session = SimpleNamespace(
        id=553,
        workline_id=45,
        trace_id="trace-dispatch-reconciliation",
        status=SessionStatus.WAITING_DEVICE_RESULT,
        current_wait_type="COMMAND_RESULT",
        current_wait_timeout_seconds=300,
        waiting_since=now,
        deadline_at=None,
        awaiting_device_command_code=command.command_code,
        reconciliation_state=None,
        context_json={},
    )
    workline = SimpleNamespace(
        runtime_status=WorkLineRuntimeStatus.ESTOPPED,
        stopped_at=old_stop,
        stopped_reason="ESTOP_PRESSED",
    )
    projection = _RuntimeStatusProjectionSpy()
    service = WorklineRuntimeReconciliationService(
        session_repository=SimpleNamespace(get_for_update=AsyncMock(return_value=session)),
        workline_repository=SimpleNamespace(get_for_update=AsyncMock(return_value=workline)),
        system_outbox_repository=SimpleNamespace(cancel_active_by_session=AsyncMock(return_value=0)),
        device_service=SimpleNamespace(
            mark_dispatch_ack_exhausted=AsyncMock(return_value=None),
            mark_callback_deadline_expired=AsyncMock(return_value=None),
        ),
        runtime_hold_creation_service=SimpleNamespace(
            create_for_dispatch_ack_exhausted=AsyncMock(return_value=SimpleNamespace(id=9904))
        ),
        rack_task_repository=SimpleNamespace(cancel_active_by_material_session=AsyncMock(return_value=0)),
        workline_status_projection_service=projection,
    )

    class _Db:
        flush = AsyncMock()

    from src.app.reconciliation.manager import ReconciliationManager, ReconciliationRegistrationResult
    from src.app.runtime.orchestration.services.idempotency_guard import ClaimResult

    class _Manager:
        def __init__(self):
            self.manager = ReconciliationManager()

        async def register_conflict_idempotent(self, *_args, **_kwargs):
            conflict = _args[1]
            return ReconciliationRegistrationResult(
                decision=self.manager.register_conflict(conflict),
                claim_result=ClaimResult.NEW,
            )

    service.reconciliation_manager = _Manager()

    from unittest.mock import patch

    with (
        patch(
            "src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl.add_timeline_with_sequence",
            new=AsyncMock(),
        ),
        patch(
            "src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl.workline_diagnostic_service.record_event",
            new=AsyncMock(),
        ),
    ):
        _ = await service.handle_dispatch_ack_exhausted(
            _Db(),
            outbox=outbox,
            command=command,
            error_message="COMMAND_ACK_TIMEOUT",
        )

    assert len(projection.reconciling_calls) == 1
    assert projection.reconciling_calls[0][0] == 45
    assert projection.reconciling_calls[0][2] == RuntimeReconciliationReason.COMMAND_ACK_EXHAUSTED.value
    assert session.reconciliation_state == RuntimeReconciliationState.PENDING
    assert session.reconciliation_source_kind == RuntimeReconciliationSourceKind.DISPATCH_ACK_EXHAUSTED
    assert workline.runtime_status == WorkLineRuntimeStatus.ESTOPPED
    assert workline.stopped_at == old_stop
    assert workline.stopped_reason == "ESTOP_PRESSED"


@pytest.mark.asyncio
async def test_remaining_safety_estop_hold_reprojects_with_incident_id(monkeypatch):
    import importlib

    from src.app.runtime.orchestration.models.runtime_hold import RuntimeHoldType
    from src.app.runtime.orchestration.services.hold.runtime_hold_release_service import RuntimeHoldReleaseService

    release_module = importlib.import_module("src.app.runtime.orchestration.services.hold.runtime_hold_release_service")
    projection = _RuntimeStatusProjectionSpy()
    service = RuntimeHoldReleaseService()
    runtime_hold = SimpleNamespace(
        hold_type=RuntimeHoldType.RUNTIME_RECONCILIATION,
        source_reason="COMMAND_ACK_EXHAUSTED",
        evidence_snapshot_json={},
    )
    safety_hold = SimpleNamespace(
        hold_type=RuntimeHoldType.SAFETY_ESTOP,
        source_reason="ESTOP_PRESSED",
        evidence_snapshot_json={"incident_id": 9007199254740993},
    )
    monkeypatch.setattr(release_module, "workline_runtime_status_projection_service", projection)

    await service._project_remaining_hold_status(
        object(),
        workline_id=45,
        remaining_holds=[runtime_hold, safety_hold],
    )

    assert projection.estop_calls == [
        (
            45,
            "ESTOP_PRESSED",
            {"active_safety_incident_id": 9007199254740993},
        )
    ]


@pytest.mark.asyncio
async def test_remaining_safety_estop_hold_falls_back_to_current_projection_incident(monkeypatch):
    import importlib

    from src.app.runtime.orchestration.models.runtime_hold import RuntimeHoldType
    from src.app.runtime.orchestration.services.hold.runtime_hold_release_service import RuntimeHoldReleaseService

    release_module = importlib.import_module("src.app.runtime.orchestration.services.hold.runtime_hold_release_service")
    projection = _RuntimeStatusProjectionSpy()

    async def runtime_status_snapshot(_db, *, workline_id):
        return SimpleNamespace(
            runtime_status=WorkLineRuntimeStatus.ESTOPPED.value,
            active_safety_incident_id=9007199254740997,
        )

    projection.runtime_status_snapshot = runtime_status_snapshot
    service = RuntimeHoldReleaseService()
    runtime_hold = SimpleNamespace(
        hold_type=RuntimeHoldType.RUNTIME_RECONCILIATION,
        source_reason="COMMAND_ACK_EXHAUSTED",
        evidence_snapshot_json={},
    )
    safety_hold = SimpleNamespace(
        hold_type=RuntimeHoldType.SAFETY_ESTOP,
        source_reason="ESTOP_PRESSED",
        evidence_snapshot_json={},
    )
    monkeypatch.setattr(release_module, "workline_runtime_status_projection_service", projection)

    await service._project_remaining_hold_status(
        object(),
        workline_id=45,
        remaining_holds=[runtime_hold, safety_hold],
    )

    assert projection.estop_calls == [
        (
            45,
            "ESTOP_PRESSED",
            {"active_safety_incident_id": 9007199254740997},
        )
    ]
