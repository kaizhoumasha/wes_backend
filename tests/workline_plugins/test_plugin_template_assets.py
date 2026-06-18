"""插件模板资产契约测试。"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from src.workline_runtime.plugin_manifest import FlowEdgeType, NodeRefKind, WorklinePluginManifest

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "docs" / "templates" / "workline_plugin"

EVENT_FIELDS = frozenset({"device_code", "event_type", "timestamp", "data"})
RESULT_FIELDS = frozenset({"command_code", "device_code", "result", "finish_time", "data", "error_detail"})
COMMAND_FIELDS = frozenset({"device_code", "command_code", "task_type", "priority", "timeout", "timestamp", "params"})
MANIFEST_TOP_LEVEL_FIELDS = (
    "plugin_key",
    "contract_version",
    "devices",
    "rack_positions",
    "topology",
    "commands",
    "events",
    "resource_boundaries",
)
REMOVED_MANIFEST_RUNTIME_FIELDS = (
    "business_key_resolver",
    "result_classifier",
    "context_model",
    "material_identity_resolver",
    "ng_reason_catalog",
)
RACK_POSITION_CONTRACT_SENTENCE = (
    "`rack_positions` 只声明 WES-managed rack docking positions / inventory-fact anchors，不枚举所有物理点位。"
)
POSITION_ARG_CONTRACT_SENTENCE = "设备 payload 由插件业务代码、设备 profile、设备网关或 PLC 理解，不进入 manifest。"


def _read_json(name: str) -> dict:
    return json.loads((TEMPLATE_DIR / "fixtures" / name).read_text(encoding="utf-8"))


def _normalized_command_result_bodies(content: str) -> list[str]:
    return re.findall(r"NormalizedCommandResult\(\n(?P<body>.*?)\n    \)", content, flags=re.DOTALL)


def _render_python_template(content: str) -> str:
    replacements = {
        "{{PLUGIN_NAME}}": "示例",
        "{{PLUGIN_MODULE}}": "sample_plugin",
        "{{CONTEXT_CLASS}}": "SampleContext",
        "{{PLUGIN_CLASS}}": "SamplePlugin",
        "{{PLUGIN_KEY}}": "sample_plugin",
        "{{CONTRACT_VERSION}}": "1.0.0",
        "{{PLUGIN_INSTANCE}}": "sample_plugin",
    }
    for placeholder, value in replacements.items():
        content = content.replace(placeholder, value)
    return content


def _render_manifest_yaml_template() -> str:
    return _render_python_template((TEMPLATE_DIR / "manifest.yaml.tmpl").read_text(encoding="utf-8"))


def test_template_assets_cover_required_files() -> None:
    expected_files = {
        "README.md",
        "manifest.yaml.tmpl",
        "plugin.py.tmpl",
        "contract.py.tmpl",
        "context.py.tmpl",
        "tests.py.tmpl",
        "sandbox_happy_path.md",
        "fixtures/event_happy_path.json",
        "fixtures/command_dispatch.json",
        "fixtures/result_success.json",
        "fixtures/result_business_ng.json",
        "fixtures/expected_business_decision.json",
        "fixtures/result_system_failure.json",
        "fixtures/event_timeout.json",
        "fixtures/invalid_event_flattened.json",
    }

    for relative_path in expected_files:
        assert (TEMPLATE_DIR / relative_path).is_file(), relative_path


def test_template_fixtures_keep_whitepaper_data_and_params_boundaries() -> None:
    for name in ("event_happy_path.json", "event_timeout.json"):
        event = _read_json(name)
        assert set(event) <= EVENT_FIELDS
        assert isinstance(event["data"], dict)
        assert "business_key" in event["data"]
        assert "sandbox" not in event

    for name in ("result_success.json", "result_business_ng.json", "result_system_failure.json"):
        result = _read_json(name)
        assert set(result) <= RESULT_FIELDS
        assert isinstance(result["data"], dict)
        assert "business_key" in result["data"]
        assert "actual_value" not in result
        assert "sandbox" not in result

    command = _read_json("command_dispatch.json")
    assert set(command) <= COMMAND_FIELDS
    assert isinstance(command["params"], dict)
    assert command["params"]["business_key"] == "ITEM-001"
    assert "business_key" not in command
    assert "sandbox" not in command


def test_invalid_fixture_documents_flattened_business_fields() -> None:
    invalid_event = _read_json("invalid_event_flattened.json")

    assert "business_key" in invalid_event
    assert "data" not in invalid_event
    assert set(invalid_event) - EVENT_FIELDS == {"business_key", "item_id", "station_code"}


def test_code_templates_do_not_use_legacy_step_code() -> None:
    for path in TEMPLATE_DIR.glob("*.py.tmpl"):
        content = path.read_text(encoding="utf-8")
        assert "step_code" not in content
        assert "ClassificationResult" not in content

    plugin_template = (TEMPLATE_DIR / "plugin.py.tmpl").read_text(encoding="utf-8")
    removed_state_key = "plugin" + "_state"
    for path in TEMPLATE_DIR.glob("*.py.tmpl"):
        assert removed_state_key not in path.read_text(encoding="utf-8")
    assert f"{removed_state_key}=" not in plugin_template


def test_plugin_guide_uses_current_result_classifier_contract() -> None:
    guide = (TEMPLATE_DIR.parents[1] / "plugin_development_guide.md").read_text(encoding="utf-8")

    assert "ClassificationResult" not in guide
    assert "def classify_result(payload: dict[str, Any]) -> str | None:" in guide


def test_registry_template_points_to_plugin_module() -> None:
    expected_module = 'plugin_module="src.workline_plugins.{{PLUGIN_MODULE}}.plugin"'

    registry_template = (TEMPLATE_DIR / "registry_entry.py.tmpl").read_text(encoding="utf-8")
    tests_template = (TEMPLATE_DIR / "tests.py.tmpl").read_text(encoding="utf-8")

    assert expected_module in registry_template
    assert expected_module in tests_template


def test_plugin_template_imports_command_result_type_at_runtime() -> None:
    plugin_template = (TEMPLATE_DIR / "plugin.py.tmpl").read_text(encoding="utf-8")

    runtime_import = "from src.workline_runtime.plugin_sdk.contracts import NormalizedCommandResult"
    type_checking_import = "if TYPE_CHECKING:\n    " + runtime_import

    assert runtime_import in plugin_template
    assert type_checking_import not in plugin_template


def test_plugin_template_failed_result_binding_has_runtime_handler() -> None:
    plugin_template = (TEMPLATE_DIR / "plugin.py.tmpl").read_text(encoding="utf-8")
    tests_template = (TEMPLATE_DIR / "tests.py.tmpl").read_text(encoding="utf-8")

    assert 'result="FAILED"' in plugin_template
    assert '@on_command("MEASURE_ITEM", result="FAILED")' in plugin_template
    assert "async def handle_measure_failed" in plugin_template
    assert "ctx.next.block(" in plugin_template
    assert "test_measure_failure_blocks_session" in tests_template


def test_plugin_template_uses_pure_data_manifest_contract() -> None:
    plugin_template = (TEMPLATE_DIR / "plugin.py.tmpl").read_text(encoding="utf-8")
    manifest_template = _render_manifest_yaml_template()

    assert "from pathlib import Path" in plugin_template
    assert 'WorklinePluginManifest.from_yaml_file(Path(__file__).with_name("manifest.yaml"))' in plugin_template

    removed_device_requirement = "Device" + "RoleRequirement"
    removed_resource_boundary = "Single" + "LayerRackBoundary"
    removed_payload_symbols = (
        "RackPositionArg",
        "RackPositionArgRole",
        "RackPositionArgSource",
        "RackPositionArgSourceKind",
        "CommandResultBinding",
        "payload_schema_ref",
        "rack_position_args",
        "result_bindings",
    )
    for removed_text in (removed_device_requirement, removed_resource_boundary, *removed_payload_symbols):
        assert removed_text not in plugin_template
        assert removed_text not in manifest_template

    for field_name in REMOVED_MANIFEST_RUNTIME_FIELDS:
        assert f"{field_name}=" not in plugin_template

    for helper_name in (
        "def resolve_business_key(",
        "def classify_result(",
        "def get_context_model(",
        "def resolve_material_identity(",
        "def list_ng_reasons(",
    ):
        assert helper_name in plugin_template
    assert "context_model = {{CONTEXT_CLASS}}" not in plugin_template
    assert "def get_context_model(self) -> type[{{CONTEXT_CLASS}}]:" in plugin_template

    assert "device_roles:" in manifest_template
    assert "rack_positions:" in manifest_template
    assert "topology:" in manifest_template
    assert "resource_boundaries:" in manifest_template


def test_manifest_yaml_template_loads_as_runtime_manifest() -> None:
    manifest_data = yaml.safe_load(_render_manifest_yaml_template())
    manifest = WorklinePluginManifest.from_yaml_dict(manifest_data)

    assert tuple(field.name for field in manifest.__dataclass_fields__.values()) == MANIFEST_TOP_LEVEL_FIELDS
    assert [device.role for device in manifest.devices] == ["ENTRY_SENSOR", "MEASURE_DEVICE"]
    assert {command.command: command.target_device_role for command in manifest.commands} == {
        "MEASURE_ITEM": "MEASURE_DEVICE"
    }
    assert {event.event: event.category for event in manifest.events}["MEASURE_ITEM_RESULT"].value == "COMMAND_RESULT"
    assert manifest.rack_positions[0].code == "ENTRY_POSITION"
    assert manifest.resource_boundaries[0].rack_position_code == "MEASURE_POSITION"

    assert {
        (edge.from_node.kind, edge.from_node.ref, edge.to_node.kind, edge.to_node.ref, edge.type)
        for edge in manifest.topology.flow_edges
    } == {
        (NodeRefKind.RACK_POSITION, "ENTRY_POSITION", NodeRefKind.DEVICE_ROLE, "ENTRY_SENSOR", FlowEdgeType.OPERATION),
        (NodeRefKind.DEVICE_ROLE, "ENTRY_SENSOR", NodeRefKind.DEVICE_ROLE, "MEASURE_DEVICE", FlowEdgeType.OPERATION),
        (
            NodeRefKind.DEVICE_ROLE,
            "MEASURE_DEVICE",
            NodeRefKind.RACK_POSITION,
            "MEASURE_POSITION",
            FlowEdgeType.OPERATION,
        ),
    }


def test_manifest_yaml_template_uses_authoring_edge_shape() -> None:
    manifest_data = yaml.safe_load(_render_manifest_yaml_template())
    flow_edges = manifest_data["topology"]["flow_edges"]

    assert flow_edges
    assert all(set(edge) == {"from", "to", "type"} for edge in flow_edges)
    assert all("from_node" not in edge and "to_node" not in edge for edge in flow_edges)
    assert any(
        "DEVICE_ROLE" in {edge["from"]["kind"], edge["to"]["kind"]} and edge["type"] == "OPERATION"
        for edge in flow_edges
    )


def test_template_tests_assert_new_manifest_and_runtime_contract() -> None:
    tests_template = (TEMPLATE_DIR / "tests.py.tmpl").read_text(encoding="utf-8")

    for field_name in MANIFEST_TOP_LEVEL_FIELDS:
        assert field_name in tests_template

    assert "fields(manifest)" in tests_template
    assert "isinstance(manifest.events[0], EventBinding)" in tests_template
    assert "isinstance(manifest.commands[0], CommandBinding)" in tests_template
    assert "isinstance(manifest.resource_boundaries[0], ResourceBoundary)" in tests_template
    assert 'manifest.rack_positions[0].code == "ENTRY_POSITION"' in tests_template
    assert 'manifest.commands[0].target_device_role == "MEASURE_DEVICE"' in tests_template
    assert 'set(manifest.commands[0].__dataclass_fields__) == {"command", "target_device_role"}' in tests_template

    for helper_name in (
        "resolve_business_key",
        "classify_result",
        "get_context_model",
        "resolve_material_identity",
        "list_ng_reasons",
    ):
        assert f"callable(plugin.{helper_name})" in tests_template or f"plugin.{helper_name}(" in tests_template

    for field_name in REMOVED_MANIFEST_RUNTIME_FIELDS:
        assert field_name in tests_template
        assert f"{field_name}=" not in tests_template


def test_template_tests_use_current_normalized_command_result_contract() -> None:
    tests_template = (TEMPLATE_DIR / "tests.py.tmpl").read_text(encoding="utf-8")
    constructor_bodies = _normalized_command_result_bodies(tests_template)

    assert constructor_bodies
    for body in constructor_bodies:
        assert re.search(r"(?m)^\s+result=", body) is None
        assert "command_code=" in body
        assert "source_result=" in body
        assert "normalized_result=" in body
        assert "command_type=" in body
        assert "device_code=" in body


def test_template_docs_explain_position_arg_static_contract() -> None:
    for relative_path in (
        "README.md",
        "sandbox_happy_path.md",
        "../../plugin_development_guide.md",
    ):
        content = (TEMPLATE_DIR / relative_path).resolve().read_text(encoding="utf-8")
        assert POSITION_ARG_CONTRACT_SENTENCE in content
        assert RACK_POSITION_CONTRACT_SENTENCE in content
        assert "设备相关物理连线使用 `OPERATION`" in content


def test_template_docs_do_not_document_legacy_context_model_or_empty_registry() -> None:
    readme = (TEMPLATE_DIR / "README.md").read_text(encoding="utf-8")
    guide = (TEMPLATE_DIR.parents[1] / "plugin_development_guide.md").read_text(encoding="utf-8")

    for content in (readme, guide):
        assert "或 `context_model`" not in content
        assert "必须实现 `get_context_model()`" in content

    assert "registry 默认为空" not in readme
    assert "在现有 registry 中显式新增/合并" in readme
