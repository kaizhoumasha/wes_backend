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


def test_rack_operation_request_intent_describes_rack_operation():
    intent = RuntimeIntent.rack_operation_request(
        operation_type="REPLACE_CLASSIFIER_WORK_RACK",
        operation_key="rack-operation:trace-001",
        target_code="http://wms-rcs/api/rack-operation",
        payload={
            "work_position_code": "SINGLE_LAYER_A",
            "new_rack_kind": "SINGLE_LAYER",
            "move_out_target_position_role": "SMT_EMPTY_RACK_AREA",
            "trace_id": "trace-001",
        },
        timeout_seconds=1800,
    )

    assert intent.kind == RuntimeIntentKind.RACK_OPERATION_REQUEST
    assert intent.action == "REPLACE_CLASSIFIER_WORK_RACK"
    assert intent.idempotency_key == "rack-operation:trace-001"
    assert intent.dispatch_key is None
    assert intent.target_code == "http://wms-rcs/api/rack-operation"
    assert intent.timeout_seconds == 1800
    assert intent.payload_json["work_position_code"] == "SINGLE_LAYER_A"
    assert intent.payload_json["move_out_target_position_role"] == "SMT_EMPTY_RACK_AREA"


def test_bin_operation_request_intent_describes_internal_handling_operation():
    intent = RuntimeIntent.bin_operation_request(
        operation_type="SORTER_FEED_BIN",
        operation_key="bin-operation:trace-001",
        moves=[
            {
                "sequence_no": 1,
                "bin_code": "BIN-001",
                "source_type": "RACK_SLOT",
                "source_code": "SINGLE_LAYER_A:01",
                "target_type": "SORTER_STATION",
                "target_code": "SORTER-01",
            }
        ],
        carrier_type="CTU",
        carrier_code="CTU-01",
        timeout_seconds=1800,
    )

    assert intent.kind == RuntimeIntentKind.BIN_OPERATION_REQUEST
    assert intent.action == "SORTER_FEED_BIN"
    assert intent.idempotency_key == "bin-operation:trace-001"
    assert intent.dispatch_key is None
    assert intent.target_code is None
    assert intent.timeout_seconds == 1800
    assert intent.payload_json["carrier_type"] == "CTU"
    assert intent.payload_json["carrier_code"] == "CTU-01"
    assert intent.payload_json["moves"][0]["bin_code"] == "BIN-001"


def test_rack_bin_exchange_request_intent_describes_composite_handling_operation():
    intent = RuntimeIntent.rack_bin_exchange_request(
        operation_type="SINGLE_LAYER_FULL_BIN_EXCHANGE",
        operation_key="rack-bin-exchange:release-001",
        moves=[
            {
                "sequence_no": 1,
                "bin_code": "BIN-FULL",
                "source_type": "RACK_SLOT",
                "source_code": "SINGLE_LAYER_A:01",
                "target_type": "BUFFER",
                "target_code": "FULL_BIN_BUFFER",
            },
            {
                "sequence_no": 2,
                "placeholder_key": "EMPTY_BIN_FOR:SINGLE_LAYER_A:01",
                "source_type": "BUFFER",
                "source_code": "EMPTY_BIN_BUFFER",
                "target_type": "RACK_SLOT",
                "target_code": "SINGLE_LAYER_A:01",
            },
        ],
        rack_code="RACK-SINGLE-01",
        carrier_type="CTU",
        timeout_seconds=1800,
    )

    assert intent.kind == RuntimeIntentKind.RACK_BIN_EXCHANGE_REQUEST
    assert intent.action == "SINGLE_LAYER_FULL_BIN_EXCHANGE"
    assert intent.idempotency_key == "rack-bin-exchange:release-001"
    assert intent.dispatch_key is None
    assert intent.target_code is None
    assert intent.payload_json["rack_code"] == "RACK-SINGLE-01"
    assert intent.payload_json["moves"][1]["placeholder_key"] == "EMPTY_BIN_FOR:SINGLE_LAYER_A:01"


def test_resource_fact_intent_requires_fact_type_and_payload():
    with pytest.raises(ValueError, match="RESOURCE_FACT intent requires action"):
        RuntimeIntent(kind=RuntimeIntentKind.RESOURCE_FACT, payload_json={"pkg_code": "PKG-001"})

    with pytest.raises(ValueError, match="RESOURCE_FACT intent requires payload"):
        RuntimeIntent(kind=RuntimeIntentKind.RESOURCE_FACT, action="MATERIAL_MOUNTED", payload_json={})


