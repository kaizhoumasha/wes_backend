"""通用 WorkLine START 的 replay 优先级与首次激活原子行为。"""

from dataclasses import fields
from datetime import datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from src.app.execution.plugin_binding import PluginRuntimeBinding
from src.app.workline.epoch_activation import (
    LineRunEpochDeviceBindingInput,
    LineRunEpochPositionBindingInput,
    WorkLineEpochActivationPlan,
)
from src.app.workline.installed_plugin import InstalledWorkLinePlugin
from src.app.workline.models.line_run_epoch import LineRunEpoch, LineRunEpochStatus
from src.app.workline.models.workline import LineType
from src.app.workline.services.workline_start_service import (
    WorkLineStartIdempotencyConflictError,
    WorkLineStartInvalidStateError,
    WorkLineStartResult,
    WorkLineStartService,
)


class EpochRepository:
    def __init__(self, existing: LineRunEpoch | None = None, active: LineRunEpoch | None = None) -> None:
        self.existing = existing
        self.active = active
        self.locked: list[str] = []
        self.lifecycle_locked: list[int] = []

    async def lock_start_request(self, _db: object, request_id: str) -> None:
        self.locked.append(request_id)

    async def get_by_epoch_code_for_update(self, _db: object, epoch_code: str) -> LineRunEpoch | None:
        assert self.locked == [epoch_code]
        return self.existing if self.existing is not None and self.existing.epoch_code == epoch_code else None

    async def get_active_for_workline(self, _db: object, workline_id: int) -> LineRunEpoch | None:
        if self.active is not None and self.active.workline_id == workline_id:
            return self.active
        return None

    async def lock_epoch_lifecycle(self, _db: object, line_run_epoch_id: int) -> None:
        self.lifecycle_locked.append(line_run_epoch_id)

    async def get_active_for_workline_for_update(self, _db: object, workline_id: int) -> LineRunEpoch | None:
        return await self.get_active_for_workline(_db, workline_id)


def test_start_result_exposes_only_target_epoch_state() -> None:
    """START 结果不再泄漏 parked SystemOutbox release 计数。"""

    assert tuple(field.name for field in fields(WorkLineStartResult)) == (
        "epoch",
        "current_workline_runtime_status",
        "created",
    )


class WorkLineRepository:
    def __init__(self, workline: object | None, *, unfinished_by_type: dict[str, bool] | None = None) -> None:
        self.workline = workline
        self.calls = 0
        self.unfinished_by_type = unfinished_by_type or {}
        self.active_writes: list[bool] = []

    async def get_for_update(self, _db: object, workline_id: int) -> object | None:
        self.calls += 1
        assert getattr(self.workline, "id", workline_id) == workline_id
        return self.workline

    async def get_unfinished_workload_summary(self, _db: object, workline_id: int) -> dict[str, object]:
        assert getattr(self.workline, "id", workline_id) == workline_id
        by_type = {"line_run_epochs": False, **self.unfinished_by_type}
        return {
            "count": sum(by_type.values()),
            "by_type": by_type,
            "sample": {"type": "material_execution", "identity": "EXEC-BLOCKING", "status": "HOLD"}
            if any(self.unfinished_by_type.values())
            else None,
        }

    async def set_active_for_start(self, _db: object, workline: object) -> object:
        cast("Any", workline).is_active = True
        self.active_writes.append(True)
        return workline


class EmptyGateRepository:
    def __init__(self, count: int = 0) -> None:
        self.calls = 0
        self.count = count

    async def get_active_for_workline(self, _db: object, workline_id: int) -> None:
        del workline_id
        self.calls += 1


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


class FactFactory:
    async def build(self, _db: object, fact: object) -> object:
        return fact


class EpochService:
    def __init__(self, events: list[str] | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self.events = events

    async def activate_epoch(self, _db: object, **kwargs: object) -> LineRunEpoch:
        if self.events is not None:
            self.events.append("activate")
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
    active: LineRunEpoch | None = None,
    events: list[str] | None = None,
    unfinished_by_type: dict[str, bool] | None = None,
    workline_active: bool = False,
    business_blocker: object | None = None,
):
    epochs = EpochRepository(existing, active)
    worklines = WorkLineRepository(
        SimpleNamespace(id=7, is_active=workline_active, plugin_key="example_plugin", line_type=LineType.AUTO),
        unfinished_by_type=unfinished_by_type,
    )
    safety = EmptyGateRepository()
    builder = Builder(events)
    epoch_service = EpochService(events)
    service = WorkLineStartService(
        epoch_repository=epochs,
        workline_repository=worklines,
        safety_repository=safety,
        plugins=(
            InstalledWorkLinePlugin(
                display_name="Example",
                runtime_binding=PluginRuntimeBinding(
                    plugin_key="example_plugin",
                    plugin_version="1.0",
                    handlers=(),
                    fact_factory=FactFactory(),
                ),
                start_plan_builder=builder,
                supported_line_types=(LineType.AUTO,),
                business_blocker=business_blocker,
            ),
        ),
        epoch_service=epoch_service,
        clock=lambda: datetime(2026, 8, 19, 9),
    )
    return service, epochs, worklines, safety, builder, epoch_service


