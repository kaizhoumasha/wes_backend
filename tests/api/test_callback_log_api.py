from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, cast
from unittest.mock import AsyncMock, PropertyMock, patch

import pytest
from fastapi.routing import APIRoute

from src.app.callback.models import (
    CallbackLogResponse,
    CallbackLogSubjectResponse,
    CallbackLogTraceResponse,
)
from src.app.callback.v1 import callback_log as callback_log_module
from src.core.response import ResponseSchemaModel
from src.core.response.response_schema import ListResponseSchemaModel

CallbackLogRouteEndpoint = Callable[..., Awaitable[dict[str, Any]]]

CALLBACK_LOG_CREATED_AT = datetime(2026, 5, 11, 0, 0, 0)


def _get_route(path: str, method: str) -> APIRoute:
    for route in callback_log_module.router.routes:
        if isinstance(route, APIRoute) and method in route.methods and route.path == path:
            return route
    raise AssertionError(f"{method} {path} route not found")


def _permission_names(path: str, method: str) -> list[str]:
    route = _get_route(path, method)
    return [getattr(dep.dependency, "permission_required", "") for dep in route.dependencies]


def _route_endpoint(path: str, method: str) -> CallbackLogRouteEndpoint:
    return cast("CallbackLogRouteEndpoint", _get_route(path, method).endpoint)


def _callback_log_response(
    *,
    log_id: int = 1,
    subject_code: str = "ARM01",
    request_id: str | None = "req-001",
    trace_id: str | None = "trace-001",
) -> CallbackLogResponse:
    return CallbackLogResponse(
        id=log_id,
        callback_type="event",
        subject_code=subject_code,
        request_body={"foo": "bar"},
        client_ip="127.0.0.1",
        user_agent="pytest",
        request_id=request_id,
        trace_id=trace_id,
        event_id=None,
        causation_id=None,
        response_status=200,
        response_time_ms=12,
        error_message=None,
        ingress_outcome="ACCEPTED",
        failure_stage=None,
        created_at=CALLBACK_LOG_CREATED_AT,
        updated_at=CALLBACK_LOG_CREATED_AT,
    )


class TestCallbackLogApi:
    def test_callback_log_routes_require_user_permissions(self) -> None:
        assert _permission_names("/logs/{id}", "GET") == ["callback:callback_log:detail"]
        assert _permission_names("/logs/query", "POST") == ["callback:callback_log:list"]
        assert _permission_names("/logs/request/{request_id}", "GET") == ["callback:callback_log:detail"]
        assert _permission_names("/logs/trace/{trace_id}", "GET") == ["callback:callback_log:list"]
        assert _permission_names("/logs/subject/{subject_code}", "GET") == ["callback:callback_log:list"]

    @pytest.mark.parametrize(
        ("path", "method", "response_model"),
        [
            ("/logs/{id}", "GET", ResponseSchemaModel[CallbackLogResponse]),
            ("/logs/query", "POST", ListResponseSchemaModel[CallbackLogResponse]),
            ("/logs/request/{request_id}", "GET", ResponseSchemaModel[CallbackLogResponse]),
            ("/logs/trace/{trace_id}", "GET", ResponseSchemaModel[CallbackLogTraceResponse]),
            ("/logs/subject/{subject_code}", "GET", ResponseSchemaModel[CallbackLogSubjectResponse]),
        ],
    )
    def test_routes_declare_enveloped_response_models(
        self,
        path: str,
        method: str,
        response_model: object,
    ) -> None:
        route = _get_route(path, method)

        assert route.response_model == response_model

    @pytest.mark.asyncio
    async def test_get_by_request_id_uses_service_method_not_repo(self) -> None:
        log = _callback_log_response()

        with (
            patch(
                "src.app.callback.v1.callback_log.callback_log_service.get_by_request_id",
                new=AsyncMock(return_value=log),
            ) as mock_get_by_request_id,
            patch.object(
                type(
                    __import__(
                        "src.app.callback.v1.callback_log", fromlist=["callback_log_service"]
                    ).callback_log_service
                ),
                "repo",
                new_callable=PropertyMock,
                side_effect=AssertionError("route must not access callback_log_service.repo"),
            ),
        ):
            result = await _route_endpoint("/logs/request/{request_id}", "GET")("req-001", db=AsyncMock())

        mock_get_by_request_id.assert_awaited_once_with(AnyArgHashable(), "req-001")
        assert result["data"] == log
        assert result["data"].request_id == "req-001"
        assert result["data"].subject_code == "ARM01"

    @pytest.mark.asyncio
    async def test_get_by_trace_id_uses_service_method_not_repo(self) -> None:
        logs = [
            _callback_log_response(log_id=1, trace_id="trace-001"),
            _callback_log_response(log_id=2, trace_id="trace-001"),
        ]

        with (
            patch(
                "src.app.callback.v1.callback_log.callback_log_service.get_by_trace_id",
                new=AsyncMock(return_value=logs),
            ) as mock_get_by_trace_id,
            patch.object(
                type(
                    __import__(
                        "src.app.callback.v1.callback_log", fromlist=["callback_log_service"]
                    ).callback_log_service
                ),
                "repo",
                new_callable=PropertyMock,
                side_effect=AssertionError("route must not access callback_log_service.repo"),
            ),
        ):
            result = await _route_endpoint("/logs/trace/{trace_id}", "GET")("trace-001", db=AsyncMock())

        mock_get_by_trace_id.assert_awaited_once_with(AnyArgHashable(), "trace-001")
        assert result["data"] == CallbackLogTraceResponse(trace_id="trace-001", count=2, items=logs)

    @pytest.mark.asyncio
    async def test_get_by_subject_code_uses_service_method_not_repo(self) -> None:
        logs = [
            _callback_log_response(log_id=1, subject_code="ARM01"),
            _callback_log_response(log_id=2, subject_code="ARM01"),
        ]

        with (
            patch(
                "src.app.callback.v1.callback_log.callback_log_service.get_by_subject_code",
                new=AsyncMock(return_value=logs),
            ) as mock_get_by_subject_code,
            patch.object(
                type(
                    __import__(
                        "src.app.callback.v1.callback_log", fromlist=["callback_log_service"]
                    ).callback_log_service
                ),
                "repo",
                new_callable=PropertyMock,
                side_effect=AssertionError("route must not access callback_log_service.repo"),
            ),
        ):
            result = await _route_endpoint("/logs/subject/{subject_code}", "GET")("ARM01", db=AsyncMock(), limit=50)

        mock_get_by_subject_code.assert_awaited_once_with(AnyArgHashable(), "ARM01", 50)
        assert result["data"] == CallbackLogSubjectResponse(subject_code="ARM01", count=2, items=logs)


class AnyArgHashable:
    def __eq__(self, other: object) -> bool:
        return True

    def __hash__(self) -> int:
        return 0
