"""插件 manifest 纯数据合同与拓扑完整性测试。"""

from dataclasses import fields
from types import SimpleNamespace

import pytest

from src.workline_plugins.rough_sorter.contract import (
    ACTION_MOVE_FORWARD,
    ACTION_MOVE_TO_NG,
    ACTION_PICK_AND_PUT,
    ACTION_PUT_TO_BIN,
    EVENT_ROUGH_SORTER_STORAGE_RETRY,
    EVENT_SCAN_COMPLETED,
    ROLE_CONVEYOR,
    ROLE_INPUT_ARM,
    ROLE_OUTPUT_ARM,
)
from src.workline_plugins.rough_sorter.plugin import (
    DEFAULT_NG_LOCATION,
    DEFAULT_PIPELINE_INPUT_LOCATION,
    DEFAULT_PIPELINE_OUTPUT_LOCATION,
    POSITION_SCAN_POINT,
    POSITION_WORK_SINGLE_LAYER,
)
from src.workline_plugins.smt_sorting_inbound.constants import (
    COMMAND_NG_PLACE,
    COMMAND_SOURCE_PICK,
    COMMAND_TARGET_PLACE,
    EVENT_SESSION_COMPLETE_REQUESTED,
    EVENT_SOURCE_PICK_REQUESTED,
    EVENT_WORKING_BIN_SCAN,
    ROLE_SORTING_SCAN_PLATFORM,
    ROLE_SORTING_SOURCE_ARM,
    ROLE_SORTING_TARGET_ARM,
    ROLE_SORTING_WORKSTATION,
)
from src.workline_runtime import plugin_manifest as manifest_contract
from src.workline_runtime.topology import WorklineTopologyView, validate_topology_manifest

EXPECTED_MANIFEST_FIELDS = (
    "plugin_key",
    "contract_version",
    "devices",
    "rack_positions",
    "topology",
    "commands",
    "events",
    "resource_boundaries",
)


def _contract(name: str):
    return getattr(manifest_contract, name)


def _device(
    device_id: int,
    *,
    code: str,
    role: str,
    upstream_device_id: int | None = None,
    capabilities_json: dict | None = None,
):
    return SimpleNamespace(
        id=device_id,
        device_code=code,
        device_role=role,
        role_index=device_id,
        sort_order=device_id,
        upstream_device_id=upstream_device_id,
        capabilities_json=capabilities_json or {},
    )


def _device_requirement(role: str = "ENTRY_SCANNER"):
    return _contract("DeviceRequirement")(
        role=role,
        min_count=1,
        max_count=1,
        hardware_capabilities=frozenset(),
    )


def _carrier(
    *,
    allowed_rack_kinds: tuple[str, ...] = ("SINGLE_LAYER",),
    min_capacity: int = 1,
    max_capacity: int = 1,
    allowed_slot_kinds: tuple[str, ...] = (),
):
    return _contract("RackPositionCarrierCapability")(
        allowed_rack_kinds=allowed_rack_kinds,
        min_capacity=min_capacity,
        max_capacity=max_capacity,
        allowed_slot_kinds=allowed_slot_kinds,
    )


def _position(
    code: str = "ENTRY_POSITION",
    *,
    role: str = "ENTRY",
    station_code: str = "ENTRY_STATION",
    allowed_rack_kinds: tuple[str, ...] = ("SINGLE_LAYER",),
):
    return _contract("RackPosition")(
        code=code,
        role=role,
        station_code=station_code,
        carrier_capability=_carrier(allowed_rack_kinds=allowed_rack_kinds),
    )


def _node(kind, ref: str):
    return _contract("NodeRef")(kind=kind, ref=ref)


def _flow_edge(*, from_node=None, to_node=None, edge_type=None):
    NodeRefKind = _contract("NodeRefKind")
    FlowEdgeType = _contract("FlowEdgeType")
    return _contract("FlowEdge")(
        from_node=from_node or _node(NodeRefKind.DEVICE_ROLE, "ENTRY_SCANNER"),
        to_node=to_node or _node(NodeRefKind.RACK_POSITION, "ENTRY_POSITION"),
        type=edge_type or FlowEdgeType.OPERATION,
    )


