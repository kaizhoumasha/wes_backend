from __future__ import annotations

from src.workline_plugins.inbound_tote_qc_v2.plugin import handle_tote_arrived, handle_weigh_result
from src.workline_runtime.runtime_intent import DestinationKind, RuntimeIntentKind


def test_tote_arrived_returns_weigh_command_intent() -> None:
    intent = handle_tote_arrived({"tote_id": "T-001", "station_code": "S1"})

    assert intent.kind == RuntimeIntentKind.COMMAND
    assert intent.device_role == "WEIGH_SCALE"
    assert intent.action == "WEIGH_TOTE"
    assert intent.destination.kind == DestinationKind.ROLE
    assert intent.destination.value == "WEIGH_SCALE"


def test_tote_arrived_keeps_payload_fields() -> None:
    intent = handle_tote_arrived({"tote_id": "T-001", "station_code": "S1"})

    assert intent.payload_json == {"tote_id": "T-001", "station_code": "S1"}
    assert intent.timeout_seconds == 120


def test_weigh_result_routes_out_of_range_to_ng_lane() -> None:
    intent = handle_weigh_result(
        {
            "tote_id": "T-001",
            "actual_weight_kg": 12.5,
            "expected_weight_kg": 10.0,
            "tolerance_kg": 1.0,
        }
    )

    assert intent.kind == RuntimeIntentKind.COMMAND
    assert intent.device_role == "DIVERT_CONVEYOR"
    assert intent.action == "DIVERT_TOTE"
    assert intent.payload_json["destination_lane"] == "NG"


def test_weigh_result_routes_within_tolerance_to_pass_lane() -> None:
    intent = handle_weigh_result(
        {
            "tote_id": "T-001",
            "actual_weight_kg": 10.5,
            "expected_weight_kg": 10.0,
            "tolerance_kg": 1.0,
        }
    )

    assert intent.device_role == "DIVERT_CONVEYOR"
    assert intent.action == "DIVERT_TOTE"
    assert intent.destination.kind == DestinationKind.ROLE
    assert intent.destination.value == "DIVERT_CONVEYOR"
    assert intent.payload_json == {"tote_id": "T-001", "destination_lane": "PASS"}
    assert intent.timeout_seconds == 120
