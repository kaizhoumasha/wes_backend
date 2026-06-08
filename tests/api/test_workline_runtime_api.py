from collections.abc import Mapping
from contextlib import nullcontext
from datetime import timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, patch

import pytest

from src.app.workline.v1 import runtime as runtime_module
from src.app.workline.v1 import trace as trace_module
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from src.app.callback.models import CallbackLog
    from src.app.device.models import Device, DeviceCommand
    from src.app.workline.models import WorkLine, WorklineInbox, WorklineSession


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


def _response_data(response: object) -> Any:
    if isinstance(response, Mapping):
        return cast("Mapping[str, Any]", response)["data"]
    return cast("Any", response).data


def _response_message(response: object) -> str:
    if isinstance(response, Mapping):
        message = cast("Mapping[str, Any]", response)["message"]
    else:
        message = cast("Any", response).message
    assert isinstance(message, str)
    return message


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
        assert _permission_names(trace_module, "/exchange/{exchange_request_code}", "GET") == ["biz:workline:list"]
        assert _permission_names(trace_module, "/query", "POST") == ["biz:workline:list"]

    def test_runtime_routes_require_expected_permissions(self) -> None:
        assert _permission_names(runtime_module, "/overview", "GET") == ["biz:workline:list"]
        assert _permission_names(runtime_module, "/worklines", "GET") == ["biz:workline:list"]
        assert _permission_names(runtime_module, "/worklines/{workline_id}", "GET") == ["biz:workline:list"]
        assert _permission_names(runtime_module, "/devices", "GET") == ["biz:device:list"]
        assert _permission_names(runtime_module, "/devices/{device_id}", "GET") == ["biz:device:list"]


