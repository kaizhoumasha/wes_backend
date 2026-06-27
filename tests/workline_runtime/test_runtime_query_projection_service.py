from datetime import timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.utils.timezone import timezone
from tests.workline_runtime.support.runtime_query_projection import AnyArgHashable

if TYPE_CHECKING:
    from src.app.callback.models import CallbackLog
    from src.app.device.models import Device, DeviceCommand
    from src.app.workline.models import WorkLine, WorklineInbox, WorklineSession


class TestRuntimeQueryService:
    @pytest.mark.asyncio
    async def test_get_trace_list_uses_database_count_and_page_query(self) -> None:
        from src.app.workline.models.runtime import RuntimeTraceListItem, TraceQueryRequest
        from src.app.workline.services.runtime_query_service import RuntimeQueryService

        session_a = SimpleNamespace(
            id=11,
            session_code="S11",
            trace_id=None,
            last_request_id=None,
            workline_id=5,
            status="RUNNING",
            current_wait_type=None,
            failure_domain=None,
            failure_code=None,
            started_at=None,
            last_ingress_at=None,
            deadline_at=None,
        )
        session_b = SimpleNamespace(
            id=12,
            session_code="S12",
            trace_id=None,
            last_request_id=None,
            workline_id=5,
            status="RUNNING",
            current_wait_type=None,
            failure_domain=None,
            failure_code=None,
            started_at=None,
            last_ingress_at=None,
            deadline_at=None,
        )
        count_result = SimpleNamespace(scalar_one=lambda: 50)
        page_result = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [session_a, session_b]))
        db = AsyncMock()
        db.execute.side_effect = [count_result, page_result]
        service = RuntimeQueryService()
        payload = TraceQueryRequest(limit=2, offset=4)
        trace_items = [
            RuntimeTraceListItem(session_id=11, session_code="S11", workline_id=5, status="RUNNING"),
            RuntimeTraceListItem(session_id=12, session_code="S12", workline_id=5, status="RUNNING"),
        ]

        with patch.object(service, "_build_trace_list_items", new=AsyncMock(return_value=trace_items)) as mock_items:
            result = await service.get_trace_list(db, payload)

        assert result.total == 50
        assert result.items == trace_items
        mock_items.assert_awaited_once_with(AnyArgHashable(), [session_a, session_b])

    def test_build_trace_list_item_exposes_operator_business_identity(self) -> None:
        from src.app.workline.services.runtime_query_service import RuntimeQueryService

        service = RuntimeQueryService()
        now = timezone.now_for_db()
        session = cast(
            "WorklineSession",
            SimpleNamespace(
                id=11,
                session_code="S11",
                trace_id="trace-11",
                last_request_id=None,
                business_key="stable-key-11",
                barcode=None,
                last_inbox_id=370,
                context_json={
                    "initial_payload": {
                        "data": {
                            "PkgID": "SVYU00125TP4LCR02_9",
                            "HHPN": "620100L00-011-G",
                        }
                    }
                },
                workline_id=5,
                status="FAILED",
                current_wait_type=None,
                failure_domain=None,
                failure_code=None,
                started_at=None,
                last_ingress_at=None,
                deadline_at=None,
            ),
        )
        workline = cast("WorkLine", SimpleNamespace(line_name="SMT 线", line_code="WL-5"))

        item = service._build_trace_list_item(
            session,
            workline,
            None,
            None,
            None,
            now,
            latest_device=None,
            action_source="NONE",
        )

        assert item.business_key == "stable-key-11"
        assert item.barcode == "SVYU00125TP4LCR02_9"
        assert item.last_inbox_id == 370

    def test_build_trace_list_item_exposes_event_payload_from_latest_inbox(self) -> None:
        from src.app.workline.services.runtime_query_service import RuntimeQueryService

        service = RuntimeQueryService()
        now = timezone.now_for_db()
        event_payload = {
            "event_type": "SCAN_COMPLETED",
            "device_code": "ARM03",
            "data": {
                "PkgID": "SVYU00125TP4LCR02_9",
                "HHPN": "620100L00-011-G",
            },
        }
        session = cast(
            "WorklineSession",
            SimpleNamespace(
                id=11,
                session_code="S11",
                trace_id="trace-11",
                last_request_id=None,
                business_key="stable-key-11",
                barcode=None,
                last_inbox_id=370,
                context_json={},
                workline_id=5,
                status="RUNNING",
                current_wait_type=None,
                failure_domain=None,
                failure_code=None,
                started_at=None,
                last_ingress_at=None,
                deadline_at=None,
            ),
        )
        inbox = cast("WorklineInbox", SimpleNamespace(id=370, payload_json=event_payload))

        item = service._build_trace_list_item(
            session,
            None,
            None,
            None,
            None,
            now,
            inbox=inbox,
            latest_device=None,
            action_source="NONE",
        )

        assert item.event_type == "SCAN_COMPLETED"
        assert item.event_payload == event_payload

    @pytest.mark.asyncio
    async def test_get_overview_uses_failure_count_query_instead_of_recent_list_length(self) -> None:
        from src.app.workline.services.runtime_query_service import RuntimeQueryService

        service = RuntimeQueryService()
        db = AsyncMock()
        recent_failed_sessions = [SimpleNamespace(id=1), SimpleNamespace(id=2)]

        with (
            patch.object(service, "list_worklines", new=AsyncMock(return_value=[])),
            patch.object(service, "list_devices", new=AsyncMock(return_value=[])),
            patch.object(service, "_load_recent_failed_sessions", new=AsyncMock(return_value=recent_failed_sessions)),
            patch.object(service, "_build_trace_list_items", new=AsyncMock(return_value=[])),
            patch.object(service, "_load_simulation_workline_ids", new=AsyncMock(return_value=[99])) as mock_sim_ids,
            patch.object(service, "_count_by_status", new=AsyncMock(side_effect=[7, 9, 10])),
            patch.object(service, "_count_waiting_sessions", new=AsyncMock(return_value=8), create=True),
            patch.object(
                service,
                "_count_failed_or_timed_out_sessions",
                new=AsyncMock(return_value=42),
                create=True,
            ) as mock_failed_count,
        ):
            result = await service.get_overview(db)

        mock_sim_ids.assert_awaited_once_with(AnyArgHashable())
        mock_failed_count.assert_awaited_once_with(AnyArgHashable(), exclude_workline_ids=[99])
        failed_card = next(item for item in result.stats if item.key == "failed_sessions")
        assert failed_card.value == 42

    def test_build_workline_summary_excludes_timed_out_sessions_from_waiting_count(self) -> None:
        from src.app.workline.services.runtime_query_service import RuntimeQueryService

        now = timezone.now_for_db()
        service = RuntimeQueryService()
        workline = cast(
            "WorkLine",
            SimpleNamespace(
                id=5,
                line_code="WL-05",
                line_name="SMT 线",
                line_type="SMT",
                zone_name=None,
                plugin_key=None,
                contract_version=None,
                is_active=True,
                run_mode="AUTO",
                runtime_status="READY",
                active_safety_incident_id=None,
                stopped_at=None,
                stopped_reason=None,
                resumed_at=None,
            ),
        )
        timed_out_session = cast(
            "WorklineSession",
            SimpleNamespace(
                status="WAITING_EXTERNAL",
                deadline_at=now - timedelta(minutes=5),
                last_ingress_at=None,
                waiting_since=now - timedelta(minutes=10),
                started_at=None,
                created_at=now - timedelta(minutes=20),
            ),
        )

        summary = service._build_workline_summary(workline, [], [timed_out_session])

        assert summary.waiting_session_count == 0
        assert summary.failed_session_count == 1

    def test_build_workline_summary_separates_active_and_waiting_sessions(self) -> None:
        from src.app.workline.services.runtime_query_service import RuntimeQueryService

        now = timezone.now_for_db()
        service = RuntimeQueryService()
        workline = cast(
            "WorkLine",
            SimpleNamespace(
                id=5,
                line_code="WL-05",
                line_name="SMT 线",
                line_type="SMT",
                zone_name=None,
                plugin_key=None,
                contract_version=None,
                is_active=True,
                run_mode="AUTO",
                runtime_status="READY",
                active_safety_incident_id=None,
                stopped_at=None,
                stopped_reason=None,
                resumed_at=None,
            ),
        )
        running_session = cast(
            "WorklineSession",
            SimpleNamespace(
                status="RUNNING",
                deadline_at=None,
                last_ingress_at=now - timedelta(minutes=1),
                waiting_since=None,
                started_at=now - timedelta(minutes=5),
                created_at=now - timedelta(minutes=6),
            ),
        )
        waiting_session = cast(
            "WorklineSession",
            SimpleNamespace(
                status="WAITING_EXTERNAL",
                deadline_at=now + timedelta(minutes=10),
                last_ingress_at=None,
                waiting_since=now - timedelta(minutes=2),
                started_at=now - timedelta(minutes=8),
                created_at=now - timedelta(minutes=9),
            ),
        )

        summary = service._build_workline_summary(workline, [], [running_session, waiting_session])

        assert summary.active_session_count == 1
        assert summary.waiting_session_count == 1
        assert summary.failed_session_count == 0

    def test_build_workline_summary_exposes_safety_projection(self) -> None:
        from src.app.workline.models.runtime import RuntimeWorklineDetailResponse
        from src.app.workline.models.safety import WorkLineRuntimeStatus
        from src.app.workline.services.runtime_query_service import RuntimeQueryService

        stopped_at = timezone.now_for_db()
        start_admission_checked_at = timezone.now_for_db()
        service = RuntimeQueryService()
        workline = cast(
            "WorkLine",
            SimpleNamespace(
                id=5,
                line_code="WL-05",
                line_name="SMT 线",
                line_type="SMT",
                zone_name=None,
                plugin_key=None,
                contract_version=None,
                is_active=True,
                run_mode="AUTO",
                runtime_status=WorkLineRuntimeStatus.ESTOPPED,
                active_safety_incident_id=1001,
                stopped_at=stopped_at,
                stopped_reason="ESTOP_PRESSED",
                resumed_at=None,
                start_admission_status="FAILED",
                start_admission_message="设备未就绪",
                start_admission_failed_device_code="PLC-01",
                start_admission_checked_at=start_admission_checked_at,
                last_start_request_id="req-start-001",
                last_start_trace_id="trace-start-001",
            ),
        )

        summary = service._build_workline_summary(workline, [], [])

        assert summary.runtime_status == WorkLineRuntimeStatus.ESTOPPED.value
        assert summary.active_safety_incident_id == 1001
        assert summary.stopped_at == stopped_at
        assert summary.stopped_reason == "ESTOP_PRESSED"
        assert summary.resumed_at is None
        assert summary.start_admission_status == "FAILED"
        assert summary.start_admission_message == "设备未就绪"
        assert summary.start_admission_failed_device_code == "PLC-01"
        assert summary.start_admission_checked_at == start_admission_checked_at
        assert summary.last_start_request_id == "req-start-001"
        assert summary.last_start_trace_id == "trace-start-001"

        detail = RuntimeWorklineDetailResponse(summary=summary)
        assert detail.model_dump()["summary"]["start_admission_status"] == "FAILED"
        assert detail.model_dump()["workline_readiness"] == "UNKNOWN"
        assert detail.model_dump()["station_lease"] == "UNKNOWN"
        assert detail.model_dump()["single_layer_rack_snapshot"] == "UNKNOWN"
        assert detail.model_dump()["rack_operation_wait"] == "NONE"
        assert detail.model_dump()["resource_evidence_kind"] == "UNKNOWN"

    def test_runtime_workline_detail_schema_exposes_structured_boundary_fields(self) -> None:
        from src.app.workline.models.runtime import RuntimeWorklineDetailResponse

        schema = RuntimeWorklineDetailResponse.model_json_schema()
        properties = schema["properties"]

        def enum_values(field_name: str) -> list[str]:
            field_schema = properties[field_name]
            if "enum" in field_schema:
                return field_schema["enum"]
            ref_name = field_schema["$ref"].removeprefix("#/$defs/")
            return schema["$defs"][ref_name]["enum"]

        assert enum_values("workline_readiness") == ["READY", "NOT_READY", "UNKNOWN"]
        assert enum_values("station_lease") == [
            "IDLE",
            "ACTIVE_RACK_BOUND",
            "ACTIVE_DISPATCH_LEASE",
            "ACTIVE_SESSION_BOUND",
            "UNKNOWN",
        ]
        assert enum_values("single_layer_rack_snapshot") == [
            "ACTIVE",
            "MISSING",
            "INVALID",
            "NON_SINGLE_LAYER_EVIDENCE",
            "UNKNOWN",
        ]
        assert enum_values("rack_operation_wait") == [
            "WAITING_WMS",
            "WMS_CALLBACK_RECEIVED",
            "TIMEOUT",
            "FAILED",
            "NONE",
            "UNKNOWN",
        ]
        assert enum_values("resource_evidence_kind") == [
            "WES_ACTIVE_SNAPSHOT",
            "WMS_CALLBACK_EVIDENCE",
            "TRACE_RESOURCE_EVIDENCE",
            "GENERIC_EVIDENCE",
            "UNKNOWN",
        ]
        assert properties["resource_evidence_total_count"]["default"] == 0
        assert properties["resource_evidence_truncated"]["default"] is False

        item_ref_name = properties["resource_evidence_items"]["items"]["$ref"].removeprefix("#/$defs/")
        item_properties = schema["$defs"][item_ref_name]["properties"]
        resource_kind_ref_name = item_properties["resource_kind"]["$ref"].removeprefix("#/$defs/")
        evidence_kind_ref_name = item_properties["evidence_kind"]["$ref"].removeprefix("#/$defs/")

        assert schema["$defs"][resource_kind_ref_name]["enum"] == [
            "RACK",
            "BIN",
            "PKG",
            "SLOT",
            "CELL",
            "MAGAZINE",
            "PART_SN",
            "UNKNOWN",
        ]
        assert schema["$defs"][evidence_kind_ref_name]["enum"] == [
            "WES_ACTIVE_SNAPSHOT",
            "WMS_CALLBACK_EVIDENCE",
            "TRACE_RESOURCE_EVIDENCE",
            "GENERIC_EVIDENCE",
            "UNKNOWN",
        ]
        assert {
            "resource_code",
            "display_label",
            "cell_code",
            "material_code",
            "date_code",
            "lot_code",
            "reel_count",
            "reel_code",
            "position_index",
            "source_session_id",
            "source_trace_id",
            "occurred_at",
        }.issubset(item_properties)

    def test_runtime_resource_evidence_projects_active_bin_rack_cell_aliases_and_nested_bins(self) -> None:
        from src.app.workline.models.runtime import RuntimeResourceEvidenceKind
        from src.app.workline.services.runtime_query_service import (
            _runtime_resource_evidence_items_from_active_snapshot,
        )

        bin_cells_items = _runtime_resource_evidence_items_from_active_snapshot(
            {
                "rack_code": "RACK-BIN-CELLS",
                "bin_cells": [
                    {
                        "rack_slot_code": "A",
                        "bin_code": "BIN-A",
                        "bin_cell_index": "1",
                        "bin_cell_code": "CELL-A",
                        "pkg_code": "PKG-A",
                        "material_code": "620100L00-011-G",
                        "DateCode": "2401",
                        "LotCode": "LOT-A",
                        "reel_count": 2,
                        "reel_code": "REEL-A",
                        "position_index": 1,
                    }
                ],
            },
            evidence_kind=RuntimeResourceEvidenceKind.WES_ACTIVE_SNAPSHOT,
        )
        cell_snapshots_items = _runtime_resource_evidence_items_from_active_snapshot(
            {
                "rack_code": "RACK-CELL-SNAPSHOTS",
                "cell_snapshots": [
                    {
                        "rack_slot_code": "B",
                        "bin_code": "BIN-B",
                        "bin_cell_index": "2",
                        "cell_code": "CELL-B",
                        "part_sn": "PART-B",
                    }
                ],
            },
            evidence_kind=RuntimeResourceEvidenceKind.WES_ACTIVE_SNAPSHOT,
        )
        nested_bin_items = _runtime_resource_evidence_items_from_active_snapshot(
            {
                "rack_code": "RACK-NESTED",
                "bins": [
                    {
                        "rack_slot_code": "C",
                        "bin_code": "BIN-C",
                        "cells": [
                            {
                                "bin_cell_index": "3",
                                "bin_cell_code": "CELL-C",
                                "pkg_code": "PKG-C",
                            }
                        ],
                    }
                ],
            },
            evidence_kind=RuntimeResourceEvidenceKind.WES_ACTIVE_SNAPSHOT,
        )
        combined_alias_items = _runtime_resource_evidence_items_from_active_snapshot(
            {
                "rack_code": "RACK-COMBINED",
                "cells": [],
                "bin_cells": [
                    {
                        "rack_slot_code": "D",
                        "bin_code": "BIN-D",
                        "bin_cell_index": "4",
                        "bin_cell_code": "CELL-D",
                        "pkg_code": "PKG-D",
                    }
                ],
                "cell_snapshots": [
                    {
                        "rack_slot_code": "E",
                        "bin_code": "BIN-E",
                        "bin_cell_index": "5",
                        "cell_code": "CELL-E",
                        "part_sn": "PART-E",
                    }
                ],
            },
            evidence_kind=RuntimeResourceEvidenceKind.WES_ACTIVE_SNAPSHOT,
        )
        location_alias_items = _runtime_resource_evidence_items_from_active_snapshot(
            {
                "rack_code": "RACK-LOCATION",
                "cells": [
                    {
                        "rack_slot_code": "F",
                        "bin_code": "BIN-F",
                        "bin_cell_index": "6",
                        "bin_cell_location": "CELL-F",
                        "pkg_code": "PKG-F",
                    }
                ],
            },
            evidence_kind=RuntimeResourceEvidenceKind.WES_ACTIVE_SNAPSHOT,
        )
        duplicate_local_cell_items = _runtime_resource_evidence_items_from_active_snapshot(
            {
                "rack_code": "RACK-LOCAL-CELLS",
                "cells": [
                    {
                        "rack_slot_code": "G",
                        "bin_code": "BIN-G1",
                        "bin_cell_location": "1",
                        "pkg_code": "PKG-G1",
                    },
                    {
                        "rack_slot_code": "H",
                        "bin_code": "BIN-G2",
                        "bin_cell_location": "1",
                        "pkg_code": "PKG-G2",
                    },
                ],
            },
            evidence_kind=RuntimeResourceEvidenceKind.WES_ACTIVE_SNAPSHOT,
        )

        assert {(item.resource_kind.value, item.resource_code) for item in bin_cells_items} >= {
            ("RACK", "RACK-BIN-CELLS"),
            ("SLOT", "A"),
            ("BIN", "BIN-A"),
            ("CELL", "CELL-A"),
            ("PKG", "PKG-A"),
        }
        pkg_a = next(item for item in bin_cells_items if item.resource_code == "PKG-A")
        assert pkg_a.cell_code == "CELL-A"
        assert pkg_a.material_code == "620100L00-011-G"
        assert pkg_a.date_code == "2401"
        assert pkg_a.lot_code == "LOT-A"
        assert pkg_a.reel_count == 2
        assert pkg_a.reel_code == "REEL-A"
        assert pkg_a.position_index == 1
        assert {(item.resource_kind.value, item.resource_code) for item in cell_snapshots_items} >= {
            ("RACK", "RACK-CELL-SNAPSHOTS"),
            ("SLOT", "B"),
            ("BIN", "BIN-B"),
            ("CELL", "CELL-B"),
            ("PART_SN", "PART-B"),
        }
        assert {(item.resource_kind.value, item.resource_code) for item in nested_bin_items} >= {
            ("RACK", "RACK-NESTED"),
            ("SLOT", "C"),
            ("BIN", "BIN-C"),
            ("CELL", "CELL-C"),
            ("PKG", "PKG-C"),
        }
        assert {(item.resource_kind.value, item.resource_code) for item in combined_alias_items} >= {
            ("RACK", "RACK-COMBINED"),
            ("SLOT", "D"),
            ("BIN", "BIN-D"),
            ("CELL", "CELL-D"),
            ("PKG", "PKG-D"),
            ("SLOT", "E"),
            ("BIN", "BIN-E"),
            ("CELL", "CELL-E"),
            ("PART_SN", "PART-E"),
        }
        assert {(item.resource_kind.value, item.resource_code) for item in location_alias_items} >= {
            ("CELL", "CELL-F"),
            ("PKG", "PKG-F"),
        }
        assert [
            (item.resource_code, item.bin_code)
            for item in duplicate_local_cell_items
            if item.resource_kind.value == "CELL"
        ] == [("1", "BIN-G1"), ("1", "BIN-G2")]

    def test_runtime_resource_evidence_does_not_synthesize_cell_resource_code_from_index(self) -> None:
        from src.app.workline.models.runtime import RuntimeResourceEvidenceKind
        from src.app.workline.services.runtime_query_service import (
            _runtime_resource_evidence_items_from_active_snapshot,
        )

        items = _runtime_resource_evidence_items_from_active_snapshot(
            {
                "rack_code": "RACK-NO-CELL-CODE",
                "cells": [
                    {
                        "bin_code": "BIN-NO-CELL-CODE",
                        "bin_cell_index": "1",
                        "pkg_code": "PKG-NO-CELL-CODE",
                    }
                ],
            },
            evidence_kind=RuntimeResourceEvidenceKind.WES_ACTIVE_SNAPSHOT,
        )

        assert {(item.resource_kind.value, item.resource_code) for item in items} >= {
            ("RACK", "RACK-NO-CELL-CODE"),
            ("BIN", "BIN-NO-CELL-CODE"),
            ("PKG", "PKG-NO-CELL-CODE"),
        }
        assert all(item.resource_kind.value != "CELL" for item in items)

    def test_runtime_resource_evidence_preserves_payload_display_label(self) -> None:
        from src.app.workline.models.runtime import RuntimeResourceEvidenceKind
        from src.app.workline.services.runtime_query_service import (
            _runtime_resource_evidence_items_from_active_snapshot,
        )

        items = _runtime_resource_evidence_items_from_active_snapshot(
            {
                "resource_kind": "RACK",
                "resource_code": "RACK-LABEL",
                "display_label": "待 WMS 到位料架 RACK-LABEL",
            },
            evidence_kind=RuntimeResourceEvidenceKind.GENERIC_EVIDENCE,
        )

        item = next(item for item in items if item.resource_code == "RACK-LABEL")
        assert item.display_label == "待 WMS 到位料架 RACK-LABEL"

    def test_runtime_resource_evidence_inherits_active_bin_rack_parent_metadata_to_flat_cells(self) -> None:
        from src.app.workline.models.runtime import RuntimeResourceEvidenceKind
        from src.app.workline.services.runtime_query_service import (
            _runtime_resource_evidence_items_from_active_snapshot,
        )

        items = _runtime_resource_evidence_items_from_active_snapshot(
            {
                "rack_code": "RACK-PARENT-META",
                "target_position_code": "POS-PARENT",
                "station": {"code": "STATION-PARENT", "position_code": "POS-STATION-FALLBACK"},
                "cells": [
                    {
                        "rack_slot_code": "SLOT-A",
                        "bin_code": "BIN-A",
                        "bin_cell_index": "1",
                        "bin_cell_code": "CELL-A",
                        "pkg_code": "PKG-A",
                    }
                ],
                "bin_cells": [
                    {
                        "rack_slot_code": "SLOT-B",
                        "bin_code": "BIN-B",
                        "bin_cell_index": "2",
                        "bin_cell_code": "CELL-B",
                        "pkg_code": "PKG-B",
                        "position_code": "POS-CHILD",
                        "station_code": "STATION-CHILD",
                    }
                ],
                "cell_snapshots": [
                    {
                        "rack_slot_code": "SLOT-C",
                        "bin_code": "BIN-C",
                        "bin_cell_index": "3",
                        "cell_code": "CELL-C",
                        "pkg_code": "PKG-C",
                    }
                ],
            },
            evidence_kind=RuntimeResourceEvidenceKind.WES_ACTIVE_SNAPSHOT,
        )

        def item_for(kind: str, code: str):
            return next(item for item in items if item.resource_kind.value == kind and item.resource_code == code)

        inherited_codes = [
            ("BIN", "BIN-A"),
            ("CELL", "CELL-A"),
            ("PKG", "PKG-A"),
            ("BIN", "BIN-C"),
            ("CELL", "CELL-C"),
            ("PKG", "PKG-C"),
        ]
        for kind, code in inherited_codes:
            item = item_for(kind, code)
            assert item.position_code == "POS-PARENT"
            assert item.station_code == "STATION-PARENT"

        child_codes = [
            ("BIN", "BIN-B"),
            ("CELL", "CELL-B"),
            ("PKG", "PKG-B"),
        ]
        for kind, code in child_codes:
            item = item_for(kind, code)
            assert item.position_code == "POS-CHILD"
            assert item.station_code == "STATION-CHILD"

    @pytest.mark.parametrize(
        ("snapshot_position_key", "expected_position_code"),
        [
            ("source_position_code", "POS-SOURCE"),
            ("position_code", "POS-TOP"),
        ],
    )
    def test_runtime_resource_evidence_inherits_snapshot_position_aliases_to_flat_children(
        self,
        snapshot_position_key: str,
        expected_position_code: str,
    ) -> None:
        from src.app.workline.models.runtime import RuntimeResourceEvidenceKind
        from src.app.workline.services.runtime_query_service import (
            _runtime_resource_evidence_items_from_active_snapshot,
        )

        items = _runtime_resource_evidence_items_from_active_snapshot(
            {
                "rack_code": "RACK-POSITION-ALIAS",
                snapshot_position_key: expected_position_code,
                "cells": [
                    {
                        "rack_slot_code": "SLOT-POSITION",
                        "bin_code": "BIN-POSITION",
                        "bin_cell_index": "1",
                        "bin_cell_code": "CELL-POSITION",
                        "pkg_code": "PKG-POSITION",
                    }
                ],
            },
            evidence_kind=RuntimeResourceEvidenceKind.WES_ACTIVE_SNAPSHOT,
        )

        def item_for(kind: str, code: str):
            return next(item for item in items if item.resource_kind.value == kind and item.resource_code == code)

        for kind, code in (
            ("BIN", "BIN-POSITION"),
            ("CELL", "CELL-POSITION"),
            ("PKG", "PKG-POSITION"),
        ):
            assert item_for(kind, code).position_code == expected_position_code

    def test_runtime_resource_evidence_uses_snapshot_rack_id_as_flat_child_rack_code(self) -> None:
        from src.app.workline.models.runtime import RuntimeResourceEvidenceKind
        from src.app.workline.services.runtime_query_service import (
            _runtime_resource_evidence_items_from_active_snapshot,
        )

        items = _runtime_resource_evidence_items_from_active_snapshot(
            {
                "rack_id": "RACK-ID-FALLBACK",
                "cells": [
                    {
                        "rack_slot_code": "SLOT-RACK-ID",
                        "bin_code": "BIN-RACK-ID",
                        "bin_cell_index": "1",
                        "bin_cell_code": "CELL-RACK-ID",
                        "pkg_code": "PKG-RACK-ID",
                    }
                ],
            },
            evidence_kind=RuntimeResourceEvidenceKind.WES_ACTIVE_SNAPSHOT,
        )

        def item_for(kind: str, code: str):
            return next(item for item in items if item.resource_kind.value == kind and item.resource_code == code)

        for kind, code in (
            ("BIN", "BIN-RACK-ID"),
            ("CELL", "CELL-RACK-ID"),
            ("PKG", "PKG-RACK-ID"),
        ):
            assert item_for(kind, code).rack_code == "RACK-ID-FALLBACK"

    def test_runtime_resource_evidence_nested_bin_metadata_priority(self) -> None:
        from src.app.workline.models.runtime import RuntimeResourceEvidenceKind
        from src.app.workline.services.runtime_query_service import (
            _runtime_resource_evidence_items_from_active_snapshot,
        )

        items = _runtime_resource_evidence_items_from_active_snapshot(
            {
                "rack_code": "RACK-NESTED-META",
                "work_position_code": "POS-SNAPSHOT",
                "station_code": "STATION-SNAPSHOT",
                "bins": [
                    {
                        "rack_slot_code": "SLOT-BIN",
                        "bin_code": "BIN-BIN",
                        "target_position_code": "POS-BIN",
                        "station": {"code": "STATION-BIN"},
                        "cells": [
                            {
                                "bin_cell_index": "1",
                                "bin_cell_code": "CELL-BIN",
                                "pkg_code": "PKG-BIN",
                            },
                            {
                                "bin_cell_index": "2",
                                "bin_cell_code": "CELL-CELL",
                                "pkg_code": "PKG-CELL",
                                "position_code": "POS-CELL",
                                "station_code": "STATION-CELL",
                            },
                        ],
                    },
                    {
                        "rack_slot_code": "SLOT-SNAPSHOT",
                        "bin_code": "BIN-SNAPSHOT",
                        "cells": [
                            {
                                "bin_cell_index": "1",
                                "bin_cell_code": "CELL-SNAPSHOT",
                                "pkg_code": "PKG-SNAPSHOT",
                            }
                        ],
                    },
                ],
            },
            evidence_kind=RuntimeResourceEvidenceKind.WES_ACTIVE_SNAPSHOT,
        )

        def item_for(kind: str, code: str):
            return next(item for item in items if item.resource_kind.value == kind and item.resource_code == code)

        bin_level_pkg = item_for("PKG", "PKG-BIN")
        bin_level_cell = item_for("CELL", "CELL-BIN")
        assert bin_level_pkg.position_code == "POS-BIN"
        assert bin_level_pkg.station_code == "STATION-BIN"
        assert bin_level_cell.position_code == "POS-BIN"
        assert bin_level_cell.station_code == "STATION-BIN"

        cell_level_pkg = item_for("PKG", "PKG-CELL")
        assert cell_level_pkg.position_code == "POS-CELL"
        assert cell_level_pkg.station_code == "STATION-CELL"

        snapshot_level_pkg = item_for("PKG", "PKG-SNAPSHOT")
        snapshot_level_cell = item_for("CELL", "CELL-SNAPSHOT")
        assert snapshot_level_pkg.position_code == "POS-SNAPSHOT"
        assert snapshot_level_pkg.station_code == "STATION-SNAPSHOT"
        assert snapshot_level_cell.position_code == "POS-SNAPSHOT"
        assert snapshot_level_cell.station_code == "STATION-SNAPSHOT"

    def test_runtime_resource_evidence_keeps_nested_bin_without_cells(self) -> None:
        from src.app.workline.models.runtime import RuntimeResourceEvidenceKind
        from src.app.workline.services.runtime_query_service import (
            _runtime_resource_evidence_items_from_active_snapshot,
        )

        items = _runtime_resource_evidence_items_from_active_snapshot(
            {
                "rack_code": "RACK-BIN-ONLY",
                "bins": [
                    {
                        "rack_slot_code": "SLOT-BIN-ONLY",
                        "bin_id": "BIN-ONLY",
                        "empty_cells": 6,
                    }
                ],
            },
            evidence_kind=RuntimeResourceEvidenceKind.WES_ACTIVE_SNAPSHOT,
        )

        assert {(item.resource_kind.value, item.resource_code) for item in items} >= {
            ("RACK", "RACK-BIN-ONLY"),
            ("SLOT", "SLOT-BIN-ONLY"),
            ("BIN", "BIN-ONLY"),
        }

    def test_runtime_resource_evidence_projects_all_reel_packages(self) -> None:
        from src.app.workline.models.runtime import RuntimeResourceEvidenceKind
        from src.app.workline.services.runtime_query_service import (
            _runtime_resource_evidence_items_from_active_snapshot,
        )

        items = _runtime_resource_evidence_items_from_active_snapshot(
            {
                "rack_code": "RACK-REELS",
                "cells": [
                    {
                        "rack_slot_code": "SLOT-REELS",
                        "bin_code": "BIN-REELS",
                        "bin_cell_index": "1",
                        "bin_cell_code": "CELL-REELS",
                        "PkgID": "PKG-LATEST",
                        "material_code": "620100L00-011-G",
                        "DateCode": "2401",
                        "LotCode": "LOT-A",
                        "reels": [
                            {
                                "pkg_code": "PKG-LATEST",
                                "reel_code": "REEL-LATEST",
                                "cell_stack_position": 2,
                            },
                            {
                                "pkg_code": "PKG-OLDER",
                                "reel_code": "REEL-OLDER",
                                "cell_stack_position": 1,
                            },
                        ],
                    }
                ],
            },
            evidence_kind=RuntimeResourceEvidenceKind.WES_ACTIVE_SNAPSHOT,
        )

        pkg_codes = {item.resource_code for item in items if item.resource_kind.value == "PKG"}
        assert pkg_codes >= {"PKG-LATEST", "PKG-OLDER"}
        older_reel = next(item for item in items if item.resource_code == "PKG-OLDER")
        assert older_reel.cell_code == "CELL-REELS"
        assert older_reel.material_code == "620100L00-011-G"
        assert older_reel.date_code == "2401"
        assert older_reel.lot_code == "LOT-A"
        assert older_reel.reel_code == "REEL-OLDER"
        assert older_reel.position_index == 1

    def test_runtime_resource_evidence_prefers_child_station_alias_over_parent_station_code(self) -> None:
        from src.app.workline.models.runtime import RuntimeResourceEvidenceKind
        from src.app.workline.services.runtime_query_service import (
            _runtime_resource_evidence_items_from_active_snapshot,
        )

        items = _runtime_resource_evidence_items_from_active_snapshot(
            {
                "rack_code": "RACK-CHILD-STATION-ALIAS",
                "station_code": "STATION-PARENT",
                "bins": [
                    {
                        "rack_slot_code": "SLOT-BIN",
                        "bin_code": "BIN-CHILD",
                        "target_station_code": "STATION-CHILD",
                        "cells": [
                            {
                                "bin_cell_index": "1",
                                "bin_cell_code": "CELL-CHILD",
                                "pkg_code": "PKG-CHILD",
                            }
                        ],
                    }
                ],
            },
            evidence_kind=RuntimeResourceEvidenceKind.WES_ACTIVE_SNAPSHOT,
        )

        def item_for(kind: str, code: str):
            return next(item for item in items if item.resource_kind.value == kind and item.resource_code == code)

        assert item_for("BIN", "BIN-CHILD").station_code == "STATION-CHILD"
        assert item_for("CELL", "CELL-CHILD").station_code == "STATION-CHILD"
        assert item_for("PKG", "PKG-CHILD").station_code == "STATION-CHILD"

    def test_runtime_resource_evidence_inherits_nested_bin_station_code_when_cell_has_position(self) -> None:
        from src.app.workline.models.runtime import RuntimeResourceEvidenceKind
        from src.app.workline.services.runtime_query_service import (
            _runtime_resource_evidence_items_from_active_snapshot,
        )

        items = _runtime_resource_evidence_items_from_active_snapshot(
            {
                "rack_code": "RACK-BIN-STATION-CELL-POSITION",
                "bins": [
                    {
                        "rack_slot_code": "SLOT-BIN",
                        "bin_code": "BIN-STATION",
                        "station": {"code": "STATION-BIN"},
                        "cells": [
                            {
                                "bin_cell_index": "1",
                                "bin_cell_code": "CELL-STATION",
                                "pkg_code": "PKG-STATION",
                                "station": {"position_code": "POS-CELL"},
                            }
                        ],
                    }
                ],
            },
            evidence_kind=RuntimeResourceEvidenceKind.WES_ACTIVE_SNAPSHOT,
        )

        def item_for(kind: str, code: str):
            return next(item for item in items if item.resource_kind.value == kind and item.resource_code == code)

        for kind, code in (
            ("CELL", "CELL-STATION"),
            ("PKG", "PKG-STATION"),
        ):
            item = item_for(kind, code)
            assert item.station_code == "STATION-BIN"
            assert item.position_code == "POS-CELL"

    def test_runtime_resource_evidence_inherits_nested_bin_position_when_cell_has_station_code(self) -> None:
        from src.app.workline.models.runtime import RuntimeResourceEvidenceKind
        from src.app.workline.services.runtime_query_service import (
            _runtime_resource_evidence_items_from_active_snapshot,
        )

        items = _runtime_resource_evidence_items_from_active_snapshot(
            {
                "rack_code": "RACK-BIN-POSITION-CELL-STATION",
                "bins": [
                    {
                        "rack_slot_code": "SLOT-BIN",
                        "bin_code": "BIN-POSITION",
                        "station": {"position_code": "POS-BIN"},
                        "cells": [
                            {
                                "bin_cell_index": "1",
                                "bin_cell_code": "CELL-POSITION",
                                "pkg_code": "PKG-POSITION",
                                "station": {"code": "STATION-CELL"},
                            }
                        ],
                    }
                ],
            },
            evidence_kind=RuntimeResourceEvidenceKind.WES_ACTIVE_SNAPSHOT,
        )

        def item_for(kind: str, code: str):
            return next(item for item in items if item.resource_kind.value == kind and item.resource_code == code)

        for kind, code in (
            ("CELL", "CELL-POSITION"),
            ("PKG", "PKG-POSITION"),
        ):
            item = item_for(kind, code)
            assert item.station_code == "STATION-CELL"
            assert item.position_code == "POS-BIN"

    def test_runtime_resource_evidence_inherits_parent_nested_station_code_when_child_station_lacks_code(
        self,
    ) -> None:
        from src.app.workline.models.runtime import RuntimeResourceEvidenceKind
        from src.app.workline.services.runtime_query_service import (
            _runtime_resource_evidence_items_from_active_snapshot,
        )

        items = _runtime_resource_evidence_items_from_active_snapshot(
            {
                "rack_code": "RACK-PARENT-NESTED-STATION",
                "station": {"code": "STATION-PARENT"},
                "bins": [
                    {
                        "rack_slot_code": "SLOT-BIN",
                        "bin_code": "BIN-CHILD",
                        "station": {"position_code": "POS-CHILD"},
                        "cells": [
                            {
                                "bin_cell_index": "1",
                                "bin_cell_code": "CELL-CHILD",
                                "pkg_code": "PKG-CHILD",
                            }
                        ],
                    }
                ],
            },
            evidence_kind=RuntimeResourceEvidenceKind.WES_ACTIVE_SNAPSHOT,
        )

        def item_for(kind: str, code: str):
            return next(item for item in items if item.resource_kind.value == kind and item.resource_code == code)

        for kind, code in (
            ("BIN", "BIN-CHILD"),
            ("CELL", "CELL-CHILD"),
            ("PKG", "PKG-CHILD"),
        ):
            item = item_for(kind, code)
            assert item.station_code == "STATION-PARENT"
            assert item.position_code == "POS-CHILD"

    @pytest.mark.parametrize(
        ("parent_station_key", "parent_station_code"),
        [
            ("target_station_code", "STATION-PARENT-TARGET"),
            ("work_station_code", "STATION-PARENT-WORK"),
        ],
    )
    def test_runtime_resource_evidence_inherits_parent_station_aliases(
        self,
        parent_station_key: str,
        parent_station_code: str,
    ) -> None:
        from src.app.workline.models.runtime import RuntimeResourceEvidenceKind
        from src.app.workline.services.runtime_query_service import (
            _runtime_resource_evidence_items_from_active_snapshot,
        )

        items = _runtime_resource_evidence_items_from_active_snapshot(
            {
                "rack_code": "RACK-PARENT-STATION-ALIAS",
                parent_station_key: parent_station_code,
                "cells": [
                    {
                        "rack_slot_code": "SLOT-A",
                        "bin_code": "BIN-A",
                        "bin_cell_index": "1",
                        "bin_cell_code": "CELL-A",
                        "pkg_code": "PKG-A",
                    }
                ],
            },
            evidence_kind=RuntimeResourceEvidenceKind.WES_ACTIVE_SNAPSHOT,
        )

        def item_for(kind: str, code: str):
            return next(item for item in items if item.resource_kind.value == kind and item.resource_code == code)

        assert item_for("BIN", "BIN-A").station_code == parent_station_code
        assert item_for("CELL", "CELL-A").station_code == parent_station_code
        assert item_for("PKG", "PKG-A").station_code == parent_station_code

    def test_runtime_resource_evidence_uses_pkgid_aliases_before_material_identity_key(self) -> None:
        from src.app.workline.models.runtime import RuntimeResourceEvidenceKind
        from src.app.workline.services.runtime_query_service import (
            _runtime_resource_evidence_items_from_active_snapshot,
        )

        items = _runtime_resource_evidence_items_from_active_snapshot(
            {
                "rack_code": "RACK-PKG-ALIASES",
                "cells": [
                    {
                        "bin_code": "BIN-CAMEL",
                        "bin_cell_index": "1",
                        "PkgID": "PKG-CAMEL",
                        "material_identity_key": "MAT-CAMEL",
                    },
                    {
                        "bin_code": "BIN-SNAKE",
                        "bin_cell_index": "1",
                        "pkg_id": "PKG-SNAKE",
                        "material_identity_key": "MAT-SNAKE",
                    },
                ],
            },
            evidence_kind=RuntimeResourceEvidenceKind.WES_ACTIVE_SNAPSHOT,
        )

        pkg_codes = {item.resource_code for item in items if item.resource_kind.value == "PKG"}
        assert pkg_codes >= {"PKG-CAMEL", "PKG-SNAKE"}
        assert "MAT-CAMEL" not in pkg_codes
        assert "MAT-SNAKE" not in pkg_codes

    @pytest.mark.asyncio
    async def test_runtime_resource_evidence_keeps_untruncated_counts_and_stable_sorting(self) -> None:
        from src.app.workline.models.runtime import (
            RuntimeSingleLayerRackSnapshot,
            RuntimeStationLease,
        )
        from src.app.workline.services.runtime_query_service import RuntimeQueryService

        service = RuntimeQueryService()
        now = timezone.now_for_db()
        workline = SimpleNamespace(runtime_status="READY")
        session = SimpleNamespace(
            id=20,
            status="WAITING_EXTERNAL",
            trace_id="trace-session",
            context_json={
                "resource_evidence": {
                    "resource_kind": "PKG",
                    "resource_code": "PKG-TRACE",
                    "pkg_code": "PKG-TRACE",
                    "bin_code": "BIN-TRACE",
                    "evidence_kind": "TRACE_RESOURCE_EVIDENCE",
                    "trace_id": "trace-resource",
                    "occurred_at": now.isoformat(),
                },
                "rack_operation": {
                    "operation_key": "rack-op-1",
                    "status": "ARRIVED",
                    "source_system": "WMS",
                    "callback_type": "WMS_RACK_ARRIVED",
                    "rack_code": "RACK-WMS",
                    "bin_code": "BIN-WMS",
                    "target_position_code": "POS-WMS",
                    "occurred_at": now.isoformat(),
                },
            },
            last_ingress_at=now,
            waiting_since=now,
            deadline_at=None,
            started_at=now,
            created_at=now,
        )
        active_snapshot = {
            "rack_code": "RACK-ACTIVE",
            "rack_kind": "SINGLE_LAYER",
            "cells": [
                {
                    "rack_slot_code": "SLOT-A",
                    "bin_code": "BIN-ACTIVE",
                    "bin_cell_code": "BIN-ACTIVE-1",
                    "pkg_code": "PKG-ACTIVE",
                    "part_sn": "PART-ACTIVE",
                }
            ],
        }

        with (
            patch.object(service, "_single_layer_boundary_positions", return_value=["SINGLE_LAYER_A"]),
            patch.object(
                service,
                "_load_runtime_station_lease",
                new=AsyncMock(return_value=RuntimeStationLease.IDLE),
            ),
            patch.object(
                service,
                "_load_single_layer_rack_snapshot_projection",
                new=AsyncMock(
                    return_value=(
                        RuntimeSingleLayerRackSnapshot.ACTIVE,
                        [("SINGLE_LAYER_A", active_snapshot)],
                    )
                ),
            ),
            patch.object(
                service,
                "_with_material_unit_locations",
                new=AsyncMock(side_effect=lambda db, projection: projection),
            ),
        ):
            boundary = await service._build_workline_runtime_boundary(AsyncMock(), workline, [session])

        items = boundary["resource_evidence_items"]
        assert boundary["resource_evidence_total_count"] == 10
        assert boundary["resource_evidence_truncated"] is False
        assert len(items) == boundary["resource_evidence_total_count"]
        assert [(item.evidence_kind.value, item.resource_kind.value, item.resource_code) for item in items] == [
            ("WES_ACTIVE_SNAPSHOT", "RACK", "RACK-ACTIVE"),
            ("WES_ACTIVE_SNAPSHOT", "SLOT", "SLOT-A"),
            ("WES_ACTIVE_SNAPSHOT", "BIN", "BIN-ACTIVE"),
            ("WES_ACTIVE_SNAPSHOT", "CELL", "BIN-ACTIVE-1"),
            ("WES_ACTIVE_SNAPSHOT", "PKG", "PKG-ACTIVE"),
            ("WES_ACTIVE_SNAPSHOT", "PART_SN", "PART-ACTIVE"),
            ("WMS_CALLBACK_EVIDENCE", "RACK", "RACK-WMS"),
            ("WMS_CALLBACK_EVIDENCE", "BIN", "BIN-WMS"),
            ("TRACE_RESOURCE_EVIDENCE", "BIN", "BIN-TRACE"),
            ("TRACE_RESOURCE_EVIDENCE", "PKG", "PKG-TRACE"),
        ]

    @pytest.mark.asyncio
    async def test_runtime_resource_evidence_kind_uses_explicit_payload_kind_without_source_hints(self) -> None:
        from src.app.workline.services.runtime_query_service import RuntimeQueryService

        service = RuntimeQueryService()
        session = SimpleNamespace(
            id=20,
            status="WAITING_EXTERNAL",
            trace_id="trace-explicit-kind",
            context_json={
                "resource_evidence": {
                    "resource_kind": "RACK",
                    "resource_code": "RACK-EXPLICIT-WMS",
                    "evidence_kind": "WMS_CALLBACK_EVIDENCE",
                }
            },
            last_ingress_at=None,
            started_at=None,
            created_at=None,
        )

        boundary = await service._build_workline_runtime_boundary(
            AsyncMock(),
            SimpleNamespace(runtime_status="READY", plugin_key=None),
            [session],
        )

        assert boundary["resource_evidence_kind"] == "WMS_CALLBACK_EVIDENCE"
        assert [(item.resource_code, item.evidence_kind.value) for item in boundary["resource_evidence_items"]] == [
            ("RACK-EXPLICIT-WMS", "WMS_CALLBACK_EVIDENCE")
        ]

    @pytest.mark.asyncio
    async def test_runtime_resource_evidence_kind_uses_active_bin_rack_when_no_payload_evidence(self) -> None:
        from src.app.workline.services.runtime_query_service import RuntimeQueryService

        service = RuntimeQueryService()
        session = SimpleNamespace(
            id=21,
            status="RUNNING",
            trace_id="trace-active-rack-only",
            context_json={
                "active_bin_rack": {
                    "rack_code": "RACK-ACTIVE-ONLY",
                    "cells": [
                        {
                            "bin_code": "BIN-ACTIVE-ONLY",
                            "bin_cell_code": "CELL-ACTIVE-ONLY",
                        }
                    ],
                }
            },
            last_ingress_at=None,
            waiting_since=None,
            deadline_at=None,
            started_at=None,
            created_at=None,
        )

        boundary = await service._build_workline_runtime_boundary(
            AsyncMock(),
            SimpleNamespace(runtime_status="READY", plugin_key=None),
            [session],
        )

        assert boundary["resource_evidence_kind"] == "GENERIC_EVIDENCE"
        items = boundary["resource_evidence_items"]
        assert boundary["resource_evidence_total_count"] == len(items)
        assert {item.evidence_kind.value for item in items} == {"GENERIC_EVIDENCE"}
        assert {item.resource_code for item in items} >= {
            "RACK-ACTIVE-ONLY",
            "BIN-ACTIVE-ONLY",
            "CELL-ACTIVE-ONLY",
        }

    @pytest.mark.asyncio
    async def test_get_workline_detail_uses_bounded_active_sessions_for_resource_evidence(self) -> None:
        from src.app.workline.models.runtime import RuntimeResourceEvidenceKind, RuntimeTraceListItem
        from src.app.workline.services.runtime_query_service import (
            _RUNTIME_DETAIL_ACTIVE_SESSION_LIMIT,
            RuntimeQueryService,
        )

        service = RuntimeQueryService()
        now = timezone.now_for_db()
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
        active_sessions = [
            SimpleNamespace(
                id=index,
                status="WAITING_EXTERNAL",
                session_code=f"SES-{index}",
                trace_id=f"trace-{index}",
                context_json={
                    "resource_evidence": {
                        "resource_kind": "PKG",
                        "resource_code": f"PKG-{index:03d}",
                        "evidence_kind": "TRACE_RESOURCE_EVIDENCE",
                    }
                },
                last_ingress_at=now,
                waiting_since=now,
                deadline_at=None,
                started_at=now,
                created_at=now,
            )
            for index in range(1, _RUNTIME_DETAIL_ACTIVE_SESSION_LIMIT + 1)
        ]
        db = AsyncMock()

        class MockExecuteResult:
            def __init__(self, value):
                self._value = value

            def scalar_one_or_none(self):
                return self._value

            def scalar_one(self):
                return self._value

            def scalars(self):
                return self

            def all(self):
                return self._value

        db.execute = AsyncMock(
            side_effect=[
                MockExecuteResult(workline),
                MockExecuteResult([("WAITING_EXTERNAL", 250)]),
                MockExecuteResult(250),
                MockExecuteResult(0),
                MockExecuteResult([]),
            ]
        )

        async def build_trace_items(_db, sessions):
            return [
                RuntimeTraceListItem(
                    session_id=session.id,
                    session_code=session.session_code,
                    workline_id=45,
                    status=session.status,
                )
                for session in sessions
            ]

        load_active_sessions = AsyncMock(return_value=active_sessions)
        with (
            patch(
                "src.app.workline.services.runtime_query_service.device_repository.get_by_work_line_id",
                new=AsyncMock(return_value=[]),
            ),
            patch.object(service, "_load_active_sessions_for_workline", new=load_active_sessions),
            patch.object(service, "_load_recent_failed_sessions_for_workline", new=AsyncMock(return_value=[])),
            patch.object(service, "_load_recent_completed_sessions_for_workline", new=AsyncMock(return_value=[])),
            patch.object(service, "_build_trace_list_items", new=AsyncMock(side_effect=build_trace_items)),
        ):
            result = await service.get_workline_detail(db, 45)

        load_active_sessions.assert_awaited_once_with(db, 45, limit=_RUNTIME_DETAIL_ACTIVE_SESSION_LIMIT)
        assert result is not None
        assert result.summary.waiting_session_count == 250
        assert len(result.active_sessions) == 20
        assert result.resource_evidence_total_count == _RUNTIME_DETAIL_ACTIVE_SESSION_LIMIT
        assert result.resource_evidence_truncated is True
        assert {item.resource_code for item in result.resource_evidence_items} >= {"PKG-001", "PKG-050"}

    def test_build_workline_summary_requires_persisted_workline(self) -> None:
        from src.app.workline.services.runtime_query_service import RuntimeQueryService

        service = RuntimeQueryService()
        workline = cast(
            "WorkLine",
            SimpleNamespace(
                id=None,
                line_code="WL-05",
                line_name="SMT 线",
                line_type="SMT",
                zone_name=None,
                plugin_key=None,
                contract_version=None,
                is_active=True,
            ),
        )

        with pytest.raises(ValueError, match=r"workline\.id"):
            _ = service._build_workline_summary(workline, [], [])

    def test_build_workline_device_item_requires_persisted_device(self) -> None:
        from src.app.workline.services.runtime_query_service import RuntimeQueryService

        service = RuntimeQueryService()
        device = cast(
            "Device",
            SimpleNamespace(
                id=None,
                device_code="ARM-01",
                device_name="机械臂",
                device_role="INPUT_ARM",
                role_index=1,
                upstream_device_id=None,
                device_status="IDLE",
                maintenance_mode=False,
                current_command_id=None,
                last_heartbeat_at=None,
                error_code=None,
            ),
        )

        with pytest.raises(ValueError, match=r"device\.id"):
            _ = service._build_workline_device_item(device)

    def test_build_device_summary_requires_persisted_device(self) -> None:
        from src.app.workline.services.runtime_query_service import RuntimeQueryService

        service = RuntimeQueryService()
        device = cast(
            "Device",
            SimpleNamespace(
                id=None,
                device_code="ARM-01",
                device_name="机械臂",
                device_role="INPUT_ARM",
                role_index=1,
                work_line_id=8,
                device_status="IDLE",
                maintenance_mode=False,
                current_command_id=None,
                last_heartbeat_at=None,
                error_code=None,
            ),
        )

        with pytest.raises(ValueError, match=r"device\.id"):
            _ = service._build_device_summary(device, None, 0, None)

    def test_build_callback_item_requires_persisted_callback_log(self) -> None:
        from src.app.workline.services.runtime_query_service import RuntimeQueryService

        service = RuntimeQueryService()
        callback_log = cast(
            "CallbackLog",
            SimpleNamespace(
                id=None,
                callback_type="event",
                subject_code="ARM-01",
                request_id=None,
                trace_id=None,
                response_status=200,
                response_time_ms=15,
                error_message=None,
                ingress_outcome=None,
                failure_stage=None,
                request_body={},
                created_at=timezone.now_for_db(),
                updated_at=None,
            ),
        )

        with pytest.raises(ValueError, match=r"callback_log\.id"):
            _ = service._build_callback_item(callback_log)

    def test_build_command_item_requires_persisted_command(self) -> None:
        from src.app.workline.services.runtime_query_service import RuntimeQueryService

        service = RuntimeQueryService()
        command = cast(
            "DeviceCommand",
            SimpleNamespace(
                id=None,
                device_id=1,
                command_code="CMD-01",
                trace_id=None,
                workline_id=8,
                session_id="9",
                task_type="MOVE",
                status="SENT",
                result=None,
                retry_count=0,
                sent_at=None,
                ack_received_at=None,
                completed_at=None,
                ack_code=None,
                ack_message=None,
                ack_trace_id=None,
                params={},
                result_data=None,
                error_detail=None,
                get_duration_ms=lambda: None,
            ),
        )

        with pytest.raises(ValueError, match=r"device_command\.id"):
            _ = service._build_command_item(command)

    def test_build_trace_list_item_requires_persisted_session(self) -> None:
        from src.app.workline.services.runtime_query_service import RuntimeQueryService

        service = RuntimeQueryService()
        session = cast(
            "WorklineSession",
            SimpleNamespace(
                id=None,
                session_code="S-01",
                trace_id=None,
                last_request_id=None,
                workline_id=8,
                status="RUNNING",
                current_wait_type=None,
                failure_domain=None,
                failure_code=None,
                started_at=None,
                last_ingress_at=None,
                deadline_at=None,
            ),
        )

        with pytest.raises(ValueError, match=r"session\.id"):
            _ = service._build_trace_list_item(
                session,
                None,
                None,
                None,
                None,
                timezone.now_for_db(),
                latest_device=None,
                action_source="NONE",
            )
