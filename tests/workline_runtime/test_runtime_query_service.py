from __future__ import annotations

import importlib
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.sys.models import SystemOutboxStatus
from src.app.workline.models.runtime import RuntimeResourceEvidenceKind, RuntimeSingleLayerRackSnapshot
from src.app.workline.services.runtime_query_service import RuntimeQueryService
from src.utils.timezone import timezone


class _ResultStub:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def scalars(self) -> SimpleNamespace:
        return SimpleNamespace(all=lambda: self._rows)


def test_pending_command_count_compat_wrapper_removed_from_runtime_query_service() -> None:
    assert not hasattr(RuntimeQueryService, "_load_pending_command_count_map")


@pytest.mark.asyncio
async def test_runtime_device_projection_includes_resource_wait_summary() -> None:
    service = RuntimeQueryService()
    blocked_at = timezone.now_for_db() - timedelta(seconds=30)
    last_check_at = timezone.now_for_db() - timedelta(seconds=5)
    device = SimpleNamespace(
        id=77,
        device_code="ARM-01",
        device_name="机械臂 01",
        device_role="ROBOT_ARM",
        role_index=1,
        upstream_device_id=None,
        work_line_id=22,
        device_status="IDLE",
        maintenance_mode=False,
        current_command_id=None,
        last_heartbeat_at=None,
        error_code=None,
    )
    blocked_outbox = SimpleNamespace(
        status=SystemOutboxStatus.BLOCKED_RESOURCE,
        blocked_device_id=77,
        target_code="ARM-01",
        blocked_reason="DEVICE_STATUS_PRECHECK_WAIT",
        blocked_at=blocked_at,
        last_blocked_check_at=last_check_at,
        blocked_check_count=4,
        blocked_detail_json={
            "device_code": "ARM-01",
            "status_url": "http://mock-ecs.internal:8010/api/v1/device/status?token=secret&device_code=ARM-01",
            "error_kind": "http_status",
            "http_status": 503,
            "raw_vendor_response": {"large": "should-not-leak"},
        },
        payload_json={"command_code": "CMD-BLOCKED-001"},
        created_at=blocked_at,
    )
    db = SimpleNamespace(execute=AsyncMock(return_value=_ResultStub([blocked_outbox])))

    projection = await service._load_blocked_outbox_projection(db, [device])
    summary = service._build_device_summary(
        device,
        None,
        open_command_count=0,
        recent_callback_at=None,
        blocked_outbox_count=projection.count_by_device_id[77],
        blocked_outbox_summary=projection.summary_by_device_id[77],
    )

    assert summary.blocked_outbox_count == 1
    assert summary.blocked_reason == "DEVICE_STATUS_PRECHECK_WAIT"
    assert summary.blocked_wait_seconds is not None and summary.blocked_wait_seconds >= 29
    assert summary.blocked_check_count == 4
    assert summary.blocked_detail_json == {
        "device_code": "ARM-01",
        "status_url": "/api/v1/device/status?device_code=ARM-01",
        "error_kind": "http_status",
        "http_status": 503,
    }


@pytest.mark.asyncio
async def test_blocked_outbox_projection_handles_missing_created_at() -> None:
    service = RuntimeQueryService()
    blocked_at = timezone.now_for_db() - timedelta(seconds=30)
    device = SimpleNamespace(
        id=77,
        device_code="ARM-01",
        device_name="机械臂 01",
        device_role="ROBOT_ARM",
        role_index=1,
        upstream_device_id=None,
        work_line_id=22,
        device_status="IDLE",
        maintenance_mode=False,
        current_command_id=None,
        last_heartbeat_at=None,
        error_code=None,
    )
    missing_created_at_outbox = SimpleNamespace(
        status=SystemOutboxStatus.BLOCKED_RESOURCE,
        blocked_device_id=77,
        target_code="ARM-01",
        blocked_reason="MISSING_CREATED_AT",
        blocked_at=blocked_at,
        blocked_check_count=1,
        blocked_detail_json={},
        payload_json={"command_code": "CMD-MISSING-CREATED-AT"},
        created_at=None,
    )
    dated_outbox = SimpleNamespace(
        status=SystemOutboxStatus.BLOCKED_RESOURCE,
        blocked_device_id=77,
        target_code="ARM-01",
        blocked_reason="DATED_HEAD",
        blocked_at=blocked_at,
        blocked_check_count=2,
        blocked_detail_json={},
        payload_json={"command_code": "CMD-DATED"},
        created_at=blocked_at,
    )
    db = SimpleNamespace(execute=AsyncMock(return_value=_ResultStub([missing_created_at_outbox, dated_outbox])))

    projection = await service._load_blocked_outbox_projection(db, [device])

    assert projection.count_by_device_id[77] == 2
    assert projection.command_codes_by_device_id[77] == {"CMD-MISSING-CREATED-AT", "CMD-DATED"}
    assert projection.summary_by_device_id[77]["blocked_reason"] == "DATED_HEAD"


