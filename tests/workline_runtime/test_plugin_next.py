from src.workline_runtime.plugin_next import PluginNext
from src.workline_runtime.runtime_intent import (
    BlockScope,
    DestinationKind,
    RuntimeIntentKind,
)


def test_plugin_next_command_builds_runtime_intent():
    intent = PluginNext().command(
        device_role="WEIGH_SCALE",
        action="WEIGH_TOTE",
        payload={"tote_id": "T-001"},
        destination_role="WEIGH_SCALE",
        timeout_seconds=120,
    )

    assert intent.kind == RuntimeIntentKind.COMMAND
    assert intent.destination.kind == DestinationKind.ROLE
    assert intent.destination.value == "WEIGH_SCALE"


def test_plugin_next_block_builds_runtime_intent():
    intent = PluginNext().block(
        scope=BlockScope.MATERIAL,
        reason_code="BARCODE_INVALID",
        message="条码无法识别",
        suggested_action="人工复核条码",
    )

    assert intent.kind == RuntimeIntentKind.BLOCK
    assert intent.reason_code == "BARCODE_INVALID"


def test_plugin_next_command_defaults_to_next_destination():
    intent = PluginNext().command(
        device_role="WEIGH_SCALE",
        action="WEIGH_TOTE",
    )

    assert intent.destination.kind == DestinationKind.NEXT
    assert intent.destination.value is None


def test_plugin_next_command_uses_independent_empty_payloads():
    first_intent = PluginNext().command(device_role="WEIGH_SCALE", action="WEIGH_TOTE")
    second_intent = PluginNext().command(device_role="WEIGH_SCALE", action="WEIGH_TOTE")

    first_intent.payload_json["tote_id"] = "T-001"

    assert second_intent.payload_json == {}
