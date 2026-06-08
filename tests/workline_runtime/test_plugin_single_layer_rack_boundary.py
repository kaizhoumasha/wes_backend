"""插件级单层货架承接边界合同测试。"""

from types import SimpleNamespace

import pytest

from src.app.workline.models import WorkLinePluginManifestSummary
from src.app.workline.services.workline_service import WorkLineService
from src.app.workline.v1 import workline as workline_api
from src.core.response import ResourceErrorCode
from src.workline_plugin_registry import list_workline_plugin_definitions
from src.workline_plugins.rough_sorter.context import RoughSorterContext
from src.workline_plugins.rough_sorter.contract import (
    EVENT_ROUGH_SORTER_STORAGE_RETRY,
    EVENT_SCAN_COMPLETED,
    PHASE_WAITING_RACK,
    ROUGH_SORTER_PLUGIN_KEY,
)
from src.workline_plugins.rough_sorter.plugin import RoughSorterPlugin
from src.workline_plugins.smt_sorting_inbound.constants import (
    COMMAND_NG_PLACE,
    ROLE_SORTING_NG_STATION,
    ROLE_SORTING_SOURCE_ARM,
    ROLE_SORTING_TARGET_ARM,
    SMT_SORTING_INBOUND_PLUGIN_KEY,
)
from src.workline_plugins.smt_sorting_inbound.plugin import SmtSortingInboundPlugin
from src.workline_runtime import plugin_manifest
from src.workline_runtime.plugin_manifest import DeviceRoleRequirement, WorklinePluginManifest
from src.workline_runtime.runtime_intent import RuntimeIntentKind


def _business_key_resolver(payload_json: dict) -> str | None:
    data = payload_json.get("data")
    return data.get("business_key") if isinstance(data, dict) else None


def _boundary_dict(boundary: object) -> dict[str, object]:
    if hasattr(boundary, "to_summary"):
        return boundary.to_summary()
    return {
        "station_code": boundary.station_code,
        "position_code": boundary.position_code,
        "rack_kind": boundary.rack_kind,
        "station_role": boundary.station_role,
        "business_demand_type": boundary.business_demand_type,
        "wms_operation_type": boundary.wms_operation_type,
        "snapshot_kind": boundary.snapshot_kind,
        "lease_scope": boundary.lease_scope,
    }


def _boundaries_by_station(manifest: WorklinePluginManifest) -> dict[str, dict[str, object]]:
    return {str(boundary.station_code): _boundary_dict(boundary) for boundary in manifest.single_layer_boundaries}


def _required_boundary_fields(boundary: dict[str, object]) -> None:
    for field_name in (
        "station_code",
        "position_code",
        "rack_kind",
        "station_role",
        "business_demand_type",
        "wms_operation_type",
        "snapshot_kind",
        "lease_scope",
    ):
        assert isinstance(boundary.get(field_name), str) and boundary[field_name]


def _rough_waiting_rack_ctx() -> SimpleNamespace:
    return SimpleNamespace(
        session=SimpleNamespace(
            id=321,
            context_json={
                "phase": PHASE_WAITING_RACK,
                "business_key": "PKG-ROUGH-001",
                "six_in_one": {"PkgID": "PKG-ROUGH-001"},
                "rack_operation": {
                    "operation_key": "external:smt_rack_bin:trace-rough-sorter-001:RACK_OPERATION",
                    "operation_type": "REPLACE_CLASSIFIER_WORK_RACK",
                    "target_code": "WMS_RCS_RACK_OPERATION",
                    "status": "REQUESTED",
                },
                "resume_source_device_code": "RS-CONVEYOR-01",
            },
        )
    )


