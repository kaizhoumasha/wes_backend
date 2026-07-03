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
        rack_positions=[
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
                    "to_node": {"kind": "RACK_POSITION", "ref": "SCAN_POSITION"},
                    "type": "OPERATION",
                }
            ]
        },
        events=[
            {
                "event": "EVENT_A",
                "source_device_roles": ["SCANNER"],
                "category": "ENTRY_DEVICE",
            }
        ],
        commands=[
            {
                "command": "COMMAND_A",
                "target_device_role": "SCANNER",
            }
        ],
        resource_boundaries=[
            {
                "rack_position_code": "SCAN_POSITION",
                "rack_kind": "SINGLE_LAYER",
                "business_demand_type": "DEMO_DEMAND",
                "wms_operation_type": "DEMO_OPERATION",
                "snapshot_kind": "ACTIVE_DEMO_RACK",
                "lease_scope": "STATION",
            }
        ],
        session_subject={
            "type": "MATERIAL_UNIT",
            "physical_form": "REEL",
            "identity_sources": ["PkgID", "material_identity_key"],
        },
        state_machines=[
            {
                "id": "smt_material_unit_reel",
                "subject": {
                    "category": "MATERIAL_UNIT",
                    "type": "MATERIAL_UNIT",
                    "physical_form": "REEL",
                },
                "state_owner": {
                    "model": "MaterialUnit",
                    "field": "status",
                },
                "granularity": "MATERIAL_LIFECYCLE",
                "transitions": [
                    {"from_state": "IN_TRANSIT", "to_states": ["STORED", "COMPLETED", "NG", "RECONCILING"]},
                    {"from_state": "STORED", "to_states": ["IN_TRANSIT", "NG", "RECONCILING"]},
                    {"from_state": "RECONCILING", "to_states": ["IN_TRANSIT", "STORED", "COMPLETED", "NG"]},
                    {"from_state": "NG", "to_states": []},
                    {"from_state": "COMPLETED", "to_states": []},
                ],
            }
        ],
        pipeline_queues=[
            {
                "code": "WORKSTATION_ACTIVE",
                "role": "WORKSTATION",
                "capacity": 1,
                "order_policy": "FIFO",
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


def test_plane_routes_require_dedicated_permissions() -> None:
    """plane scene/snapshot 使用独立权限, 不能复用普通 detail。"""

    from src.app.workline.services.plane_service import plane_read_security_policy

    scene_route = next(
        route
        for route in workline_api.router.routes
        if route.path == "/work_lines/{id}/plane/scene" and "GET" in route.methods
    )
    snapshot_route = next(
        route
        for route in workline_api.router.routes
        if route.path == "/work_lines/{id}/plane/snapshot" and "GET" in route.methods
    )

    assert [getattr(dep.dependency, "permission_required", "") for dep in scene_route.dependencies] == [
        plane_read_security_policy.scene_permission
    ]
    assert [getattr(dep.dependency, "permission_required", "") for dep in snapshot_route.dependencies] == [
        plane_read_security_policy.snapshot_permission
    ]


@pytest.mark.asyncio
async def test_plane_scene_route_records_read_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    """plane scene route 返回成功时必须记录读取审计。"""

    from src.app.workline.models import PlaneSceneView
    from src.app.workline.services.plane_service import PlaneReadPrincipal

    service = SimpleNamespace(
        get_scene=AsyncMock(
            return_value=PlaneSceneView(
                schema_version="plane.scene.v1",
                workline_code="WL-7",
                nodes=[],
                edges=[],
            )
        ),
        record_read_audit=AsyncMock(),
    )
    monkeypatch.setattr(workline_api, "workline_plane_service", service)
    db = SimpleNamespace()
    cache = SimpleNamespace()
    principal = PlaneReadPrincipal(user_id=42, is_superuser=False)

    await workline_api.get_workline_plane_scene(db=db, cache=cache, id=7, principal=principal)

    service.get_scene.assert_awaited_once_with(db, cache, 7, principal=principal)
    service.record_read_audit.assert_awaited_once_with(db, view="scene", workline_id=7, workline_code="WL-7")


@pytest.mark.asyncio
async def test_plane_snapshot_route_records_read_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    """plane snapshot route 返回成功时必须记录读取审计。"""

    from src.app.workline.models import PlaneSnapshot
    from src.app.workline.services.plane_service import PlaneReadPrincipal

    service = SimpleNamespace(
        get_snapshot=AsyncMock(
            return_value=PlaneSnapshot(
                schema_version="plane.snapshot.v1",
                workline_code="WL-7",
                scene_schema_version="plane.scene.v1",
                objects=[],
                extremes=[],
            )
        ),
        record_read_audit=AsyncMock(),
    )
    monkeypatch.setattr(workline_api, "workline_plane_service", service)
    db = SimpleNamespace()
    cache = SimpleNamespace()
    principal = PlaneReadPrincipal(user_id=42, is_superuser=False)

    await workline_api.get_workline_plane_snapshot(db=db, cache=cache, id=7, principal=principal)

    service.get_snapshot.assert_awaited_once_with(db, cache, 7, principal=principal)
    service.record_read_audit.assert_awaited_once_with(db, view="snapshot", workline_id=7, workline_code="WL-7")


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


def test_plugin_manifest_openapi_exposes_contract_version_query() -> None:
    app = FastAPI()
    app.include_router(workline_api.router)

    parameters = app.openapi()["paths"]["/plugins/{plugin_key}/manifest"]["get"]["parameters"]

    assert {
        "name": "contract_version",
        "in": "query",
        "required": False,
    } in [
        {
            "name": parameter["name"],
            "in": parameter["in"],
            "required": parameter.get("required", False),
        }
        for parameter in parameters
    ]


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
    seen_requests: list[tuple[str, str | None]] = []

    def get_plugin_manifest_summary(
        plugin_key: str,
        contract_version: str | None = None,
    ) -> WorkLinePluginManifestSummary:
        seen_requests.append((plugin_key, contract_version))
        return summary

    service = SimpleNamespace(get_plugin_manifest_summary=get_plugin_manifest_summary)
    monkeypatch.setattr(workline_api, "workline_service", service)

    response = await workline_api.get_workline_plugin_manifest(
        "demo_plugin",
        contract_version="demo.v1",
    )

    assert response["code"] == "1000"
    assert response["data"] == summary
    assert seen_requests == [("demo_plugin", "demo.v1")]
    assert set(response["data"].model_dump()) == {
        "plugin_key",
        "contract_version",
        "devices",
        "rack_positions",
        "topology",
        "events",
        "commands",
        "resource_boundaries",
        "safety_zones",
        "shared_devices",
        "session_subject",
        "state_machines",
        "pipeline_queues",
    }


@pytest.mark.asyncio
async def test_get_plugin_manifest_decodes_encoded_plugin_key(monkeypatch) -> None:
    seen_plugin_keys: list[str] = []
    summary = _manifest_summary(plugin_key="rough sorter/1")

    def get_plugin_manifest_summary(
        plugin_key: str,
        contract_version: str | None = None,
    ) -> WorkLinePluginManifestSummary:
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
    service = SimpleNamespace(get_plugin_manifest_summary=lambda plugin_key, contract_version=None: None)
    monkeypatch.setattr(workline_api, "workline_service", service)

    response = await workline_api.get_workline_plugin_manifest("unknown_plugin")

    assert response["code"] == ResourceErrorCode.NOT_FOUND.code
    assert response["message"] == "工作线插件不存在: unknown_plugin"


@pytest.mark.asyncio
async def test_get_plugin_manifest_returns_validation_error_for_invalid_manifest(monkeypatch) -> None:
    def get_plugin_manifest_summary(
        plugin_key: str,
        contract_version: str | None = None,
    ) -> None:
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
        "rack_positions",
        "topology",
        "events",
        "commands",
        "resource_boundaries",
        "safety_zones",
        "shared_devices",
        "session_subject",
        "state_machines",
        "pipeline_queues",
    }


def test_openapi_manifest_rack_position_schemas_document_rack_position_semantics() -> None:
    position_schema = _workline_openapi_component("RackPosition")
    carrier_schema = _workline_openapi_component("RackPositionCarrierCapability")

    assert "货架停靠位" in position_schema["description"]
    assert "库存事实锚点" in position_schema["description"]
    assert "货架停靠位" in position_schema["properties"]["code"]["description"]
    assert "承载能力" in position_schema["properties"]["carrier_capability"]["description"]
    assert "承载能力" in carrier_schema["description"]
    assert "货架类型" in carrier_schema["properties"]["allowed_rack_kinds"]["description"]


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
