from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.runtime.orchestration.services.hold.runtime_hold_creation_service import RuntimeHoldCreationService
from src.app.runtime.orchestration.services.workline_runtime_status_projection_service import (
    WorkLineRuntimeStatusProjectionService,
)
from src.app.workline.models.safety import WorkLineRuntimeStatus
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

    def project_reconciling(self, workline, *, occurred_at, reason):
        self.calls.append((workline, occurred_at, reason))


class _RuntimeStatusProjectionSpy:
    def __init__(self):
        self.inner = WorkLineRuntimeStatusProjectionService()
        self.estop_calls = []
        self.reconciling_calls = []
        self.accepting_calls = []

    def assert_accepting_runtime_work(self, workline, *, workline_id=None, blocked_error=RuntimeError):
        self.accepting_calls.append((workline, workline_id, blocked_error))
        return self.inner.assert_accepting_runtime_work(
            workline,
            workline_id=workline_id,
            blocked_error=blocked_error,
        )

    def project_estopped_active_hold(self, workline, *, reason):
        self.estop_calls.append((workline, reason))
        self.inner.project_estopped_active_hold(workline, reason=reason)

    def project_reconciling(self, workline, *, occurred_at, reason):
        self.reconciling_calls.append((workline, occurred_at, reason))
        return self.inner.project_reconciling(workline, occurred_at=occurred_at, reason=reason)


def test_projection_ready_after_start_sets_ready_snapshot_and_resume_time():
    projection = WorkLineRuntimeStatusProjectionService()
    resumed_at = timezone.now_for_db()
    workline = SimpleNamespace(
        runtime_status=WorkLineRuntimeStatus.STOPPED,
        stopped_at="old-stop",
        stopped_reason="RECOVERY_CLEARED_WAITING_START",
        resumed_at=None,
        active_safety_incident_id=None,
    )

    projection.project_ready_after_start(workline, occurred_at=resumed_at)
    snapshot = projection.runtime_status_snapshot(workline)

    assert workline.runtime_status == WorkLineRuntimeStatus.READY
    assert workline.stopped_reason is None
    assert workline.resumed_at == resumed_at
    assert snapshot.runtime_status == WorkLineRuntimeStatus.READY.value
    assert snapshot.source == "runtime/orchestration"
    assert snapshot.resumed_at == resumed_at
    assert projection.is_ready(workline) is True


def test_projection_snapshot_treats_null_or_missing_runtime_status_as_absent():
    projection = WorkLineRuntimeStatusProjectionService()

    for workline in (
        SimpleNamespace(id=45, runtime_status=None, active_safety_incident_id=True),
        SimpleNamespace(id=46),
    ):
        snapshot = projection.runtime_status_snapshot(workline)

        assert snapshot.runtime_status is None
        assert snapshot.active_safety_incident_id is None
        with pytest.raises(RuntimeError, match="WORKLINE_UNKNOWN"):
            projection.assert_accepting_runtime_work(workline, workline_id=workline.id)


def test_projection_reconciling_preserves_estopped_projection():
    projection = WorkLineRuntimeStatusProjectionService()
    workline = SimpleNamespace(
        runtime_status=WorkLineRuntimeStatus.ESTOPPED,
        stopped_at="existing-stop",
        stopped_reason="ESTOP_PRESSED",
        resumed_at=None,
    )

    projected = projection.project_reconciling(workline, occurred_at="now", reason="RESOURCE_CONFLICT")

    assert projected is False
    assert workline.runtime_status == WorkLineRuntimeStatus.ESTOPPED
    assert workline.stopped_at == "existing-stop"
    assert workline.stopped_reason == "ESTOP_PRESSED"


def test_projection_reconciling_sets_runtime_hold_reason_and_clears_resume_time():
    projection = WorkLineRuntimeStatusProjectionService()
    stopped_at = timezone.now_for_db()
    workline = SimpleNamespace(
        runtime_status=WorkLineRuntimeStatus.READY,
        stopped_at=None,
        stopped_reason=None,
        resumed_at="old-resume",
        active_safety_incident_id=None,
    )

    projected = projection.project_reconciling(workline, occurred_at=stopped_at, reason="RESOURCE_CONFLICT")
    snapshot = projection.runtime_status_snapshot(workline)

    assert projected is True
    assert workline.runtime_status == WorkLineRuntimeStatus.RECONCILING
    assert workline.stopped_at == stopped_at
    assert workline.stopped_reason == "RESOURCE_CONFLICT"
    assert workline.resumed_at is None
    assert snapshot.runtime_status == WorkLineRuntimeStatus.RECONCILING.value
    assert snapshot.stopped_reason == "RESOURCE_CONFLICT"
    assert snapshot.resumed_at is None


