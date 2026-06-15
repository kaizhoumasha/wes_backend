from __future__ import annotations

import importlib
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

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
