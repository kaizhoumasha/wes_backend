from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI

from src.app.workline.models import WorkLinePluginManifestSummary, WorkLinePluginOption, WorkLineStateTransitionRequest
from src.app.workline.v1 import workline as workline_api
from src.core.response import ClientErrorCode, ResourceErrorCode


def _manifest_summary(plugin_key: str = "demo_plugin") -> WorkLinePluginManifestSummary:
    return WorkLinePluginManifestSummary(
        plugin_key=plugin_key,
        contract_version="demo.v1",
        devices=[
            {
                "role": "SCANNER",
                "min_count": 1,
                "max_count": 1,
                "hardware_capabilities": ["barcode_reader"],
            }
        ],
        positions=[
            {
                "code": "SCAN_POSITION",
                "role": "SCAN",
                "station_code": "SCAN_STATION",
                "carrier_capability": {
                    "allowed_rack_kinds": ["SINGLE_LAYER"],
                    "min_capacity": 1,
                    "max_capacity": 2,
                    "allowed_slot_kinds": ["BIN_SLOT"],
                },
            }
        ],
        topology={
            "flow_edges": [
                {
                    "from_node": {"kind": "DEVICE_ROLE", "ref": "SCANNER"},
                    "to_node": {"kind": "POSITION", "ref": "SCAN_POSITION"},
                    "type": "OPERATION",
                }
            ]
        },
        events=[
            {
                "event": "EVENT_A",
                "source_device_roles": ["SCANNER"],
                "category": "ENTRY_DEVICE",
                "payload_schema_ref": "schemas.EventA",
            }
        ],
        commands=[
            {
                "command": "COMMAND_A",
                "target_device_role": "SCANNER",
                "position_args": [
                    {
                        "name": "target_position",
                        "role": "TARGET",
                        "required": True,
                        "position_ref": "SCAN_POSITION",
                        "source": None,
                    }
                ],
                "payload_schema_ref": "schemas.CommandA",
                "result_bindings": [
                    {
                        "result": "OK",
                        "event": "COMMAND_A_OK",
                        "category": "COMMAND_RESULT",
                        "classification": "SUCCESS",
                        "terminal": True,
                        "next_event": None,
                    }
                ],
            }
        ],
        resource_boundaries=[
            {
                "position_code": "SCAN_POSITION",
                "rack_kind": "SINGLE_LAYER",
                "business_demand_type": "DEMO_DEMAND",
                "wms_operation_type": "DEMO_OPERATION",
                "snapshot_kind": "ACTIVE_DEMO_RACK",
                "lease_scope": "STATION",
            }
        ],
    )


def _workline_openapi_component(component_name: str) -> dict[str, object]:
    app = FastAPI()
    app.include_router(workline_api.router)
    return app.openapi()["components"]["schemas"][component_name]


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
async def test_list_plugin_options_returns_selector_only_fields(monkeypatch) -> None:
    option = WorkLinePluginOption(
        plugin_key="demo_plugin",
        label="demo_plugin",
        contract_versions=["demo.v1"],
        default_contract_version="demo.v1",
    )
    service = SimpleNamespace(list_plugin_options=lambda: [option])
    monkeypatch.setattr(workline_api, "workline_service", service)

    response = await workline_api.list_workline_plugin_options()

    assert response["code"] == "1000"
    assert [item.model_dump() for item in response["data"]] == [
        {
            "plugin_key": "demo_plugin",
            "label": "demo_plugin",
            "contract_versions": ["demo.v1"],
            "default_contract_version": "demo.v1",
        }
    ]


@pytest.mark.asyncio
async def test_get_plugin_manifest_returns_registered_plugin_summary(monkeypatch) -> None:
    summary = _manifest_summary()
    service = SimpleNamespace(get_plugin_manifest_summary=lambda plugin_key: summary)
    monkeypatch.setattr(workline_api, "workline_service", service)

    response = await workline_api.get_workline_plugin_manifest("demo_plugin")

    assert response["code"] == "1000"
    assert response["data"] == summary
    assert set(response["data"].model_dump()) == {
        "plugin_key",
        "contract_version",
        "devices",
        "positions",
        "topology",
        "events",
        "commands",
        "resource_boundaries",
    }


@pytest.mark.asyncio
async def test_get_plugin_manifest_decodes_encoded_plugin_key(monkeypatch) -> None:
    seen_plugin_keys: list[str] = []
    summary = _manifest_summary(plugin_key="rough sorter/1")

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


def test_openapi_workline_plugin_option_excludes_old_ability_fields() -> None:
    schema = _workline_openapi_component("WorkLinePluginOption")

    assert set(schema["properties"]) == {
        "plugin_key",
        "label",
        "contract_versions",
        "default_contract_version",
    }


def test_openapi_workline_plugin_manifest_summary_includes_new_manifest_fields() -> None:
    schema = _workline_openapi_component("WorkLinePluginManifestSummary")

    assert set(schema["properties"]) == {
        "plugin_key",
        "contract_version",
        "devices",
        "positions",
        "topology",
        "events",
        "commands",
        "resource_boundaries",
    }


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