def _topology(*, flow_edges=None):
    return _contract("TopologySpec")(flow_edges=tuple(flow_edges or (_flow_edge(),)))


def _event_binding(
    event: str = "TOTE_ARRIVED",
    *,
    source_device_roles: tuple[str, ...] = ("ENTRY_SCANNER",),
    category=None,
):
    EventCategory = _contract("EventCategory")
    return _contract("EventBinding")(
        event=event,
        source_device_roles=source_device_roles,
        category=category or EventCategory.ENTRY_DEVICE,
        payload_schema_ref=None,
    )


def _rack_position_arg_source(
    *,
    kind=None,
    path: str = "data.position_code",
    fallback_rack_position_ref: str | None = None,
):
    RackPositionArgSourceKind = _contract("RackPositionArgSourceKind")
    return _contract("RackPositionArgSource")(
        kind=kind or RackPositionArgSourceKind.EVENT_PAYLOAD,
        path=path,
        fallback_rack_position_ref=fallback_rack_position_ref,
    )


def _rack_position_arg(
    *,
    name: str = "target_position",
    role=None,
    required: bool = True,
    rack_position_ref: str | None = "ENTRY_POSITION",
    source=None,
):
    RackPositionArgRole = _contract("RackPositionArgRole")
    return _contract("RackPositionArg")(
        name=name,
        role=role or RackPositionArgRole.TARGET,
        required=required,
        rack_position_ref=rack_position_ref,
        source=source,
    )


def _command_result_binding(
    *,
    result: str = "SUCCESS",
    event: str = "TOTE_WEIGHED",
    category=None,
):
    EventCategory = _contract("EventCategory")
    return _contract("CommandResultBinding")(
        result=result,
        event=event,
        category=category or EventCategory.COMMAND_RESULT,
        classification="success",
        terminal=False,
        next_event=None,
    )


def _command_binding(
    command: str = "WEIGH_TOTE",
    *,
    target_device_role: str = "ENTRY_SCANNER",
    rack_position_args=None,
    result_bindings=None,
):
    return _contract("CommandBinding")(
        command=command,
        target_device_role=target_device_role,
        rack_position_args=tuple(rack_position_args or (_rack_position_arg(),)),
        payload_schema_ref=None,
        result_bindings=tuple(result_bindings or (_command_result_binding(),)),
    )


def _resource_boundary(
    rack_position_code: str = "ENTRY_POSITION",
    *,
    rack_kind: str = "SINGLE_LAYER",
):
    return _contract("ResourceBoundary")(
        rack_position_code=rack_position_code,
        rack_kind=rack_kind,
        business_demand_type="ENTRY_RACK_DEMAND",
        wms_operation_type="SUPPLY_ENTRY_RACK",
        snapshot_kind="ACTIVE_ENTRY_RACK",
        lease_scope="STATION",
    )


def _manifest_kwargs(**overrides):
    values = {
        "plugin_key": "example_plugin",
        "contract_version": "pure-data.v1",
        "devices": (_device_requirement(),),
        "rack_positions": (_position(),),
        "topology": _topology(),
        "commands": (_command_binding(),),
        "events": (_event_binding(),),
        "resource_boundaries": (_resource_boundary(),),
    }
    values.update(overrides)
    return values


def _manifest(**overrides):
    return _contract("WorklinePluginManifest")(**_manifest_kwargs(**overrides))


def test_manifest_accepts_complete_pure_data_contract() -> None:
    manifest = _manifest()

    assert tuple(field.name for field in fields(manifest)) == EXPECTED_MANIFEST_FIELDS
    for field_name in EXPECTED_MANIFEST_FIELDS:
        value = getattr(manifest, field_name)
        assert not callable(value)
        assert not isinstance(value, type)