def _rough_external_http_inbox(payload: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(
        source_system="WMS",
        payload_json=payload,
        idempotency_key=str(payload.get("source_event_id") or "wms-rack-arrived-001"),
    )


def test_manifest_normalizes_single_layer_boundary_contracts() -> None:
    assert hasattr(plugin_manifest, "SingleLayerRackBoundary")
    boundary_type = plugin_manifest.SingleLayerRackBoundary
    manifest = WorklinePluginManifest(
        plugin_key="example_plugin",
        contract_version="spike",
        required_device_roles=(DeviceRoleRequirement("ENTRY_SCANNER"),),
        business_key_resolver=_business_key_resolver,
        requires_single_layer_boundary=True,
        resource_kinds={"SINGLE_LAYER"},
        capabilities=["station_lease", "active_snapshot"],
        single_layer_boundaries=[
            {
                "station_code": "SOURCE_STATION_A",
                "position_code": "SOURCE_STATION_A",
                "rack_kind": "SINGLE_LAYER",
                "station_role": "SOURCE",
                "business_demand_type": "SORTING_INBOUND_SOURCE",
                "wms_operation_type": "SUPPLY_SINGLE_LAYER_RACK",
                "snapshot_kind": "ACTIVE_SOURCE_BIN_RACK",
                "lease_scope": "STATION",
            }
        ],
    )

    assert isinstance(manifest.single_layer_boundaries, tuple)
    assert manifest.single_layer_boundaries == (
        boundary_type(
            station_code="SOURCE_STATION_A",
            position_code="SOURCE_STATION_A",
            rack_kind="SINGLE_LAYER",
            station_role="SOURCE",
            business_demand_type="SORTING_INBOUND_SOURCE",
            wms_operation_type="SUPPLY_SINGLE_LAYER_RACK",
            snapshot_kind="ACTIVE_SOURCE_BIN_RACK",
            lease_scope="STATION",
        ),
    )
    assert manifest.requires_single_layer_boundary is True
    assert manifest.resource_kinds == frozenset({"SINGLE_LAYER"})
    assert manifest.capabilities == frozenset({"active_snapshot", "station_lease"})


def test_manifest_rejects_non_single_layer_boundary_kind() -> None:
    with pytest.raises(ValueError, match="rack_kind must be SINGLE_LAYER"):
        plugin_manifest.SingleLayerRackBoundary(
            station_code="TARGET_STATION",
            position_code="TARGET_STATION",
            rack_kind="FIVE_LAYER",
            station_role="TARGET",
            business_demand_type="SORTING_INBOUND_TARGET",
            wms_operation_type="SUPPLY_TARGET_RACK",
            snapshot_kind="ACTIVE_TARGET_BIN_RACK",
            lease_scope="STATION",
        )


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("station_role", "UNDECLARED_ROLE"),
        ("business_demand_type", "UNDECLARED_DEMAND"),
        ("wms_operation_type", "UNDECLARED_WMS_OPERATION"),
        ("snapshot_kind", "UNDECLARED_SNAPSHOT"),
        ("lease_scope", "WORK_POSITION"),
    ],
)
def test_manifest_rejects_unknown_single_layer_boundary_values(field_name: str, field_value: str) -> None:
    values = {
        "station_code": "TARGET_STATION",
        "position_code": "TARGET_STATION",
        "rack_kind": "SINGLE_LAYER",
        "station_role": "TARGET",
        "business_demand_type": "SORTING_INBOUND_TARGET",
        "wms_operation_type": "ALLOCATE_SORTING_TARGET_BIN",
        "snapshot_kind": "ACTIVE_TARGET_BIN_RACK",
        "lease_scope": "STATION",
    }
    values[field_name] = field_value

    with pytest.raises(ValueError, match=field_name):
        plugin_manifest.SingleLayerRackBoundary(**values)


@pytest.mark.parametrize(
    "marker",
    [
        {"requires_single_layer_boundary": True},
        {"resource_kinds": {"SINGLE_LAYER"}},
        {"capabilities": {"station_lease"}},
        {"capabilities": {"active_snapshot"}},
        {"capabilities": {"rack_operation"}},
    ],
)
def test_manifest_rejects_single_layer_marker_without_boundaries(marker: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="single_layer_boundaries"):
        WorklinePluginManifest(
            plugin_key="broken_single_layer_plugin",
            contract_version="broken.v1",
            required_device_roles=(DeviceRoleRequirement("ENTRY_SCANNER"),),
            business_key_resolver=_business_key_resolver,
            **marker,
        )


def test_manifest_summary_exports_single_layer_boundaries() -> None:
    summary = WorkLineService().get_plugin_manifest_summary(SMT_SORTING_INBOUND_PLUGIN_KEY)

    assert summary is not None
    assert summary.single_layer_boundaries
    for boundary in summary.single_layer_boundaries:
        _required_boundary_fields(boundary.model_dump())


def test_registered_single_layer_plugins_have_station_rack_boundary_contract() -> None:
    for definition in list_workline_plugin_definitions():
        manifest = definition.manifest
        marker_requires_boundary = (
            getattr(manifest, "requires_single_layer_boundary", False)
            or "SINGLE_LAYER" in getattr(manifest, "resource_kinds", frozenset())
            or "station_lease" in getattr(manifest, "capabilities", frozenset())
            or bool(getattr(manifest, "single_layer_boundaries", ()))
        )
        if not marker_requires_boundary:
            continue

        assert manifest.single_layer_boundaries, f"{definition.plugin_key} must declare single_layer_boundaries"
        for boundary in manifest.single_layer_boundaries:
            _required_boundary_fields(_boundary_dict(boundary))


