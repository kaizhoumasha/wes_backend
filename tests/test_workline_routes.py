from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.workline.models import WorkLinePluginManifestSummary, WorkLineStateTransitionRequest
from src.app.workline.v1 import workline as workline_api
from src.core.response import ClientErrorCode, ResourceErrorCode


def test_plugin_manifest_route_requires_workline_list_permission() -> None:
    route = next(
        route
        for route in workline_api.router.routes
        if route.path == "/plugins/{plugin_key:path}/manifest" and "GET" in route.methods
    )

    assert [getattr(dep.dependency, "permission_required", "") for dep in route.dependencies] == ["biz:workline:list"]


def test_plugin_manifest_route_accepts_encoded_slash_plugin_keys() -> None:
    route = next(
        route
        for route in workline_api.router.routes
        if route.path == "/plugins/{plugin_key:path}/manifest" and "GET" in route.methods
    )

    match, child_scope = route.matches(
        {
            "type": "http",
            "method": "GET",
            "path": "/plugins/rough%20sorter%2F1/manifest",
            "root_path": "",
        }
    )

    assert match.name == "FULL"
    assert child_scope["path_params"]["plugin_key"] == "rough%20sorter%2F1"


@pytest.mark.asyncio
async def test_get_plugin_manifest_returns_registered_plugin_summary(monkeypatch) -> None:
    summary = WorkLinePluginManifestSummary(
        plugin_key="demo_plugin",
        contract_version="demo.v1",
        required_device_roles=[],
        event_source_roles={"EVENT_A": ["SCANNER"]},
        command_target_roles={"COMMAND_A": ["ARM"]},
        supported_events=["EVENT_A"],
        supported_commands=["COMMAND_A"],
    )
    service = SimpleNamespace(get_plugin_manifest_summary=lambda plugin_key: summary)
    monkeypatch.setattr(workline_api, "workline_service", service)

    response = await workline_api.get_workline_plugin_manifest("demo_plugin")

    assert response["code"] == "1000"
    assert response["data"] == summary


@pytest.mark.asyncio
async def test_get_plugin_manifest_decodes_encoded_plugin_key(monkeypatch) -> None:
    seen_plugin_keys: list[str] = []
    summary = WorkLinePluginManifestSummary(
        plugin_key="rough sorter/1",
        contract_version="demo.v1",
        required_device_roles=[],
        event_source_roles={},
        command_target_roles={},
        supported_events=[],
        supported_commands=[],
    )

    def get_plugin_manifest_summary(plugin_key: str) -> WorkLinePluginManifestSummary:
        seen_plugin_keys.append(plugin_key)
        return summary

    service = SimpleNamespace(get_plugin_manifest_summary=get_plugin_manifest_summary)
    monkeypatch.setattr(workline_api, "workline_service", service)

    response = await workline_api.get_workline_plugin_manifest("rough%20sorter%2F1")

    assert response["code"] == "1000"
    assert response["data"] == summary
    assert seen_plugin_keys == ["rough sorter/1"]


@pytest.mark.asyncio
async def test_get_plugin_manifest_returns_not_found_for_unknown_plugin(monkeypatch) -> None:
    service = SimpleNamespace(get_plugin_manifest_summary=lambda plugin_key: None)
    monkeypatch.setattr(workline_api, "workline_service", service)

    response = await workline_api.get_workline_plugin_manifest("unknown_plugin")

    assert response["code"] == ResourceErrorCode.NOT_FOUND.code
    assert response["message"] == "工作线插件不存在: unknown_plugin"


@pytest.mark.asyncio
async def test_get_plugin_manifest_returns_validation_error_for_invalid_manifest(monkeypatch) -> None:
    def get_plugin_manifest_summary(plugin_key: str) -> None:
        raise TypeError(f"工作线插件 {plugin_key} 缺少有效 manifest")

    service = SimpleNamespace(get_plugin_manifest_summary=get_plugin_manifest_summary)
    monkeypatch.setattr(workline_api, "workline_service", service)

    response = await workline_api.get_workline_plugin_manifest("broken_plugin")

    assert response["code"] == ClientErrorCode.VALIDATION_ERROR.code
    assert response["message"] == "工作线插件 manifest 无效: broken_plugin: 工作线插件 broken_plugin 缺少有效 manifest"


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