def test_manifest_rejects_missing_required_topology() -> None:
    values = _manifest_kwargs()
    values.pop("topology")

    with pytest.raises(ValueError, match="topology"):
        _contract("WorklinePluginManifest")(**values)
    with pytest.raises(ValueError, match="topology"):
        _manifest(topology=None)


@pytest.mark.parametrize(
    "bad_node",
    [
        lambda: _node(_contract("NodeRefKind").DEVICE_ROLE, "UNKNOWN_ROLE"),
        lambda: _node(_contract("NodeRefKind").RACK_POSITION, "UNKNOWN_POSITION"),
    ],
)
def test_manifest_rejects_unknown_topology_node_ref(bad_node) -> None:
    edge = _flow_edge(to_node=bad_node())

    with pytest.raises(ValueError, match=r"UNKNOWN|NodeRef|ref"):
        _manifest(topology=_topology(flow_edges=(edge,)))


def test_manifest_rejects_illegal_flow_edge_type() -> None:
    with pytest.raises(ValueError, match=r"MATERIAL_FLOW|OPERATION|type"):
        _manifest(topology=_topology(flow_edges=(_flow_edge(edge_type="SIDE_EFFECT"),)))


@pytest.mark.parametrize(
    "edge",
    [
        lambda: _flow_edge(
            from_node=_node(_contract("NodeRefKind").DEVICE_ROLE, "ENTRY_SCANNER"),
            to_node=_node(_contract("NodeRefKind").RACK_POSITION, "ENTRY_POSITION"),
            edge_type=_contract("FlowEdgeType").MATERIAL_FLOW,
        ),
        lambda: _flow_edge(
            from_node=_node(_contract("NodeRefKind").RACK_POSITION, "ENTRY_POSITION"),
            to_node=_node(_contract("NodeRefKind").DEVICE_ROLE, "ENTRY_SCANNER"),
            edge_type=_contract("FlowEdgeType").MATERIAL_FLOW,
        ),
    ],
)
def test_material_flow_edges_must_connect_rack_positions(edge) -> None:
    with pytest.raises(ValueError, match=r"MATERIAL_FLOW|RACK_POSITION"):
        _manifest(topology=_topology(flow_edges=(edge(),)))


def test_event_binding_rejects_unknown_source_role() -> None:
    event = _event_binding(source_device_roles=("UNKNOWN_ROLE",))

    with pytest.raises(ValueError, match=r"UNKNOWN_ROLE|source"):
        _manifest(events=(event,))


def test_event_binding_rejects_mapping_source_roles() -> None:
    EventCategory = _contract("EventCategory")

    with pytest.raises(TypeError, match=r"source_device_roles|string collection"):
        _contract("EventBinding")(
            event="TOTE_ARRIVED",
            source_device_roles={"ENTRY_SCANNER": "bad"},
            category=EventCategory.ENTRY_DEVICE,
            payload_schema_ref=None,
        )


def test_event_binding_entry_device_is_only_entry_filter_source() -> None:
    EventCategory = _contract("EventCategory")
    manifest = _manifest(
        events=(
            _event_binding("ENTRY_SCAN", category=EventCategory.ENTRY_DEVICE),
            _event_binding("INTERNAL_RETRY", category=EventCategory.INTERNAL),
            _event_binding("COMMAND_DONE", category=EventCategory.COMMAND_RESULT),
        )
    )

    assert {event.event for event in manifest.events if event.category == EventCategory.ENTRY_DEVICE} == {"ENTRY_SCAN"}


def test_event_binding_accepts_operator_and_safety_categories() -> None:
    EventCategory = _contract("EventCategory")
    manifest = _manifest(
        events=(
            _event_binding("OPERATOR_OVERRIDE", category=EventCategory.OPERATOR),
            _event_binding("SAFETY_RESET", category="SAFETY"),
        )
    )

    assert [event.category for event in manifest.events] == [EventCategory.OPERATOR, EventCategory.SAFETY]


