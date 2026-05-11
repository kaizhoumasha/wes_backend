import pytest

from src.workline_runtime.runtime_intent import (
    BlockScope,
    Destination,
    DestinationKind,
    RuntimeIntent,
    RuntimeIntentKind,
)


def test_command_intent_describes_device_action_and_destination():
    intent = RuntimeIntent.command(
        device_role="WEIGH_SCALE",
        action="WEIGH_TOTE",
        payload={"tote_id": "T-001"},
        destination=Destination.role("WEIGH_SCALE"),
        timeout_seconds=120,
    )

    assert intent.kind == RuntimeIntentKind.COMMAND
    assert intent.device_role == "WEIGH_SCALE"
    assert intent.action == "WEIGH_TOTE"
    assert intent.payload_json == {"tote_id": "T-001"}
    assert intent.destination == Destination(kind=DestinationKind.ROLE, value="WEIGH_SCALE")
    assert intent.timeout_seconds == 120


def test_block_intent_requires_reason_and_scope():
    intent = RuntimeIntent.block(
        scope=BlockScope.MATERIAL,
        reason_code="BARCODE_INVALID",
        message="条码无法识别",
        suggested_action="人工复核条码",
    )

    assert intent.kind == RuntimeIntentKind.BLOCK
    assert intent.block_scope == BlockScope.MATERIAL
    assert intent.reason_code == "BARCODE_INVALID"
    assert intent.message == "条码无法识别"
    assert intent.suggested_action == "人工复核条码"


def test_invalid_command_requires_action():
    with pytest.raises(ValueError, match="action"):
        RuntimeIntent.command(device_role="WEIGH_SCALE", action="", payload={})
