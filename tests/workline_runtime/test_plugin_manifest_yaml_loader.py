"""插件 manifest YAML authoring 合同测试。"""

from pathlib import Path

import pytest

from src.workline_runtime import plugin_manifest as manifest_contract
from src.workline_runtime.plugin_manifest import (
    CommandBinding,
    EventCategory,
    FlowEdgeType,
    NodeRefKind,
    PipelineQueue,
    SessionSubject,
    StateMachine,
    StateMachineOwner,
    StateMachineSubject,
    StateMachineTransition,
    WorklinePluginManifest,
)

MATERIAL_UNIT_STATUS_TRANSITIONS = [
    {"from": "IN_TRANSIT", "to": ["STORED", "COMPLETED", "NG", "RECONCILING"]},
    {"from": "STORED", "to": ["IN_TRANSIT", "NG", "RECONCILING"]},
    {"from": "RECONCILING", "to": ["IN_TRANSIT", "STORED", "COMPLETED", "NG"]},
    {"from": "NG", "to": []},
    {"from": "COMPLETED", "to": []},
]


def _material_unit_status_transitions() -> list[dict]:
    return [{"from": item["from"], "to": list(item["to"])} for item in MATERIAL_UNIT_STATUS_TRANSITIONS]


def _material_unit_session_subject() -> dict:
    return {
        "type": "MATERIAL_UNIT",
        "physical_form": "REEL",
        "identity_sources": ["PkgID", "material_identity_key"],
    }


def _material_unit_state_machine() -> dict:
    return {
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
        "transitions": _material_unit_status_transitions(),
    }


def _pipeline_queue() -> dict:
    return {
        "code": "WORKSTATION_ACTIVE",
        "role": "WORKSTATION",
        "capacity": 1,
        "order_policy": "FIFO",
    }


def _manifest_yaml_dict(**overrides):
    values = {
        "plugin_key": "yaml_plugin",
        "contract_version": "yaml.v1",
        "session_subject": _material_unit_session_subject(),
        "state_machines": [_material_unit_state_machine()],
        "pipeline_queues": [_pipeline_queue()],
        "device_roles": {
            "ENTRY_SCANNER": {
                "min_count": 1,
                "max_count": 1,
                "hardware_capabilities": ["barcode_scan"],
                "commands": ["SCAN_TOTE"],
                "events": [
                    {"event": "TOTE_ARRIVED", "category": "ENTRY_DEVICE"},
                ],
            },
            "EXIT_ARM": {
                "commands": ["PUT_TOTE"],
                "events": [{"event": "TOTE_PLACED", "category": "ENTRY_DEVICE"}],
            },
        },
        "rack_positions": [
            {
                "code": "ENTRY_POSITION",
                "role": "ENTRY",
                "station_code": "ENTRY_POSITION",
                "carrier_capability": {
                    "allowed_rack_kinds": ["SINGLE_LAYER"],
                    "allowed_slot_kinds": [],
                    "min_capacity": 1,
                    "max_capacity": 1,
                },
            },
            {
                "code": "EXIT_POSITION",
                "role": "EXIT",
                "station_code": "EXIT_POSITION",
                "carrier_capability": {
                    "allowed_rack_kinds": ["SINGLE_LAYER"],
                    "min_capacity": 1,
                    "max_capacity": 1,
                },
            },
        ],
        "topology": {
            "flow_edges": [
                {
                    "from": {"kind": "RACK_POSITION", "ref": "ENTRY_POSITION"},
                    "to": {"kind": "DEVICE_ROLE", "ref": "ENTRY_SCANNER"},
                    "type": "OPERATION",
                },
                {
                    "from": {"kind": "DEVICE_ROLE", "ref": "ENTRY_SCANNER"},
                    "to": {"kind": "DEVICE_ROLE", "ref": "EXIT_ARM"},
                    "type": "OPERATION",
                },
                {
                    "from": {"kind": "DEVICE_ROLE", "ref": "EXIT_ARM"},
                    "to": {"kind": "RACK_POSITION", "ref": "EXIT_POSITION"},
                    "type": "OPERATION",
                },
            ]
        },
        "resource_boundaries": [
            {
                "rack_position_code": "ENTRY_POSITION",
                "rack_kind": "SINGLE_LAYER",
                "business_demand_type": "ENTRY_DEMAND",
                "wms_operation_type": "SUPPLY_ENTRY_RACK",
                "snapshot_kind": "ACTIVE_ENTRY_RACK",
                "lease_scope": "STATION",
            }
        ],
    }
    values.update(overrides)
    return values