def test_single_layer_boundary_positions_derive_from_manifest_resource_boundaries(monkeypatch) -> None:
    query_module = importlib.import_module("src.app.workline.services.runtime_query_service")

    manifest = SimpleNamespace(
        resource_boundaries=(
            SimpleNamespace(rack_position_code="INBOUND_SLOT", rack_kind="SINGLE_LAYER"),
            SimpleNamespace(rack_position_code="BUFFER_STACK", rack_kind="FIVE_LAYER"),
            SimpleNamespace(rack_position_code="INBOUND_SLOT", rack_kind="SINGLE_LAYER"),
        )
    )
    monkeypatch.setattr(
        query_module,
        "get_workline_plugin_definition",
        lambda plugin_key: SimpleNamespace(manifest=manifest) if plugin_key == "manifest_boundaries" else None,
    )

    positions = RuntimeQueryService._single_layer_boundary_positions(SimpleNamespace(plugin_key="manifest_boundaries"))

    assert positions == ["INBOUND_SLOT"]


def test_manifest_position_metadata_derives_station_code_and_role_from_rack_positions(monkeypatch) -> None:
    query_module = importlib.import_module("src.app.workline.services.runtime_query_service")

    manifest = SimpleNamespace(
        rack_positions=(
            SimpleNamespace(code="INBOUND_SLOT", role="ENTRY_STATION", station_code="ST-01"),
            SimpleNamespace(code="BUFFER_STACK", role="BUFFER", station_code="ST-02"),
        )
    )
    monkeypatch.setattr(
        query_module,
        "get_workline_plugin_definition",
        lambda plugin_key: SimpleNamespace(manifest=manifest) if plugin_key == "manifest_positions" else None,
    )

    metadata = RuntimeQueryService._manifest_position_metadata_by_code(SimpleNamespace(plugin_key="manifest_positions"))

    assert metadata["INBOUND_SLOT"] == {
        "position_code": "INBOUND_SLOT",
        "station_code": "ST-01",
        "station_role": "ENTRY_STATION",
    }
    assert metadata["BUFFER_STACK"] == {
        "position_code": "BUFFER_STACK",
        "station_code": "ST-02",
        "station_role": "BUFFER",
    }


@pytest.mark.asyncio
async def test_runtime_boundary_keeps_five_layer_generic_evidence_with_single_layer_filter(monkeypatch) -> None:
    query_module = importlib.import_module("src.app.workline.services.runtime_query_service")

    manifest = SimpleNamespace(
        rack_positions=(SimpleNamespace(code="INBOUND_SLOT", role="ENTRY_STATION", station_code="ST-01"),),
        resource_boundaries=(SimpleNamespace(rack_position_code="INBOUND_SLOT", rack_kind="SINGLE_LAYER"),),
    )
    monkeypatch.setattr(
        query_module,
        "get_workline_plugin_definition",
        lambda plugin_key: SimpleNamespace(manifest=manifest) if plugin_key == "manifest_boundaries" else None,
    )

    async def _lease_status(*args, **kwargs):
        return SimpleNamespace(available=True)

    async def _active_snapshot(*args, **kwargs):
        return None

    monkeypatch.setattr(query_module.station_lease_service, "get_station_lease_status", _lease_status)
    monkeypatch.setattr(query_module.smt_active_rack_snapshot_service, "get_active_bin_rack", _active_snapshot)

    boundary = await RuntimeQueryService()._build_workline_runtime_boundary(
        SimpleNamespace(),
        SimpleNamespace(id=42, line_code="WL-BOUNDARY", plugin_key="manifest_boundaries"),
        [
            SimpleNamespace(
                id=10,
                trace_id="trace-five-layer",
                context_json={"active_bin_rack": {"rack_kind": "FIVE_LAYER", "rack_code": "RACK-5"}},
                last_ingress_at=None,
                started_at=None,
                created_at=None,
            )
        ],
    )

    assert boundary["single_layer_rack_snapshot"] == RuntimeSingleLayerRackSnapshot.NON_SINGLE_LAYER_EVIDENCE.value
    assert boundary["resource_evidence_kind"] == RuntimeResourceEvidenceKind.GENERIC_EVIDENCE.value
    assert [item.resource_code for item in boundary["resource_evidence_items"]] == ["RACK-5"]


