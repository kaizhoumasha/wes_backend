"""Callback API 测试共享支撑。"""

import importlib
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request
from fastapi.routing import APIRoute
from pydantic import TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.callback.models import CallbackEventIngressResponse
from src.app.callback.v1 import callback as callback_module

JsonDict = dict[str, object]
RequestFactory = Callable[..., Request]
callback_ingress_module = importlib.import_module("src.app.callback.services.callback_ingress_service")


def _await_kwargs(mock: AsyncMock) -> JsonDict:
    await_args = mock.await_args
    assert await_args is not None
    return cast("JsonDict", await_args.kwargs)


def _response_data(response: JsonDict) -> JsonDict:
    data = response["data"]
    if hasattr(data, "model_dump"):
        return cast("JsonDict", data.model_dump())
    return cast("JsonDict", data)


def _response_model_data(response: JsonDict) -> JsonDict:
    validated = TypeAdapter(CallbackEventIngressResponse).validate_python(response)
    serialized = TypeAdapter(CallbackEventIngressResponse).dump_python(validated, mode="json")
    return _response_data(cast("JsonDict", serialized))


def _get_route(path: str, method: str) -> APIRoute:
    for route in callback_module.router.routes:
        if isinstance(route, APIRoute) and method in route.methods and route.path == path:
            return route
    raise AssertionError(f"{method} {path} route not found")


@pytest.fixture(autouse=True)
def mock_fast_fail_check():
    """自动 mock fast_fail_check 和设备上下文服务，避免在测试中执行真实的基础设施检查。

    注意：由于测试直接调用 callback 函数而非通过 FastAPI，
    依赖注入可能不会自动触发。
    """
    # Mock fast_fail_check 函数本身
    with patch("src.utils.fast_fail.fast_fail_check", new_callable=AsyncMock) as mock:
        # 同时 mock 健康检查函数
        with (
            patch("src.utils.health.check_database_health", new_callable=AsyncMock) as db_mock,
            patch("src.utils.health.check_redis_health", new_callable=AsyncMock) as redis_mock,
            patch("src.utils.health.check_celery_health", new_callable=AsyncMock) as celery_mock,
            patch("src.app.callback.services.callback_ingress_service.device_context_service.resolve") as ctx_mock,
            patch(
                "src.app.callback.services.callback_ingress_service.workline_diagnostic_service.record_event",
                new_callable=AsyncMock,
            ),
        ):
            # 返回健康状态
            db_mock.return_value = {"status": "healthy"}
            redis_mock.return_value = {"status": "healthy"}
            celery_mock.return_value = {"status": "healthy"}

            # 返回设备上下文（模拟 DeviceContextService.resolve）
            def ctx_resolve_side_effect(db: object, device_code: str):
                # 模拟成功返回：返回 (DeviceContextResult, None)
                return (
                    SimpleNamespace(
                        device=SimpleNamespace(
                            id=1,
                            code=device_code,
                            work_line_id=1,
                            plugin_key="test_workline_plugin",
                            contract_version="1.0",
                            device_status="ONLINE",
                        ),
                        workline=SimpleNamespace(
                            id=1,
                            is_active=True,
                            plugin_key="test_workline_plugin",
                        ),
                        plugin_key="test_workline_plugin",
                        contract_version="1.0",
                        work_line_id=1,
                        is_workline_bound=True,
                    ),
                    None,  # 无错误
                )

            ctx_mock.side_effect = ctx_resolve_side_effect

            mock.return_value = None  # 允许请求通过
            yield mock


@pytest.fixture
def db_session() -> AsyncSession:
    mock = AsyncMock(spec=AsyncSession)
    mock.commit = AsyncMock()
    mock.rollback = AsyncMock()
    mock.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=MagicMock(return_value=None)))
    mock.add = MagicMock()
    return cast("AsyncSession", mock)


@pytest.fixture
def build_request() -> RequestFactory:
    def _build_request(
        *,
        body: JsonDict,
        path: str,
        client_ip: str = "192.168.1.100",
        user_agent: str = "TestClient",
    ) -> Request:
        request = MagicMock()
        request.client = MagicMock()
        request.client.host = client_ip
        request.url = MagicMock()
        request.url.path = path
        request.headers = {"User-Agent": user_agent}
        request.method = "POST"
        request.json = AsyncMock(return_value=body)
        return cast("Request", request)

    return _build_request


def create_result_payload(**overrides: object) -> JsonDict:
    payload: JsonDict = {
        "command_code": "CMD-20250317-001",
        "device_code": "ARM_01",
        "result": "SUCCESS",
        "finish_time": 1702627250000,
        "data": {"task_type": "PICK_AND_PUT"},
    }
    payload.update(overrides)
    return payload


def create_event_payload(**overrides: object) -> JsonDict:
    payload: JsonDict = {
        "device_code": "ARM_01",
        "event_type": "SCAN_COMPLETED",
        "timestamp": 1702627300000,
        "data": {
            "location": "STATION_INPUT1",
            # 使用完整的 SixInOne 字段（对齐硬件约定）
            "LotCode": "LOTABC123",  # 批次码
            "DateCode": "20260409",  # 日期码
            "Qty": "100",  # 数量
            "ProductNo": "PN001",  # 产品PN码
            "MfrPN": "MFR002",  # 制造商PN码
            "PONumber": "PO2026040901",  # 订单码
        },
    }
    payload.update(overrides)
    return payload


def create_external_payload(**overrides: object) -> JsonDict:
    payload: JsonDict = {
        "callback_type": "AGV_TASK_RESULT",
        "trace_id": "trace-agv-001",
        "command_code": "AGV-REQ-001",
        "result": "SUCCESS",
        "data": {"to_location": "STATION_OUTPUT1"},
    }
    payload.update(overrides)
    return payload


def create_wms_external_payload(**overrides: object) -> JsonDict:
    payload: JsonDict = {
        "callback_type": "WMS_RACK_ARRIVED",
        "trace_id": "trace-wms-001",
        "dispatch_key": "external:test_workline_plugin:trace-wms-001:RACK_EXCHANGE_AND_SUPPLY",
        "status": "SUCCEEDED",
        "source_system": "WMS",
        "source_event_id": "wms-event-001",
        "source_version": "1",
        "occurred_at": "2026-05-16T08:00:00Z",
        "request_id": "REQ-WMS-001",
        "timestamp": "2026-05-16T08:00:01Z",
        "signature": "test-signature",
        "active_bin_rack": {"rack_id": "RACK-001", "cells": []},
    }
    payload.update(overrides)
    return payload


def create_full_box_exchange_external_payload(**overrides: object) -> JsonDict:
    payload: JsonDict = {
        "callback_type": "WMS_FULL_BOX_EXCHANGE_RESULT",
        "trace_id": "trace-full-box-001",
        "dispatch_key": "handling:full-box:release-001:move:1",
        "exchange_request_code": "handling:full-box:release-001:move:1",
        "rack_release_id": "rack-release-001",
        "wms_rcs_task_id": "RCS-TASK-FULL-001",
        "source_system": "WMS",
        "source_event_id": "wms-full-box-event-001",
        "source_version": "1",
        "occurred_at": "2026-05-22T08:00:00Z",
        "request_id": "REQ-FULL-BOX-001",
        "timestamp": "2026-05-22T08:00:01Z",
        "signature": "test-signature",
        "exchange_status": "BUSINESS_COMPLETED",
    }
    payload.update(overrides)
    return payload
