from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.app.workline.v1 import runtime as runtime_module
from src.app.workline.v1 import trace as trace_module
from src.utils.timezone import timezone


class _TraceContextStub:
    def __init__(self, **payload):
        self._payload = payload

    def as_dict(self):
        return self._payload


class AnyArgHashable:
    def __eq__(self, other):
        return True

    def __hash__(self):
        return 0


def _get_route(module, path: str, method: str):
    for route in module.router.routes:
        if method in route.methods and route.path == path:
            return route
    raise AssertionError(f"{method} {path} route not found")


def _permission_names(module, path: str, method: str) -> list[str]:
    route = _get_route(module, path, method)
    return [getattr(dep.dependency, "permission_required", "") for dep in route.dependencies]


class TestWorklineRuntimeRoutePermissions:
    def test_trace_routes_require_workline_list_permission(self) -> None:
        assert _permission_names(trace_module, "/request/{request_id}", "GET") == ["biz:workline:list"]
        assert _permission_names(trace_module, "/trace/{trace_id}", "GET") == ["biz:workline:list"]
        assert _permission_names(trace_module, "/session/{session_id}", "GET") == ["biz:workline:list"]
        assert _permission_names(trace_module, "/command/{command_code}", "GET") == ["biz:workline:list"]
        assert _permission_names(trace_module, "/dispatch/{dispatch_key}", "GET") == ["biz:workline:list"]
        assert _permission_names(trace_module, "/query", "POST") == ["biz:workline:list"]

    def test_runtime_routes_require_expected_permissions(self) -> None:
        assert _permission_names(runtime_module, "/overview", "GET") == ["biz:workline:list"]
        assert _permission_names(runtime_module, "/worklines", "GET") == ["biz:workline:list"]
        assert _permission_names(runtime_module, "/worklines/{workline_id}", "GET") == ["biz:workline:list"]
        assert _permission_names(runtime_module, "/devices", "GET") == ["biz:device:list"]
        assert _permission_names(runtime_module, "/devices/{device_id}", "GET") == ["biz:device:list"]


class TestWorklineTraceApi:
    @pytest.mark.asyncio
    async def test_get_trace_by_request_id_uses_trace_query_service(self) -> None:
        from src.app.workline.v1.trace import get_trace_by_request_id

        trace_result = SimpleNamespace(
            trace=_TraceContextStub(request_id="req-001", trace_id="trace-001"),
            callback_logs=[],
            inboxes=[],
            session=None,
            sessions=[],
            commands=[],
            outboxes=[],
            dispatch_attempts=[],
            timelines=[],
            diagnostics=[],
        )

        with patch(
            "src.app.workline.v1.trace.trace_query_service.by_request_id",
            new=AsyncMock(return_value=trace_result),
        ) as mock_by_request_id:
            response = await get_trace_by_request_id("req-001", db=AsyncMock())

        mock_by_request_id.assert_awaited_once_with(AnyArgHashable(), "req-001")
        assert response["data"].trace.request_id == "req-001"
        assert response["data"].summary.callback_logs == 0

    @pytest.mark.asyncio
    async def test_query_trace_list_uses_runtime_query_service(self) -> None:
        from src.app.workline.models.runtime import RuntimeTraceListResponse, TraceQueryRequest
        from src.app.workline.v1.trace import query_trace_list

        payload = TraceQueryRequest(only_active=True, limit=10, offset=0)
        result = RuntimeTraceListResponse(total=1, items=[])

        with patch(
            "src.app.workline.v1.trace.runtime_query_service.get_trace_list",
            new=AsyncMock(return_value=result),
        ) as mock_get_trace_list:
            response = await query_trace_list(payload=payload, db=AsyncMock())

        mock_get_trace_list.assert_awaited_once_with(AnyArgHashable(), payload)
        assert response["data"].total == 1


def test_trace_callback_log_item_allows_null_updated_at() -> None:
    from datetime import datetime

    from src.app.workline.models.runtime import TraceCallbackLogItem

    item = TraceCallbackLogItem(
        id=1,
        callback_type="event",
        subject_code="ARM01",
        request_id="req-001",
        trace_id="trace-001",
        response_status=200,
        response_time_ms=12,
        error_message=None,
        ingress_outcome="ACCEPTED",
        failure_stage=None,
        request_body={"foo": "bar"},
        created_at=datetime.now(),
        updated_at=None,
    )

    assert item.updated_at is None