def test_command_binding_rejects_unknown_target_role() -> None:
    command = _command_binding(target_device_role="UNKNOWN_ROLE")

    with pytest.raises(ValueError, match=r"UNKNOWN_ROLE|target"):
        _manifest(commands=(command,))


def test_command_result_binding_requires_command_result_category() -> None:
    EventCategory = _contract("EventCategory")

    with pytest.raises(ValueError, match=r"COMMAND_RESULT|category"):
        command = _command_binding(result_bindings=(_command_result_binding(category=EventCategory.INTERNAL),))
        _manifest(commands=(command,))


def test_rack_position_arg_ref_and_source_are_mutually_exclusive() -> None:
    source = _rack_position_arg_source()

    with pytest.raises(ValueError, match=r"rack_position_ref|source"):
        _rack_position_arg(rack_position_ref="ENTRY_POSITION", source=source)
    with pytest.raises(ValueError, match=r"rack_position_ref|source|required"):
        _rack_position_arg(required=True, rack_position_ref=None, source=None)

    optional_arg = _rack_position_arg(required=False, rack_position_ref=None, source=None)
    assert optional_arg.rack_position_ref is None
    assert optional_arg.source is None


def test_rack_position_arg_source_rejects_static_kind() -> None:
    RackPositionArgSourceKind = _contract("RackPositionArgSourceKind")

    assert "STATIC" not in {kind.value for kind in RackPositionArgSourceKind}
    with pytest.raises(ValueError, match="STATIC"):
        _rack_position_arg_source(kind="STATIC")


def test_rack_position_carrier_capability_validates_capacity_and_rack_kind() -> None:
    with pytest.raises(ValueError, match=r"capacity|min|max"):
        _carrier(min_capacity=10, max_capacity=1)
    with pytest.raises(ValueError, match=r"rack|UNKNOWN_RACK"):
        _carrier(allowed_rack_kinds=("UNKNOWN_RACK",))


def test_rack_position_carrier_capability_rejects_mapping_rack_kinds() -> None:
    with pytest.raises(TypeError, match=r"allowed_rack_kinds|string collection"):
        _carrier(allowed_rack_kinds={"SINGLE_LAYER": "bad"})


def test_resource_boundary_references_rack_position_and_omits_station_fields() -> None:
    with pytest.raises(ValueError, match=r"rack_position_code|UNKNOWN_POSITION"):
        _manifest(resource_boundaries=(_resource_boundary("UNKNOWN_POSITION"),))

    manifest = _manifest()
    boundary = manifest.resource_boundaries[0]
    assert "station_code" not in {field.name for field in fields(boundary)}
    assert "station_role" not in {field.name for field in fields(boundary)}

    rack_positions_by_code = {rack_position.code: rack_position for rack_position in manifest.rack_positions}
    assert rack_positions_by_code[boundary.rack_position_code].station_code == "ENTRY_STATION"


def test_resource_boundary_rack_kind_must_match_position_carrier_capability() -> None:
    with pytest.raises(ValueError, match=r"rack_kind|FIVE_LAYER|allowed_rack_kinds"):
        _manifest(
            rack_positions=(_position(allowed_rack_kinds=("SINGLE_LAYER",)),),
            resource_boundaries=(_resource_boundary(rack_kind="FIVE_LAYER"),),
        )


def test_topology_view_derives_roles_and_upstream_downstream() -> None:
    scanner = _device(1, code="SCAN01", role="ENTRY_SCANNER")
    scale = _device(2, code="SCALE01", role="WEIGH_SCALE", upstream_device_id=1)
    conveyor = _device(3, code="CONV01", role="DIVERT_CONVEYOR", upstream_device_id=2)

    topology = WorklineTopologyView.from_devices([conveyor, scale, scanner])

    assert topology.devices_for_role("ENTRY_SCANNER")[0].device_code == "SCAN01"
    assert topology.device_by_id[2].upstream_device_id == 1
    assert topology.upstream_by_device_id[3] == 2
    assert topology.downstream_by_device_id[1] == (2,)
    assert topology.downstream_by_device_id[2] == (3,)


