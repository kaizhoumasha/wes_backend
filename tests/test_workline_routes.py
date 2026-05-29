from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.workline.models import WorkLineStateTransitionRequest
from src.app.workline.v1 import workline as workline_api
from src.core.response import ResourceErrorCode


@pytest.mark.asyncio
async def test_configuration_status_route_converts_missing_workline_to_not_found(monkeypatch) -> None:
    service = SimpleNamespace(configuration_status=AsyncMock(side_effect=ValueError("WorkLine 不存在: 404")))
    monkeypatch.setattr(workline_api, "workline_service", service)

    response = await workline_api.get_workline_configuration_status(object(), id=404)

    assert response["code"] == ResourceErrorCode.NOT_FOUND.code
    assert "不存在" in response["message"]


@pytest.mark.asyncio
async def test_activate_route_converts_missing_workline_to_not_found(monkeypatch) -> None:
    service = SimpleNamespace(activate=AsyncMock(side_effect=ValueError("WorkLine 不存在: 404")))
    monkeypatch.setattr(workline_api, "workline_service", service)

    response = await workline_api.activate_workline(
        object(),
        object(),
        id=404,
        payload=WorkLineStateTransitionRequest(version=0),
    )

    assert response["code"] == ResourceErrorCode.NOT_FOUND.code
    assert "不存在" in response["message"]


@pytest.mark.asyncio
async def test_deactivate_route_converts_missing_workline_to_not_found(monkeypatch) -> None:
    service = SimpleNamespace(deactivate=AsyncMock(side_effect=ValueError("WorkLine 不存在: 404")))
    monkeypatch.setattr(workline_api, "workline_service", service)

    response = await workline_api.deactivate_workline(
        object(),
        object(),
        id=404,
        payload=WorkLineStateTransitionRequest(version=0),
    )

    assert response["code"] == ResourceErrorCode.NOT_FOUND.code
    assert "不存在" in response["message"]