def _legacy_manifest_yaml_dict(**overrides):
    values = _manifest_yaml_dict(**overrides)
    values.pop("session_subject")
    values.pop("state_machines")
    values.pop("pipeline_queues")
    return values


def test_manifest_contract_removed_payload_binding_symbols() -> None:
    """运行时 manifest 合同不再导出 payload binding 旧模型。"""

    removed_symbols = {
        "RackPositionArgRole",
        "RackPositionArgSourceKind",
        "RackPositionArgSource",
        "RackPositionArg",
        "CommandResultBinding",
    }

    assert removed_symbols.isdisjoint(set(manifest_contract.__all__))
    for symbol in removed_symbols:
        assert not hasattr(manifest_contract, symbol)


def test_command_and_event_bindings_keep_only_static_capability_fields() -> None:
    """命令/事件绑定只表达能力目录，不携带 payload 语义。"""

    command = CommandBinding(command="SCAN_TOTE", target_device_role="ENTRY_SCANNER")
    event = WorklinePluginManifest.from_yaml_dict(_manifest_yaml_dict()).events[0]

    assert set(command.__dataclass_fields__) == {"command", "target_device_role"}
    assert set(event.__dataclass_fields__) == {"event", "source_device_roles", "category"}


def test_manifest_contract_exports_material_unit_status_subject_models() -> None:
    """manifest 合同导出物料单元状态归属结构。"""

    assert set(SessionSubject.__dataclass_fields__) == {"type", "physical_form", "identity_sources"}
    assert set(StateMachineSubject.__dataclass_fields__) == {"category", "type", "physical_form"}
    assert set(StateMachineOwner.__dataclass_fields__) == {"model", "field"}
    assert set(StateMachineTransition.__dataclass_fields__) == {"from_state", "to_states"}
    assert set(StateMachine.__dataclass_fields__) == {
        "id",
        "subject",
        "state_owner",
        "granularity",
        "transitions",
    }
    assert set(PipelineQueue.__dataclass_fields__) == {"code", "role", "capacity", "order_policy"}


def test_from_yaml_dict_projects_device_roles_to_runtime_manifest() -> None:
    manifest = WorklinePluginManifest.from_yaml_dict(_manifest_yaml_dict())

    assert [device.role for device in manifest.devices] == ["ENTRY_SCANNER", "EXIT_ARM"]
    assert [command.command for command in manifest.commands] == ["SCAN_TOTE", "PUT_TOTE"]
    assert {command.target_device_role for command in manifest.commands} == {"ENTRY_SCANNER", "EXIT_ARM"}

    events_by_name = {event.event: event for event in manifest.events}
    assert events_by_name["TOTE_ARRIVED"].source_device_roles == ("ENTRY_SCANNER",)
    assert events_by_name["TOTE_ARRIVED"].category is EventCategory.ENTRY_DEVICE
    assert events_by_name["TOTE_PLACED"].source_device_roles == ("EXIT_ARM",)
    assert events_by_name["TOTE_PLACED"].category is EventCategory.ENTRY_DEVICE

    first_edge = manifest.topology.flow_edges[0]
    assert first_edge.from_node.kind is NodeRefKind.RACK_POSITION
    assert first_edge.from_node.ref == "ENTRY_POSITION"
    assert first_edge.to_node.kind is NodeRefKind.DEVICE_ROLE
    assert first_edge.to_node.ref == "ENTRY_SCANNER"
    assert first_edge.type is FlowEdgeType.OPERATION