def _topology_manifest_for_validation():
    return _manifest(
        devices=(
            _contract("DeviceRequirement")(
                role="ENTRY_SCANNER",
                min_count=1,
                max_count=1,
                hardware_capabilities=("barcode_scan",),
            ),
        ),
        events=(
            _event_binding(
                "TOTE_ARRIVED",
                source_device_roles=("ENTRY_SCANNER",),
            ),
        ),
        commands=(
            _command_binding(
                "WEIGH_TOTE",
                target_device_role="ENTRY_SCANNER",
            ),
        ),
    )


def test_validate_topology_manifest_reads_devices_events_commands_happy_path() -> None:
    topology = WorklineTopologyView.from_devices(
        [
            _device(
                1,
                code="SCAN01",
                role="ENTRY_SCANNER",
                capabilities_json={
                    "capabilities": ["barcode_scan"],
                    "supports_event_types": ["TOTE_ARRIVED"],
                    "supports_command_types": ["WEIGH_TOTE"],
                },
            )
        ]
    )

    validate_topology_manifest(_topology_manifest_for_validation(), topology)


def test_validate_topology_manifest_only_requires_entry_device_event_capability() -> None:
    EventCategory = _contract("EventCategory")
    topology = WorklineTopologyView.from_devices(
        [
            _device(
                1,
                code="SCAN01",
                role="ENTRY_SCANNER",
                capabilities_json={
                    "capabilities": ["barcode_scan"],
                    "supports_event_types": ["TOTE_ARRIVED"],
                    "supports_command_types": ["WEIGH_TOTE"],
                },
            )
        ]
    )
    manifest = _topology_manifest_for_validation()
    object.__setattr__(
        manifest,
        "events",
        (
            *manifest.events,
            _event_binding("INTERNAL_RETRY", category=EventCategory.INTERNAL),
            _event_binding("COMMAND_DONE", category=EventCategory.COMMAND_RESULT),
        ),
    )

    validate_topology_manifest(manifest, topology)


def test_validate_topology_manifest_rejects_missing_hardware_capability() -> None:
    topology = WorklineTopologyView.from_devices(
        [
            _device(
                1,
                code="SCAN01",
                role="ENTRY_SCANNER",
                capabilities_json={
                    "capabilities": [],
                    "supports_event_types": ["TOTE_ARRIVED"],
                    "supports_command_types": ["WEIGH_TOTE"],
                },
            )
        ]
    )

    with pytest.raises(ValueError, match="barcode_scan"):
        validate_topology_manifest(_topology_manifest_for_validation(), topology)


@pytest.mark.parametrize(
    ("capabilities_json", "message"),
    [
        (
            {
                "capabilities": ["barcode_scan"],
                "supports_event_types": ["OTHER_EVENT"],
                "supports_command_types": ["WEIGH_TOTE"],
            },
            "事件 TOTE_ARRIVED",
        ),
        (
            {
                "capabilities": ["barcode_scan"],
                "supports_event_types": ["TOTE_ARRIVED"],
                "supports_command_types": ["OTHER_COMMAND"],
            },
            "命令 WEIGH_TOTE",
        ),
    ],
)
def test_validate_topology_manifest_rejects_unsupported_event_or_command(
    capabilities_json: dict[str, list[str]],
    message: str,
) -> None:
    topology = WorklineTopologyView.from_devices(
        [_device(1, code="SCAN01", role="ENTRY_SCANNER", capabilities_json=capabilities_json)]
    )

    with pytest.raises(ValueError, match=message):
        validate_topology_manifest(_topology_manifest_for_validation(), topology)