def test_rough_sorter_single_layer_rack_boundary_targets_seeded_work_position() -> None:
    boundaries = _boundaries_by_station(RoughSorterPlugin.manifest)

    assert set(boundaries) == {"CLASSIFIER_WORK_POSITION"}
    boundary = boundaries["CLASSIFIER_WORK_POSITION"]
    assert boundary["position_code"] == "SINGLE_LAYER_A"
    assert boundary["rack_kind"] == "SINGLE_LAYER"
    assert boundary["station_role"] == "CLASSIFIER_WORK"
    assert boundary["business_demand_type"] == "ROUGH_SORTER_BIN_ALLOCATION"
    assert boundary["wms_operation_type"] == "REPLACE_CLASSIFIER_WORK_RACK"
    assert boundary["snapshot_kind"] == "ACTIVE_CLASSIFIER_BIN_RACK"
    assert boundary["lease_scope"] == "STATION"
    assert SMT_SORTING_INBOUND_PLUGIN_KEY not in str(boundary)


def test_rough_sorter_default_rack_operation_position_matches_manifest_boundary() -> None:
    boundaries = _boundaries_by_station(RoughSorterPlugin.manifest)
    boundary_position_code = boundaries["CLASSIFIER_WORK_POSITION"]["position_code"]

    rack_tasks = RoughSorterPlugin._rack_tasks_from_actions(
        {"actions": ["ALLOCATE_AND_MOVE_RACK"], "work_position_code": boundary_position_code}
    )

    assert len(rack_tasks) == 1
    assert rack_tasks[0]["target_position_code"] == boundary_position_code


def test_rough_sorter_rack_operation_actions_require_explicit_work_position() -> None:
    with pytest.raises(ValueError, match="work_position_code"):
        RoughSorterPlugin._rack_tasks_from_actions({"actions": ["ALLOCATE_AND_MOVE_RACK"]})


def test_rough_sorter_rack_operation_request_without_work_position_blocks() -> None:
    plugin = RoughSorterPlugin()
    intents = plugin._rack_operation_required_intents(
        SimpleNamespace(trace_id="trace-rough", config={}, session=SimpleNamespace(id=321)),
        {},
        RoughSorterContext(six_in_one={"PkgID": "PKG-ROUGH-001"}, business_key="PKG-ROUGH-001"),
        "PKG-ROUGH-001",
        SimpleNamespace(
            kind="RACK_OPERATION_REQUIRED",
            reason_code="NO_CAPACITY",
            message="no capacity",
            rack_operation_request=SimpleNamespace(
                operation_key="external:smt_rack_bin:trace-rough:RACK_OPERATION",
                target_code="WMS_RCS_RACK_OPERATION",
                payload={"actions": ["ALLOCATE_AND_MOVE_RACK"]},
                timeout_seconds=1800,
            ),
        ),
    )

    assert [intent.kind for intent in intents] == [RuntimeIntentKind.BLOCK]
    assert intents[0].reason_code == "ROUGH_SORTER_ALLOCATION_DECISION_INVALID"
    assert "work_position_code" in intents[0].message


@pytest.mark.asyncio
async def test_rough_sorter_rack_arrived_resumes_only_waiting_rough_sorter_session() -> None:
    intents = await RoughSorterPlugin().on_external_http(
        _rough_waiting_rack_ctx(),
        _rough_external_http_inbox(
            {
                "callback_type": "WMS_RACK_ARRIVED",
                "dispatch_key": "rack-operation:dispatch-001",
                "operation_key": "external:smt_rack_bin:trace-rough-sorter-001:RACK_OPERATION",
                "source_event_id": "wms-rack-arrived-001",
                "rack_code": "RACK-001",
                "rack_kind": "SINGLE_LAYER",
                "target_position_code": "SINGLE_LAYER_A",
            }
        ),
    )

    retry_events = [
        intent
        for intent in intents
        if getattr(intent, "payload_json", {}).get("canonical_event_type") == EVENT_ROUGH_SORTER_STORAGE_RETRY
    ]
    assert len(retry_events) == 1
    retry_payload = retry_events[0].payload_json
    assert retry_payload["event_type"] == EVENT_ROUGH_SORTER_STORAGE_RETRY
    assert retry_payload["device_code"] == "RS-CONVEYOR-01"
    assert SMT_SORTING_INBOUND_PLUGIN_KEY not in str(retry_payload)


def test_sorting_inbound_start_ready_does_not_bind_station_or_create_commands() -> None:
    manifest = SmtSortingInboundPlugin.manifest

    assert "WORKLINE_START_REQUESTED" not in manifest.event_source_roles
    assert manifest.single_layer_boundaries


