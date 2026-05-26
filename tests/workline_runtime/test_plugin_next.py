import pytest

from src.workline_runtime.plugin_next import PluginNext
from src.workline_runtime.runtime_intent import (
    BlockScope,
    Destination,
    DestinationKind,
    RuntimeIntent,
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


def test_plugin_next_update_context_builds_runtime_intent():
    intent = PluginNext().update_context({"pkg_id": "L0001-1"})

    assert intent.kind == RuntimeIntentKind.UPDATE_CONTEXT
    assert intent.context_patch == {"pkg_id": "L0001-1"}


def test_plugin_next_complete_builds_runtime_intent():
    intent = PluginNext().complete({"bin_code": "BIN_463"})

    assert intent.kind == RuntimeIntentKind.COMPLETE
    assert intent.context_patch == {"bin_code": "BIN_463"}


def test_plugin_next_mark_ng_builds_runtime_intent():
    intent = PluginNext().mark_ng(
        reason_code="SCAN_NG",
        message="扫码判定 NG",
        payload={"PkgID": "BAD"},
        destination=Destination.ng_route(),
    )

    assert intent.kind == RuntimeIntentKind.MARK_NG
    assert intent.reason_code == "SCAN_NG"
    assert intent.message == "扫码判定 NG"
    assert intent.payload_json == {"PkgID": "BAD"}
    assert intent.destination == Destination.ng_route()


def test_plugin_next_continue_next_builds_runtime_intent_with_default_destination():
    intent = PluginNext().continue_next(action="MOVE_FORWARD", payload={"pkg_id": "L0001-1"})

    assert intent.kind == RuntimeIntentKind.CONTINUE_NEXT
    assert intent.action == "MOVE_FORWARD"
    assert intent.payload_json == {"pkg_id": "L0001-1"}
    assert intent.destination == Destination.next()


def test_plugin_next_external_request_builds_runtime_intent():
    intent = PluginNext().external_request(
        dispatch_key="external:smt:release-001:FULL_BIN_EXCHANGE",
        target_code="WMS_RCS_FULL_BOX_EXCHANGE",
        payload={"rack_release_id": "release-001"},
        timeout_seconds=1800,
        source_system="WMS_RCS",
    )

    assert intent.kind == RuntimeIntentKind.EXTERNAL_REQUEST
    assert intent.dispatch_key == "external:smt:release-001:FULL_BIN_EXCHANGE"
    assert intent.target_code == "WMS_RCS_FULL_BOX_EXCHANGE"
    assert intent.payload_json == {"rack_release_id": "release-001"}
    assert intent.timeout_seconds == 1800
    assert intent.source_system == "WMS_RCS"


def test_device_event_intent_builder():
    intent = PluginNext().device_event(
        device_code="SMT-RACK-RELEASE",
        event_type="SINGLE_LAYER_RACK_RELEASED",
        data={"rack_release_id": "release-001"},
        event_id="smt-release:release-001",
    )

    assert intent.kind == RuntimeIntentKind.DEVICE_EVENT
    assert intent.payload_json["device_code"] == "SMT-RACK-RELEASE"
    assert intent.payload_json["event_type"] == "SINGLE_LAYER_RACK_RELEASED"
    assert intent.payload_json["data"] == {"rack_release_id": "release-001"}
    assert intent.payload_json["event_id"] == "smt-release:release-001"


def test_plugin_next_resource_fact_builds_runtime_intent():
    intent = PluginNext().resource_fact(
        fact_type="MATERIAL_MOUNTED",
        payload={"pkg_code": "PKG-001", "bin_code": "BIN-001", "bin_cell_index": "4"},
        idempotency_key="MATERIAL_MOUNTED:CMD-001:PKG-001:BIN-001:4",
    )

    assert intent.kind == RuntimeIntentKind.RESOURCE_FACT
    assert intent.action == "MATERIAL_MOUNTED"
    assert intent.idempotency_key == "MATERIAL_MOUNTED:CMD-001:PKG-001:BIN-001:4"


def test_plugin_next_resource_reservation_builds_runtime_intent():
    intent = PluginNext().resource_reservation(
        operation="CLAIM_BIN_CELL",
        payload={"pkg_code": "PKG-001", "bin_code": "BIN-001", "bin_cell_index": "4"},
        idempotency_key="CLAIM_BIN_CELL:2001:BIN-001:4:PKG-001",
    )

    assert intent.kind == RuntimeIntentKind.RESOURCE_RESERVATION
    assert intent.action == "CLAIM_BIN_CELL"
    assert intent.idempotency_key == "CLAIM_BIN_CELL:2001:BIN-001:4:PKG-001"


def test_plugin_next_rack_operation_request_builds_runtime_intent():
    intent = PluginNext().rack_operation_request(
        operation_type="REPLACE_CLASSIFIER_WORK_RACK",
        operation_key="rack-operation:trace-001",
        target_code="WMS_RCS_RACK_OPERATION",
        payload={
            "work_position_code": "SINGLE_LAYER_A",
            "new_rack_kind": "SINGLE_LAYER",
            "move_out_target_position_role": "SMT_EMPTY_RACK_AREA",
        },
        timeout_seconds=1800,
    )

    assert intent.kind == RuntimeIntentKind.RACK_OPERATION_REQUEST
    assert intent.action == "REPLACE_CLASSIFIER_WORK_RACK"
    assert intent.idempotency_key == "rack-operation:trace-001"
    assert intent.dispatch_key is None
    assert intent.target_code == "WMS_RCS_RACK_OPERATION"
    assert intent.payload_json["work_position_code"] == "SINGLE_LAYER_A"
    assert intent.payload_json["new_rack_kind"] == "SINGLE_LAYER"


def test_plugin_next_bin_operation_request_builds_transport_free_runtime_intent():
    intent = PluginNext().bin_operation_request(
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
    assert intent.payload_json["carrier_type"] == "CTU"
    assert intent.payload_json["carrier_code"] == "CTU-01"
    assert intent.payload_json["moves"][0]["target_code"] == "SORTER-01"


def test_plugin_next_rack_bin_exchange_request_builds_transport_free_runtime_intent():
    intent = PluginNext().rack_bin_exchange_request(
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
        timeout_seconds=1800,
    )

    assert intent.kind == RuntimeIntentKind.RACK_BIN_EXCHANGE_REQUEST
    assert intent.action == "SINGLE_LAYER_FULL_BIN_EXCHANGE"
    assert intent.idempotency_key == "rack-bin-exchange:release-001"
    assert intent.dispatch_key is None
    assert intent.target_code is None
    assert intent.payload_json["carrier_type"] == "CTU"
    assert intent.payload_json["rack_code"] == "RACK-SINGLE-01"
    assert intent.payload_json["moves"][1]["placeholder_key"] == "EMPTY_BIN_FOR:SINGLE_LAYER_A:01"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {"device_code": "SMT-RACK-RELEASE", "event_type": "SINGLE_LAYER_RACK_RELEASED", "data": {}},
            "DEVICE_EVENT intent requires timestamp",
        ),
        (
            {
                "device_code": "SMT-RACK-RELEASE",
                "event_type": "SINGLE_LAYER_RACK_RELEASED",
                "timestamp": "invalid",
                "data": {},
            },
            "DEVICE_EVENT intent timestamp must be an integer",
        ),
        (
            {
                "device_code": "SMT-RACK-RELEASE",
                "event_type": "SINGLE_LAYER_RACK_RELEASED",
                "timestamp": 1770000000000,
                "data": None,
            },
            "DEVICE_EVENT intent data must be a dict",
        ),
        (
            {
                "device_code": "SMT-RACK-RELEASE",
                "event_type": "SINGLE_LAYER_RACK_RELEASED",
                "timestamp": 1770000000000,
                "data": [],
            },
            "DEVICE_EVENT intent data must be a dict",
        ),
    ],
)
def test_device_event_intent_rejects_invalid_payload_contract(payload, message):
    with pytest.raises(ValueError, match=message):
        RuntimeIntent(kind=RuntimeIntentKind.DEVICE_EVENT, payload_json=payload)