@pytest.mark.asyncio
async def test_active_snapshot_resource_evidence_derives_station_code_from_manifest_position(monkeypatch) -> None:
    query_module = importlib.import_module("src.app.workline.services.runtime_query_service")

    manifest = SimpleNamespace(
        rack_positions=(SimpleNamespace(code="INBOUND_SLOT", role="ENTRY_STATION", station_code="ST-01"),),
        resource_boundaries=(SimpleNamespace(rack_position_code="INBOUND_SLOT", rack_kind="SINGLE_LAYER"),),
    )
    monkeypatch.setattr(
        query_module,
        "get_workline_plugin_definition",
        lambda plugin_key: SimpleNamespace(manifest=manifest) if plugin_key == "manifest_positions" else None,
    )

    async def _lease_status(*args, **kwargs):
        return SimpleNamespace(available=True)

    async def _active_snapshot(*args, **kwargs):
        return {"rack_code": "RACK-1"}

    monkeypatch.setattr(query_module.station_lease_service, "get_station_lease_status", _lease_status)
    monkeypatch.setattr(query_module.smt_active_rack_snapshot_service, "get_active_bin_rack", _active_snapshot)

    boundary = await RuntimeQueryService()._build_workline_runtime_boundary(
        SimpleNamespace(),
        SimpleNamespace(id=42, line_code="WL-POSITION", plugin_key="manifest_positions"),
        [],
    )

    assert boundary["single_layer_rack_snapshot"] == RuntimeSingleLayerRackSnapshot.ACTIVE.value
    assert [(item.position_code, item.station_code) for item in boundary["resource_evidence_items"]] == [
        ("INBOUND_SLOT", "ST-01")
    ]


# ----------------------------------------------------------------------
# RuntimeMonitorCommandSnapshot tests (Task 1)
# ----------------------------------------------------------------------


class _MonitorExecuteResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value

    def scalar_one(self) -> Any:
        return self._value

    def scalars(self) -> Any:
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = self._value
        return mock_scalars

    def all(self) -> Any:
        return self._value


def _build_workline_stub() -> SimpleNamespace:
    return SimpleNamespace(
        id=45,
        is_deleted=False,
        line_code="WL-45",
        line_name="SMT 线",
        line_type="SMT",
        zone_name=None,
        plugin_key=None,
        contract_version=None,
        is_active=True,
        run_mode="SIMULATION",
        runtime_status="READY",
        active_safety_incident_id=None,
        stopped_at=None,
        stopped_reason=None,
        resumed_at=None,
    )


def _build_db_stub(workline: SimpleNamespace) -> AsyncMock:
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            _MonitorExecuteResult(workline),  # workline lookup
            _MonitorExecuteResult([("RUNNING", 0)]),  # active session counts
            _MonitorExecuteResult(0),  # waiting count
            _MonitorExecuteResult(0),  # failed count
            _MonitorExecuteResult(0),  # completed count
            _MonitorExecuteResult(None),  # pending reconciliation
        ]
    )
    return db


def _empty_evidence_boundary() -> dict[str, Any]:
    return {
        "workline_readiness": "READY",
        "station_lease": "IDLE",
        "single_layer_rack_snapshot": "ACTIVE",
        "rack_operation_wait": "NONE",
        "resource_evidence_kind": "WES_ACTIVE_SNAPSHOT",
        "resource_evidence_items": [],
        "resource_evidence_total_count": 0,
        "resource_evidence_truncated": False,
    }


async def _run_monitor_projection_with_devices(
    devices: list[SimpleNamespace],
    command_rows: dict[int, Any],
    *,
    spy_load_command_map: AsyncMock | None = None,
) -> tuple[Any, AsyncMock]:
    service = RuntimeQueryService()
    workline = _build_workline_stub()
    db = _build_db_stub(workline)

    if spy_load_command_map is None:
        spy_load_command_map = AsyncMock(return_value=command_rows)

    async def mock_build_trace_list_items(_db: Any, _sessions: Any) -> list[Any]:
        return []

    with (
        patch(
            "src.app.workline.services.runtime_query_service.device_repository.get_by_work_line_id",
            new=AsyncMock(return_value=devices),
        ),
        patch.object(service, "_load_active_sessions_for_workline", new=AsyncMock(return_value=[])),
        patch.object(service, "_load_recent_failed_sessions_for_workline", new=AsyncMock(return_value=[])),
        patch.object(service, "_load_recent_completed_sessions_for_workline", new=AsyncMock(return_value=[])),
        patch.object(
            service,
            "_load_blocked_outbox_projection",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    command_codes_by_device_id={},
                    count_by_device_id={},
                    summary_by_device_id={},
                )
            ),
        ),
        patch.object(service, "_load_open_command_count_map", new=AsyncMock(return_value={})),
        patch.object(service, "_load_active_runtime_hold_ids_map", new=AsyncMock(return_value={})),
        patch.object(service, "_load_command_map_by_ids", new=spy_load_command_map),
        patch.object(service, "_build_trace_list_items", new=mock_build_trace_list_items),
        patch.object(
            service,
            "_build_workline_runtime_boundary",
            new=AsyncMock(return_value=_empty_evidence_boundary()),
        ),
    ):
        result = await service.get_workline_monitor_projection(db, 45)

    assert result is not None
    return result, spy_load_command_map