def test_rack_operation_request_requires_operation_key_target_payload_and_timeout():
    with pytest.raises(ValueError, match="RACK_OPERATION_REQUEST intent requires operation_type"):
        RuntimeIntent(
            kind=RuntimeIntentKind.RACK_OPERATION_REQUEST,
            idempotency_key="rack-operation:trace-001",
            target_code="http://wms-rcs/api/rack-operation",
            payload_json={"work_position_code": "SINGLE_LAYER_A"},
            timeout_seconds=1800,
        )

    with pytest.raises(ValueError, match="RACK_OPERATION_REQUEST intent requires operation_key"):
        RuntimeIntent(
            kind=RuntimeIntentKind.RACK_OPERATION_REQUEST,
            action="REPLACE_CLASSIFIER_WORK_RACK",
            target_code="http://wms-rcs/api/rack-operation",
            payload_json={"work_position_code": "SINGLE_LAYER_A"},
            timeout_seconds=1800,
        )

    with pytest.raises(ValueError, match="RACK_OPERATION_REQUEST intent requires target_code"):
        RuntimeIntent(
            kind=RuntimeIntentKind.RACK_OPERATION_REQUEST,
            action="REPLACE_CLASSIFIER_WORK_RACK",
            idempotency_key="rack-operation:trace-001",
            payload_json={"work_position_code": "SINGLE_LAYER_A"},
            timeout_seconds=1800,
        )

    with pytest.raises(ValueError, match="RACK_OPERATION_REQUEST intent requires payload"):
        RuntimeIntent(
            kind=RuntimeIntentKind.RACK_OPERATION_REQUEST,
            action="REPLACE_CLASSIFIER_WORK_RACK",
            idempotency_key="rack-operation:trace-001",
            target_code="http://wms-rcs/api/rack-operation",
            payload_json={},
            timeout_seconds=1800,
        )

    with pytest.raises(ValueError, match="RACK_OPERATION_REQUEST intent requires timeout_seconds"):
        RuntimeIntent(
            kind=RuntimeIntentKind.RACK_OPERATION_REQUEST,
            action="REPLACE_CLASSIFIER_WORK_RACK",
            idempotency_key="rack-operation:trace-001",
            target_code="http://wms-rcs/api/rack-operation",
            payload_json={"work_position_code": "SINGLE_LAYER_A"},
            timeout_seconds=0,
        )


@pytest.mark.parametrize(
    "kind",
    [RuntimeIntentKind.BIN_OPERATION_REQUEST, RuntimeIntentKind.RACK_BIN_EXCHANGE_REQUEST],
)
def test_handling_operation_request_rejects_transport_fields(kind):
    with pytest.raises(ValueError, match=f"{kind.value} intent must not expose transport fields"):
        RuntimeIntent(
            kind=kind,
            action="SORTER_FEED_BIN",
            idempotency_key="bin-operation:trace-001",
            target_code="http://wms-rcs/api/bin-operation",
            payload_json={
                "carrier_type": "CTU",
                "moves": [
                    {
                        "sequence_no": 1,
                        "bin_code": "BIN-001",
                        "source_type": "RACK_SLOT",
                        "source_code": "SINGLE_LAYER_A:01",
                        "target_type": "SORTER_STATION",
                        "target_code": "SORTER-01",
                    }
                ],
            },
            timeout_seconds=1800,
        )

    with pytest.raises(ValueError, match=f"{kind.value} intent must not expose transport fields"):
        RuntimeIntent(
            kind=kind,
            action="SORTER_FEED_BIN",
            idempotency_key="bin-operation:trace-001",
            payload_json={
                "carrier_type": "CTU",
                "moves": [
                    {
                        "sequence_no": 1,
                        "bin_code": "BIN-001",
                        "source_type": "RACK_SLOT",
                        "source_code": "SINGLE_LAYER_A:01",
                        "target_type": "SORTER_STATION",
                        "target_code": "SORTER-01",
                        "dispatch_key": "caller-owned-dispatch-key",
                    }
                ],
            },
            timeout_seconds=1800,
        )


@pytest.mark.parametrize(
    "kind",
    [RuntimeIntentKind.BIN_OPERATION_REQUEST, RuntimeIntentKind.RACK_BIN_EXCHANGE_REQUEST],
)
def test_handling_operation_request_requires_operation_key_moves_carrier_and_timeout(kind):
    with pytest.raises(ValueError, match=f"{kind.value} intent requires operation_type"):
        RuntimeIntent(
            kind=kind,
            idempotency_key="bin-operation:trace-001",
            payload_json={"carrier_type": "CTU", "moves": [{"sequence_no": 1}]},
            timeout_seconds=1800,
        )

    with pytest.raises(ValueError, match=f"{kind.value} intent requires operation_key"):
        RuntimeIntent(
            kind=kind,
            action="SORTER_FEED_BIN",
            payload_json={"carrier_type": "CTU", "moves": [{"sequence_no": 1}]},
            timeout_seconds=1800,
        )

    with pytest.raises(ValueError, match=f"{kind.value} intent requires payload.moves"):
        RuntimeIntent(
            kind=kind,
            action="SORTER_FEED_BIN",
            idempotency_key="bin-operation:trace-001",
            payload_json={"carrier_type": "CTU", "moves": []},
            timeout_seconds=1800,
        )

    with pytest.raises(ValueError, match=f"{kind.value} intent requires carrier_type"):
        RuntimeIntent(
            kind=kind,
            action="SORTER_FEED_BIN",
            idempotency_key="bin-operation:trace-001",
            payload_json={"moves": [{"sequence_no": 1}]},
            timeout_seconds=1800,
        )

    with pytest.raises(ValueError, match=f"{kind.value} intent requires timeout_seconds"):
        RuntimeIntent(
            kind=kind,
            action="SORTER_FEED_BIN",
            idempotency_key="bin-operation:trace-001",
            payload_json={"carrier_type": "CTU", "moves": [{"sequence_no": 1}]},
            timeout_seconds=0,
        )