def _assert_real_manifest_surface(manifest) -> None:
    assert tuple(field.name for field in fields(manifest)) == EXPECTED_MANIFEST_FIELDS
    assert len(fields(manifest)) == 8
    for field_name in EXPECTED_MANIFEST_FIELDS:
        value = getattr(manifest, field_name)
        assert not callable(value)
        assert not isinstance(value, type)

    old_manifest_fields = {
        "supported_" + "events",
        "event_" + "source_roles",
        "supported_" + "commands",
        "command_" + "target_roles",
        "single_" + "layer_boundaries",
        "resource_" + "kinds",
        "requires_" + "single_layer_boundary",
    }
    assert old_manifest_fields.isdisjoint({field.name for field in fields(manifest)})
    assert all(not hasattr(manifest, old_field) for old_field in old_manifest_fields)


def _assert_topology_uses_node_refs(manifest) -> None:
    NodeRef = _contract("NodeRef")

    assert manifest.topology.flow_edges
    for edge in manifest.topology.flow_edges:
        assert isinstance(edge.from_node, NodeRef)
        assert isinstance(edge.to_node, NodeRef)


def _assert_material_flow_edges_are_rack_position_to_rack_position(manifest) -> None:
    FlowEdgeType = _contract("FlowEdgeType")
    NodeRefKind = _contract("NodeRefKind")

    material_flow_edges = [edge for edge in manifest.topology.flow_edges if edge.type == FlowEdgeType.MATERIAL_FLOW]
    assert material_flow_edges
    assert all(
        edge.from_node.kind == NodeRefKind.RACK_POSITION and edge.to_node.kind == NodeRefKind.RACK_POSITION
        for edge in material_flow_edges
    )


def _events_by_name(manifest):
    return {event.event: event for event in manifest.events}


def _commands_by_name(manifest):
    return {command.command: command for command in manifest.commands}


def _boundaries_by_rack_position(manifest):
    return {boundary.rack_position_code: boundary for boundary in manifest.resource_boundaries}


def test_rough_sorter_real_manifest_declares_new_contract_shape() -> None:
    from src.workline_plugins.rough_sorter.plugin import RoughSorterPlugin

    manifest = RoughSorterPlugin.manifest
    EventCategory = _contract("EventCategory")
    RackPositionArgRole = _contract("RackPositionArgRole")
    RackPositionArgSourceKind = _contract("RackPositionArgSourceKind")
    FlowEdgeType = _contract("FlowEdgeType")
    NodeRefKind = _contract("NodeRefKind")

    _assert_real_manifest_surface(manifest)
    _assert_topology_uses_node_refs(manifest)
    assert {device.role for device in manifest.devices} == {ROLE_INPUT_ARM, ROLE_CONVEYOR, ROLE_OUTPUT_ARM}
    assert {rack_position.code for rack_position in manifest.rack_positions} == {POSITION_WORK_SINGLE_LAYER}
    assert manifest.rack_positions[0].role == "CLASSIFIER_WORK"

    events = _events_by_name(manifest)
    assert events[EVENT_SCAN_COMPLETED].source_device_roles == (ROLE_INPUT_ARM,)
    assert events[EVENT_SCAN_COMPLETED].category == EventCategory.ENTRY_DEVICE
    assert events[EVENT_ROUGH_SORTER_STORAGE_RETRY].source_device_roles == (ROLE_OUTPUT_ARM,)
    assert events[EVENT_ROUGH_SORTER_STORAGE_RETRY].category == EventCategory.INTERNAL

    commands = _commands_by_name(manifest)
    assert commands[ACTION_PICK_AND_PUT].target_device_role == ROLE_INPUT_ARM
    assert commands[ACTION_MOVE_FORWARD].target_device_role == ROLE_CONVEYOR
    assert commands[ACTION_PUT_TO_BIN].target_device_role == ROLE_OUTPUT_ARM
    assert commands[ACTION_MOVE_TO_NG].target_device_role == ROLE_INPUT_ARM
    for command in commands.values():
        assert command.result_bindings
    assert commands[ACTION_PICK_AND_PUT].rack_position_args == ()
    assert commands[ACTION_MOVE_FORWARD].rack_position_args == ()
    assert commands[ACTION_MOVE_TO_NG].rack_position_args == ()

    assert len(commands[ACTION_PUT_TO_BIN].rack_position_args) == 1
    bin_location = commands[ACTION_PUT_TO_BIN].rack_position_args[0]
    assert bin_location.name == "bin_location"
    assert bin_location.role == RackPositionArgRole.TARGET
    assert bin_location.rack_position_ref is None
    assert bin_location.source is not None
    assert bin_location.source.kind == RackPositionArgSourceKind.RESOURCE_OVERLAY
    assert bin_location.source.path == "target_bin_location.bin_cell_location"
    assert bin_location.source.fallback_rack_position_ref == POSITION_WORK_SINGLE_LAYER

    for edge in manifest.topology.flow_edges:
        if NodeRefKind.DEVICE_ROLE in {edge.from_node.kind, edge.to_node.kind}:
            assert edge.type == FlowEdgeType.OPERATION

    boundary = _boundaries_by_rack_position(manifest)[POSITION_WORK_SINGLE_LAYER]
    assert boundary.rack_kind == "SINGLE_LAYER"
    assert boundary.business_demand_type == "ROUGH_SORTER_BIN_ALLOCATION"
    assert boundary.snapshot_kind == "ACTIVE_CLASSIFIER_BIN_RACK"