@pytest.mark.asyncio
async def test_monitor_projection_emits_current_command_snapshot_when_command_row_present() -> None:
    db_time = timezone.now_for_db()
    devices = [
        SimpleNamespace(
            id=12,
            device_code="DEV-12",
            device_name="设备 12",
            device_role="SORTER",
            role_index=1,
            upstream_device_id=None,
            device_status="IDLE",
            maintenance_mode=False,
            current_command_id=777,
            last_heartbeat_at=db_time,
            error_code=None,
        )
    ]
    command_row = SimpleNamespace(
        id=777,
        command_code="CMD-DEV-12-777",
        status="SENT",
        sent_at=db_time,
        ack_received_at=None,
        ack_code=None,
        ack_message=None,
    )
    result, _ = await _run_monitor_projection_with_devices(devices, {777: command_row})

    node = result.device_nodes[0]
    assert node.current_command_id == 777
    assert node.current_command is not None
    assert node.current_command.id == 777
    assert node.current_command.command_code == "CMD-DEV-12-777"
    assert node.current_command.status == "SENT"
    assert node.current_command.sent_at is not None
    assert node.current_command.sent_at.utcoffset() == timedelta(0)
    assert node.current_command.ack_received_at is None
    assert node.current_command.ack_code is None
    assert node.current_command.ack_message is None


@pytest.mark.asyncio
async def test_monitor_projection_returns_null_command_when_device_has_no_current_command_id() -> None:
    db_time = timezone.now_for_db()
    devices = [
        SimpleNamespace(
            id=13,
            device_code="DEV-13",
            device_name="设备 13",
            device_role="SCANNER",
            role_index=2,
            upstream_device_id=None,
            device_status="IDLE",
            maintenance_mode=False,
            current_command_id=None,
            last_heartbeat_at=db_time,
            error_code=None,
        )
    ]
    spy = AsyncMock(return_value={})
    result, spy_returned = await _run_monitor_projection_with_devices(devices, {}, spy_load_command_map=spy)

    node = result.device_nodes[0]
    assert node.current_command_id is None
    assert node.current_command is None
    # 没有任何 device 携带 current_command_id 时仍只调用一次（传入空列表，命中早返回分支）。
    assert spy_returned.await_count == 1
    assert spy_returned.await_args.args[1] == []


@pytest.mark.asyncio
async def test_monitor_projection_handles_dangling_current_command_id_without_raising() -> None:
    db_time = timezone.now_for_db()
    devices = [
        SimpleNamespace(
            id=14,
            device_code="DEV-14",
            device_name="设备 14",
            device_role="ROBOT_ARM",
            role_index=3,
            upstream_device_id=None,
            device_status="IDLE",
            maintenance_mode=False,
            current_command_id=999,  # 指向已被删除/不存在的 command 行
            last_heartbeat_at=db_time,
            error_code=None,
        )
    ]
    # _load_command_map_by_ids 返回空，模拟 dangling FK
    result, _ = await _run_monitor_projection_with_devices(devices, {})

    node = result.device_nodes[0]
    # 兼容契约：保留 id，snapshot 为 None
    assert node.current_command_id == 999
    assert node.current_command is None


@pytest.mark.asyncio
async def test_monitor_projection_loads_command_map_in_a_single_batch_for_multiple_devices() -> None:
    db_time = timezone.now_for_db()
    devices = [
        SimpleNamespace(
            id=index,
            device_code=f"DEV-{index}",
            device_name=f"设备 {index}",
            device_role="SORTER",
            role_index=index,
            upstream_device_id=None,
            device_status="IDLE",
            maintenance_mode=False,
            current_command_id=1000 + index,
            last_heartbeat_at=db_time,
            error_code=None,
        )
        for index in range(1, 6)
    ]
    command_rows = {
        1000 + index: SimpleNamespace(
            id=1000 + index,
            command_code=f"CMD-{1000 + index}",
            status="SENT",
            sent_at=db_time,
            ack_received_at=None,
            ack_code=None,
            ack_message=None,
        )
        for index in range(1, 6)
    }
    spy = AsyncMock(return_value=command_rows)
    result, spy_returned = await _run_monitor_projection_with_devices(devices, command_rows, spy_load_command_map=spy)

    # T12 性能门禁：批量加载只应触发一次。
    assert spy_returned.await_count == 1
    requested_ids = spy_returned.await_args.args[1]
    assert sorted(requested_ids) == [1001, 1002, 1003, 1004, 1005]
    # 每个 device 都拿到对应 snapshot。
    assert [node.current_command.command_code for node in result.device_nodes] == [
        f"CMD-{1000 + index}" for index in range(1, 6)
    ]
