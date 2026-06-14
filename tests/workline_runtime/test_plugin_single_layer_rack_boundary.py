"""插件通用资源边界合同测试。"""

from dataclasses import fields

from src.workline_plugin_registry import list_workline_plugin_definitions
from src.workline_plugins.rough_sorter.contract import ROUGH_SORTER_PLUGIN_KEY
from src.workline_plugins.smt_sorting_inbound.constants import SMT_SORTING_INBOUND_PLUGIN_KEY
from src.workline_plugins.smt_sorting_inbound.plugin import SmtSortingInboundPlugin
from src.workline_runtime import plugin_manifest as manifest_contract


def _contract(name: str):
    return getattr(manifest_contract, name)


def _device_requirement(role: str = "ENTRY_SCANNER"):
    return _contract("DeviceRequirement")(
        role=role,
        min_count=1,
        max_count=1,
        hardware_capabilities=frozenset(),
    )


def _carrier(
    *,
    allowed_rack_kinds: tuple[str, ...],
    min_capacity: int = 1,
    max_capacity: int = 1,
):
    return _contract("RackPositionCarrierCapability")(
        allowed_rack_kinds=allowed_rack_kinds,
        min_capacity=min_capacity,
        max_capacity=max_capacity,
        allowed_slot_kinds=(),
    )


def _position(code: str, *, role: str, station_code: str, rack_kinds: tuple[str, ...]):
    return _contract("RackPosition")(
        code=code,
        role=role,
        station_code=station_code,
        carrier_capability=_carrier(allowed_rack_kinds=rack_kinds),
    )


def _node(kind, ref: str):
    return _contract("NodeRef")(kind=kind, ref=ref)


def _topology():
    NodeRefKind = _contract("NodeRefKind")
    FlowEdgeType = _contract("FlowEdgeType")
    return _contract("TopologySpec")(
        flow_edges=(
            _contract("FlowEdge")(
                from_node=_node(NodeRefKind.DEVICE_ROLE, "ENTRY_SCANNER"),
                to_node=_node(NodeRefKind.RACK_POSITION, "SOURCE_RACK"),
                type=FlowEdgeType.OPERATION,
            ),
        )
    )


def _event_binding():
    return _contract("EventBinding")(
        event="ENTRY_SCAN",
        source_device_roles=("ENTRY_SCANNER",),
        category=_contract("EventCategory").ENTRY_DEVICE,
        payload_schema_ref=None,
    )


def _resource_boundary(rack_position_code: str, *, rack_kind: str):
    return _contract("ResourceBoundary")(
        rack_position_code=rack_position_code,
        rack_kind=rack_kind,
        business_demand_type=f"{rack_kind}_DEMAND",
        wms_operation_type=f"SUPPLY_{rack_kind}",
        snapshot_kind=f"ACTIVE_{rack_kind}",
        lease_scope="STATION",
    )


def _manifest(*, rack_positions, resource_boundaries):
    return _contract("WorklinePluginManifest")(
        plugin_key="example_plugin",
        contract_version="pure-data.v1",
        devices=(_device_requirement(),),
        rack_positions=tuple(rack_positions),
        topology=_topology(),
        commands=(),
        events=(_event_binding(),),
        resource_boundaries=tuple(resource_boundaries),
    )


def test_resource_boundary_accepts_single_layer_and_five_layer_kinds() -> None:
    manifest = _manifest(
        rack_positions=(
            _position(
                "SOURCE_RACK",
                role="SOURCE",
                station_code="SOURCE_STATION",
                rack_kinds=("SINGLE_LAYER", "FIVE_LAYER"),
            ),
        ),
        resource_boundaries=(
            _resource_boundary("SOURCE_RACK", rack_kind="SINGLE_LAYER"),
            _resource_boundary("SOURCE_RACK", rack_kind="FIVE_LAYER"),
        ),
    )

    assert {boundary.rack_kind for boundary in manifest.resource_boundaries} == {"SINGLE_LAYER", "FIVE_LAYER"}


def test_resource_boundary_derives_station_from_position() -> None:
    manifest = _manifest(
        rack_positions=(
            _position(
                "SOURCE_RACK",
                role="SOURCE",
                station_code="SOURCE_STATION",
                rack_kinds=("SINGLE_LAYER",),
            ),
        ),
        resource_boundaries=(_resource_boundary("SOURCE_RACK", rack_kind="SINGLE_LAYER"),),
    )
    boundary = manifest.resource_boundaries[0]

    assert "station_code" not in {field.name for field in fields(boundary)}
    assert "station_role" not in {field.name for field in fields(boundary)}
    rack_position = {item.code: item for item in manifest.rack_positions}[boundary.rack_position_code]
    assert rack_position.station_code == "SOURCE_STATION"
    assert rack_position.role == "SOURCE"


def test_registered_plugins_declare_resource_boundaries_for_rack_operations() -> None:
    rack_plugin_keys = {ROUGH_SORTER_PLUGIN_KEY, SMT_SORTING_INBOUND_PLUGIN_KEY}
    manifests_by_key = {
        definition.plugin_key: definition.manifest
        for definition in list_workline_plugin_definitions()
        if definition.plugin_key in rack_plugin_keys
    }

    assert set(manifests_by_key) == rack_plugin_keys
    for plugin_key, manifest in manifests_by_key.items():
        assert manifest.resource_boundaries, f"{plugin_key} must declare resource boundaries"


def test_smt_manifest_declares_five_layer_resource_boundary() -> None:
    manifest = SmtSortingInboundPlugin.manifest

    assert any(boundary.rack_kind == "FIVE_LAYER" for boundary in manifest.resource_boundaries)