def test_sorting_inbound_source_flow_requires_active_source_snapshot_and_station_lease() -> None:
    source_boundaries = [
        _boundary_dict(boundary)
        for boundary in SmtSortingInboundPlugin.manifest.single_layer_boundaries
        if boundary.station_role == "SOURCE"
    ]

    assert source_boundaries
    for boundary in source_boundaries:
        assert boundary["business_demand_type"] == "SORTING_INBOUND_SOURCE"
        assert boundary["snapshot_kind"] == "ACTIVE_SOURCE_BIN_RACK"
        assert boundary["lease_scope"] == "STATION"


def test_sorting_inbound_target_flow_declares_target_station_boundary() -> None:
    target_boundaries = [
        _boundary_dict(boundary)
        for boundary in SmtSortingInboundPlugin.manifest.single_layer_boundaries
        if boundary.station_role == "TARGET"
    ]

    assert target_boundaries == [
        {
            "station_code": "TARGET_STATION",
            "position_code": "TARGET_STATION",
            "rack_kind": "SINGLE_LAYER",
            "station_role": "TARGET",
            "business_demand_type": "SORTING_INBOUND_TARGET",
            "wms_operation_type": "ALLOCATE_SORTING_TARGET_BIN",
            "snapshot_kind": "ACTIVE_TARGET_BIN_RACK",
            "lease_scope": "STATION",
        }
    ]
    assert "FIVE_LAYER" in SmtSortingInboundPlugin.manifest.resource_kinds
    assert all(
        boundary.rack_kind == "SINGLE_LAYER" for boundary in SmtSortingInboundPlugin.manifest.single_layer_boundaries
    )


def test_sorting_inbound_boundaries_support_multiple_source_stations() -> None:
    source_station_codes = {
        boundary.station_code
        for boundary in SmtSortingInboundPlugin.manifest.single_layer_boundaries
        if boundary.station_role == "SOURCE"
    }

    assert source_station_codes == {"SOURCE_STATION_A", "SOURCE_STATION_B"}


def test_sorting_inbound_ng_place_uses_target_arm_and_ng_station_as_evidence() -> None:
    manifest = SmtSortingInboundPlugin.manifest

    assert manifest.command_target_roles[COMMAND_NG_PLACE] == (ROLE_SORTING_TARGET_ARM,)
    assert ROLE_SORTING_NG_STATION in {requirement.role for requirement in manifest.required_device_roles}
    assert ROLE_SORTING_NG_STATION not in {role for roles in manifest.command_target_roles.values() for role in roles}


@pytest.mark.asyncio
async def test_plugin_manifest_route_exports_single_layer_boundaries(monkeypatch) -> None:
    summary = WorkLinePluginManifestSummary(
        plugin_key=SMT_SORTING_INBOUND_PLUGIN_KEY,
        contract_version="demo.v1",
        required_device_roles=[],
        event_source_roles={},
        command_target_roles={},
        supported_events=[],
        supported_commands=[],
        single_layer_boundaries=[
            {
                "station_code": "SOURCE_STATION_A",
                "position_code": "SOURCE_STATION_A",
                "rack_kind": "SINGLE_LAYER",
                "station_role": "SOURCE",
                "business_demand_type": "SORTING_INBOUND_SOURCE",
                "wms_operation_type": "SUPPLY_SINGLE_LAYER_RACK",
                "snapshot_kind": "ACTIVE_SOURCE_BIN_RACK",
                "lease_scope": "STATION",
            }
        ],
    )
    service = SimpleNamespace(get_plugin_manifest_summary=lambda plugin_key: summary)
    monkeypatch.setattr(workline_api, "workline_service", service)

    response = await workline_api.get_workline_plugin_manifest(SMT_SORTING_INBOUND_PLUGIN_KEY)

    assert response["code"] == "1000"
    assert response["data"].single_layer_boundaries == summary.single_layer_boundaries

    unknown_service = SimpleNamespace(get_plugin_manifest_summary=lambda plugin_key: None)
    monkeypatch.setattr(workline_api, "workline_service", unknown_service)
    unknown_response = await workline_api.get_workline_plugin_manifest("unknown_plugin")
    assert unknown_response["code"] == ResourceErrorCode.NOT_FOUND.code


def test_plugin_manifest_openapi_schema_contains_single_layer_boundaries() -> None:
    schema = WorkLinePluginManifestSummary.model_json_schema()

    assert "single_layer_boundaries" in schema["properties"]
    boundary_schema = schema["properties"]["single_layer_boundaries"]
    assert boundary_schema["type"] == "array"
    boundary_ref = boundary_schema["items"]["$ref"].removeprefix("#/$defs/")
    boundary_def = schema["$defs"][boundary_ref]
    assert set(boundary_def["properties"]) >= {
        "station_code",
        "position_code",
        "rack_kind",
        "station_role",
        "business_demand_type",
        "wms_operation_type",
        "snapshot_kind",
        "lease_scope",
    }