@pytest.mark.asyncio
async def test_replay_returns_historical_epoch_before_workline_or_current_gates() -> None:
    service, epochs, worklines, safety, builder, epoch_service = _service(existing=_epoch())
    worklines.workline = None  # 历史 replay 不依赖当前 WorkLine 是否已软删除。

    result = await service.start(object(), workline_id=7, request_id=" REQUEST-1 ")

    assert epochs.locked == ["REQUEST-1"]
    assert result.epoch.status == LineRunEpochStatus.CLOSED.value
    assert result.created is False
    assert result.current_workline_runtime_status is None
    assert worklines.calls == builder.calls == 0
    assert safety.calls == 0
    assert epoch_service.calls == []


@pytest.mark.asyncio
async def test_replay_rejects_same_request_for_different_workline_before_builder() -> None:
    service, _epochs, worklines, safety, builder, epoch_service = _service(existing=_epoch(workline_id=8))

    with pytest.raises(WorkLineStartIdempotencyConflictError, match="REQUEST-1"):
        await service.start(object(), workline_id=7, request_id="REQUEST-1")

    assert worklines.calls == builder.calls == 0
    assert safety.calls == 0
    assert epoch_service.calls == []


@pytest.mark.asyncio
async def test_first_start_builds_complete_epoch_after_target_admission() -> None:
    service, _epochs, worklines, safety, builder, epoch_service = _service()

    result = await service.start(object(), workline_id=7, request_id=" REQUEST-2 ")

    assert result.created is True
    assert result.epoch.epoch_code == "REQUEST-2"
    assert result.current_workline_runtime_status == "READY"
    assert worklines.calls == builder.calls == 1
    assert worklines.active_writes == [True]
    assert safety.calls == 1
    assert epoch_service.calls[0]["configuration_snapshot"] == {"mode": "GENERIC"}


@pytest.mark.asyncio
async def test_first_start_rejects_an_already_active_workline() -> None:
    service, *_prefix, builder, epoch_service = _service(workline_active=True)

    with pytest.raises(WorkLineStartInvalidStateError, match="已启用"):
        await service.start(object(), workline_id=7, request_id="REQUEST-NEXT")

    assert builder.calls == 0
    assert epoch_service.calls == []


@pytest.mark.asyncio
async def test_first_start_rejects_inactive_workline_with_active_epoch_before_builder() -> None:
    service, *_prefix, builder, epoch_service = _service(active=_epoch(status=LineRunEpochStatus.ACTIVE))

    with pytest.raises(WorkLineStartInvalidStateError, match="ACTIVE Epoch"):
        await service.start(object(), workline_id=7, request_id="REQUEST-NEXT")

    assert builder.calls == 0
    assert epoch_service.calls == []


@pytest.mark.asyncio
async def test_first_start_rejects_unfinished_execution_owner_before_epoch_creation() -> None:
    events: list[str] = []
    service, *_prefix, builder, epoch_service = _service(
        events=events,
        unfinished_by_type={"material_executions": True},
    )

    with pytest.raises(WorkLineStartInvalidStateError, match="EXEC-BLOCKING"):
        await service.start(object(), workline_id=7, request_id="REQUEST-BLOCKED")

    assert events == []
    assert builder.calls == 0
    assert epoch_service.calls == []


@pytest.mark.asyncio
async def test_first_start_rejects_current_plugins_unfinished_business_task() -> None:
    blocker = SimpleNamespace(
        get_unfinished_workload_summary=AsyncMock(return_value={"count": 1, "sample": {"identity": "PICK-1"}})
    )
    service, *_prefix, builder, epoch_service = _service(business_blocker=blocker)

    with pytest.raises(WorkLineStartInvalidStateError, match="PICK-1"):
        await service.start(object(), workline_id=7, request_id="REQUEST-BLOCKED")

    assert builder.calls == 0
    assert epoch_service.calls == []


@pytest.mark.asyncio
async def test_first_start_rejects_a_plugin_that_is_not_installed() -> None:
    service, _epochs, worklines, _safety, builder, epoch_service = _service()
    cast("Any", worklines.workline).plugin_key = "missing_plugin"

    with pytest.raises(Exception, match="not installed"):
        await service.start(object(), workline_id=7, request_id="REQUEST-MISSING")

    assert builder.calls == 0
    assert epoch_service.calls == []
