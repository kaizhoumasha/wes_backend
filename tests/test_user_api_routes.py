from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.app.admin.v1 import user as user_module
from src.database import redis_client


def _get_route(path: str, method: str):
    for route in user_module.router.routes:
        if method in route.methods and route.path == path:
            return route
    raise AssertionError(f"{method} {path} route not found")


def test_stats_cache_route_requires_explicit_permission() -> None:
    route = _get_route("/users/stats/cache", "GET")
    permissions = [getattr(dep.dependency, "permission_required", "") for dep in route.dependencies]
    assert permissions == ["admin:user:stats"]


@pytest.mark.asyncio
async def test_stats_cache_route_returns_standard_response(monkeypatch: pytest.MonkeyPatch) -> None:
    route = _get_route("/users/stats/cache", "GET")
    monkeypatch.setattr(user_module.user_service, "count", AsyncMock(return_value=12))
    monkeypatch.setattr(redis_client, "is_redis_available", lambda: False)

    class FakeCache:
        def get_status(self) -> dict[str, object]:
            return {"available": True}

    response = await route.endpoint(db=object(), cache=FakeCache())

    assert response["code"] == "1000"
    assert response["data"] == {
        "total_users": 12,
        "cache_status": {"available": True},
        "cache_keys_count": None,
    }
