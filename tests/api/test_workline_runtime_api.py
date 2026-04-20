from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.app.workline.v1 import runtime as runtime_module
from src.app.workline.v1 import trace as trace_module


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
    async def test_get_runtime_devices_passes_optional_workline_filter(self) -> None:
        from src.app.workline.v1.runtime import get_runtime_devices

        with patch(
            "src.app.workline.v1.runtime.runtime_query_service.list_devices",
            new=AsyncMock(return_value=[]),
        ) as mock_list_devices:
            response = await get_runtime_devices(db=AsyncMock(), workline_id=45)

        mock_list_devices.assert_awaited_once_with(AnyArgHashable(), workline_id=45)
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
    async def test_get_runtime_device_detail_returns_not_found_when_missing(self) -> None:
        from src.app.workline.v1.runtime import get_runtime_device_detail

        with patch(
            "src.app.workline.v1.runtime.runtime_query_service.get_device_detail",
            new=AsyncMock(return_value=None),
        ) as mock_get_device_detail:
            response = await get_runtime_device_detail(device_id=404, db=AsyncMock())

        mock_get_device_detail.assert_awaited_once_with(AnyArgHashable(), 404)
        assert response["message"] == "设备运行态不存在: 404"
