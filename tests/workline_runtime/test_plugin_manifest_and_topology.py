"""插件 manifest 纯数据合同与拓扑完整性测试。"""

from dataclasses import fields
from types import SimpleNamespace

import pytest

from src.workline_runtime import plugin_manifest as manifest_contract
from src.workline_runtime.topology import WorklineTopologyView, validate_topology_manifest

EXPECTED_MANIFEST_FIELDS = (
    "plugin_key",
    "contract_version",
    "devices",
    "positions",
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
    return _contract("PositionCarrierCapability")(
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
):
    return _contract("Position")(
        code=code,
        role=role,
        station_code=station_code,
        carrier_capability=_carrier(),
    )


def _node(kind, ref: str):
    return _contract("NodeRef")(kind=kind, ref=ref)


def _flow_edge(*, from_node=None, to_node=None, edge_type=None):
    NodeRefKind = _contract("NodeRefKind")
    FlowEdgeType = _contract("FlowEdgeType")
    return _contract("FlowEdge")(
        from_node=from_node or _node(NodeRefKind.DEVICE_ROLE, "ENTRY_SCANNER"),
        to_node=to_node or _node(NodeRefKind.POSITION, "ENTRY_POSITION"),
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


def _position_arg_source(*, kind=None, path: str = "data.position_code", fallback_position_ref: str | None = None):
    PositionArgSourceKind = _contract("PositionArgSourceKind")
    return _contract("PositionArgSource")(
        kind=kind or PositionArgSourceKind.EVENT_PAYLOAD,
        path=path,
        fallback_position_ref=fallback_position_ref,
    )


def _position_arg(
    *,
    name: str = "target_position",
    role=None,
    required: bool = True,
    position_ref: str | None = "ENTRY_POSITION",
    source=None,
):
    PositionArgRole = _contract("PositionArgRole")
    return _contract("PositionArg")(
        name=name,
        role=role or PositionArgRole.TARGET,
        required=required,
        position_ref=position_ref,
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
    position_args=None,
    result_bindings=None,
):
    return _contract("CommandBinding")(
        command=command,
        target_device_role=target_device_role,
        position_args=tuple(position_args or (_position_arg(),)),
        payload_schema_ref=None,
        result_bindings=tuple(result_bindings or (_command_result_binding(),)),
    )


def _resource_boundary(
    position_code: str = "ENTRY_POSITION",
    *,
    rack_kind: str = "SINGLE_LAYER",
):
    return _contract("ResourceBoundary")(
        position_code=position_code,
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
        "positions": (_position(),),
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
        lambda: _node(_contract("NodeRefKind").POSITION, "UNKNOWN_POSITION"),
    ],
)
def test_manifest_rejects_unknown_topology_node_ref(bad_node) -> None:
    edge = _flow_edge(to_node=bad_node())

    with pytest.raises(ValueError, match=r"UNKNOWN|NodeRef|ref"):
        _manifest(topology=_topology(flow_edges=(edge,)))


def test_manifest_rejects_illegal_flow_edge_type() -> None:
    with pytest.raises(ValueError, match=r"MATERIAL_FLOW|OPERATION|type"):
        _manifest(topology=_topology(flow_edges=(_flow_edge(edge_type="SIDE_EFFECT"),)))


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


def test_command_binding_rejects_unknown_target_role() -> None:
    command = _command_binding(target_device_role="UNKNOWN_ROLE")

    with pytest.raises(ValueError, match=r"UNKNOWN_ROLE|target"):
        _manifest(commands=(command,))


def test_command_result_binding_requires_command_result_category() -> None:
    EventCategory = _contract("EventCategory")

    with pytest.raises(ValueError, match=r"COMMAND_RESULT|category"):
        command = _command_binding(result_bindings=(_command_result_binding(category=EventCategory.INTERNAL),))
        _manifest(commands=(command,))


def test_position_arg_position_ref_and_source_are_mutually_exclusive() -> None:
    source = _position_arg_source()

    with pytest.raises(ValueError, match=r"position_ref|source"):
        _position_arg(position_ref="ENTRY_POSITION", source=source)
    with pytest.raises(ValueError, match=r"position_ref|source|required"):
        _position_arg(required=True, position_ref=None, source=None)

    optional_arg = _position_arg(required=False, position_ref=None, source=None)
    assert optional_arg.position_ref is None
    assert optional_arg.source is None


def test_position_arg_source_rejects_static_kind() -> None:
    PositionArgSourceKind = _contract("PositionArgSourceKind")

    assert "STATIC" not in {kind.value for kind in PositionArgSourceKind}
    with pytest.raises(ValueError, match="STATIC"):
        _position_arg_source(kind="STATIC")


def test_position_carrier_capability_validates_capacity_and_rack_kind() -> None:
    with pytest.raises(ValueError, match=r"capacity|min|max"):
        _carrier(min_capacity=10, max_capacity=1)
    with pytest.raises(ValueError, match=r"rack|UNKNOWN_RACK"):
        _carrier(allowed_rack_kinds=("UNKNOWN_RACK",))


def test_position_carrier_capability_rejects_mapping_rack_kinds() -> None:
    with pytest.raises(TypeError, match=r"allowed_rack_kinds|string collection"):
        _carrier(allowed_rack_kinds={"SINGLE_LAYER": "bad"})


def test_resource_boundary_references_position_and_omits_station_fields() -> None:
    with pytest.raises(ValueError, match=r"position_code|UNKNOWN_POSITION"):
        _manifest(resource_boundaries=(_resource_boundary("UNKNOWN_POSITION"),))

    manifest = _manifest()
    boundary = manifest.resource_boundaries[0]
    assert "station_code" not in {field.name for field in fields(boundary)}
    assert "station_role" not in {field.name for field in fields(boundary)}

    positions_by_code = {position.code: position for position in manifest.positions}
    assert positions_by_code[boundary.position_code].station_code == "ENTRY_STATION"


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
