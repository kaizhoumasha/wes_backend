"""插件模板资产契约测试。"""

from __future__ import annotations

import json
import re
from pathlib import Path

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "docs" / "templates" / "workline_plugin"

EVENT_FIELDS = frozenset({"device_code", "event_type", "timestamp", "data"})
RESULT_FIELDS = frozenset({"command_code", "device_code", "result", "finish_time", "data", "error_detail"})
COMMAND_FIELDS = frozenset({"device_code", "command_code", "task_type", "priority", "timeout", "timestamp", "params"})
MANIFEST_TOP_LEVEL_FIELDS = (
    "plugin_key",
    "contract_version",
    "devices",
    "positions",
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


def _read_json(name: str) -> dict:
    return json.loads((TEMPLATE_DIR / "fixtures" / name).read_text(encoding="utf-8"))


def _normalized_command_result_bodies(content: str) -> list[str]:
    return re.findall(r"NormalizedCommandResult\(\n(?P<body>.*?)\n    \)", content, flags=re.DOTALL)


def test_template_assets_cover_required_files() -> None:
    expected_files = {
        "README.md",
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


def test_plugin_template_uses_pure_data_manifest_contract() -> None:
    plugin_template = (TEMPLATE_DIR / "plugin.py.tmpl").read_text(encoding="utf-8")

    for name in (
        "DeviceRequirement",
        "EventBinding",
        "CommandBinding",
        "ResourceBoundary",
        "Position",
        "PositionCarrierCapability",
        "TopologySpec",
        "NodeRef",
        "NodeRefKind",
        "FlowEdge",
        "FlowEdgeType",
        "PositionArg",
        "PositionArgRole",
        "PositionArgSource",
        "PositionArgSourceKind",
    ):
        assert name in plugin_template

    removed_device_requirement = "Device" + "RoleRequirement"
    removed_resource_boundary = "Single" + "LayerRackBoundary"
    assert removed_device_requirement not in plugin_template
    assert removed_resource_boundary not in plugin_template

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

    for manifest_field, binding_name in (
        ("devices", "DeviceRequirement("),
        ("events", "EventBinding("),
        ("commands", "CommandBinding("),
        ("resource_boundaries", "ResourceBoundary("),
    ):
        assert f"{manifest_field}=(" in plugin_template
        assert binding_name in plugin_template


def test_template_tests_assert_new_manifest_and_runtime_contract() -> None:
    tests_template = (TEMPLATE_DIR / "tests.py.tmpl").read_text(encoding="utf-8")

    for field_name in MANIFEST_TOP_LEVEL_FIELDS:
        assert field_name in tests_template

    assert "fields(manifest)" in tests_template
    assert "manifest.events" in tests_template
    assert "manifest.commands" in tests_template
    assert "manifest.resource_boundaries" in tests_template

    for helper_name in (
        "resolve_business_key",
        "classify_result",
        "get_context_model",
        "resolve_material_identity",
        "list_ng_reasons",
    ):
        assert helper_name in tests_template

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
    required_sentence = (
        "PositionArg 静态位置使用 `position_ref`，`position_ref` 与 `source` 互斥；"
        "`PositionArgSource` 不支持 `STATIC`。"
    )

    for relative_path in (
        "README.md",
        "sandbox_happy_path.md",
        "../../plugin_development_guide.md",
    ):
        content = (TEMPLATE_DIR / relative_path).resolve().read_text(encoding="utf-8")
        assert required_sentence in content