class TestWorklineRuntimeApi:
    @pytest.mark.asyncio
    async def test_get_runtime_overview_uses_runtime_query_service(self) -> None:
        from src.app.workline.models.runtime import RuntimeOverviewResponse
        from src.app.workline.v1.runtime import get_runtime_overview

        result = RuntimeOverviewResponse(stats=[], recent_failed_traces=[], hot_worklines=[], abnormal_devices=[])

        with patch(
            "src.app.workline.v1.runtime.runtime_query_service.get_overview",
            new=AsyncMock(return_value=result),
        ) as mock_get_overview:
            response = await get_runtime_overview(db=AsyncMock(), include_sim=False)

        mock_get_overview.assert_awaited_once_with(AnyArgHashable(), include_sim=False)
        assert response["data"].stats == []

    @pytest.mark.asyncio
    async def test_get_runtime_devices_uses_workline_scoped_service(self) -> None:
        from src.app.workline.v1.runtime import get_runtime_devices

        with (
            patch(
                "src.app.workline.v1.runtime.runtime_query_service.list_workline_devices",
                new=AsyncMock(return_value=[]),
                create=True,
            ) as mock_list_devices,
            patch(
                "src.app.workline.v1.runtime.runtime_query_service.list_devices",
                new=AsyncMock(side_effect=AssertionError("global device query should not be used by the API")),
            ),
        ):
            response = await get_runtime_devices(db=AsyncMock(), workline_id=45)

        mock_list_devices.assert_awaited_once_with(AnyArgHashable(), 45)
        assert response["data"] == []

    @pytest.mark.asyncio
    async def test_get_runtime_workline_detail_returns_not_found_when_missing(self) -> None:
        from src.app.workline.v1.runtime import get_runtime_workline_detail

        with patch(
            "src.app.workline.v1.runtime.runtime_query_service.get_workline_detail",
            new=AsyncMock(return_value=None),
        ) as mock_get_workline_detail:
            response = await get_runtime_workline_detail(workline_id=404, db=AsyncMock())

        mock_get_workline_detail.assert_awaited_once_with(AnyArgHashable(), 404)
        assert response["message"] == "工作线运行态不存在: 404"

    @pytest.mark.asyncio
    async def test_get_runtime_device_detail_uses_workline_scoped_service(self) -> None:
        from src.app.workline.models.runtime import RuntimeDeviceDetailResponse, RuntimeDeviceSummary
        from src.app.workline.v1.runtime import get_runtime_device_detail

        result = RuntimeDeviceDetailResponse(
            summary=RuntimeDeviceSummary(
                id=39,
                device_code="ARM03",
                device_name="右侧进料机械臂",
                device_role="INPUT_ARM",
                role_index=1,
                workline_id=45,
                workline_name="右侧 SMT 粗分线",
                workline_code="WL-CONVEYOR-02",
                device_status="IDLE",
                maintenance_mode=False,
                pending_command_count=0,
            ),
            recent_commands=[],
            recent_callbacks=[],
            active_sessions=[],
        )

        with (
            patch(
                "src.app.workline.v1.runtime.runtime_query_service.get_workline_device_detail",
                new=AsyncMock(return_value=result),
                create=True,
            ) as mock_get_device_detail,
            patch(
                "src.app.workline.v1.runtime.runtime_query_service.get_device_detail",
                new=AsyncMock(side_effect=AssertionError("unscoped device detail query should not be used by the API")),
            ),
        ):
            response = await get_runtime_device_detail(device_id=39, db=AsyncMock(), workline_id=45)

        mock_get_device_detail.assert_awaited_once_with(AnyArgHashable(), 45, 39)
        assert response["data"].summary.workline_id == 45

    @pytest.mark.asyncio
    async def test_get_runtime_device_detail_returns_not_found_when_missing(self) -> None:
        from src.app.workline.v1.runtime import get_runtime_device_detail

        with (
            patch(
                "src.app.workline.v1.runtime.runtime_query_service.get_workline_device_detail",
                new=AsyncMock(return_value=None),
                create=True,
            ) as mock_get_device_detail,
            patch(
                "src.app.workline.v1.runtime.runtime_query_service.get_device_detail",
                new=AsyncMock(side_effect=AssertionError("unscoped device detail query should not be used by the API")),
            ),
        ):
            response = await get_runtime_device_detail(device_id=404, db=AsyncMock(), workline_id=45)

        mock_get_device_detail.assert_awaited_once_with(AnyArgHashable(), 45, 404)
        assert response["message"] == "工作线设备运行态不存在: worklineId=45, deviceId=404"


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
            plugin_state=None,
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
            plugin_state=None,
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
        session = SimpleNamespace(
            id=11,
            session_code="S11",
            trace_id="trace-11",
            last_request_id=None,
            business_key="stable-key-11",
            barcode=None,
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
            plugin_state=None,
            current_wait_type=None,
            failure_domain=None,
            failure_code=None,
            started_at=None,
            last_ingress_at=None,
            deadline_at=None,
        )
        workline = SimpleNamespace(line_name="SMT 线", line_code="WL-5")

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
        workline = SimpleNamespace(
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
        )
        timed_out_session = SimpleNamespace(
            status="WAITING_EXTERNAL",
            deadline_at=now - timedelta(minutes=5),
            last_ingress_at=None,
            waiting_since=now - timedelta(minutes=10),
            started_at=None,
            created_at=now - timedelta(minutes=20),
        )

        summary = service._build_workline_summary(workline, [], [timed_out_session])

        assert summary.waiting_session_count == 0
        assert summary.failed_session_count == 1

    def test_build_workline_summary_separates_active_and_waiting_sessions(self) -> None:
        from src.app.workline.services.runtime_query_service import RuntimeQueryService

        now = timezone.now_for_db()
        service = RuntimeQueryService()
        workline = SimpleNamespace(
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
        )
        running_session = SimpleNamespace(
            status="RUNNING",
            deadline_at=None,
            last_ingress_at=now - timedelta(minutes=1),
            waiting_since=None,
            started_at=now - timedelta(minutes=5),
            created_at=now - timedelta(minutes=6),
        )
        waiting_session = SimpleNamespace(
            status="WAITING_EXTERNAL",
            deadline_at=now + timedelta(minutes=10),
            last_ingress_at=None,
            waiting_since=now - timedelta(minutes=2),
            started_at=now - timedelta(minutes=8),
            created_at=now - timedelta(minutes=9),
        )

        summary = service._build_workline_summary(workline, [], [running_session, waiting_session])

        assert summary.active_session_count == 1
        assert summary.waiting_session_count == 1
        assert summary.failed_session_count == 0

    def test_build_workline_summary_exposes_safety_projection(self) -> None:
        from src.app.workline.models.safety import WorkLineRuntimeStatus
        from src.app.workline.services.runtime_query_service import RuntimeQueryService

        stopped_at = timezone.now_for_db()
        service = RuntimeQueryService()
        workline = SimpleNamespace(
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
        )

        summary = service._build_workline_summary(workline, [], [])

        assert summary.runtime_status == WorkLineRuntimeStatus.ESTOPPED.value
        assert summary.active_safety_incident_id == 1001
        assert summary.stopped_at == stopped_at
        assert summary.stopped_reason == "ESTOP_PRESSED"
        assert summary.resumed_at is None

    def test_build_workline_summary_requires_persisted_workline(self) -> None:
        from src.app.workline.services.runtime_query_service import RuntimeQueryService

        service = RuntimeQueryService()
        workline = SimpleNamespace(
            id=None,
            line_code="WL-05",
            line_name="SMT 线",
            line_type="SMT",
            zone_name=None,
            plugin_key=None,
            contract_version=None,
            is_active=True,
        )

        with pytest.raises(ValueError, match=r"workline\.id"):
            service._build_workline_summary(workline, [], [])

    def test_build_workline_device_item_requires_persisted_device(self) -> None:
        from src.app.workline.services.runtime_query_service import RuntimeQueryService

        service = RuntimeQueryService()
        device = SimpleNamespace(
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
        )

        with pytest.raises(ValueError, match=r"device\.id"):
            service._build_workline_device_item(device)

    def test_build_device_summary_requires_persisted_device(self) -> None:
        from src.app.workline.services.runtime_query_service import RuntimeQueryService

        service = RuntimeQueryService()
        device = SimpleNamespace(
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
        )

        with pytest.raises(ValueError, match=r"device\.id"):
            service._build_device_summary(device, None, 0, None)

    def test_build_callback_item_requires_persisted_callback_log(self) -> None:
        from src.app.workline.services.runtime_query_service import RuntimeQueryService

        service = RuntimeQueryService()
        callback_log = SimpleNamespace(
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
        )

        with pytest.raises(ValueError, match=r"callback_log\.id"):
            service._build_callback_item(callback_log)

    def test_build_command_item_requires_persisted_command(self) -> None:
        from src.app.workline.services.runtime_query_service import RuntimeQueryService

        service = RuntimeQueryService()
        command = SimpleNamespace(
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
            issued_plugin_state=None,
            params={},
            result_data=None,
            error_detail=None,
            get_duration_ms=lambda: None,
        )

        with pytest.raises(ValueError, match=r"device_command\.id"):
            service._build_command_item(command)

    def test_build_trace_list_item_requires_persisted_session(self) -> None:
        from src.app.workline.services.runtime_query_service import RuntimeQueryService

        service = RuntimeQueryService()
        session = SimpleNamespace(
            id=None,
            session_code="S-01",
            trace_id=None,
            last_request_id=None,
            workline_id=8,
            status="RUNNING",
            plugin_state=None,
            current_wait_type=None,
            failure_domain=None,
            failure_code=None,
            started_at=None,
            last_ingress_at=None,
            deadline_at=None,
        )

        with pytest.raises(ValueError, match=r"session\.id"):
            service._build_trace_list_item(
                session,
                None,
                None,
                None,
                None,
                timezone.now_for_db(),
                latest_device=None,
                action_source="NONE",
            )

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
                    plugin_key="smt_classifier",
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
        assert response.diagnostics[0].plugin_key == "smt_classifier"
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
            awaiting_command_id=None,
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

        with patch("src.app.workline.services.runtime_query_service.build_trace_response", return_value=None):
            path = service._build_trace_path(result)

        groups = {group.group_key: group for group in path.timeline_groups}

        assert [event.id for event in groups["operator:sandbox"].events] == [1]
        assert [event.id for event in groups["orchestrator:session"].events] == [2]
        assert [event.id for event in groups["device:301"].events] == [3]
        assert [event.id for event in groups["device:302"].events] == [4]
        assert [event.id for event in groups["external:erp"].events] == [5]

    def test_build_trace_path_uses_canonical_device_identity_for_timeline_groups(self) -> None:
        from src.app.workline.services.runtime_query_service import RuntimeQueryService

        service = RuntimeQueryService()
        now = timezone.now_for_db()
        session = SimpleNamespace(
            id=20,
            workline_id=45,
            current_wait_type=None,
            awaiting_command_id=None,
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

        with patch("src.app.workline.services.runtime_query_service.build_trace_response", return_value=None):
            path = service._build_trace_path(result, devices=[device])

        groups = {group.group_key: group for group in path.timeline_groups}

        assert list(groups) == ["device:39"]
        assert groups["device:39"].display_name == "右侧进料机械臂"
        assert groups["device:39"].device_code == "ARM03"
        assert [event.id for event in groups["device:39"].events] == [1, 2]

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
        assert "session_id_int" in str(executed_query)

    @pytest.mark.asyncio
    async def test_load_latest_command_by_session_uses_window_query(self) -> None:
        from src.app.workline.services.runtime_query_service import RuntimeQueryService

        service = RuntimeQueryService()
        db = AsyncMock()
        latest_command = SimpleNamespace(id=2, session_id="SES-11", session_id_int=11)
        db.execute.return_value = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [latest_command]))

        result = await service._load_latest_command_by_session(db, [11])

        executed_query = db.execute.await_args.args[0]
        assert "row_number" in str(executed_query).lower()
        assert result == {11: latest_command}
