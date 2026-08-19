"""通用 WorkLine START 的 replay 优先级与首次激活原子行为。"""

from datetime import datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest

from src.app.workline.epoch_activation import (
    LineRunEpochDeviceBindingInput,
    LineRunEpochPositionBindingInput,
    WorkLineEpochActivationPlan,
)
from src.app.workline.models.line_run_epoch import LineRunEpoch, LineRunEpochStatus
from src.app.workline.services.line_run_epoch_service import ActiveLineRunEpochExistsError
from src.app.workline.services.workline_start_service import (
    WorkLineStartIdempotencyConflictError,
    WorkLineStartInvalidStateError,
    WorkLineStartService,
)


class EpochRepository:
    def __init__(self, existing: LineRunEpoch | None = None) -> None:
        self.existing = existing
        self.locked: list[str] = []

    async def lock_start_request(self, _db: object, request_id: str) -> None:
        self.locked.append(request_id)

    async def get_by_epoch_code_for_update(self, _db: object, epoch_code: str) -> LineRunEpoch | None:
        assert self.locked == [epoch_code]
        return self.existing if self.existing is not None and self.existing.epoch_code == epoch_code else None


class WorkLineRepository:
    def __init__(self, workline: object | None) -> None:
        self.workline = workline
        self.calls = 0

    async def get_for_update(self, _db: object, workline_id: int) -> object | None:
        self.calls += 1
        assert getattr(self.workline, "id", workline_id) == workline_id
        return self.workline


class ProjectionService:
    def __init__(self, runtime_status: str | None = "STOPPED") -> None:
        self.runtime_status = runtime_status
        self.snapshots = 0
        self.ready_calls = 0

    async def runtime_status_snapshot(self, _db: object, *, workline_id: int) -> object:
        del workline_id
        self.snapshots += 1
        return SimpleNamespace(
            runtime_status=self.runtime_status,
            active_safety_incident_id=None,
        )

    async def project_ready_after_start(self, _db: object, *, workline_id: int, occurred_at: datetime) -> object:
        del workline_id, occurred_at
        self.ready_calls += 1
        self.runtime_status = "READY"
        return object()


class EmptyGateRepository:
    def __init__(self, count: int = 0) -> None:
        self.calls = 0
        self.count = count

    async def get_active_for_workline(self, _db: object, workline_id: int) -> None:
        del workline_id
        self.calls += 1

    async def count_active_by_workline(self, _db: object, workline_id: int) -> int:
        del workline_id
        self.calls += 1
        return self.count

    async def count_pending_reconciliations_for_workline(self, _db: object, workline_id: int) -> int:
        del workline_id
        self.calls += 1
        return self.count


class Builder:
    def __init__(self, events: list[str] | None = None) -> None:
        self.calls = 0
        self.events = events

    async def build(self, _db: object, workline: object) -> WorkLineEpochActivationPlan:
        self.calls += 1
        if self.events is not None:
            self.events.append("build")
        assert cast("Any", workline).id == 7
        return WorkLineEpochActivationPlan(
            plugin_key="example_plugin",
            plugin_version="1.0",
            flow_mode="GENERIC_FLOW",
            configuration_snapshot={"mode": "GENERIC"},
            device_bindings=(
                LineRunEpochDeviceBindingInput(
                    device_id=9,
                    device_code="DEVICE-9",
                    device_role="DEVICE_ROLE",
                    endpoint_base_url="http://ecs-start:8080",
                    contract_key="generic.contract",
                    contract_version="1.0",
                    status_max_age_ms=1_000,
                    command_timeout_ms=5_000,
                ),
            ),
            position_bindings=(
                LineRunEpochPositionBindingInput(
                    position_role="INPUT_POSITION",
                    location_id="LOCATION-1",
                    location_type="RACK_CELL",
                ),
            ),
        )