class TestWorklineIntegrationDebugApi:
    def test_integration_debug_routes_require_workline_list_permission(self) -> None:
        from src.app.workline.v1 import integration_debug as integration_debug_module

        assert _permission_names(integration_debug_module, "/cases/latest", "GET") == ["biz:workline:list"]
        assert _permission_names(integration_debug_module, "/cases/lookup", "GET") == ["biz:workline:list"]

    @pytest.mark.asyncio
    async def test_lookup_case_uses_integration_debug_service(self) -> None:
        from src.app.workline.models.integration_debug import IntegrationDebugCaseResponse
        from src.app.workline.v1.integration_debug import lookup_integration_debug_case

        result = IntegrationDebugCaseResponse(
            case_id="session:11",
            status="MANUAL_HOLD",
            phase="external_wms",
            verdict="blocked",
            blocking_domain="INTEGRATION",
            blocking_code="WMS_TIMEOUT",
            owner="integration",
            severity="error",
            recoverability="manual_intervention_required",
            summary="设备链路已完成，当前阻塞在 WMS 库存同步超时",
            facts={"session_id": 11},
            stage_checks=[],
            evidence_links=[],
            next_actions=[],
        )

        with patch(
            "src.app.workline.v1.integration_debug.integration_debug_service.lookup_case",
            new=AsyncMock(return_value=result),
        ) as mock_lookup:
            response = await lookup_integration_debug_case(
                anchor_type="session_id",
                anchor="11",
                db=AsyncMock(),
                include_raw=False,
            )

        mock_lookup.assert_awaited_once_with(
            AnyArgHashable(),
            anchor_type="session_id",
            anchor="11",
            include_raw=False,
        )
        assert _response_data(response).blocking_code == "WMS_TIMEOUT"

    @pytest.mark.asyncio
    async def test_latest_cases_uses_integration_debug_service(self) -> None:
        from src.app.workline.models.integration_debug import IntegrationDebugCaseListResponse
        from src.app.workline.v1.integration_debug import get_latest_integration_debug_cases

        result = IntegrationDebugCaseListResponse(total=0, items=[])

        with patch(
            "src.app.workline.v1.integration_debug.integration_debug_service.latest_cases",
            new=AsyncMock(return_value=result),
        ) as mock_latest:
            response = await get_latest_integration_debug_cases(
                db=AsyncMock(),
                limit=10,
                workline_id=None,
                device_id=None,
                status=None,
            )

        mock_latest.assert_awaited_once_with(
            AnyArgHashable(),
            limit=10,
            workline_id=None,
            device_id=None,
            status=None,
        )
        assert _response_data(response).total == 0


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
        assert _response_data(response).trace.request_id == "req-001"
        assert _response_data(response).summary.callback_logs == 0

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
        assert _response_data(response).total == 1

    @pytest.mark.asyncio
    async def test_get_trace_by_exchange_request_code_uses_trace_query_service(self) -> None:
        from src.app.workline.v1.trace import get_trace_by_exchange_request_code

        trace_result = SimpleNamespace(
            trace=_TraceContextStub(
                trace_id="trace-001",
                dispatch_key="external:smt:release-001:FULL_BIN_EXCHANGE",
            ),
            callback_logs=[],
            inboxes=[],
            session=None,
            sessions=[],
            commands=[],
            outboxes=[],
            dispatch_attempts=[],
            timelines=[],
            diagnostics=[],
            resource_state_events=[],
            rack_releases=[],
            rack_release_bin_snapshots=[],
            wms_writeback_evidence=[],
            rack_bin_mounts=[],
        )

        with patch(
            "src.app.workline.v1.trace.trace_query_service.by_exchange_request_code",
            new=AsyncMock(return_value=trace_result),
        ) as mock_by_exchange_request_code:
            response = await get_trace_by_exchange_request_code(
                "external:smt:release-001:FULL_BIN_EXCHANGE",
                db=AsyncMock(),
            )

        mock_by_exchange_request_code.assert_awaited_once_with(
            AnyArgHashable(),
            "external:smt:release-001:FULL_BIN_EXCHANGE",
        )
        assert _response_data(response).trace.dispatch_key == "external:smt:release-001:FULL_BIN_EXCHANGE"


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
        assert _response_data(response).stats == []

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
        assert _response_data(response) == []

    @pytest.mark.asyncio
    async def test_get_runtime_workline_detail_returns_not_found_when_missing(self) -> None:
        from src.app.workline.v1.runtime import get_runtime_workline_detail

        with patch(
            "src.app.workline.v1.runtime.runtime_query_service.get_workline_detail",
            new=AsyncMock(return_value=None),
        ) as mock_get_workline_detail:
            response = await get_runtime_workline_detail(workline_id=404, db=AsyncMock())

        mock_get_workline_detail.assert_awaited_once_with(AnyArgHashable(), 404)
        assert _response_message(response) == "工作线运行态不存在: 404"

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
        assert _response_data(response).summary.workline_id == 45

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
        assert _response_message(response) == "工作线设备运行态不存在: worklineId=45, deviceId=404"


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
        assert {"resource_code", "display_label", "source_session_id", "source_trace_id", "occurred_at"}.issubset(
            item_properties
        )

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
                        "PkgID": "PKG-LATEST",
                        "reels": [
                            {
                                "pkg_code": "PKG-LATEST",
                                "cell_stack_position": 2,
                            },
                            {
                                "pkg_code": "PKG-OLDER",
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
        from src.app.workline.models.runtime import RuntimeTraceListItem
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
            for index in range(1, 26)
        ]
        db = AsyncMock()
        db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: workline)

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
        assert result.summary.waiting_session_count == 25
        assert len(result.active_sessions) == 20
        assert result.resource_evidence_total_count == 25
        assert result.resource_evidence_truncated is False
        assert {item.resource_code for item in result.resource_evidence_items} >= {"PKG-001", "PKG-025"}

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
            awaiting_command_id=None,
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
        assert mock_station_lease.await_args_list[0].kwargs["position_code"] == "SOURCE_STATION_A"
        assert mock_station_lease.await_args_list[1].kwargs["position_code"] == "SOURCE_STATION_B"
        assert mock_station_lease.await_args_list[2].kwargs["position_code"] == "TARGET_STATION"
        assert mock_snapshot.await_args_list[0].kwargs["context"] == {"station": {"position_code": "SOURCE_STATION_A"}}
        assert mock_snapshot.await_args_list[1].kwargs["context"] == {"station": {"position_code": "SOURCE_STATION_B"}}
        assert mock_snapshot.await_args_list[2].kwargs["context"] == {"station": {"position_code": "TARGET_STATION"}}

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
        db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: workline)
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
                awaiting_command_id=None,
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
