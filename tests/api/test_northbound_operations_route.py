"""北向只读运维 API facade 合同。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import Request


def test_northbound_operations_route_requires_dedicated_read_permission() -> None:
    from src.app.workline.v1 import runtime_operations as runtime_operations_api

    route = next(
        route
        for route in runtime_operations_api.router.routes
        if route.path == "/runtime-operations/northbound" and "GET" in route.methods
    )
    assert [getattr(dep.dependency, "permission_required", "") for dep in route.dependencies] == [
        "sys:runtime-operations:view"
    ]


@pytest.mark.asyncio
async def test_northbound_operations_route_delegates_only_to_query_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.app.runtime.orchestration.operational_models import (
        NorthboundOperationalPrincipal,
        NorthboundOperationalSnapshot,
    )
    from src.app.workline.v1 import runtime_operations as runtime_operations_api

    snapshot = NorthboundOperationalSnapshot(
        generated_at="2026-07-23T00:00:00+00:00",
        tenant_scope="WORKLINE_OWNER",
        tenant_id=42,
        workline_id=7,
        operations=[],
    )
    service = SimpleNamespace(get_snapshot=AsyncMock(return_value=snapshot))
    monkeypatch.setattr(runtime_operations_api, "northbound_operations_query_service", service)
    principal = NorthboundOperationalPrincipal(tenant_id=42, user_id=42)
    db = SimpleNamespace()

    response = await runtime_operations_api.get_northbound_runtime_operations(
        db=db,
        principal=principal,
        workline_id=7,
    )

    service.get_snapshot.assert_awaited_once_with(
        db,
        principal=principal,
        workline_id=7,
    )
    assert response["data"] == snapshot


def test_northbound_operational_principal_uses_authenticated_owner_scope() -> None:
    from src.app.workline.v1.runtime_operations import _northbound_operational_principal

    request = Request({"type": "http"})
    request.state.is_superuser = False

    principal = _northbound_operational_principal(request, user_id=42)

    assert principal.tenant_id == 42
    assert principal.user_id == 42
    assert principal.is_superuser is False
