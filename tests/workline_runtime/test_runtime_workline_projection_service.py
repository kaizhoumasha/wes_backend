from datetime import timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from src.utils.timezone import timezone
from tests.workline_runtime.support.runtime_query_projection import AnyArgHashable

if TYPE_CHECKING:
    from src.app.workline.models import WorklineInbox, WorklineSession


class TestRuntimeQueryService:
    @pytest.mark.asyncio
    async def test_get_workline_detail_returns_none_for_soft_deleted_workline(self) -> None:
        from src.app.workline.services.runtime_query_service import RuntimeQueryService

        service = RuntimeQueryService()
        deleted_workline = SimpleNamespace(
            id=45,
            is_deleted=True,
            line_code="WL-45",
            line_name="已删除线体",
            line_type="AUTO",
            zone_name=None,
            plugin_key=None,
            contract_version=None,
            is_active=True,
        )
        db = AsyncMock()
        db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: deleted_workline)

        with (
            patch(
                "src.app.workline.services.runtime_query_service.device_repository.get_by_work_line_id",
                new=AsyncMock(return_value=[]),
            ),
            patch.object(service, "_load_active_sessions_for_workline", new=AsyncMock(return_value=[])),
            patch.object(service, "_load_recent_failed_sessions_for_workline", new=AsyncMock(return_value=[])),
            patch.object(service, "_build_trace_list_items", new=AsyncMock(return_value=[])),
        ):
            result = await service.get_workline_detail(db, 45)

        assert result is None

    @pytest.mark.asyncio
    async def test_get_workline_detail_returns_recent_completed_traces(self) -> None:
        from src.app.workline.models.runtime import RuntimeResourceEvidenceKind, RuntimeTraceListItem
        from src.app.workline.services.runtime_query_service import RuntimeQueryService

        service = RuntimeQueryService()
        workline = SimpleNamespace(
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
        now = timezone.now_for_db()
        completed_session = SimpleNamespace(
            id=20,
            status="COMPLETED",
            last_ingress_at=None,
            waiting_since=None,
            ended_at=now,
            started_at=now - timedelta(minutes=5),
            created_at=now - timedelta(minutes=6),
        )
        completed_trace = RuntimeTraceListItem(
            session_id=20,
            session_code="SES-20",
            workline_id=45,
            status="COMPLETED",
        )
        db = AsyncMock()
        db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: workline)

        async def build_trace_items(_db, sessions):
            if sessions == [completed_session]:
                return [completed_trace]
            return []

        with (
            patch(
                "src.app.workline.services.runtime_query_service.device_repository.get_by_work_line_id",
                new=AsyncMock(return_value=[]),
            ),
            patch.object(service, "_load_active_sessions_for_workline", new=AsyncMock(return_value=[])),
            patch.object(service, "_load_recent_failed_sessions_for_workline", new=AsyncMock(return_value=[])),
            patch.object(
                service,
                "_load_recent_completed_sessions_for_workline",
                new=AsyncMock(return_value=[completed_session]),
                create=True,
            ) as mock_completed_sessions,
            patch.object(service, "_build_trace_list_items", new=AsyncMock(side_effect=build_trace_items)),
        ):
            result = await service.get_workline_detail(db, 45)

        mock_completed_sessions.assert_awaited_once_with(AnyArgHashable(), 45, limit=10)
        assert result is not None
        assert result.recent_completed_traces == [completed_trace]

    @pytest.mark.asyncio
    async def test_get_workline_detail_ignores_completed_session_for_current_rack_operation_wait(self) -> None:
        from src.app.workline.models.runtime import RuntimeTraceListItem
        from src.app.workline.services.runtime_query_service import RuntimeQueryService

        service = RuntimeQueryService()
        workline = SimpleNamespace(
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
        now = timezone.now_for_db()
        completed_session = SimpleNamespace(
            id=20,
            status="COMPLETED",
            context_json={"rack_operation": {"operation_key": "rack-op-1", "status": "ARRIVED"}},
            last_ingress_at=None,
            waiting_since=None,
            ended_at=now,
            started_at=now - timedelta(minutes=5),
            created_at=now - timedelta(minutes=6),
        )
        completed_trace = RuntimeTraceListItem(
            session_id=20,
            session_code="SES-20",
            workline_id=45,
            status="COMPLETED",
        )
        db = AsyncMock()
        db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: workline)

        async def build_trace_items(_db, sessions):
            if sessions == [completed_session]:
                return [completed_trace]
            return []

        with (
            patch(
                "src.app.workline.services.runtime_query_service.device_repository.get_by_work_line_id",
                new=AsyncMock(return_value=[]),
            ),
            patch.object(service, "_load_active_sessions_for_workline", new=AsyncMock(return_value=[])),
            patch.object(service, "_load_recent_failed_sessions_for_workline", new=AsyncMock(return_value=[])),
            patch.object(
                service,
                "_load_recent_completed_sessions_for_workline",
                new=AsyncMock(return_value=[completed_session]),
                create=True,
            ),
            patch.object(service, "_build_trace_list_items", new=AsyncMock(side_effect=build_trace_items)),
        ):
            result = await service.get_workline_detail(db, 45)

        assert result is not None
        assert result.rack_operation_wait == "NONE"
        assert result.recent_completed_traces == [completed_trace]

    @pytest.mark.asyncio
    async def test_get_workline_detail_returns_structured_boundary_contract(self) -> None:
        from src.app.workline.services.runtime_query_service import RuntimeQueryService

        service = RuntimeQueryService()
        workline = SimpleNamespace(
            id=45,
            is_deleted=False,
            line_code="WL-45",
            line_name="SMT 线",
            line_type="SMT",
            zone_name=None,
            plugin_key="SMT_SORTING_INBOUND",
            contract_version=None,
            is_active=True,
            run_mode="SIMULATION",
            runtime_status="READY",
            active_safety_incident_id=None,
            stopped_at=None,
            stopped_reason=None,
            resumed_at=None,
        )
        waiting_session = SimpleNamespace(
            id=20,
            status="WAITING_EXTERNAL",
            context_json={"waiting_rack_operation_key": "rack-op-1"},
            last_ingress_at=None,
            waiting_since=timezone.now_for_db(),
            deadline_at=None,
            started_at=timezone.now_for_db(),
            created_at=timezone.now_for_db(),
        )
        db = AsyncMock()
        db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: workline)
        station_statuses = [
            SimpleNamespace(available=True, reason_code=None),
            SimpleNamespace(available=False, reason_code="ACTIVE_DISPATCH_LEASE"),
            SimpleNamespace(available=True, reason_code=None),
            SimpleNamespace(available=True, reason_code=None),
        ]

        with (
            patch(
                "src.app.workline.services.runtime_query_service.device_repository.get_by_work_line_id",
                new=AsyncMock(return_value=[]),
            ),
            patch.object(service, "_load_active_sessions_for_workline", new=AsyncMock(return_value=[waiting_session])),
            patch.object(service, "_load_recent_failed_sessions_for_workline", new=AsyncMock(return_value=[])),
            patch.object(service, "_load_recent_completed_sessions_for_workline", new=AsyncMock(return_value=[])),
            patch.object(service, "_build_trace_list_items", new=AsyncMock(return_value=[])),
            patch(
                "src.app.workline.services.runtime_query_service.station_lease_service.get_station_lease_status",
                new=AsyncMock(side_effect=station_statuses),
            ) as mock_station_lease,
            patch(
                "src.app.workline.services.runtime_query_service.smt_active_rack_snapshot_service.get_active_bin_rack",
                new=AsyncMock(return_value={"rack_code": "RACK-001"}),
            ) as mock_snapshot,
        ):
            result = await service.get_workline_detail(db, 45)

        assert result is not None
        assert result.workline_readiness == "READY"
        assert result.station_lease == "ACTIVE_DISPATCH_LEASE"
        assert result.single_layer_rack_snapshot == "ACTIVE"
        assert result.rack_operation_wait == "WAITING_WMS"
        assert result.resource_evidence_kind == "WES_ACTIVE_SNAPSHOT"
        expected_single_layer_positions = [
            "SOURCE_STATION_A",
            "SOURCE_STATION_B",
        ]
        station_lease_positions = [call.kwargs["position_code"] for call in mock_station_lease.await_args_list]
        snapshot_contexts = [call.kwargs["context"] for call in mock_snapshot.await_args_list]
        assert station_lease_positions == expected_single_layer_positions
        assert snapshot_contexts == [
            {"station": {"position_code": position_code}} for position_code in expected_single_layer_positions
        ]
        assert "NG_STATION" not in station_lease_positions
        assert "WORKSTATION" not in station_lease_positions
        assert {"station": {"position_code": "NG_STATION"}} not in snapshot_contexts
        assert {"station": {"position_code": "WORKSTATION"}} not in snapshot_contexts

    @pytest.mark.asyncio
    async def test_get_workline_detail_downgrades_boundary_when_station_config_missing(self) -> None:
        from src.app.workline.services.runtime_query_service import RuntimeQueryService

        service = RuntimeQueryService()
        workline = SimpleNamespace(
            id=45,
            is_deleted=False,
            line_code="WL-45",
            line_name="SMT 线",
            line_type="SMT",
            zone_name=None,
            plugin_key="SMT_SORTING_INBOUND",
            contract_version=None,
            is_active=True,
            run_mode="SIMULATION",
            runtime_status="READY",
            active_safety_incident_id=None,
            stopped_at=None,
            stopped_reason=None,
            resumed_at=None,
        )
        db = AsyncMock()
        db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: workline)

        with (
            patch(
                "src.app.workline.services.runtime_query_service.device_repository.get_by_work_line_id",
                new=AsyncMock(return_value=[]),
            ),
            patch.object(service, "_load_active_sessions_for_workline", new=AsyncMock(return_value=[])),
            patch.object(service, "_load_recent_failed_sessions_for_workline", new=AsyncMock(return_value=[])),
            patch.object(service, "_load_recent_completed_sessions_for_workline", new=AsyncMock(return_value=[])),
            patch.object(service, "_build_trace_list_items", new=AsyncMock(return_value=[])),
            patch(
                "src.app.workline.services.runtime_query_service.station_lease_service.get_station_lease_status",
                new=AsyncMock(side_effect=ValueError("workline rack position not found")),
            ),
            patch(
                "src.app.workline.services.runtime_query_service.smt_active_rack_snapshot_service.get_active_bin_rack",
                new=AsyncMock(return_value=None),
            ),
        ):
            result = await service.get_workline_detail(db, 45)

        assert result is not None
        assert result.workline_readiness == "READY"
        assert result.station_lease == "UNKNOWN"
        assert result.single_layer_rack_snapshot == "MISSING"
        assert result.rack_operation_wait == "NONE"

    @pytest.mark.asyncio
    async def test_get_workline_detail_keeps_station_lease_unknown_when_any_source_config_missing(self) -> None:
        from src.app.workline.services.runtime_query_service import RuntimeQueryService

        service = RuntimeQueryService()
        workline = SimpleNamespace(
            id=45,
            is_deleted=False,
            line_code="WL-45",
            line_name="SMT 线",
            line_type="SMT",
            zone_name=None,
            plugin_key="SMT_SORTING_INBOUND",
            contract_version=None,
            is_active=True,
            run_mode="SIMULATION",
            runtime_status="READY",
            active_safety_incident_id=None,
            stopped_at=None,
            stopped_reason=None,
            resumed_at=None,
        )
        db = AsyncMock()
        db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: workline)

        with (
            patch(
                "src.app.workline.services.runtime_query_service.device_repository.get_by_work_line_id",
                new=AsyncMock(return_value=[]),
            ),
            patch.object(service, "_load_active_sessions_for_workline", new=AsyncMock(return_value=[])),
            patch.object(service, "_load_recent_failed_sessions_for_workline", new=AsyncMock(return_value=[])),
            patch.object(service, "_load_recent_completed_sessions_for_workline", new=AsyncMock(return_value=[])),
            patch.object(service, "_build_trace_list_items", new=AsyncMock(return_value=[])),
            patch(
                "src.app.workline.services.runtime_query_service.station_lease_service.get_station_lease_status",
                new=AsyncMock(
                    side_effect=[
                        SimpleNamespace(available=True, reason_code=None),
                        ValueError("workline rack position not found"),
                        SimpleNamespace(available=True, reason_code=None),
                        SimpleNamespace(available=True, reason_code=None),
                    ]
                ),
            ),
            patch(
                "src.app.workline.services.runtime_query_service.smt_active_rack_snapshot_service.get_active_bin_rack",
                new=AsyncMock(return_value=None),
            ),
        ):
            result = await service.get_workline_detail(db, 45)

        assert result is not None
        assert result.station_lease == "UNKNOWN"

    @pytest.mark.asyncio
    async def test_get_workline_detail_projects_wms_callback_and_non_single_layer_evidence(self) -> None:
        from src.app.workline.services.runtime_query_service import RuntimeQueryService

        service = RuntimeQueryService()
        workline = SimpleNamespace(
            id=45,
            is_deleted=False,
            line_code="WL-45",
            line_name="SMT 线",
            line_type="SMT",
            zone_name=None,
            plugin_key="SMT_SORTING_INBOUND",
            contract_version=None,
            is_active=True,
            run_mode="SIMULATION",
            runtime_status="READY",
            active_safety_incident_id=None,
            stopped_at=None,
            stopped_reason=None,
            resumed_at=None,
        )
        session = SimpleNamespace(
            id=20,
            status="WAITING_EXTERNAL",
            context_json={
                "rack_operation": {
                    "operation_key": "rack-op-1",
                    "status": "ARRIVED",
                    "source_system": "WMS",
                    "callback_type": "WMS_RACK_ARRIVED",
                    "rack_kind": "FIVE_LAYER",
                },
            },
            last_ingress_at=None,
            waiting_since=timezone.now_for_db(),
            deadline_at=None,
            started_at=timezone.now_for_db(),
            created_at=timezone.now_for_db(),
        )
        db = AsyncMock()
        db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: workline)

        with (
            patch(
                "src.app.workline.services.runtime_query_service.device_repository.get_by_work_line_id",
                new=AsyncMock(return_value=[]),
            ),
            patch.object(service, "_load_active_sessions_for_workline", new=AsyncMock(return_value=[session])),
            patch.object(service, "_load_recent_failed_sessions_for_workline", new=AsyncMock(return_value=[])),
            patch.object(service, "_load_recent_completed_sessions_for_workline", new=AsyncMock(return_value=[])),
            patch.object(service, "_build_trace_list_items", new=AsyncMock(return_value=[])),
            patch(
                "src.app.workline.services.runtime_query_service.station_lease_service.get_station_lease_status",
                new=AsyncMock(return_value=SimpleNamespace(available=True, reason_code=None)),
            ),
            patch(
                "src.app.workline.services.runtime_query_service.smt_active_rack_snapshot_service.get_active_bin_rack",
                new=AsyncMock(return_value=None),
            ),
        ):
            result = await service.get_workline_detail(db, 45)

        assert result is not None
        assert result.rack_operation_wait == "WMS_CALLBACK_RECEIVED"
        assert result.resource_evidence_kind == "WMS_CALLBACK_EVIDENCE"
        assert result.single_layer_rack_snapshot == "NON_SINGLE_LAYER_EVIDENCE"

    @pytest.mark.asyncio
    async def test_get_workline_detail_does_not_treat_resource_kind_as_rack_kind(self) -> None:
        from src.app.workline.services.runtime_query_service import RuntimeQueryService

        service = RuntimeQueryService()
        workline = SimpleNamespace(
            id=45,
            is_deleted=False,
            line_code="WL-45",
            line_name="SMT 线",
            line_type="SMT",
            zone_name=None,
            plugin_key="SMT_SORTING_INBOUND",
            contract_version=None,
            is_active=True,
            run_mode="SIMULATION",
            runtime_status="READY",
            active_safety_incident_id=None,
            stopped_at=None,
            stopped_reason=None,
            resumed_at=None,
        )
        session = SimpleNamespace(
            id=20,
            status="WAITING_EXTERNAL",
            context_json={
                "resource_evidence": {
                    "resource_kind": "RACK",
                    "resource_code": "RACK-WITHOUT-KIND",
                },
            },
            last_ingress_at=None,
            waiting_since=timezone.now_for_db(),
            deadline_at=None,
            started_at=timezone.now_for_db(),
            created_at=timezone.now_for_db(),
        )
        db = AsyncMock()
        db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: workline)

        with (
            patch(
                "src.app.workline.services.runtime_query_service.device_repository.get_by_work_line_id",
                new=AsyncMock(return_value=[]),
            ),
            patch.object(service, "_load_active_sessions_for_workline", new=AsyncMock(return_value=[session])),
            patch.object(service, "_load_recent_failed_sessions_for_workline", new=AsyncMock(return_value=[])),
            patch.object(service, "_load_recent_completed_sessions_for_workline", new=AsyncMock(return_value=[])),
            patch.object(service, "_build_trace_list_items", new=AsyncMock(return_value=[])),
            patch(
                "src.app.workline.services.runtime_query_service.station_lease_service.get_station_lease_status",
                new=AsyncMock(return_value=SimpleNamespace(available=True, reason_code=None)),
            ),
            patch(
                "src.app.workline.services.runtime_query_service.smt_active_rack_snapshot_service.get_active_bin_rack",
                new=AsyncMock(return_value=None),
            ),
        ):
            result = await service.get_workline_detail(db, 45)

        assert result is not None
        assert result.resource_evidence_items[0].resource_code == "RACK-WITHOUT-KIND"
        assert result.single_layer_rack_snapshot == "MISSING"

    @pytest.mark.asyncio
    async def test_get_workline_detail_projects_structured_resource_evidence_items(self) -> None:
        from src.app.workline.services.runtime_query_service import RuntimeQueryService

        service = RuntimeQueryService()
        now = timezone.now_for_db()
        workline = SimpleNamespace(
            id=45,
            is_deleted=False,
            line_code="WL-45",
            line_name="粗分线",
            line_type="SORTING",
            zone_name=None,
            plugin_key="rough_sorter",
            contract_version=None,
            is_active=True,
            run_mode="SIMULATION",
            runtime_status="READY",
            active_safety_incident_id=None,
            stopped_at=None,
            stopped_reason=None,
            resumed_at=None,
        )
        duplicate_trace_evidence = {
            "resource_kind": "PKG",
            "resource_code": "PKG-001",
            "pkg_code": "PKG-001",
            "bin_code": "BIN-WMS",
            "evidence_kind": "TRACE_RESOURCE_EVIDENCE",
            "trace_id": "trace-resource",
            "occurred_at": now.isoformat(),
        }
        session = SimpleNamespace(
            id=20,
            status="WAITING_EXTERNAL",
            trace_id="trace-20",
            context_json={
                "rack_operation": {
                    "operation_key": "rack-op-1",
                    "status": "ARRIVED",
                    "source_system": "WMS",
                    "callback_type": "WMS_RACK_ARRIVED",
                    "rack_code": "RACK-WMS",
                    "bin_code": "BIN-WMS",
                    "target_position_code": "SINGLE_LAYER_A",
                    "occurred_at": now.isoformat(),
                },
                "resource_evidence": duplicate_trace_evidence,
                "resource_state_events": [
                    duplicate_trace_evidence,
                    *[
                        {
                            "resource_kind": "PKG",
                            "resource_code": f"PKG-{index:03d}",
                            "pkg_code": f"PKG-{index:03d}",
                            "evidence_kind": "TRACE_RESOURCE_EVIDENCE",
                            "trace_id": "trace-resource",
                            "occurred_at": now.isoformat(),
                        }
                        for index in range(2, 55)
                    ],
                ],
            },
            last_ingress_at=now,
            waiting_since=now,
            deadline_at=None,
            started_at=now,
            created_at=now,
        )
        db = AsyncMock()
        db.execute.return_value = SimpleNamespace(
            scalar_one_or_none=lambda: workline,
            scalars=lambda: SimpleNamespace(all=list),
        )
        active_snapshot = {
            "rack_code": "RACK-ACTIVE",
            "rack_kind": "SINGLE_LAYER",
            "cells": [
                {
                    "rack_slot_code": "A",
                    "bin_code": "BIN-ACTIVE",
                    "bin_cell_index": 1,
                    "bin_cell_code": "BIN-ACTIVE-1",
                    "pkg_code": "PKG-ACTIVE",
                }
            ],
        }

        with (
            patch(
                "src.app.workline.services.runtime_query_service.device_repository.get_by_work_line_id",
                new=AsyncMock(return_value=[]),
            ),
            patch.object(service, "_load_active_sessions_for_workline", new=AsyncMock(return_value=[session])),
            patch.object(service, "_load_recent_failed_sessions_for_workline", new=AsyncMock(return_value=[])),
            patch.object(service, "_load_recent_completed_sessions_for_workline", new=AsyncMock(return_value=[])),
            patch.object(service, "_build_trace_list_items", new=AsyncMock(return_value=[])),
            patch(
                "src.app.workline.services.runtime_query_service.station_lease_service.get_station_lease_status",
                new=AsyncMock(return_value=SimpleNamespace(available=True, reason_code=None)),
            ),
            patch(
                "src.app.workline.services.runtime_query_service.smt_active_rack_snapshot_service.get_active_bin_rack",
                new=AsyncMock(return_value=active_snapshot),
            ),
        ):
            result = await service.get_workline_detail(db, 45)

        assert result is not None
        assert result.resource_evidence_total_count > 50
        assert result.resource_evidence_truncated is True
        assert len(result.resource_evidence_items) == 50

        active_rack = next(item for item in result.resource_evidence_items if item.resource_code == "RACK-ACTIVE")
        assert active_rack.resource_kind == "RACK"
        assert active_rack.evidence_kind == "WES_ACTIVE_SNAPSHOT"
        assert active_rack.position_code == "SINGLE_LAYER_A"

        wms_bin = next(item for item in result.resource_evidence_items if item.resource_code == "BIN-WMS")
        assert wms_bin.resource_kind == "BIN"
        assert wms_bin.evidence_kind == "WMS_CALLBACK_EVIDENCE"
        assert wms_bin.rack_code == "RACK-WMS"
        assert wms_bin.position_code == "SINGLE_LAYER_A"
        assert wms_bin.source_session_id == 20

        trace_pkg = next(item for item in result.resource_evidence_items if item.resource_code == "PKG-001")
        assert trace_pkg.resource_kind == "PKG"
        assert trace_pkg.evidence_kind == "TRACE_RESOURCE_EVIDENCE"
        assert trace_pkg.pkg_code == "PKG-001"
        assert trace_pkg.source_session_id == 20
        assert trace_pkg.source_trace_id == "trace-resource"
        assert (
            sum(
                1
                for item in result.resource_evidence_items
                if item.resource_code == "PKG-001" and item.evidence_kind == "TRACE_RESOURCE_EVIDENCE"
            )
            == 1
        )

    @pytest.mark.asyncio
    async def test_get_workline_detail_prefers_material_unit_current_location_for_pkg_evidence(self) -> None:
        from src.app.workline.models import MaterialUnit, MaterialUnitStatus
        from src.app.workline.services.runtime_query_service import RuntimeQueryService

        service = RuntimeQueryService()
        now = timezone.now_for_db()
        workline = SimpleNamespace(
            id=45,
            is_deleted=False,
            line_code="WL-45",
            line_name="SMT 线",
            line_type="SMT",
            zone_name=None,
            plugin_key="smt_sorting_inbound",
            contract_version=None,
            is_active=True,
            run_mode="SIMULATION",
            runtime_status="READY",
            active_safety_incident_id=None,
            stopped_at=None,
            stopped_reason=None,
            resumed_at=None,
        )
        session = SimpleNamespace(
            id=20,
            status="RUNNING",
            trace_id="trace-20",
            context_json={
                "resource_evidence": {
                    "resource_kind": "PKG",
                    "resource_code": "PKG-001",
                    "pkg_code": "PKG-001",
                    "evidence_kind": "TRACE_RESOURCE_EVIDENCE",
                    "trace_id": "trace-20",
                    "occurred_at": now.isoformat(),
                }
            },
            last_ingress_at=now,
            waiting_since=None,
            deadline_at=None,
            started_at=now,
            created_at=now,
        )
        material_unit = MaterialUnit(
            id=9001,
            pkg_code="PKG-001",
            material_identity_key="MAT:620100L00-011-G:122625:8904936031",
            six_in_one={},
            status=MaterialUnitStatus.STORED,
            current_location="BIN-ROOT:4",
            current_session_id=20,
        )
        execute_results = [
            SimpleNamespace(scalar_one_or_none=lambda: workline),
            SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [material_unit])),
        ]
        db = AsyncMock()
        db.execute.side_effect = execute_results

        with (
            patch(
                "src.app.workline.services.runtime_query_service.device_repository.get_by_work_line_id",
                new=AsyncMock(return_value=[]),
            ),
            patch.object(service, "_load_active_sessions_for_workline", new=AsyncMock(return_value=[session])),
            patch.object(service, "_load_recent_failed_sessions_for_workline", new=AsyncMock(return_value=[])),
            patch.object(service, "_load_recent_completed_sessions_for_workline", new=AsyncMock(return_value=[])),
            patch.object(service, "_build_trace_list_items", new=AsyncMock(return_value=[])),
            patch(
                "src.app.workline.services.runtime_query_service.station_lease_service.get_station_lease_status",
                new=AsyncMock(return_value=SimpleNamespace(available=True, reason_code=None)),
            ),
            patch(
                "src.app.workline.services.runtime_query_service.smt_active_rack_snapshot_service.get_active_bin_rack",
                new=AsyncMock(return_value=None),
            ),
        ):
            result = await service.get_workline_detail(db, 45)

        assert result is not None
        pkg_item = next(item for item in result.resource_evidence_items if item.resource_code == "PKG-001")
        assert pkg_item.position_code == "BIN-ROOT:4"
        assert pkg_item.evidence_kind == "TRACE_RESOURCE_EVIDENCE"

    @pytest.mark.asyncio
    async def test_get_workline_detail_keeps_projection_position_when_material_unit_cache_differs(self) -> None:
        from src.app.workline.models import MaterialUnit, MaterialUnitStatus
        from src.app.workline.services.runtime_query_service import RuntimeQueryService

        service = RuntimeQueryService()
        now = timezone.now_for_db()
        workline = SimpleNamespace(
            id=45,
            is_deleted=False,
            line_code="WL-45",
            line_name="SMT 线",
            line_type="SMT",
            zone_name=None,
            plugin_key="smt_sorting_inbound",
            contract_version=None,
            is_active=True,
            run_mode="SIMULATION",
            runtime_status="READY",
            active_safety_incident_id=None,
            stopped_at=None,
            stopped_reason=None,
            resumed_at=None,
        )
        session = SimpleNamespace(
            id=20,
            status="RUNNING",
            trace_id="trace-20",
            context_json={
                "resource_evidence": {
                    "resource_kind": "PKG",
                    "resource_code": "PKG-001",
                    "pkg_code": "PKG-001",
                    "position_code": "PROJECTION-AUTHORITY",
                    "evidence_kind": "TRACE_RESOURCE_EVIDENCE",
                    "trace_id": "trace-20",
                    "occurred_at": now.isoformat(),
                }
            },
            last_ingress_at=now,
            waiting_since=None,
            deadline_at=None,
            started_at=now,
            created_at=now,
        )
        material_unit = MaterialUnit(
            id=9001,
            pkg_code="PKG-001",
            material_identity_key="MAT:620100L00-011-G:122625:8904936031",
            six_in_one={},
            status=MaterialUnitStatus.STORED,
            current_location="STALE-CACHE:9",
            current_session_id=20,
        )
        db = AsyncMock()
        db.execute.side_effect = [
            SimpleNamespace(scalar_one_or_none=lambda: workline),
            SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [material_unit])),
        ]

        with (
            patch(
                "src.app.workline.services.runtime_query_service.device_repository.get_by_work_line_id",
                new=AsyncMock(return_value=[]),
            ),
            patch.object(service, "_load_active_sessions_for_workline", new=AsyncMock(return_value=[session])),
            patch.object(service, "_load_recent_failed_sessions_for_workline", new=AsyncMock(return_value=[])),
            patch.object(service, "_load_recent_completed_sessions_for_workline", new=AsyncMock(return_value=[])),
            patch.object(service, "_build_trace_list_items", new=AsyncMock(return_value=[])),
            patch(
                "src.app.workline.services.runtime_query_service.station_lease_service.get_station_lease_status",
                new=AsyncMock(return_value=SimpleNamespace(available=True, reason_code=None)),
            ),
            patch(
                "src.app.workline.services.runtime_query_service.smt_active_rack_snapshot_service.get_active_bin_rack",
                new=AsyncMock(return_value=None),
            ),
        ):
            result = await service.get_workline_detail(db, 45)

        assert result is not None
        pkg_item = next(item for item in result.resource_evidence_items if item.resource_code == "PKG-001")
        assert pkg_item.position_code == "PROJECTION-AUTHORITY"

    @pytest.mark.asyncio
    async def test_get_workline_detail_projects_timed_out_rack_operation_wait(self) -> None:
        from src.app.workline.services.runtime_query_service import RuntimeQueryService

        service = RuntimeQueryService()
        workline = SimpleNamespace(
            id=45,
            is_deleted=False,
            line_code="WL-45",
            line_name="SMT 线",
            line_type="SMT",
            zone_name=None,
            plugin_key="rough_sorter",
            contract_version=None,
            is_active=True,
            run_mode="SIMULATION",
            runtime_status="READY",
            active_safety_incident_id=None,
            stopped_at=None,
            stopped_reason=None,
            resumed_at=None,
        )
        session = SimpleNamespace(
            id=20,
            status="WAITING_EXTERNAL",
            context_json={
                "waiting_rack_operation_key": "rack-op-timeout",
                "rack_operation": {"operation_key": "rack-op-timeout", "status": "PENDING"},
                "resource_evidence": {"resource_evidence_kind": "TRACE_RESOURCE_EVIDENCE"},
            },
            last_ingress_at=None,
            waiting_since=timezone.now_for_db() - timedelta(minutes=5),
            deadline_at=timezone.now_for_db() - timedelta(minutes=1),
            started_at=timezone.now_for_db() - timedelta(minutes=10),
            created_at=timezone.now_for_db() - timedelta(minutes=11),
        )
        db = AsyncMock()
        db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: workline)

        with (
            patch(
                "src.app.workline.services.runtime_query_service.device_repository.get_by_work_line_id",
                new=AsyncMock(return_value=[]),
            ),
            patch.object(service, "_load_active_sessions_for_workline", new=AsyncMock(return_value=[session])),
            patch.object(service, "_load_recent_failed_sessions_for_workline", new=AsyncMock(return_value=[])),
            patch.object(service, "_load_recent_completed_sessions_for_workline", new=AsyncMock(return_value=[])),
            patch.object(service, "_build_trace_list_items", new=AsyncMock(return_value=[])),
            patch(
                "src.app.workline.services.runtime_query_service.station_lease_service.get_station_lease_status",
                new=AsyncMock(return_value=SimpleNamespace(available=True, reason_code=None)),
            ),
            patch(
                "src.app.workline.services.runtime_query_service.smt_active_rack_snapshot_service.get_active_bin_rack",
                new=AsyncMock(side_effect=ValueError("invalid active snapshot")),
            ),
        ):
            result = await service.get_workline_detail(db, 45)

        assert result is not None
        assert result.single_layer_rack_snapshot == "INVALID"
        assert result.rack_operation_wait == "TIMEOUT"
        assert result.resource_evidence_kind == "TRACE_RESOURCE_EVIDENCE"

    @pytest.mark.asyncio
    async def test_get_workline_detail_projects_explicit_timeout_rack_operation_status(self) -> None:
        from src.app.workline.services.runtime_query_service import RuntimeQueryService

        service = RuntimeQueryService()
        workline = SimpleNamespace(
            id=45,
            is_deleted=False,
            line_code="WL-45",
            line_name="SMT 线",
            line_type="SMT",
            zone_name=None,
            plugin_key="rough_sorter",
            contract_version=None,
            is_active=True,
            run_mode="SIMULATION",
            runtime_status="READY",
            active_safety_incident_id=None,
            stopped_at=None,
            stopped_reason=None,
            resumed_at=None,
        )
        session = SimpleNamespace(
            id=20,
            status="WAITING_EXTERNAL",
            context_json={
                "waiting_rack_operation_key": "rack-op-timeout",
                "rack_operation": {"operation_key": "rack-op-timeout", "status": "TIMEOUT"},
            },
            last_ingress_at=None,
            waiting_since=timezone.now_for_db() - timedelta(minutes=5),
            deadline_at=None,
            started_at=timezone.now_for_db() - timedelta(minutes=10),
            created_at=timezone.now_for_db() - timedelta(minutes=11),
        )
        db = AsyncMock()
        db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: workline)

        with (
            patch(
                "src.app.workline.services.runtime_query_service.device_repository.get_by_work_line_id",
                new=AsyncMock(return_value=[]),
            ),
            patch.object(service, "_load_active_sessions_for_workline", new=AsyncMock(return_value=[session])),
            patch.object(service, "_load_recent_failed_sessions_for_workline", new=AsyncMock(return_value=[])),
            patch.object(service, "_load_recent_completed_sessions_for_workline", new=AsyncMock(return_value=[])),
            patch.object(service, "_build_trace_list_items", new=AsyncMock(return_value=[])),
            patch(
                "src.app.workline.services.runtime_query_service.station_lease_service.get_station_lease_status",
                new=AsyncMock(return_value=SimpleNamespace(available=True, reason_code=None)),
            ),
            patch(
                "src.app.workline.services.runtime_query_service.smt_active_rack_snapshot_service.get_active_bin_rack",
                new=AsyncMock(return_value=None),
            ),
        ):
            result = await service.get_workline_detail(db, 45)

        assert result is not None
        assert result.rack_operation_wait == "TIMEOUT"

    @pytest.mark.asyncio
    async def test_load_active_sessions_for_device_queries_sessions_directly(self) -> None:
        from src.app.workline.services.runtime_query_service import RuntimeQueryService

        service = RuntimeQueryService()
        db = AsyncMock()
        active_session = SimpleNamespace(id=101, status="RUNNING")
        db.execute.return_value = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [active_session]))

        result = await service._load_active_sessions_for_device(db, device_id=9, limit=10)

        executed_query = db.execute.await_args.args[0]
        assert result == [active_session]
        assert db.execute.await_count == 1
        assert "workline_sessions" in str(executed_query)
        assert "device_commands.session_id_int" not in str(executed_query)

    @pytest.mark.asyncio
    async def test_load_latest_command_by_session_uses_window_query(self) -> None:
        from src.app.workline.services.runtime_query_service import RuntimeQueryService

        service = RuntimeQueryService()
        db = AsyncMock()
        session = SimpleNamespace(id=11, trace_id="trace-11")
        latest_command = SimpleNamespace(id=2, trace_id="trace-11")
        db.execute.return_value = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [latest_command]))

        result = await service._load_latest_command_by_session(db, [session])

        executed_query = db.execute.await_args.args[0]
        assert "row_number" in str(executed_query).lower()
        assert result == {11: latest_command}

    @pytest.mark.asyncio
    async def test_build_trace_list_items_uses_device_event_inbox_for_event_payload(self) -> None:
        from src.app.workline.services.runtime_query_service import RuntimeQueryService

        service = RuntimeQueryService()
        now = timezone.now_for_db()
        session = cast(
            "WorklineSession",
            SimpleNamespace(
                id=31,
                session_code="S31",
                trace_id="trace-31",
                last_request_id=None,
                business_key="stable-key-31",
                barcode=None,
                last_inbox_id=802,
                context_json={},
                workline_id=5,
                status="RUNNING",
                awaiting_device_command_code=None,
                current_wait_type=None,
                failure_domain=None,
                failure_code=None,
                started_at=now,
                last_ingress_at=now,
                waiting_since=None,
                ended_at=None,
                created_at=now,
                deadline_at=None,
            ),
        )
        device_event_inbox = cast(
            "WorklineInbox",
            SimpleNamespace(
                id=801,
                device_id=None,
                payload_json={
                    "event_type": "SCAN_COMPLETED",
                    "device_code": "ARM03",
                    "data": {"PkgID": "EVENT-PKG-31"},
                },
            ),
        )
        command_result_inbox = cast(
            "WorklineInbox",
            SimpleNamespace(
                id=802,
                device_id=None,
                payload_json={
                    "event_type": "COMMAND_RESULT",
                    "data": {"PkgID": "RESULT-PKG-31"},
                },
            ),
        )

        with (
            patch.object(service, "_load_workline_map", new=AsyncMock(return_value={})),
            patch.object(service, "_load_latest_command_by_session", new=AsyncMock(return_value={})),
            patch.object(service, "_load_command_map_by_ids", new=AsyncMock(return_value={})),
            patch.object(
                service,
                "_load_latest_inbox_by_session",
                new=AsyncMock(return_value={31: command_result_inbox}),
            ),
            patch.object(
                service,
                "_load_latest_event_inbox_by_session",
                new=AsyncMock(return_value={31: device_event_inbox}),
            ),
            patch.object(service, "_load_latest_timeline_by_session", new=AsyncMock(return_value={})),
            patch.object(service, "_load_device_map", new=AsyncMock(return_value={})),
        ):
            result = await service._build_trace_list_items(AsyncMock(), [session])

        assert result[0].event_type == "SCAN_COMPLETED"
        assert result[0].event_payload == device_event_inbox.payload_json

    @pytest.mark.asyncio
    async def test_get_workline_monitor_projection(self) -> None:
        from unittest.mock import MagicMock, call

        from src.app.workline.models.runtime import (
            RuntimeResourceEvidenceItem,
            RuntimeWorklineMonitorProjectionResponse,
        )
        from src.app.workline.services.runtime_query_service import (
            _RUNTIME_DETAIL_ACTIVE_SESSION_LIMIT,
            RuntimeQueryService,
        )
        from src.utils.timezone import timezone

        service = RuntimeQueryService()
        db = AsyncMock()
        db_time = timezone.now_for_db()

        workline = SimpleNamespace(
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

        class MockExecuteResult:
            def __init__(self, value):
                self._value = value

            def scalar_one_or_none(self):
                return self._value

            def scalar_one(self):
                return self._value

            def scalars(self):
                mock_scalars = MagicMock()
                mock_scalars.all.return_value = self._value
                return mock_scalars

            def all(self):
                return self._value

        pending_session = SimpleNamespace(
            id=99,
            session_code="SES-99",
            trace_id="trace-99",
            request_id="req-99",
            reconciliation_state="PENDING",
            reconciliation_reason=SimpleNamespace(value="CALLBACK_DEADLINE_EXPIRED"),
            reconciliation_source_kind=SimpleNamespace(value="DEVICE"),
            reconciliation_device_id=12,
            reconciliation_command_id=34,
            reconciliation_wait_token="token-99",
            reconciliation_occurred_at=db_time,
            reconciliation_deadline_at=db_time,
            reconciliation_late_evidence_received=True,
            updated_at=db_time,
        )

        db_executes = [
            MockExecuteResult(workline),  # 1. workline lookup
            MockExecuteResult([("RUNNING", 25), ("WAITING_EXTERNAL", 4)]),  # 2. active sessions status counts
            MockExecuteResult(3),  # 3. non-timed-out waiting sessions count
            MockExecuteResult(5),  # 4. failed traces count
            MockExecuteResult(8),  # 5. completed traces count
            MockExecuteResult(pending_session),  # 6. pending reconciliation session lookup
        ]
        db.execute = AsyncMock(side_effect=db_executes)

        active_sessions = [
            SimpleNamespace(
                id=i,
                session_code=f"SES-{i}",
                status="RUNNING",
                last_ingress_at=db_time,
                waiting_since=None,
                ended_at=None,
                started_at=db_time,
                created_at=db_time,
            )
            for i in range(25)
        ]
        recent_failed = [
            SimpleNamespace(
                id=i,
                session_code=f"SES-F-{i}",
                status="FAILED",
                last_ingress_at=db_time,
                waiting_since=None,
                ended_at=None,
                started_at=db_time,
                created_at=db_time,
            )
            for i in range(3)
        ]
        recent_completed = [
            SimpleNamespace(
                id=i,
                session_code=f"SES-C-{i}",
                status="COMPLETED",
                last_ingress_at=db_time,
                waiting_since=None,
                ended_at=None,
                started_at=db_time,
                created_at=db_time,
            )
            for i in range(4)
        ]
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
                current_command_id=None,
                last_heartbeat_at=db_time,
                error_code=None,
            )
        ]

        async def mock_build_trace_list_items(db, sessions):
            from src.app.workline.models.runtime import RuntimeTraceListItem

            return [
                RuntimeTraceListItem(
                    session_id=s.id,
                    session_code=s.session_code,
                    last_inbox_id=9000 + s.id,
                    workline_id=45,
                    status=s.status,
                    started_at=s.started_at,
                    last_ingress_at=s.last_ingress_at,
                    deadline_at=getattr(s, "deadline_at", None),
                )
                for s in sessions
            ]

        load_active_sessions = AsyncMock(side_effect=[active_sessions[:20], active_sessions])
        build_boundary = AsyncMock(
            return_value={
                "workline_readiness": "READY",
                "station_lease": "IDLE",
                "single_layer_rack_snapshot": "ACTIVE",
                "rack_operation_wait": "NONE",
                "resource_evidence_kind": "WES_ACTIVE_SNAPSHOT",
                "resource_evidence_items": [
                    RuntimeResourceEvidenceItem(
                        resource_kind="BIN",
                        resource_code="BIN-WMS",
                        display_label="BIN BIN-WMS",
                        evidence_kind="WMS_CALLBACK_EVIDENCE",
                        rack_code="RACK-WMS",
                        cell_code="CELL-WMS",
                        material_code="620100L00-011-G",
                        date_code="2401",
                        lot_code="LOT-A",
                        reel_count=2,
                        reel_code="REEL-WMS",
                        position_index=1,
                        occurred_at=db_time,
                    )
                ],
                "resource_evidence_total_count": 1,
                "resource_evidence_truncated": False,
            }
        )
        with (
            patch(
                "src.app.workline.services.runtime_query_service.device_repository.get_by_work_line_id",
                new=AsyncMock(return_value=devices),
            ),
            patch.object(service, "_load_active_sessions_for_workline", new=load_active_sessions),
            patch.object(
                service, "_load_recent_failed_sessions_for_workline", new=AsyncMock(return_value=recent_failed)
            ),
            patch.object(
                service, "_load_recent_completed_sessions_for_workline", new=AsyncMock(return_value=recent_completed)
            ),
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
            patch.object(service, "_build_trace_list_items", new=mock_build_trace_list_items),
            patch.object(service, "_build_workline_runtime_boundary", new=build_boundary),
        ):
            result = await service.get_workline_monitor_projection(db, 45)

        load_active_sessions.assert_has_awaits(
            [
                call(db, 45, limit=20),
                call(db, 45, limit=_RUNTIME_DETAIL_ACTIVE_SESSION_LIMIT),
            ]
        )
        assert build_boundary.await_args.args[2] == active_sessions
        assert isinstance(result, RuntimeWorklineMonitorProjectionResponse)
        assert result.summary.line_code == "WL-45"
        assert result.summary.active_session_count == 25
        assert result.summary.waiting_session_count == 3
        assert result.summary.failed_session_count == 5
        assert result.summary.last_activity_at is not None
        assert result.summary.last_activity_at.utcoffset() == timedelta(0)
        assert result.boundary.workline_readiness.value == "READY"
        assert result.device_nodes[0].last_heartbeat_at is not None
        assert result.device_nodes[0].last_heartbeat_at.utcoffset() == timedelta(0)
        assert result.resource_evidence.items[0].rack_code == "RACK-WMS"
        assert result.resource_evidence.items[0].cell_code == "CELL-WMS"
        assert result.resource_evidence.items[0].material_code == "620100L00-011-G"
        assert result.resource_evidence.items[0].date_code == "2401"
        assert result.resource_evidence.items[0].lot_code == "LOT-A"
        assert result.resource_evidence.items[0].reel_count == 2
        assert result.resource_evidence.items[0].reel_code == "REEL-WMS"
        assert result.resource_evidence.items[0].position_index == 1
        assert result.resource_evidence.items[0].occurred_at is not None
        assert result.resource_evidence.items[0].occurred_at.utcoffset() == timedelta(0)

        assert len(result.active_sessions.items) == 20
        assert result.active_sessions.items[0].last_inbox_id == 9000
        assert result.active_sessions.items[0].started_at is not None
        assert result.active_sessions.items[0].started_at.utcoffset() == timedelta(0)
        assert result.active_sessions.items[0].last_ingress_at is not None
        assert result.active_sessions.items[0].last_ingress_at.utcoffset() == timedelta(0)
        assert result.active_sessions.total_count == 29
        assert result.active_sessions.truncated is True

        assert len(result.recent_failed_traces.items) == 3
        assert result.recent_failed_traces.items[0].started_at is not None
        assert result.recent_failed_traces.items[0].started_at.utcoffset() == timedelta(0)
        assert result.recent_failed_traces.total_count == 5
        assert result.recent_failed_traces.truncated is False

        assert len(result.recent_completed_traces.items) == 4
        assert result.recent_completed_traces.items[0].last_ingress_at is not None
        assert result.recent_completed_traces.items[0].last_ingress_at.utcoffset() == timedelta(0)
        assert result.recent_completed_traces.total_count == 8
        assert result.recent_completed_traces.truncated is False

        assert result.action_candidates.pending_reconciliation is not None
        assert result.action_candidates.pending_reconciliation.session_id == 99
        assert result.action_candidates.pending_reconciliation.reason == "CALLBACK_DEADLINE_EXPIRED"
        assert result.action_candidates.pending_reconciliation.late_evidence_received is True
        assert result.action_candidates.pending_reconciliation.occurred_at.utcoffset() == timedelta(0)
        assert result.action_candidates.pending_reconciliation.deadline_at is not None
        assert result.action_candidates.pending_reconciliation.deadline_at.utcoffset() == timedelta(0)
        assert result.generated_at.tzinfo is not None
        assert result.generated_at.utcoffset() == timedelta(0)

    async def test_get_workline_monitor_projection_counts_resource_evidence_beyond_active_session_cap(
        self,
    ) -> None:
        from src.app.workline.models.runtime import RuntimeResourceEvidenceKind, RuntimeTraceListItem
        from src.app.workline.services.runtime_query_service import (
            _RUNTIME_DETAIL_ACTIVE_SESSION_LIMIT,
            _RUNTIME_RESOURCE_EVIDENCE_ITEM_LIMIT,
            RuntimeQueryService,
        )
        from src.utils.timezone import timezone

        service = RuntimeQueryService()
        db = AsyncMock()
        db_time = timezone.now_for_db()

        workline = SimpleNamespace(
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

        class MockExecuteResult:
            def __init__(self, value):
                self._value = value

            def scalar_one_or_none(self):
                return self._value

            def scalar_one(self):
                return self._value

            def scalars(self):
                mock_scalars = MagicMock()
                mock_scalars.all.return_value = self._value
                return mock_scalars

            def all(self):
                return self._value

        active_total = _RUNTIME_DETAIL_ACTIVE_SESSION_LIMIT + 25
        db.execute = AsyncMock(
            side_effect=[
                MockExecuteResult(workline),
                MockExecuteResult([("RUNNING", active_total)]),
                MockExecuteResult(0),
                MockExecuteResult(0),
                MockExecuteResult(0),
                MockExecuteResult(None),
            ]
        )

        active_sessions = [
            SimpleNamespace(
                id=index,
                session_code=f"SES-{index}",
                trace_id=f"trace-{index}",
                request_id=f"req-{index}",
                status="RUNNING",
                context_json={
                    "resource_evidence": {
                        "resource_kind": "PKG",
                        "resource_code": f"PKG-{index:03d}",
                        "evidence_kind": "TRACE_RESOURCE_EVIDENCE",
                    }
                },
                last_ingress_at=db_time,
                waiting_since=None,
                deadline_at=None,
                started_at=db_time,
                created_at=db_time,
            )
            for index in range(1, active_total + 1)
        ]
        evidence_projection = SimpleNamespace(
            kind=RuntimeResourceEvidenceKind.TRACE_RESOURCE_EVIDENCE,
            items=[],
            total_count=active_total,
            truncated=True,
            has_non_single_layer=False,
        )

        async def build_trace_items(_db, sessions):
            return [
                RuntimeTraceListItem(
                    session_id=session.id,
                    session_code=session.session_code,
                    trace_id=session.trace_id,
                    request_id=session.request_id,
                    workline_id=45,
                    status=session.status,
                    started_at=session.started_at,
                    last_ingress_at=session.last_ingress_at,
                )
                for session in sessions
            ]

        load_active_sessions = AsyncMock(
            side_effect=[active_sessions[:20], active_sessions[:_RUNTIME_DETAIL_ACTIVE_SESSION_LIMIT]]
        )
        load_resource_evidence_projection = AsyncMock(return_value=evidence_projection)
        with (
            patch(
                "src.app.workline.services.runtime_query_service.device_repository.get_by_work_line_id",
                new=AsyncMock(return_value=[]),
            ),
            patch.object(service, "_load_active_sessions_for_workline", new=load_active_sessions),
            patch.object(service, "_load_recent_failed_sessions_for_workline", new=AsyncMock(return_value=[])),
            patch.object(service, "_load_recent_completed_sessions_for_workline", new=AsyncMock(return_value=[])),
            patch.object(service, "_build_trace_list_items", new=AsyncMock(side_effect=build_trace_items)),
            patch.object(
                service,
                "_load_runtime_resource_evidence_projection_for_workline",
                new=load_resource_evidence_projection,
            ),
        ):
            result = await service.get_workline_monitor_projection(db, 45)

        load_active_sessions.assert_has_awaits(
            [
                call(db, 45, limit=20),
                call(db, 45, limit=_RUNTIME_DETAIL_ACTIVE_SESSION_LIMIT),
            ]
        )
        assert all(await_call.kwargs.get("limit") is not None for await_call in load_active_sessions.await_args_list)
        load_resource_evidence_projection.assert_awaited_once()
        assert result is not None
        assert result.active_sessions.total_count == active_total
        assert result.active_sessions.truncated is True
        assert result.resource_evidence.total_count == active_total
        assert result.resource_evidence.truncated is True

    async def test_runtime_resource_evidence_projection_pages_and_caps_items(self) -> None:
        from src.app.workline.models.runtime import RuntimeRackOperationWait, RuntimeResourceEvidenceKind
        from src.app.workline.services.runtime_query_service import (
            _RUNTIME_DETAIL_ACTIVE_SESSION_LIMIT,
            _RUNTIME_RESOURCE_EVIDENCE_ITEM_LIMIT,
            RuntimeQueryService,
        )
        from src.utils.timezone import timezone

        service = RuntimeQueryService()
        db = AsyncMock()
        db_time = timezone.now_for_db()
        active_total = _RUNTIME_DETAIL_ACTIVE_SESSION_LIMIT + 25
        rows = [
            {
                "id": index,
                "trace_id": f"trace-{index}",
                "context_json": {
                    "resource_evidence": {
                        "resource_kind": "PKG",
                        "resource_code": f"PKG-{index:03d}",
                        "evidence_kind": "TRACE_RESOURCE_EVIDENCE",
                    }
                },
                "last_ingress_at": db_time,
                "started_at": db_time,
                "created_at": db_time,
            }
            for index in range(1, active_total + 1)
        ]

        class MockMappingResult:
            def __init__(self, value):
                self._value = value

            def mappings(self):
                mock_mappings = MagicMock()
                mock_mappings.all.return_value = self._value
                return mock_mappings

        db.execute = AsyncMock(
            side_effect=[
                MockMappingResult(rows[:_RUNTIME_DETAIL_ACTIVE_SESSION_LIMIT]),
                MockMappingResult(rows[_RUNTIME_DETAIL_ACTIVE_SESSION_LIMIT:]),
            ]
        )

        result = await service._load_runtime_resource_evidence_projection_for_workline(
            db,
            45,
            active_snapshots=[],
            current=RuntimeResourceEvidenceKind.UNKNOWN,
            rack_operation_wait=RuntimeRackOperationWait.NONE,
        )

        assert db.execute.await_count == 2
        assert result.kind == RuntimeResourceEvidenceKind.TRACE_RESOURCE_EVIDENCE
        assert result.total_count == active_total
        assert result.truncated is True
        assert len(result.items) == _RUNTIME_RESOURCE_EVIDENCE_ITEM_LIMIT
