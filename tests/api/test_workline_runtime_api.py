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
        assert _permission_names(trace_module, "/correlation/{correlation_id}", "GET") == ["biz:workline:list"]
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
            trace=_TraceContextStub(request_id="req-001", correlation_id="corr-001"),
            callback_logs=[],
            inboxes=[],
            session=None,
            commands=[],
            outboxes=[],
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
        device_id="ARM01",
        request_id="req-001",
        correlation_id="corr-001",
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
            response = await get_runtime_overview(db=AsyncMock())

        mock_get_overview.assert_awaited_once_with(AnyArgHashable())
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
            correlation_id=None,
            last_request_id=None,
            workline_id=5,
            status="RUNNING",
            step_code=None,
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
            correlation_id=None,
            last_request_id=None,
            workline_id=5,
            status="RUNNING",
            step_code=None,
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

        mock_failed_count.assert_awaited_once_with(AnyArgHashable())
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
            owner_team=None,
            support_contact=None,
            is_active=True,
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
            owner_team=None,
            support_contact=None,
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
            device_id="ARM-01",
            request_id=None,
            correlation_id=None,
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
            correlation_id=None,
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
            step_code=None,
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
            correlation_id=None,
            last_request_id=None,
            workline_id=8,
            status="RUNNING",
            step_code=None,
            current_wait_type=None,
            failure_domain=None,
            failure_code=None,
            started_at=None,
            last_ingress_at=None,
            deadline_at=None,
        )

        with pytest.raises(ValueError, match=r"session\.id"):
            service._build_trace_list_item(session, None, None, None, None, timezone.now_for_db())