def test_from_yaml_dict_projects_material_unit_status_contract() -> None:
    manifest = WorklinePluginManifest.from_yaml_dict(_manifest_yaml_dict())

    assert manifest.session_subject == SessionSubject(
        type="MATERIAL_UNIT",
        physical_form="REEL",
        identity_sources=("PkgID", "material_identity_key"),
    )
    assert manifest.state_machines == (
        StateMachine(
            id="smt_material_unit_reel",
            subject=StateMachineSubject(category="MATERIAL_UNIT", type="MATERIAL_UNIT", physical_form="REEL"),
            state_owner=StateMachineOwner(model="MaterialUnit", field="status"),
            granularity="MATERIAL_LIFECYCLE",
            transitions=(
                StateMachineTransition(
                    from_state="IN_TRANSIT",
                    to_states=("STORED", "COMPLETED", "NG", "RECONCILING"),
                ),
                StateMachineTransition(from_state="STORED", to_states=("IN_TRANSIT", "NG", "RECONCILING")),
                StateMachineTransition(
                    from_state="RECONCILING",
                    to_states=("IN_TRANSIT", "STORED", "COMPLETED", "NG"),
                ),
                StateMachineTransition(from_state="NG", to_states=()),
                StateMachineTransition(from_state="COMPLETED", to_states=()),
            ),
        ),
    )
    assert manifest.pipeline_queues == (
        PipelineQueue(code="WORKSTATION_ACTIVE", role="WORKSTATION", capacity=1, order_policy="FIFO"),
    )


def test_from_yaml_dict_keeps_legacy_manifest_compatible() -> None:
    """旧 manifest 缺少新增合同字段时仍可加载。"""

    manifest = WorklinePluginManifest.from_yaml_dict(_legacy_manifest_yaml_dict())

    assert manifest.session_subject is None
    assert manifest.state_machines == ()
    assert manifest.pipeline_queues == ()


def test_yaml_loader_rejects_invalid_pipeline_queue_role() -> None:
    """PipelineQueue.role 必须在白名单内，拼写错误 fail-fast。"""
    data = _manifest_yaml_dict()
    data["pipeline_queues"][0]["role"] = "WORKSTTION"  # 拼写错误

    with pytest.raises(ValueError, match=r"PipelineQueue\.role must be one of"):
        WorklinePluginManifest.from_yaml_dict(data)


def test_yaml_loader_rejects_invalid_state_machine_granularity() -> None:
    """StateMachine.granularity 必须在白名单内。"""
    data = _manifest_yaml_dict()
    data["state_machines"][0]["granularity"] = "BOGUS_GRANULARITY"

    with pytest.raises(ValueError, match=r"StateMachine\.granularity must be one of"):
        WorklinePluginManifest.from_yaml_dict(data)


def test_yaml_loader_rejects_invalid_pipeline_queue_capacity() -> None:
    """PipelineQueue.capacity 仅接受正整数或 MANY。"""
    data = _manifest_yaml_dict()
    data["pipeline_queues"][0]["capacity"] = "UNLIMITED"

    with pytest.raises(ValueError, match="capacity must be a positive integer or MANY"):
        WorklinePluginManifest.from_yaml_dict(data)


def test_yaml_loader_rejects_zero_pipeline_queue_capacity() -> None:
    data = _manifest_yaml_dict()
    data["pipeline_queues"][0]["capacity"] = 0

    with pytest.raises(ValueError, match="capacity must be a positive integer or MANY"):
        WorklinePluginManifest.from_yaml_dict(data)


def test_yaml_loader_accepts_many_pipeline_queue_capacity() -> None:
    data = _manifest_yaml_dict()
    data["pipeline_queues"][0]["capacity"] = "MANY"

    manifest = WorklinePluginManifest.from_yaml_dict(data)

    assert manifest.pipeline_queues[0].capacity == "MANY"