def test_rough_sorter_internal_physical_points_do_not_enter_manifest_rack_positions_or_rack_position_args() -> None:
    from src.workline_plugins.rough_sorter.plugin import RoughSorterPlugin

    manifest = RoughSorterPlugin.manifest
    internal_physical_points = {
        POSITION_SCAN_POINT,
        DEFAULT_PIPELINE_INPUT_LOCATION,
        DEFAULT_PIPELINE_OUTPUT_LOCATION,
        DEFAULT_NG_LOCATION,
    }
    rack_position_codes = {rack_position.code for rack_position in manifest.rack_positions}
    rack_position_arg_refs: set[str] = set()

    for command in manifest.commands:
        for rack_position_arg in command.rack_position_args:
            if rack_position_arg.rack_position_ref is not None:
                rack_position_arg_refs.add(rack_position_arg.rack_position_ref)
            if rack_position_arg.source is not None and rack_position_arg.source.fallback_rack_position_ref is not None:
                rack_position_arg_refs.add(rack_position_arg.source.fallback_rack_position_ref)

    assert internal_physical_points.isdisjoint(rack_position_codes)
    assert internal_physical_points.isdisjoint(rack_position_arg_refs)


def test_smt_sorting_inbound_real_manifest_declares_new_contract_shape() -> None:
    from src.workline_plugins.smt_sorting_inbound.plugin import SmtSortingInboundPlugin

    manifest = SmtSortingInboundPlugin.manifest
    EventCategory = _contract("EventCategory")
    FlowEdgeType = _contract("FlowEdgeType")
    NodeRefKind = _contract("NodeRefKind")

    _assert_real_manifest_surface(manifest)
    _assert_topology_uses_node_refs(manifest)
    device_roles = {device.role for device in manifest.devices}
    rack_position_codes = {rack_position.code for rack_position in manifest.rack_positions}
    business_demand_types = {boundary.business_demand_type for boundary in manifest.resource_boundaries}

    assert device_roles == {
        ROLE_SORTING_SOURCE_ARM,
        ROLE_SORTING_TARGET_ARM,
        ROLE_SORTING_SCAN_PLATFORM,
        ROLE_SORTING_WORKSTATION,
    }
    assert "SORTING_NG_STATION" not in device_roles
    assert rack_position_codes == {"SOURCE_STATION_A", "SOURCE_STATION_B", "TARGET_STATION"}
    assert "NG_STATION" not in rack_position_codes
    assert "WORKSTATION" not in rack_position_codes

    events = _events_by_name(manifest)
    assert events[EVENT_WORKING_BIN_SCAN].source_device_roles == (ROLE_SORTING_SCAN_PLATFORM,)
    assert events[EVENT_WORKING_BIN_SCAN].category == EventCategory.ENTRY_DEVICE
    assert events[EVENT_SESSION_COMPLETE_REQUESTED].source_device_roles == (ROLE_SORTING_WORKSTATION,)
    assert events[EVENT_SESSION_COMPLETE_REQUESTED].category == EventCategory.ENTRY_DEVICE
    assert EVENT_SOURCE_PICK_REQUESTED not in events

    commands = _commands_by_name(manifest)
    assert commands[COMMAND_SOURCE_PICK].target_device_role == ROLE_SORTING_SOURCE_ARM
    assert commands[COMMAND_TARGET_PLACE].target_device_role == ROLE_SORTING_TARGET_ARM
    assert commands[COMMAND_NG_PLACE].target_device_role == ROLE_SORTING_TARGET_ARM
    assert commands[COMMAND_NG_PLACE].rack_position_args == ()
    target_place_target = commands[COMMAND_TARGET_PLACE].rack_position_args[0]
    assert target_place_target.rack_position_ref == "TARGET_STATION"
    assert target_place_target.source is None
    for command in commands.values():
        assert command.result_bindings
    _assert_material_flow_edges_are_rack_position_to_rack_position(manifest)
    assert all(
        edge.type == FlowEdgeType.OPERATION
        for edge in manifest.topology.flow_edges
        if NodeRefKind.DEVICE_ROLE in {edge.from_node.kind, edge.to_node.kind}
    )

    boundaries = _boundaries_by_rack_position(manifest)
    assert boundaries["SOURCE_STATION_A"].business_demand_type == "SORTING_INBOUND_SOURCE"
    assert boundaries["SOURCE_STATION_A"].rack_kind == "SINGLE_LAYER"
    assert boundaries["TARGET_STATION"].business_demand_type == "SORTING_INBOUND_TARGET"
    assert boundaries["TARGET_STATION"].rack_kind == "FIVE_LAYER"
    assert business_demand_types == {"SORTING_INBOUND_SOURCE", "SORTING_INBOUND_TARGET"}
    assert "SORTING_INBOUND_NG" not in business_demand_types
    assert "SORTING_INBOUND_WORK" not in business_demand_types


