from collections.abc import Mapping
from contextlib import nullcontext
from datetime import timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock, call, patch

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


def _has_route(module, path: str, method: str) -> bool:
    return any(method in route.methods and route.path == path for route in module.router.routes)


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
        assert not _has_route(runtime_module, "/worklines/{workline_id}/monitor-projection", "GET")
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

        with (
            patch(
                "src.app.workline.v1.runtime.runtime_query_service.get_workline_monitor_projection",
                new=AsyncMock(return_value=None),
            ) as mock_get_monitor_projection,
            patch(
                "src.app.workline.v1.runtime.runtime_query_service.get_workline_detail",
                new=AsyncMock(side_effect=AssertionError("runtime workline endpoint must use the monitor projection")),
            ),
        ):
            response = await get_runtime_workline_detail(workline_id=404, db=AsyncMock())

        mock_get_monitor_projection.assert_awaited_once_with(AnyArgHashable(), 404)
        assert _response_message(response) == "工作线运行态监控投影不存在: 404"

    def test_runtime_workline_detail_route_uses_monitor_projection_response_contract(self) -> None:
        from src.app.workline.models.runtime import RuntimeWorklineMonitorProjectionResponse
        from src.app.workline.v1.runtime import router
        from src.core.response import ResponseSchemaModel

        route = next(item for item in router.routes if getattr(item, "path", None) == "/worklines/{workline_id}")

        assert getattr(route, "response_model", None) == ResponseSchemaModel[RuntimeWorklineMonitorProjectionResponse]

    @pytest.mark.asyncio
    async def test_get_runtime_workline_detail_uses_monitor_projection_service(self) -> None:
        from src.app.workline.v1.runtime import get_runtime_workline_detail

        result = SimpleNamespace(summary=SimpleNamespace(line_code="WL-45"))

        with patch(
            "src.app.workline.v1.runtime.runtime_query_service.get_workline_monitor_projection",
            new=AsyncMock(return_value=result),
        ) as mock_get_monitor_projection:
            response = await get_runtime_workline_detail(workline_id=45, db=AsyncMock())

        mock_get_monitor_projection.assert_awaited_once_with(AnyArgHashable(), 45)
        assert _response_data(response) is result

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
