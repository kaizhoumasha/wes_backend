from types import SimpleNamespace

import pytest

from src.app.runtime.orchestration.services.hold.runtime_hold_creation_service import RuntimeHoldCreationService
from src.app.runtime.orchestration.services.workline_runtime_status_projection_service import (
    WorkLineRuntimeStatusProjectionService,
)
from src.app.workline.models.safety import WorkLineRuntimeStatus


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


def test_projection_reconciling_preserves_estopped_projection():
    projection = WorkLineRuntimeStatusProjectionService()
    workline = SimpleNamespace(
        runtime_status=WorkLineRuntimeStatus.ESTOPPED,
        stopped_at="existing-stop",
        stopped_reason="ESTOP_PRESSED",
    )

    projected = projection.project_reconciling(workline, occurred_at="now", reason="RESOURCE_CONFLICT")

    assert projected is False
    assert workline.runtime_status == WorkLineRuntimeStatus.ESTOPPED
    assert workline.stopped_at == "existing-stop"
    assert workline.stopped_reason == "ESTOP_PRESSED"


def test_projection_stopped_waiting_start_clears_resume_projection():
    projection = WorkLineRuntimeStatusProjectionService()
    workline = SimpleNamespace(
        runtime_status=WorkLineRuntimeStatus.RECONCILING,
        resumed_at="old-resume",
        stopped_reason="CALLBACK_DEADLINE_EXPIRED",
    )

    projection.project_stopped_waiting_start(workline)

    assert workline.runtime_status == WorkLineRuntimeStatus.STOPPED
    assert workline.resumed_at is None
    assert workline.stopped_reason == "RECOVERY_CLEARED_WAITING_START"


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