def test_projection_stopped_waiting_start_clears_resume_projection():
    projection = WorkLineRuntimeStatusProjectionService()
    workline = SimpleNamespace(
        runtime_status=WorkLineRuntimeStatus.RECONCILING,
        stopped_at="cleared-at",
        resumed_at="old-resume",
        stopped_reason="CALLBACK_DEADLINE_EXPIRED",
        active_safety_incident_id=None,
    )

    projection.project_stopped_waiting_start(workline)
    snapshot = projection.runtime_status_snapshot(workline)

    assert workline.runtime_status == WorkLineRuntimeStatus.STOPPED
    assert workline.resumed_at is None
    assert workline.stopped_reason == "RECOVERY_CLEARED_WAITING_START"
    assert snapshot.runtime_status == WorkLineRuntimeStatus.STOPPED.value
    assert snapshot.stopped_reason == "RECOVERY_CLEARED_WAITING_START"
    assert snapshot.resumed_at is None


def test_projection_estopped_active_hold_sets_reason_and_blocks_runtime_work():
    projection = WorkLineRuntimeStatusProjectionService()
    workline = SimpleNamespace(
        id=45,
        runtime_status=WorkLineRuntimeStatus.READY,
        stopped_at=None,
        stopped_reason=None,
        resumed_at="old-resume",
        active_safety_incident_id=9901,
    )

    projection.project_estopped_active_hold(workline, reason="ESTOP_PRESSED")
    snapshot = projection.runtime_status_snapshot(workline)

    assert workline.runtime_status == WorkLineRuntimeStatus.ESTOPPED
    assert workline.stopped_reason == "ESTOP_PRESSED"
    assert workline.resumed_at is None
    assert snapshot.runtime_status == WorkLineRuntimeStatus.ESTOPPED.value
    assert snapshot.active_safety_incident_id == 9901
    with pytest.raises(RuntimeError, match="WORKLINE_ESTOPPED"):
        projection.assert_accepting_runtime_work(workline, workline_id=45)


def test_projection_assert_accepting_runtime_work_accepts_ready_only():
    projection = WorkLineRuntimeStatusProjectionService()
    workline = SimpleNamespace(id=45, runtime_status=WorkLineRuntimeStatus.READY)

    projection.assert_accepting_runtime_work(workline, workline_id=45)


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
    assert projection.calls[0][0] is workline
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
    assert projection.estop_calls[0][0] is workline
    assert projection.estop_calls[0][1] == "ESTOP_PRESSED"
    assert workline.runtime_status == WorkLineRuntimeStatus.ESTOPPED
    assert workline.active_safety_incident_id == 9901
    assert workline.stopped_at is not None
    assert workline.stopped_reason == "ESTOP_PRESSED"


@pytest.mark.asyncio
async def test_safety_assert_accepting_work_delegates_runtime_projection_service():
    from src.app.workline.services.safety_service import WorkLineSafetyService

    workline = SimpleNamespace(id=45, runtime_status=WorkLineRuntimeStatus.READY)
    projection = _RuntimeStatusProjectionSpy()
    service = WorkLineSafetyService(
        workline_repository=SimpleNamespace(get_for_update=AsyncMock(return_value=workline)),
        workline_status_projection_service=projection,
    )

    await service.assert_accepting_work(object(), workline_id=45)

    assert len(projection.accepting_calls) == 1
    assert projection.accepting_calls[0][0] is workline
    assert projection.accepting_calls[0][1] == 45


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
    assert projection.reconciling_calls[0][0] is workline
    assert projection.reconciling_calls[0][2] == RuntimeReconciliationReason.COMMAND_ACK_EXHAUSTED.value
    assert session.reconciliation_state == RuntimeReconciliationState.PENDING
    assert session.reconciliation_source_kind == RuntimeReconciliationSourceKind.DISPATCH_ACK_EXHAUSTED
    assert workline.runtime_status == WorkLineRuntimeStatus.ESTOPPED
    assert workline.stopped_at == old_stop
    assert workline.stopped_reason == "ESTOP_PRESSED"
