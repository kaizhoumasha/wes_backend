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
        target_code="http://wms-rcs/api/full-box-exchange",
        payload={"rack_release_id": "release-001"},
        timeout_seconds=1800,
        source_system="WMS_RCS",
    )

    assert intent.kind == RuntimeIntentKind.EXTERNAL_REQUEST
    assert intent.dispatch_key == "external:smt:release-001:FULL_BIN_EXCHANGE"
    assert intent.target_code == "http://wms-rcs/api/full-box-exchange"
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