def test_smt_sorting_inbound_topology_connects_declared_source_stations() -> None:
    from src.workline_plugins.smt_sorting_inbound.plugin import SmtSortingInboundPlugin

    manifest = SmtSortingInboundPlugin.manifest
    FlowEdgeType = _contract("FlowEdgeType")
    NodeRefKind = _contract("NodeRefKind")
    source_station_codes = {
        rack_position.code for rack_position in manifest.rack_positions if rack_position.role == "SOURCE"
    }

    operation_targets = {
        edge.to_node.ref
        for edge in manifest.topology.flow_edges
        if edge.type == FlowEdgeType.OPERATION
        and edge.from_node.kind == NodeRefKind.DEVICE_ROLE
        and edge.to_node.kind == NodeRefKind.RACK_POSITION
    }
    material_flow_sources = {
        edge.from_node.ref
        for edge in manifest.topology.flow_edges
        if edge.type == FlowEdgeType.MATERIAL_FLOW
        and edge.from_node.kind == NodeRefKind.RACK_POSITION
        and edge.to_node.kind == NodeRefKind.RACK_POSITION
    }

    assert source_station_codes <= operation_targets
    assert source_station_codes <= material_flow_sources


def test_smt_sorting_inbound_material_flow_edges_are_rack_position_to_rack_position() -> None:
    from src.workline_plugins.smt_sorting_inbound.plugin import SmtSortingInboundPlugin

    _assert_material_flow_edges_are_rack_position_to_rack_position(SmtSortingInboundPlugin.manifest)
