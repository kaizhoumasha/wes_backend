from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, PropertyMock, patch

import pytest

from src.app.callback.v1 import callback_log as callback_log_module


def _get_route(path: str, method: str):
    for route in callback_log_module.router.routes:
        if method in route.methods and route.path == path:
            return route
    raise AssertionError(f"{method} {path} route not found")


def _permission_names(path: str, method: str) -> list[str]:
    route = _get_route(path, method)
    return [getattr(dep.dependency, "permission_required", "") for dep in route.dependencies]


class TestCallbackLogApi:
    def test_callback_log_routes_require_user_permissions(self) -> None:
        assert _permission_names("/request/{request_id}", "GET") == ["callback:callback_log:detail"]
        assert _permission_names("/correlation/{correlation_id}", "GET") == ["callback:callback_log:list"]
        assert _permission_names("/device/{device_id}", "GET") == ["callback:callback_log:list"]
        assert _permission_names("/query", "POST") == ["callback:callback_log:list"]

    @pytest.mark.asyncio
    async def test_get_by_request_id_uses_service_method_not_repo(self) -> None:
        from src.app.callback.v1.callback_log import get_by_request_id

        log = SimpleNamespace(
            id=1,
            callback_type="event",
            device_id="ARM01",
            request_body={"foo": "bar"},
            client_ip="127.0.0.1",
            user_agent="pytest",
            request_id="req-001",
            correlation_id="corr-001",
            response_status=200,
            response_time_ms=12,
            error_message=None,
            ingress_outcome="ACCEPTED",
            failure_stage=None,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )

        with (
            patch(
                "src.app.callback.v1.callback_log.callback_log_service.get_by_request_id",
                new=AsyncMock(return_value=log),
            ) as mock_get_by_request_id,
            patch.object(
                type(__import__("src.app.callback.v1.callback_log", fromlist=["callback_log_service"]).callback_log_service),
                "repo",
                new_callable=PropertyMock,
                side_effect=AssertionError("route must not access callback_log_service.repo"),
            ),
        ):
            result = await get_by_request_id("req-001", db=AsyncMock())

        mock_get_by_request_id.assert_awaited_once_with(AnyArgHashable(), "req-001")
        assert result["data"] is log
        assert result["data"].request_id == "req-001"
        assert result["data"].device_id == "ARM01"

    @pytest.mark.asyncio
    async def test_get_by_correlation_id_uses_service_method_not_repo(self) -> None:
        from src.app.callback.v1.callback_log import get_by_correlation_id

        logs = [
            SimpleNamespace(id=1, correlation_id="corr-001"),
            SimpleNamespace(id=2, correlation_id="corr-001"),
        ]

        with (
            patch(
                "src.app.callback.v1.callback_log.callback_log_service.get_by_correlation_id",
                new=AsyncMock(return_value=logs),
            ) as mock_get_by_correlation_id,
            patch.object(
                type(__import__("src.app.callback.v1.callback_log", fromlist=["callback_log_service"]).callback_log_service),
                "repo",
                new_callable=PropertyMock,
                side_effect=AssertionError("route must not access callback_log_service.repo"),
            ),
        ):
            result = await get_by_correlation_id("corr-001", db=AsyncMock())

        mock_get_by_correlation_id.assert_awaited_once_with(AnyArgHashable(), "corr-001")
        assert result["data"] == {
            "correlation_id": "corr-001",
            "count": 2,
            "items": logs,
        }

    @pytest.mark.asyncio
    async def test_get_by_device_id_uses_service_method_not_repo(self) -> None:
        from src.app.callback.v1.callback_log import get_by_device_id

        logs = [
            SimpleNamespace(id=1, device_id="ARM01"),
            SimpleNamespace(id=2, device_id="ARM01"),
        ]

        with (
            patch(
                "src.app.callback.v1.callback_log.callback_log_service.get_by_device_id",
                new=AsyncMock(return_value=logs),
            ) as mock_get_by_device_id,
            patch.object(
                type(__import__("src.app.callback.v1.callback_log", fromlist=["callback_log_service"]).callback_log_service),
                "repo",
                new_callable=PropertyMock,
                side_effect=AssertionError("route must not access callback_log_service.repo"),
            ),
        ):
            result = await get_by_device_id("ARM01", db=AsyncMock(), limit=50)

        mock_get_by_device_id.assert_awaited_once_with(AnyArgHashable(), "ARM01", 50)
        assert result["data"] == {
            "device_id": "ARM01",
            "count": 2,
            "items": logs,
        }


class AnyArgHashable:
    def __eq__(self, other):
        return True

    def __hash__(self):
        return 0
