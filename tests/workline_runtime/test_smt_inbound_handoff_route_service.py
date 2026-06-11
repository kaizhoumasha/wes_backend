"""SMT inbound handoff target WorkLine route service tests."""

from __future__ import annotations

import importlib
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from src.app.workline.domain.services.smt_inbound_handoff_reason import SmtInboundHandoffReasonCode
from src.app.workline.models.safety import WorkLineRuntimeStatus
from src.workline_plugins.smt_sorting_inbound.constants import SMT_SORTING_INBOUND_PLUGIN_KEY


def _route_module() -> Any:
    try:
        return importlib.import_module("src.app.workline.domain.services.smt_inbound_handoff_route_service")
    except ModuleNotFoundError as exc:
        pytest.fail(f"缺少 SMT inbound handoff route service 模块: {exc}")


class _LeaseService:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.calls: list[dict[str, Any]] = []

    async def get_station_lease_status(self, _db: object, **kwargs: Any) -> object:
        self.calls.append(kwargs)
        return SimpleNamespace(available=self.available, reason_code="ACTIVE_SESSION_BOUND")


class _SessionRepository:
    def __init__(self, sessions_by_workline: dict[int, list[object]] | None = None) -> None:
        self.sessions_by_workline = sessions_by_workline or {}

    async def list_open_by_workline_id(self, _db: object, *, workline_id: int, limit: int = 50) -> list[object]:
        _ = limit
        return self.sessions_by_workline.get(workline_id, [])


class _EcsProbe:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.calls: list[tuple[object, object]] = []

    async def __call__(self, _db: object, *, workline: object, route: object) -> object:
        self.calls.append((workline, route))
        return SimpleNamespace(available=self.available, reason_code="ECS_DEVICE_NOT_IDLE")


def _workline(
    *,
    workline_id: int,
    line_code: str,
    priority: int = 100,
    runtime_status: WorkLineRuntimeStatus = WorkLineRuntimeStatus.READY,
    route_enabled: bool = True,
) -> object:
    route_config = {
        "enabled": route_enabled,
        "priority": priority,
        "source_station_code": "SOURCE_STATION_A",
    }
    return SimpleNamespace(
        id=workline_id,
        line_code=line_code,
        plugin_key=SMT_SORTING_INBOUND_PLUGIN_KEY,
        runtime_status=runtime_status,
        config={"smt_inbound_handoff_route": route_config},
        runtime_config_json={},
        is_active=True,
    )


def _demand(**overrides: Any) -> object:
    data = {
        "id": 11,
        "rack_release_id": "release-route-001",
        "single_layer_rack_code": "RACK-ROUTE-001",
        "source_workline_code": "WL-ROUGH-01",
        "trace_id": "trace-route-001",
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _source_item(**overrides: Any) -> object:
    data = {
        "id": 22,
        "handoff_demand_id": 11,
        "item_key": "release-route-001:BIN-A:A01",
        "bin_code": "BIN-A",
        "bin_cell_code": "A01",
        "bin_cell_index": 1,
        "material_identity_key": "MAT-A",
        "pkg_code": "PKG-A",
        "claim_attempt_no": 1,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


@pytest.mark.asyncio
async def test_route_missing_returns_manual_hold_route_not_found() -> None:
    module = _route_module()
    service = module.SmtInboundHandoffRouteService(
        station_lease_service=_LeaseService(),
        session_repository=_SessionRepository(),
        ecs_status_probe=_EcsProbe(),
    )

    result = await service.resolve_route(
        object(),
        demand=_demand(),
        source_item=_source_item(),
        candidate_worklines=[],
    )

    assert result.kind == "MANUAL_HOLD"
    assert result.manual_hold is True
    assert result.failure_code == SmtInboundHandoffReasonCode.ROUTE_NOT_FOUND.value
    assert result.next_attempt_at is None


@pytest.mark.asyncio
async def test_route_uses_priority_workline_code_and_id_stable_order() -> None:
    module = _route_module()
    lease_service = _LeaseService()
    service = module.SmtInboundHandoffRouteService(
        station_lease_service=lease_service,
        session_repository=_SessionRepository(),
        ecs_status_probe=_EcsProbe(),
    )
    candidates = [
        _workline(workline_id=30, line_code="WL-SORT-B", priority=5),
        _workline(workline_id=10, line_code="WL-SORT-A", priority=10),
        _workline(workline_id=20, line_code="WL-SORT-A", priority=5),
    ]

    result = await service.resolve_route(
        object(),
        demand=_demand(),
        source_item=_source_item(),
        candidate_worklines=candidates,
    )

    assert result.kind == "SELECTED"
    assert result.selected_workline_id == 20
    assert result.selected_workline_code == "WL-SORT-A"
    assert result.route_evidence["candidate_order"] == [
        {"priority": 5, "workline_code": "WL-SORT-A", "workline_id": 20},
        {"priority": 5, "workline_code": "WL-SORT-B", "workline_id": 30},
        {"priority": 10, "workline_code": "WL-SORT-A", "workline_id": 10},
    ]
    assert lease_service.calls[0]["position_code"] == "SOURCE_STATION_A"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("workline_not_ready", SmtInboundHandoffReasonCode.TARGET_WORKLINE_NOT_READY.value),
        ("station_busy", SmtInboundHandoffReasonCode.SOURCE_STATION_BUSY.value),
        ("current_material_open", SmtInboundHandoffReasonCode.TARGET_SESSION_BUSY.value),
        ("ecs_not_idle", SmtInboundHandoffReasonCode.ECS_DEVICE_NOT_IDLE.value),
    ],
)
async def test_config_candidate_runtime_busy_is_retry_not_route_missing(case: str, expected_code: str) -> None:
    module = _route_module()
    workline = _workline(
        workline_id=20,
        line_code="WL-SORT-A",
        runtime_status=(WorkLineRuntimeStatus.STOPPED if case == "workline_not_ready" else WorkLineRuntimeStatus.READY),
    )
    sessions = (
        [
            SimpleNamespace(
                id=501,
                context_json={"sorting": {"current_material": {"handoff_source_item_id": 22}}},
            )
        ]
        if case == "current_material_open"
        else []
    )
    service = module.SmtInboundHandoffRouteService(
        station_lease_service=_LeaseService(available=case != "station_busy"),
        session_repository=_SessionRepository({20: sessions}),
        ecs_status_probe=_EcsProbe(available=case != "ecs_not_idle"),
    )

    result = await service.resolve_route(
        object(),
        demand=_demand(),
        source_item=_source_item(),
        candidate_worklines=[workline],
    )

    assert result.kind == "RETRY"
    assert result.manual_hold is False
    assert result.retryable is True
    assert result.failure_code == expected_code
    assert isinstance(result.next_attempt_at, datetime)
