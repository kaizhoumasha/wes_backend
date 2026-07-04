"""Phase4 read model API facade tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.runtime.orchestration.services.query.material_location_query_service import (
    MaterialLocationConflictState,
    MaterialLocationResult,
)
from src.app.runtime.orchestration.services.query.workline_active_objects_service import WorklineActiveObjectsResponse


def test_material_location_query_route_requires_dedicated_permission() -> None:
    from src.app.material.v1 import material_unit as material_unit_api

    route = next(
        route
        for route in material_unit_api.router.routes
        if route.path == "/material-units/location-query" and "GET" in route.methods
    )

    assert [getattr(dep.dependency, "permission_required", "") for dep in route.dependencies] == [
        "biz:material:location-query"
    ]


@pytest.mark.asyncio
async def test_material_location_query_route_delegates_to_service(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.app.material.v1 import material_unit as material_unit_api

    service = SimpleNamespace(
        query_by_package_or_bin=AsyncMock(
            return_value=MaterialLocationResult(
                query_entry="by package or bin",
                conflict_state=MaterialLocationConflictState.OK,
                object_type="PKG",
                object_key="PKG-API",
                location_scope="BIN_CELL",
                location_code="BIN-API:C01",
                evidence=[],
            )
        )
    )
    monkeypatch.setattr(material_unit_api, "material_location_query_service", service)
    db = SimpleNamespace()

    response = await material_unit_api.query_material_unit_location(
        db=db,
        package_id="PKG-API",
        bin_code=None,
        material_identity_key=None,
        rack_code=None,
        rack_side=None,
        external_reference_type=None,
        external_reference_value=None,
        provider_code=None,
        correlation_id=None,
    )

    service.query_by_package_or_bin.assert_awaited_once_with(db, package_id="PKG-API", bin_code=None)
    assert response["data"].location_code == "BIN-API:C01"


def test_workline_active_objects_route_requires_detail_permission() -> None:
    from src.app.workline.v1 import active_objects as active_objects_api

    route = next(
        route
        for route in active_objects_api.router.routes
        if route.path == "/work_lines/{id}/active-objects" and "GET" in route.methods
    )

    assert [getattr(dep.dependency, "permission_required", "") for dep in route.dependencies] == ["biz:workline:detail"]


@pytest.mark.asyncio
async def test_workline_active_objects_route_delegates_to_service(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.app.workline.v1 import active_objects as active_objects_api

    service = SimpleNamespace(
        get_active_objects=AsyncMock(
            return_value=WorklineActiveObjectsResponse(
                workline_id=7,
                objects=[],
                truncated=False,
                total_count=0,
            )
        )
    )
    monkeypatch.setattr(active_objects_api, "workline_active_objects_service", service)
    db = SimpleNamespace()

    response = await active_objects_api.get_workline_active_objects(db=db, id=7)

    service.get_active_objects.assert_awaited_once_with(db, workline_id=7)
    assert response["data"].workline_id == 7