def test_yaml_loader_accepts_all_documented_pipeline_queue_roles() -> None:
    """设计文档队列角色（Buffer/Gate/Wait/Workstation/Exception）均应通过白名单。"""
    for role in ("BUFFER", "GATE", "WAIT", "WORKSTATION", "EXCEPTION", "ENTRY", "SCAN", "WORK"):
        data = _manifest_yaml_dict()
        data["pipeline_queues"][0]["role"] = role
        manifest = WorklinePluginManifest.from_yaml_dict(data)
        assert manifest.pipeline_queues[0].role == role


def test_yaml_loader_accepts_command_result_event_category_for_compatibility() -> None:
    """loader 保持 enum 兼容；真实 manifest 和模板层负责清理命令结果事件。"""

    data = _manifest_yaml_dict()
    data["device_roles"]["ENTRY_SCANNER"]["events"][0]["event"] = "SCAN_TOTE_RESULT"
    data["device_roles"]["ENTRY_SCANNER"]["events"][0]["category"] = "COMMAND_RESULT"

    manifest = WorklinePluginManifest.from_yaml_dict(data)

    events_by_name = {event.event: event for event in manifest.events}
    assert events_by_name["SCAN_TOTE_RESULT"].category is EventCategory.COMMAND_RESULT
    assert events_by_name["SCAN_TOTE_RESULT"].source_device_roles == ("ENTRY_SCANNER",)


