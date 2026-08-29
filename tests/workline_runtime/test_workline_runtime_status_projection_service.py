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
    def __init__(self):
        self.calls = []

    async def create_open_hold(self, *_args, **_kwargs):
        self.calls.append(_kwargs)
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
        self.stopped_calls = []

    async def runtime_status_snapshot(self, _db, *, workline_id):
        return SimpleNamespace(
            runtime_status=WorkLineRuntimeStatus.READY.value,
            stopped_at=None,
            stopped_reason=None,
            resumed_at=None,
            active_safety_incident_id=None,
        )

    async def assert_accepting_runtime_work(
        self,
        _db,
        *,
        workline_id,
        blocked_error=RuntimeError,
        populate_existing=False,
    ):
        self.accepting_calls.append((workline_id, blocked_error, populate_existing))

    async def project_estopped_active_hold(self, _db, *, workline_id, reason, **kwargs):
        self.estop_calls.append((workline_id, reason, kwargs))

    async def project_reconciling(self, _db, *, workline_id, occurred_at, reason):
        self.reconciling_calls.append((workline_id, occurred_at, reason))
        return True

    async def project_stopped_waiting_start(self, _db, *, workline_id, evidence_json=None):
        self.stopped_calls.append((workline_id, evidence_json))


class _ProjectionRepository:
    def __init__(self, rows=None):
        self.rows = dict(rows or {})
        self.get_calls = []
        self.list_calls = []
        self.ensure_calls = []
        self.upsert_calls = []

    async def get_by_workline_id(self, _db, workline_id, *, for_update=False, populate_existing=False):
        self.get_calls.append((workline_id, for_update, populate_existing))
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

    snapshot = await projection.runtime_status_snapshot(
        object(),
        workline_id=45,
        populate_existing=True,
    )

    assert snapshot.runtime_status is None
    assert snapshot.source == "runtime/orchestration:missing"
    assert snapshot.active_safety_incident_id is None
    assert repository.ensure_calls == []
    assert repository.upsert_calls == []
    assert repository.get_calls == [(45, False, True)]
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
    hold_repository = _RuntimeHoldRepository()
    service = RuntimeHoldCreationService(
        repository=hold_repository,
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
    assert {"plugin_key", "contract_version"}.isdisjoint(hold_repository.calls[0])


@pytest.mark.asyncio
async def test_callback_timeout_hold_ignores_retired_session_plugin_identity():
    repository = _RuntimeHoldRepository()
    service = RuntimeHoldCreationService(repository=repository)

    await service.create_for_callback_deadline_expired(
        object(),
        session=SimpleNamespace(
            id=11,
            workline_id=7,
            trace_id="trace-11",
            plugin_key="poison-plugin",
            contract_version="poison-contract",
        ),
        inbox=SimpleNamespace(id=21, payload_json={}),
        source_inbox_id=21,
    )

    assert {"plugin_key", "contract_version"}.isdisjoint(repository.calls[0])


@pytest.mark.asyncio
async def test_safety_estop_uses_runtime_projection_service():
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
        command_repository=SimpleNamespace(fail_pending_by_workline=AsyncMock(return_value=0)),
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

    incident = SimpleNamespace(
        id=9901,
        source_evidence_id=None,
        drain_status="PENDING",
        drain_error_json={},
        evidence_json={},
    )
    incident_repository = SimpleNamespace(get_active_for_workline=AsyncMock(return_value=incident))
    service = WorkLineSafetyService(
        workline_repository=SimpleNamespace(get_for_update=AsyncMock(return_value=SimpleNamespace(id=45))),
        incident_repository=incident_repository,
        command_repository=SimpleNamespace(fail_pending_by_workline=AsyncMock(return_value=0)),
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
async def test_safety_drain_is_bounded_and_preserves_possibly_dispatched_commands():
    from src.app.workline.services.safety_service import WorkLineSafetyService

    incident = SimpleNamespace(
        workline_id=45,
        drain_status="PENDING",
        drain_error_json={"old": True},
        evidence_json={"pending_commands_failed": 3},
    )
    incidents = SimpleNamespace(claim_next_drain=AsyncMock(return_value=incident))
    commands = SimpleNamespace(fail_pending_by_workline=AsyncMock(return_value=100))
    service = WorkLineSafetyService(
        incident_repository=incidents,
        command_repository=commands,
        workline_status_projection_service=_RuntimeStatusProjectionSpy(),
    )
    db = AsyncMock()

    result = await service.drain_one(db, command_limit=100)

    assert result is incident
    assert incident.drain_status == "PENDING"
    assert incident.drain_error_json == {}
    assert incident.evidence_json == {
        "pending_commands_failed": 103,
        "dispatched_acknowledged_or_reconciling_commands_preserved": True,
    }
    commands.fail_pending_by_workline.assert_awaited_once_with(
        db,
        workline_id=45,
        failure_code="WORKLINE_ESTOPPED_BEFORE_SEND",
        limit=100,
    )
    db.flush.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_clear_estop_closes_incident_and_waits_for_new_start_without_runtime_hold() -> None:
    from src.app.workline.models.safety import WorklineSafetyIncidentStatus
    from src.app.workline.services.safety_service import WorkLineSafetyService

    incident = SimpleNamespace(
        id=9901,
        status=WorklineSafetyIncidentStatus.ACTIVE,
        recovery_check_json={},
        clear_reason=None,
        cleared_by=None,
        cleared_at=None,
        release_evidence_json={},
    )
    projection = _RuntimeStatusProjectionSpy()
    projection.runtime_status_snapshot = AsyncMock(
        return_value=SimpleNamespace(runtime_status=WorkLineRuntimeStatus.ESTOPPED.value)
    )
    service = WorkLineSafetyService(
        workline_repository=SimpleNamespace(get_for_update=AsyncMock(return_value=SimpleNamespace(id=45))),
        incident_repository=SimpleNamespace(get_active_for_workline=AsyncMock(return_value=incident)),
        workline_status_projection_service=projection,
    )
    db = AsyncMock()

    result = await service.clear_estop(
        db,
        workline_id=45,
        checks={"device_reset": True, "area_clear": True},
        reason="现场复位完成",
        operator_id=8,
    )

    assert result.status is WorklineSafetyIncidentStatus.CLEARED
    assert result.release_evidence_json["workline_runtime_status"] == "STOPPED"
    assert projection.stopped_calls == [(45, {"safety_incident_id": 9901, "cleared_by": 8})]


@pytest.mark.asyncio
async def test_safety_assert_accepting_work_delegates_runtime_projection_service():
    from src.app.workline.services.safety_service import WorkLineSafetyService

    lock_order: list[str] = []
    workline = SimpleNamespace(id=45, runtime_status=WorkLineRuntimeStatus.READY)

    class Repository:
        async def get_for_update(self, _db, workline_id: int, *, populate_existing: bool = False):
            assert workline_id == 45
            assert populate_existing is True
            lock_order.append("row")
            return workline

    class Projection(_RuntimeStatusProjectionSpy):
        async def assert_accepting_runtime_work(
            self,
            _db,
            *,
            workline_id,
            blocked_error=RuntimeError,
            populate_existing=False,
        ):
            lock_order.append("projection")
            await super().assert_accepting_runtime_work(
                _db,
                workline_id=workline_id,
                blocked_error=blocked_error,
                populate_existing=populate_existing,
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
    assert projection.accepting_calls[0][2] is True
    assert lock_order == ["row", "projection"]


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