class EpochService:
    def __init__(self, events: list[str] | None = None, *, close_error: Exception | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self.events = events
        self.close_error = close_error

    async def close_active_epoch(self, _db: object, **kwargs: object) -> None:
        assert kwargs["workline_id"] == 7
        assert kwargs["closed_at"] == datetime(2026, 8, 19, 9)
        if self.events is not None:
            self.events.append("close")
        if self.close_error is not None:
            raise self.close_error

    async def activate_epoch(self, _db: object, **kwargs: object) -> LineRunEpoch:
        self.calls.append(kwargs)
        return LineRunEpoch(
            id=31,
            epoch_code=str(kwargs["epoch_code"]),
            workline_id=int(kwargs["workline_id"]),
            plugin_key=str(kwargs["plugin_key"]),
            plugin_version=str(kwargs["plugin_version"]),
            flow_mode=str(kwargs["flow_mode"]),
            topology_digest="a" * 64,
            configuration_digest="b" * 64,
            configuration_snapshot_json=dict(kwargs["configuration_snapshot"]),  # type: ignore[arg-type]
            started_at=kwargs["started_at"],  # type: ignore[arg-type]
        )


class OutboxRepository:
    def __init__(self, released: int = 0) -> None:
        self.released = released
        self.calls = 0

    async def release_parked_after_workline_start(self, _db: object, workline_id: int) -> int:
        assert workline_id == 7
        self.calls += 1
        return self.released


def _epoch(*, workline_id: int = 7, status: LineRunEpochStatus = LineRunEpochStatus.CLOSED) -> LineRunEpoch:
    return LineRunEpoch(
        id=21,
        epoch_code=" REQUEST-1 ".strip(),
        workline_id=workline_id,
        plugin_key="historical_plugin",
        plugin_version="0.9",
        flow_mode="HISTORICAL_FLOW",
        topology_digest="a" * 64,
        configuration_digest="b" * 64,
        configuration_snapshot_json={"historical": True},
        status=status,
        started_at=datetime(2026, 8, 18),
        closed_at=datetime(2026, 8, 19) if status is LineRunEpochStatus.CLOSED else None,
    )


def _service(
    *,
    existing: LineRunEpoch | None = None,
    runtime_status: str | None = "STOPPED",
    released: int = 0,
    active_holds: int = 0,
    pending_reconciliations: int = 0,
    events: list[str] | None = None,
    close_error: Exception | None = None,
):
    epochs = EpochRepository(existing)
    worklines = WorkLineRepository(SimpleNamespace(id=7, is_active=True))
    projection = ProjectionService(runtime_status)
    safety = EmptyGateRepository()
    holds = EmptyGateRepository(active_holds)
    reconciliations = EmptyGateRepository(pending_reconciliations)
    builder = Builder(events)
    epoch_service = EpochService(events, close_error=close_error)
    outboxes = OutboxRepository(released)
    service = WorkLineStartService(
        epoch_repository=epochs,
        workline_repository=worklines,
        projection_service=projection,
        safety_repository=safety,
        runtime_hold_repository=holds,
        reconciliation_repository=reconciliations,
        plan_builder=builder,
        epoch_service=epoch_service,
        command_repository=object(),
        outbox_repository=outboxes,
        clock=lambda: datetime(2026, 8, 19, 9),
    )
    return service, epochs, worklines, projection, safety, holds, reconciliations, builder, epoch_service, outboxes


@pytest.mark.asyncio
async def test_replay_returns_historical_epoch_before_workline_or_current_gates() -> None:
    service, epochs, worklines, projection, safety, holds, reconciliations, builder, epoch_service, outboxes = _service(
        existing=_epoch(), runtime_status=None
    )
    worklines.workline = None  # 历史 replay 不依赖当前 WorkLine 是否已软删除。

    result = await service.start(object(), workline_id=7, request_id=" REQUEST-1 ")

    assert epochs.locked == ["REQUEST-1"]
    assert result.epoch.status == LineRunEpochStatus.CLOSED.value
    assert result.created is False
    assert result.current_workline_runtime_status is None
    assert result.released_outbox_count == 0
    assert worklines.calls == builder.calls == projection.ready_calls == outboxes.calls == 0
    assert safety.calls == holds.calls == reconciliations.calls == 0
    assert epoch_service.calls == []
    assert projection.snapshots == 1


@pytest.mark.asyncio
async def test_replay_rejects_same_request_for_different_workline_before_builder() -> None:
    service, _epochs, worklines, projection, safety, holds, reconciliations, builder, epoch_service, outboxes = (
        _service(existing=_epoch(workline_id=8))
    )

    with pytest.raises(WorkLineStartIdempotencyConflictError, match="REQUEST-1"):
        await service.start(object(), workline_id=7, request_id="REQUEST-1")

    assert worklines.calls == projection.snapshots == projection.ready_calls == builder.calls == outboxes.calls == 0
    assert safety.calls == holds.calls == reconciliations.calls == 0
    assert epoch_service.calls == []


@pytest.mark.asyncio
async def test_first_start_builds_complete_epoch_projects_ready_and_releases_only_start_parked_outbox() -> None:
    service, _epochs, worklines, projection, safety, holds, reconciliations, builder, epoch_service, outboxes = (
        _service(released=2)
    )

    result = await service.start(object(), workline_id=7, request_id=" REQUEST-2 ")

    assert result.created is True
    assert result.epoch.epoch_code == "REQUEST-2"
    assert result.current_workline_runtime_status == "READY"
    assert result.released_outbox_count == 2
    assert worklines.calls == projection.ready_calls == builder.calls == outboxes.calls == 1
    assert safety.calls == holds.calls == reconciliations.calls == 1
    assert epoch_service.calls[0]["configuration_snapshot"] == {"mode": "GENERIC"}


@pytest.mark.asyncio
async def test_first_start_closes_safe_active_epoch_before_building_replacement() -> None:
    events: list[str] = []
    service, *_rest = _service(events=events)

    result = await service.start(object(), workline_id=7, request_id="REQUEST-NEXT")

    assert result.created is True
    assert events == ["close", "build"]


@pytest.mark.asyncio
async def test_first_start_rejects_active_epoch_with_unclosed_commands_before_builder() -> None:
    events: list[str] = []
    service, *_prefix, builder, epoch_service, outboxes = _service(
        events=events,
        close_error=ActiveLineRunEpochExistsError("still unclosed"),
    )

    with pytest.raises(WorkLineStartInvalidStateError, match="still unclosed"):
        await service.start(object(), workline_id=7, request_id="REQUEST-NEXT")

    assert events == ["close"]
    assert builder.calls == 0
    assert epoch_service.calls == []
    assert outboxes.calls == 0


@pytest.mark.asyncio
async def test_first_start_rejects_current_non_stopped_state_before_builder() -> None:
    service, _epochs, _worklines, projection, safety, holds, reconciliations, builder, epoch_service, outboxes = (
        _service(runtime_status="READY")
    )

    with pytest.raises(WorkLineStartInvalidStateError, match="READY"):
        await service.start(object(), workline_id=7, request_id="REQUEST-3")

    assert projection.ready_calls == builder.calls == outboxes.calls == 0
    assert safety.calls == holds.calls == reconciliations.calls == 0
    assert epoch_service.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("active_holds", "pending_reconciliations", "error"),
    [
        (1, 0, "runtime hold"),
        (0, 1, "runtime reconciliation"),
    ],
)
async def test_first_start_rejects_active_runtime_blockers_before_builder_or_release(
    active_holds: int,
    pending_reconciliations: int,
    error: str,
) -> None:
    service, _epochs, _worklines, projection, _safety, _holds, _reconciliations, builder, epoch_service, outboxes = (
        _service(
            active_holds=active_holds,
            pending_reconciliations=pending_reconciliations,
            released=3,
        )
    )

    with pytest.raises(WorkLineStartInvalidStateError, match=error):
        await service.start(object(), workline_id=7, request_id="REQUEST-BLOCKED")

    assert projection.ready_calls == builder.calls == outboxes.calls == 0
    assert epoch_service.calls == []