def test_from_yaml_file_reports_path_for_invalid_manifest(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text("plugin_key: yaml_plugin\nunexpected: true\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"manifest\.yaml|unexpected"):
        WorklinePluginManifest.from_yaml_file(manifest_path)


def test_from_yaml_file_rejects_duplicate_mapping_keys(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(
        """
plugin_key: yaml_plugin
contract_version: yaml.v1
device_roles:
  ENTRY_SCANNER:
    commands: []
    commands: ["SCAN_TOTE"]
rack_positions: []
topology:
  flow_edges: []
resource_boundaries: []
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as exc_info:
        WorklinePluginManifest.from_yaml_file(manifest_path)

    message = str(exc_info.value)
    assert "manifest.yaml" in message
    assert "duplicate" in message
    assert "commands" in message


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda data: data.update({"commands": []}),
            r"commands|device_roles",
        ),
        (
            lambda data: data["device_roles"]["ENTRY_SCANNER"].update({"payload_schema_ref": "Payload"}),
            r"payload_schema_ref|device_roles\.ENTRY_SCANNER",
        ),
        (
            lambda data: data["device_roles"]["ENTRY_SCANNER"].update({"rack_position_args": []}),
            r"rack_position_args|device_roles\.ENTRY_SCANNER",
        ),
        (
            lambda data: data["topology"]["flow_edges"][0].update({"from_node": {"kind": "RACK_POSITION", "ref": "X"}}),
            r"from_node|topology\.flow_edges\[0\]",
        ),
        (
            lambda data: data["device_roles"]["ENTRY_SCANNER"]["events"][0].update({"classification": "success"}),
            r"classification|events\[0\]",
        ),
    ],
)
def test_yaml_loader_rejects_legacy_or_runtime_only_fields(mutate, message: str) -> None:
    data = _manifest_yaml_dict()
    mutate(data)

    with pytest.raises(ValueError, match=message):
        WorklinePluginManifest.from_yaml_dict(data)


def test_yaml_loader_rejects_duplicate_command_across_roles() -> None:
    data = _manifest_yaml_dict()
    data["device_roles"]["EXIT_ARM"]["commands"].append("SCAN_TOTE")

    with pytest.raises(ValueError, match=r"SCAN_TOTE|command|duplicate"):
        WorklinePluginManifest.from_yaml_dict(data)


def test_yaml_loader_rejects_event_category_conflict_across_roles() -> None:
    data = _manifest_yaml_dict()
    data["device_roles"]["EXIT_ARM"]["events"][0]["event"] = "TOTE_ARRIVED"
    data["device_roles"]["EXIT_ARM"]["events"][0]["category"] = "INTERNAL"

    with pytest.raises(ValueError, match=r"TOTE_ARRIVED|category"):
        WorklinePluginManifest.from_yaml_dict(data)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda data: data["device_roles"]["ENTRY_SCANNER"].update({"min_count": None}),
            r"device_roles\.ENTRY_SCANNER\.min_count",
        ),
        (
            lambda data: data["rack_positions"][0]["carrier_capability"].update({"min_capacity": None}),
            r"rack_positions\[0\]\.carrier_capability\.min_capacity",
        ),
        (
            lambda data: data["rack_positions"][0]["carrier_capability"].update({"max_capacity": None}),
            r"rack_positions\[0\]\.carrier_capability\.max_capacity",
        ),
        (
            lambda data: data["device_roles"]["ENTRY_SCANNER"].update({"min_count": False}),
            r"device_roles\.ENTRY_SCANNER\.min_count",
        ),
    ],
)
def test_yaml_loader_rejects_null_or_bool_for_non_nullable_int_fields(mutate, message: str) -> None:
    data = _manifest_yaml_dict()
    mutate(data)

    with pytest.raises((TypeError, ValueError), match=message):
        WorklinePluginManifest.from_yaml_dict(data)


def test_yaml_loader_allows_null_max_count() -> None:
    data = _manifest_yaml_dict()
    data["device_roles"]["ENTRY_SCANNER"]["max_count"] = None

    manifest = WorklinePluginManifest.from_yaml_dict(data)

    assert manifest.devices[0].max_count is None


def test_yaml_loader_rejects_unknown_refs_and_invalid_edge_types() -> None:
    unknown_role_data = _manifest_yaml_dict()
    unknown_role_data["topology"]["flow_edges"][1]["to"]["ref"] = "UNKNOWN_ARM"
    with pytest.raises(ValueError, match=r"UNKNOWN_ARM|topology"):
        WorklinePluginManifest.from_yaml_dict(unknown_role_data)

    material_flow_data = _manifest_yaml_dict()
    material_flow_data["topology"]["flow_edges"][0]["type"] = "MATERIAL_FLOW"
    with pytest.raises(ValueError, match=r"MATERIAL_FLOW|RACK_POSITION"):
        WorklinePluginManifest.from_yaml_dict(material_flow_data)


def test_yaml_loader_reports_path_for_unknown_topology_device_role() -> None:
    data = _manifest_yaml_dict()
    data["topology"]["flow_edges"][1]["to"]["ref"] = "UNKNOWN_ARM"

    with pytest.raises(ValueError) as exc_info:
        WorklinePluginManifest.from_yaml_dict(data)

    message = str(exc_info.value)
    assert "topology.flow_edges[1].to.ref" in message
    assert "UNKNOWN_ARM" in message


def test_yaml_loader_reports_path_for_unknown_resource_boundary_rack_position() -> None:
    data = _manifest_yaml_dict()
    data["resource_boundaries"][0]["rack_position_code"] = "UNKNOWN_POSITION"

    with pytest.raises(ValueError) as exc_info:
        WorklinePluginManifest.from_yaml_dict(data)

    message = str(exc_info.value)
    assert "resource_boundaries[0].rack_position_code" in message
    assert "UNKNOWN_POSITION" in message


def test_yaml_loader_reports_path_for_resource_boundary_rack_kind_mismatch() -> None:
    data = _manifest_yaml_dict()
    data["resource_boundaries"][0]["rack_kind"] = "FIVE_LAYER"

    with pytest.raises(ValueError) as exc_info:
        WorklinePluginManifest.from_yaml_dict(data)

    message = str(exc_info.value)
    assert "resource_boundaries[0].rack_kind" in message
    assert "FIVE_LAYER" in message


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda data: data["state_machines"][0]["state_owner"].update({"model": "WorklineSession"}),
            r"state_machines\[0\]\.state_owner\.model|MaterialUnit",
        ),
        (
            lambda data: data["state_machines"][0]["state_owner"].update({"field": "phase"}),
            r"state_machines\[0\]\.state_owner\.field|status",
        ),
        (
            lambda data: data["state_machines"][0]["subject"].update({"physical_form": "TRAY"}),
            r"state_machines\[0\]\.subject\.physical_form|session_subject",
        ),
        (
            lambda data: data["state_machines"][0]["transitions"].append({"from": "STORED", "to": ["NG"]}),
            r"state_machines\[0\]\.transitions\.from|STORED|unique",
        ),
        (
            lambda data: data["state_machines"][0]["transitions"][0].update({"from": ""}),
            r"state_machines\[0\]\.transitions\[0\]\.from",
        ),
        (
            lambda data: data["state_machines"][0]["transitions"][0].update({"from": "STOREDD"}),
            r"state_machines\[0\]\.transitions\[0\]\.from|from_state|STOREDD",
        ),
        (
            lambda data: data["state_machines"][0]["transitions"][0].update({"to": [""]}),
            r"state_machines\[0\]\.transitions\[0\]\.to\[0\]",
        ),
        (
            lambda data: data["state_machines"][0]["transitions"][0].update({"to": ["DONE"]}),
            r"state_machines\[0\]\.transitions\[0\]\.to|to_states|DONE",
        ),
        (
            lambda data: data["state_machines"].append(_material_unit_state_machine()),
            r"state_machines\.id|smt_material_unit_reel|unique",
        ),
        (
            lambda data: data["pipeline_queues"].append(_pipeline_queue()),
            r"pipeline_queues\.code|WORKSTATION_ACTIVE|unique",
        ),
        (
            lambda data: data["pipeline_queues"][0].update({"capacity": 0}),
            r"pipeline_queues\[0\]\.capacity",
        ),
        (
            lambda data: data["pipeline_queues"][0].update({"capacity": "ONE"}),
            r"pipeline_queues\[0\]\.capacity",
        ),
        (
            lambda data: data["pipeline_queues"][0].update({"order_policy": "RANDOM"}),
            r"pipeline_queues\[0\]\.order_policy",
        ),
    ],
)
def test_yaml_loader_validates_material_unit_status_contract(mutate, message: str) -> None:
    data = _manifest_yaml_dict()
    mutate(data)

    with pytest.raises((TypeError, ValueError), match=message):
        WorklinePluginManifest.from_yaml_dict(data)


def test_yaml_loader_does_not_warn_when_terminal_exception_exit_rows_are_declared(caplog) -> None:
    data = _manifest_yaml_dict()
    data["state_machines"][0]["transitions"][0]["to"] = ["STORED"]
    data["state_machines"][0]["transitions"][1]["to"] = ["IN_TRANSIT"]

    manifest = WorklinePluginManifest.from_yaml_dict(data)

    assert manifest.state_machines[0].transitions[0].to_states == ("STORED",)
    assert caplog.text == ""


def test_yaml_loader_warns_when_terminal_exception_exit_rows_are_missing(caplog) -> None:
    data = _manifest_yaml_dict()
    data["state_machines"][0]["transitions"] = [
        {"from": "IN_TRANSIT", "to": ["STORED", "COMPLETED", "NG", "RECONCILING"]},
        {"from": "STORED", "to": ["IN_TRANSIT", "NG", "RECONCILING"]},
        {"from": "COMPLETED", "to": []},
    ]

    manifest = WorklinePluginManifest.from_yaml_dict(data)

    assert {transition.from_state for transition in manifest.state_machines[0].transitions} == {
        "IN_TRANSIT",
        "STORED",
        "COMPLETED",
    }
    assert "NG" in caplog.text
    assert "RECONCILING" in caplog.text
