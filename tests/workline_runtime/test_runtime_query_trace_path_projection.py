from contextlib import nullcontext
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.utils.timezone import timezone
from tests.workline_runtime.support.runtime_query_projection import _TraceContextStub


class TestRuntimeQueryService:
    def test_build_trace_response_preserves_diagnostic_context(self) -> None:
        from src.app.workline.services.trace_response_builder import build_trace_response
        from src.workline_runtime.diagnostics import DiagnosticContext

        result = SimpleNamespace(
            trace=_TraceContextStub(request_id="req-001", trace_id="trace-001"),
            session=None,
            sessions=[],
            callback_logs=[],
            inboxes=[],
            commands=[],
            outboxes=[],
            dispatch_attempts=[],
            timelines=[],
            diagnostics=[
                DiagnosticContext(
                    request_id="req-001",
                    trace_id="trace-001",
                    session_id=21,
                    inbox_id=31,
                    command_code="CMD-001",
                    device_code="ARM01",
                    workline_id=45,
                    plugin_key="test_workline_plugin",
                    canonical_event_type="SCAN_COMPLETED",
                    transition="WAITING->RUNNING",
                    extra={"source": "session_snapshot"},
                )
            ],
        )

        response = build_trace_response(result)

        assert response.diagnostics[0].request_id == "req-001"
        assert response.diagnostics[0].trace_id == "trace-001"
        assert response.diagnostics[0].session_id == 21
        assert response.diagnostics[0].inbox_id == 31
        assert response.diagnostics[0].command_code == "CMD-001"
        assert response.diagnostics[0].device_code == "ARM01"
        assert response.diagnostics[0].workline_id == 45
        assert response.diagnostics[0].plugin_key == "test_workline_plugin"
        assert response.diagnostics[0].canonical_event_type == "SCAN_COMPLETED"
        assert response.diagnostics[0].transition == "WAITING->RUNNING"
        assert response.diagnostics[0].extra == {"source": "session_snapshot"}

    def test_build_trace_path_groups_timelines_by_owner(self) -> None:
        from src.app.workline.services.runtime_query_service import RuntimeQueryService

        service = RuntimeQueryService()
        now = timezone.now_for_db()
        session = SimpleNamespace(
            id=20,
            workline_id=45,
            current_wait_type=None,
            awaiting_device_command_code=None,
            failure_domain=None,
            failure_message=None,
        )
        command = SimpleNamespace(
            id=101,
            device_id=301,
            device_code="ARM01",
            command_code="CMD-101",
            task_type="MOVE",
            status="COMPLETED",
            result="SUCCESS",
            completed_at=now,
            sent_at=now,
        )
        inbox = SimpleNamespace(
            id=201,
            device_id=302,
            device_code="SCAN01",
            kind="DEVICE_EVENT",
            status="PROCESSED",
            processed_at=now,
            received_at=now,
            error_message=None,
        )

        def timeline(**overrides):
            defaults = {
                "id": 1,
                "session_id": 20,
                "workline_id": 45,
                "trace_id": "trace-20",
                "seq_no": 1,
                "occurred_at": now,
                "stage": "INGEST",
                "action_type": "EVENT_RECEIVED",
                "actor_type": "ORCHESTRATOR",
                "actor_code": "runtime",
                "from_status": None,
                "to_status": None,
                "status": "SUCCESS",
                "failure_domain": None,
                "message": None,
                "payload_json": {},
                "related_inbox_id": None,
                "related_command_id": None,
            }
            defaults.update(overrides)
            return SimpleNamespace(**defaults)

        timelines = [
            timeline(
                id=1,
                seq_no=1,
                action_type="EVENT_RECEIVED",
                actor_type="MANUAL_OPERATOR",
                actor_code="sandbox",
                related_command_id=None,
                related_inbox_id=None,
                payload_json={"trigger": "sandbox_event_submit"},
            ),
            timeline(
                id=2,
                seq_no=2,
                action_type="SESSION_CREATED",
                actor_type="ORCHESTRATOR",
                actor_code="runtime",
                related_command_id=None,
                related_inbox_id=None,
                payload_json={},
            ),
            timeline(
                id=3,
                seq_no=3,
                action_type="COMMAND_COMPLETED",
                actor_type="DEVICE",
                actor_code="ARM01",
                related_command_id=101,
                related_inbox_id=None,
                payload_json={},
            ),
            timeline(
                id=4,
                seq_no=4,
                action_type="EVENT_PROCESSED",
                actor_type="DEVICE",
                actor_code="SCAN01",
                related_command_id=None,
                related_inbox_id=201,
                payload_json={},
            ),
            timeline(
                id=5,
                seq_no=5,
                action_type="EXTERNAL_CALL_COMPLETED",
                actor_type="EXTERNAL_SYSTEM",
                actor_code="erp",
                related_command_id=None,
                related_inbox_id=None,
                payload_json={},
            ),
        ]
        result = SimpleNamespace(
            trace=SimpleNamespace(trace_id="trace-20"),
            session=session,
            commands=[command],
            inboxes=[inbox],
            outboxes=[],
            dispatch_attempts=[],
            timelines=timelines,
            sessions=[],
            callback_logs=[],
            diagnostics=[],
        )

        from src.app.workline.services import runtime_query_service as runtime_query_module

        patch_build_detail = (
            patch.object(runtime_query_module, "build_trace_response", return_value=None)
            if hasattr(runtime_query_module, "build_trace_response")
            else nullcontext()
        )
        with patch_build_detail:
            path = service._build_trace_path(result)

        groups = {group.group_key: group for group in path.timeline_groups}

        assert [event.id for event in groups["operator:sandbox"].events] == [1]
        assert [event.id for event in groups["orchestrator:session"].events] == [2]
        assert [event.id for event in groups["device:301"].events] == [3]
        assert [event.id for event in groups["device:302"].events] == [4]
        assert [event.id for event in groups["external:erp"].events] == [5]

    def test_build_trace_path_returns_slim_contract_without_evidence(self) -> None:
        from src.app.workline.models.runtime import RuntimeTracePathResponse
        from src.app.workline.services import runtime_query_service as runtime_query_module
        from src.app.workline.services.diagnosis_verdict_builder import diagnosis_verdict_builder
        from src.app.workline.services.runtime_query_service import RuntimeQueryService

        service = RuntimeQueryService()
        now = timezone.now_for_db()
        session = SimpleNamespace(
            id=20,
            session_code="SESSION-20",
            workline_id=45,
            plugin_key="test_workline_plugin",
            run_mode="SIMULATION",
            business_key="BK-20",
            barcode="BC-20",
            trace_id="trace-20",
            status="RUNNING",
            started_at=now,
            ended_at=None,
            current_wait_type=None,
            current_wait_timeout_seconds=None,
            waiting_since=None,
            deadline_at=None,
            awaiting_device_command_code=None,
            failure_domain=None,
            failure_code=None,
            failure_message=None,
            ingress_count=1,
            last_request_id="req-20",
            last_ingress_at=now,
            last_inbox_id=None,
            context_json={
                "active_bin_rack": {
                    "rack_code": "PATH-RACK",
                    "cells": [
                        {
                            "rack_slot_code": "P01",
                            "bin_code": "PATH-BIN",
                            "bin_cell_index": 1,
                            "status": "OCCUPIED",
                        }
                    ],
                }
            },
        )
        result = SimpleNamespace(
            trace=SimpleNamespace(trace_id="trace-20"),
            session=session,
            sessions=[session],
            commands=[],
            inboxes=[],
            outboxes=[],
            dispatch_attempts=[SimpleNamespace(id=999)],
            timelines=[],
            callback_logs=[],
            diagnostics=[],
        )

        patch_build_detail = (
            patch.object(
                runtime_query_module,
                "build_trace_response",
                side_effect=AssertionError("Path 响应不应构建完整 TraceDetailResponse"),
            )
            if hasattr(runtime_query_module, "build_trace_response")
            else nullcontext()
        )
        with patch_build_detail:
            path = service._build_trace_path(result)

        payload = path.model_dump(mode="json")

        assert "evidence" not in RuntimeTracePathResponse.model_fields
        assert "evidence" not in payload
        assert path.diagnosis_verdict == diagnosis_verdict_builder.build(result)
        assert path.sessions[0].id == 20
        assert "active_bin_rack" not in path.sessions[0].context_json
        assert path.resource_view.active_bin_racks[0].rack_code == "PATH-RACK"

    @pytest.mark.asyncio
    async def test_get_trace_path_keeps_trace_id_fallback_facts_without_session_or_callback(self) -> None:
        from src.app.workline.services.runtime_query_service import RuntimeQueryService

        service = RuntimeQueryService()
        result = SimpleNamespace(
            trace=SimpleNamespace(trace_id="trace-command-only"),
            session=None,
            sessions=[],
            callback_logs=[],
            commands=[
                SimpleNamespace(
                    id=101,
                    device_id=301,
                    device_code="ARM01",
                    command_code="CMD-101",
                    task_type="MOVE",
                    status="SENT",
                    result=None,
                    completed_at=None,
                    sent_at=timezone.now_for_db(),
                )
            ],
            inboxes=[],
            outboxes=[],
            dispatch_attempts=[],
            timelines=[],
            diagnostics=[],
        )

        with patch(
            "src.app.workline.services.trace_query_service.trace_query_service.path_by_trace_id",
            new=AsyncMock(return_value=result),
        ):
            path = await service.get_trace_path(AsyncMock(), "trace-command-only")

        assert path is not None
        assert path.trace_id == "trace-command-only"
        assert path.devices[0].device_id == 301

    def test_build_trace_path_uses_canonical_device_identity_for_timeline_groups(self) -> None:
        from src.app.workline.services.runtime_query_service import RuntimeQueryService

        service = RuntimeQueryService()
        now = timezone.now_for_db()
        session = SimpleNamespace(
            id=20,
            workline_id=45,
            current_wait_type=None,
            awaiting_device_command_code=None,
            failure_domain=None,
            failure_message=None,
        )
        command = SimpleNamespace(
            id=101,
            device_id=39,
            command_code="CMD-101",
            task_type="MOVE",
            status="COMPLETED",
            result="SUCCESS",
            completed_at=now,
            sent_at=now,
        )
        device = SimpleNamespace(
            id=39,
            device_code="ARM03",
            device_name="右侧进料机械臂",
        )

        def timeline(**overrides):
            defaults = {
                "id": 1,
                "session_id": 20,
                "workline_id": 45,
                "trace_id": "trace-20",
                "seq_no": 1,
                "occurred_at": now,
                "stage": "INGEST",
                "action_type": "COMMAND_SENT",
                "actor_type": "DEVICE",
                "actor_code": "ARM03",
                "from_status": None,
                "to_status": None,
                "status": "SUCCESS",
                "failure_domain": None,
                "message": None,
                "payload_json": {},
                "related_inbox_id": None,
                "related_command_id": None,
            }
            defaults.update(overrides)
            return SimpleNamespace(**defaults)

        result = SimpleNamespace(
            trace=SimpleNamespace(trace_id="trace-20"),
            session=session,
            commands=[command],
            inboxes=[],
            outboxes=[],
            dispatch_attempts=[],
            timelines=[
                timeline(id=1, related_command_id=101),
                timeline(id=2, related_command_id=None, actor_code="ARM03"),
            ],
            sessions=[],
            callback_logs=[],
            diagnostics=[],
        )

        from src.app.workline.services import runtime_query_service as runtime_query_module

        patch_build_detail = (
            patch.object(runtime_query_module, "build_trace_response", return_value=None)
            if hasattr(runtime_query_module, "build_trace_response")
            else nullcontext()
        )
        with patch_build_detail:
            path = service._build_trace_path(result, devices=[device])

        groups = {group.group_key: group for group in path.timeline_groups}

        assert list(groups) == ["device:39"]
        assert groups["device:39"].display_name == "右侧进料机械臂"
        assert groups["device:39"].device_code == "ARM03"
        assert [event.id for event in groups["device:39"].events] == [1, 2]

    def test_trace_resource_view_builder_projects_flat_active_bin_rack_payload(self) -> None:
        from src.app.workline.services.trace_resource_view_builder import build_trace_resource_view

        result = SimpleNamespace(
            sessions=[
                SimpleNamespace(
                    context_json={
                        "active_bin_rack": {
                            "rack_code": "RACK-01",
                            "rack_id": "rack-id-ignored",
                            "rack_kind": "SINGLE_LAYER",
                            "rack_type": "ROUGH_SORTER",
                            "cells": [
                                {
                                    "rack_slot_code": "A01",
                                    "rack_slot_location_code": "LOC-A01",
                                    "bin_id": "bin-1",
                                    "bin_code": "BIN-01",
                                    "bin_type": "FULL",
                                    "bin_orientation_code": "N",
                                    "bin_cell_index": 1,
                                    "bin_cell_code": "",
                                    "status": "",
                                    "used_depth_mm": None,
                                },
                                {
                                    "rack_slot_code": "A01",
                                    "bin_code": "BIN-01",
                                    "bin_cell_index": 1,
                                    "bin_cell_code": "CELL-01",
                                    "status": "OCCUPIED",
                                    "capacity_depth_mm": 100,
                                    "used_depth_mm": 60,
                                    "material_identity_key": "MAT-01",
                                    "pkg_code": "PKG-01",
                                    "is_reserved": True,
                                },
                            ],
                        }
                    }
                )
            ],
            inboxes=[],
            outboxes=[],
            timelines=[],
        )

        view = build_trace_resource_view(result)

        assert len(view.active_bin_racks) == 1
        rack = view.active_bin_racks[0]
        assert rack.rack_code == "RACK-01"
        assert rack.rack_id == "rack-id-ignored"
        assert rack.rack_kind == "SINGLE_LAYER"
        assert len(rack.bins) == 1
        bin_view = rack.bins[0]
        assert bin_view.rack_slot_code == "A01"
        assert bin_view.rack_slot_location_code == "LOC-A01"
        assert bin_view.bin_code == "BIN-01"
        assert len(bin_view.cells) == 1
        cell = bin_view.cells[0]
        assert cell.bin_cell_index == 1
        assert cell.bin_cell_code == "CELL-01"
        assert cell.status == "OCCUPIED"
        assert cell.capacity_depth_mm == 100
        assert cell.used_depth_mm == 60
        assert cell.material_identity_key == "MAT-01"
        assert cell.pkg_code == "PKG-01"
        assert cell.is_reserved is True

    def test_trace_resource_view_builder_projects_nested_active_bin_rack_payload_and_skips_invalid_cells(
        self,
    ) -> None:
        from src.app.workline.services.trace_resource_view_builder import build_trace_resource_view

        result = SimpleNamespace(
            sessions=[],
            inboxes=[
                SimpleNamespace(
                    payload_json={
                        "active_bin_rack": {
                            "rack_id": "rack-02",
                            "bins": [
                                {
                                    "rack_slot_code": "B01",
                                    "rack_slot_location_code": "LOC-B01",
                                    "bin_code": "BIN-02",
                                    "bin_type": "EMPTY",
                                    "cells": [
                                        {
                                            "bin_cell_index": 1,
                                            "bin_cell_code": "CELL-02",
                                            "bin_cell_location": "L1",
                                            "status": "EMPTY",
                                        },
                                        {
                                            "bin_cell_code": "MISSING-INDEX",
                                            "status": "SHOULD_SKIP",
                                        },
                                    ],
                                }
                            ],
                        }
                    }
                )
            ],
            outboxes=[
                SimpleNamespace(
                    payload_json={
                        "active_bin_rack": {
                            "rack_code": "",
                            "rack_id": "",
                            "cells": [
                                {
                                    "rack_slot_code": "DROP",
                                    "bin_code": "DROP",
                                    "bin_cell_index": 1,
                                }
                            ],
                        }
                    }
                )
            ],
            timelines=[],
        )

        view = build_trace_resource_view(result)

        assert len(view.active_bin_racks) == 1
        rack = view.active_bin_racks[0]
        assert rack.rack_id == "rack-02"
        assert rack.rack_code is None
        assert len(rack.bins) == 1
        assert rack.bins[0].rack_slot_code == "B01"
        assert rack.bins[0].bin_code == "BIN-02"
        assert len(rack.bins[0].cells) == 1
        assert rack.bins[0].cells[0].bin_cell_code == "CELL-02"
        assert rack.bins[0].cells[0].bin_cell_location == "L1"

    def test_trace_resource_view_builder_keeps_flat_cell_with_bin_code_without_slot(self) -> None:
        from src.app.workline.services.trace_resource_view_builder import build_trace_resource_view

        result = SimpleNamespace(
            sessions=[
                SimpleNamespace(
                    context_json={
                        "active_bin_rack": {
                            "rack_id": "rack-03",
                            "cells": [
                                {
                                    "bin_code": "BIN-03",
                                    "bin_cell_index": 1,
                                    "status": "OCCUPIED",
                                }
                            ],
                        }
                    }
                )
            ],
            inboxes=[],
            outboxes=[],
            timelines=[],
        )

        view = build_trace_resource_view(result)

        assert len(view.active_bin_racks) == 1
        rack = view.active_bin_racks[0]
        assert len(rack.bins) == 1
        assert rack.bins[0].rack_slot_code is None
        assert rack.bins[0].bin_code == "BIN-03"
        assert len(rack.bins[0].cells) == 1
        assert rack.bins[0].cells[0].bin_cell_index == 1
        assert rack.bins[0].cells[0].status == "OCCUPIED"

    def test_trace_resource_view_builder_merges_later_slot_payload_into_existing_bin(self) -> None:
        from src.app.workline.services.trace_resource_view_builder import build_trace_resource_view

        result = SimpleNamespace(
            sessions=[
                SimpleNamespace(
                    context_json={
                        "active_bin_rack": {
                            "rack_id": "rack-04",
                            "cells": [
                                {
                                    "bin_code": "BIN-04",
                                    "bin_cell_index": 1,
                                    "status": "RESERVED",
                                }
                            ],
                        }
                    }
                )
            ],
            inboxes=[
                SimpleNamespace(
                    payload_json={
                        "active_bin_rack": {
                            "rack_id": "rack-04",
                            "cells": [
                                {
                                    "rack_slot_code": "D01",
                                    "rack_slot_location_code": "LOC-D01",
                                    "bin_code": "BIN-04",
                                    "bin_cell_index": 1,
                                    "status": "OCCUPIED",
                                    "pkg_code": "PKG-04",
                                }
                            ],
                        }
                    }
                )
            ],
            outboxes=[],
            timelines=[],
        )

        view = build_trace_resource_view(result)

        assert len(view.active_bin_racks) == 1
        rack = view.active_bin_racks[0]
        assert len(rack.bins) == 1
        bin_view = rack.bins[0]
        assert bin_view.rack_slot_code == "D01"
        assert bin_view.rack_slot_location_code == "LOC-D01"
        assert bin_view.bin_code == "BIN-04"
        assert len(bin_view.cells) == 1
        cell = bin_view.cells[0]
        assert cell.bin_cell_index == 1
        assert cell.status == "OCCUPIED"
        assert cell.pkg_code == "PKG-04"

    def test_trace_resource_view_builder_merges_payloads_by_history_order(self) -> None:
        from src.app.workline.services.trace_resource_view_builder import build_trace_resource_view

        result = SimpleNamespace(
            sessions=[
                SimpleNamespace(
                    updated_at="2026-06-03T01:05:00Z",
                    context_json={
                        "active_bin_rack": {
                            "rack_id": "rack-05",
                            "cells": [
                                {
                                    "rack_slot_code": "E01",
                                    "bin_code": "BIN-05",
                                    "bin_cell_index": 1,
                                    "status": "LATEST",
                                }
                            ],
                        }
                    },
                )
            ],
            inboxes=[
                SimpleNamespace(
                    received_at="2026-06-03T01:01:00Z",
                    payload_json={
                        "active_bin_rack": {
                            "rack_id": "rack-05",
                            "cells": [
                                {
                                    "rack_slot_code": "E01",
                                    "bin_code": "BIN-05",
                                    "bin_cell_index": 1,
                                    "status": "OLDER",
                                }
                            ],
                        }
                    },
                )
            ],
            outboxes=[],
            timelines=[
                SimpleNamespace(
                    seq_no=1,
                    occurred_at="2026-06-03T01:00:00Z",
                    payload_json={
                        "active_bin_rack": {
                            "rack_id": "rack-05",
                            "cells": [
                                {
                                    "rack_slot_code": "E01",
                                    "bin_code": "BIN-05",
                                    "bin_cell_index": 1,
                                    "status": "OLDEST",
                                }
                            ],
                        }
                    },
                )
            ],
        )

        view = build_trace_resource_view(result)

        assert view.active_bin_racks[0].bins[0].cells[0].status == "LATEST"

    def test_trace_resource_view_builder_uses_timestamp_before_timeline_sequence(self) -> None:
        from src.app.workline.services.trace_resource_view_builder import build_trace_resource_view

        result = SimpleNamespace(
            sessions=[],
            inboxes=[
                SimpleNamespace(
                    received_at="2026-06-03T01:05:00Z",
                    payload_json={
                        "active_bin_rack": {
                            "rack_id": "rack-ordered",
                            "cells": [
                                {
                                    "rack_slot_code": "F01",
                                    "bin_code": "BIN-ORDER",
                                    "bin_cell_index": 1,
                                    "status": "INBOX",
                                }
                            ],
                        }
                    },
                )
            ],
            outboxes=[],
            timelines=[
                SimpleNamespace(
                    seq_no=1,
                    occurred_at="2026-06-03T01:10:00Z",
                    payload_json={
                        "active_bin_rack": {
                            "rack_id": "rack-ordered",
                            "cells": [
                                {
                                    "rack_slot_code": "F01",
                                    "bin_code": "BIN-ORDER",
                                    "bin_cell_index": 1,
                                    "status": "TIMELINE_LATEST",
                                }
                            ],
                        }
                    },
                )
            ],
        )

        view = build_trace_resource_view(result)

        assert view.active_bin_racks[0].bins[0].cells[0].status == "TIMELINE_LATEST"

    def test_trace_resource_view_builder_treats_naive_iso_timestamp_as_utc(self) -> None:
        from src.app.workline.services.trace_resource_view_builder import build_trace_resource_view

        result = SimpleNamespace(
            sessions=[],
            inboxes=[
                SimpleNamespace(
                    received_at="2026-06-03T01:05:00Z",
                    payload_json={
                        "active_bin_rack": {
                            "rack_id": "rack-utc",
                            "cells": [
                                {
                                    "rack_slot_code": "F01",
                                    "bin_code": "BIN-UTC",
                                    "bin_cell_index": 1,
                                    "status": "AWARE",
                                }
                            ],
                        }
                    },
                )
            ],
            outboxes=[
                SimpleNamespace(
                    created_at="2026-06-03T01:06:00",
                    payload_json={
                        "active_bin_rack": {
                            "rack_id": "rack-utc",
                            "cells": [
                                {
                                    "rack_slot_code": "F01",
                                    "bin_code": "BIN-UTC",
                                    "bin_cell_index": 1,
                                    "status": "NAIVE_UTC",
                                }
                            ],
                        }
                    },
                )
            ],
            timelines=[],
        )

        view = build_trace_resource_view(result)

        assert view.active_bin_racks[0].bins[0].cells[0].status == "NAIVE_UTC"

    def test_trace_resource_view_builder_ignores_whitespace_keys(self) -> None:
        from src.app.workline.services.trace_resource_view_builder import build_trace_resource_view

        result = SimpleNamespace(
            sessions=[
                SimpleNamespace(
                    context_json={
                        "active_bin_rack": {
                            "rack_code": "   ",
                            "rack_id": "\t",
                            "cells": [
                                {
                                    "rack_slot_code": " A01 ",
                                    "bin_code": " BIN-06 ",
                                    "bin_cell_index": " 1 ",
                                    "status": "OCCUPIED",
                                }
                            ],
                        }
                    }
                ),
                SimpleNamespace(
                    context_json={
                        "active_bin_rack": {
                            "rack_id": " rack-06 ",
                            "cells": [
                                {
                                    "rack_slot_code": " A01 ",
                                    "bin_code": " BIN-06 ",
                                    "bin_cell_index": " 1 ",
                                    "status": "OCCUPIED",
                                }
                            ],
                        }
                    }
                ),
            ],
            inboxes=[],
            outboxes=[],
            timelines=[],
        )

        view = build_trace_resource_view(result)

        assert len(view.active_bin_racks) == 1
        rack = view.active_bin_racks[0]
        assert rack.rack_id == "rack-06"
        assert rack.bins[0].rack_slot_code == "A01"
        assert rack.bins[0].bin_code == "BIN-06"
        assert rack.bins[0].cells[0].bin_cell_index == "1"

    def test_trace_resource_view_builder_does_not_call_active_snapshot_service(self) -> None:
        from src.app.workline.services.trace_resource_view_builder import build_trace_resource_view

        result = SimpleNamespace(sessions=[], inboxes=[], outboxes=[], timelines=[])

        with patch(
            "src.app.resource.services.active_rack_snapshot_service.smt_active_rack_snapshot_service",
            side_effect=AssertionError("resource view 必须只投影历史 payload"),
        ):
            assert build_trace_resource_view(result).active_bin_racks == []
