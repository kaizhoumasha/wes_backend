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


def test_resource_fact_intent_describes_append_only_resource_fact():
    intent = RuntimeIntent.resource_fact(
        fact_type="MATERIAL_MOUNTED",
        payload={
            "pkg_code": "PKG-001",
            "bin_code": "BIN-001",
            "bin_cell_index": "4",
        },
        idempotency_key="MATERIAL_MOUNTED:CMD-001:PKG-001:BIN-001:4",
    )

    assert intent.kind == RuntimeIntentKind.RESOURCE_FACT
    assert intent.action == "MATERIAL_MOUNTED"
    assert intent.idempotency_key == "MATERIAL_MOUNTED:CMD-001:PKG-001:BIN-001:4"
    assert intent.payload_json["pkg_code"] == "PKG-001"


def test_resource_reservation_intent_describes_planned_bin_cell_claim():
    intent = RuntimeIntent.resource_reservation(
        operation="CLAIM_BIN_CELL",
        payload={
            "pkg_code": "PKG-001",
            "bin_code": "BIN-001",
            "bin_cell_index": "4",
        },
        idempotency_key="CLAIM_BIN_CELL:2001:BIN-001:4:PKG-001",
    )

    assert intent.kind == RuntimeIntentKind.RESOURCE_RESERVATION
    assert intent.action == "CLAIM_BIN_CELL"
    assert intent.idempotency_key == "CLAIM_BIN_CELL:2001:BIN-001:4:PKG-001"
    assert intent.payload_json["bin_code"] == "BIN-001"


def test_resource_fact_intent_requires_fact_type_and_payload():
    with pytest.raises(ValueError, match="RESOURCE_FACT intent requires action"):
        RuntimeIntent(kind=RuntimeIntentKind.RESOURCE_FACT, payload_json={"pkg_code": "PKG-001"})

    with pytest.raises(ValueError, match="RESOURCE_FACT intent requires payload"):
        RuntimeIntent(kind=RuntimeIntentKind.RESOURCE_FACT, action="MATERIAL_MOUNTED", payload_json={})
