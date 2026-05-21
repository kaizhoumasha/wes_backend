from __future__ import annotations

from typing import Any, cast

from src.workline_runtime.runtime_intent import Destination, RuntimeIntent


def handle_tote_arrived(payload: dict[str, object]) -> RuntimeIntent:
    tote_id = str(payload["tote_id"])
    station_code = str(payload["station_code"])

    return RuntimeIntent.command(
        device_role="WEIGH_SCALE",
        action="WEIGH_TOTE",
        payload={"tote_id": tote_id, "station_code": station_code},
        destination=Destination.role("WEIGH_SCALE"),
        timeout_seconds=120,
    )


def handle_weigh_result(payload: dict[str, object]) -> RuntimeIntent:
    tote_id = str(payload["tote_id"])
    actual_weight_kg = float(cast("Any", payload["actual_weight_kg"]))
    expected_weight_kg = float(cast("Any", payload["expected_weight_kg"]))
    tolerance_kg = float(cast("Any", payload["tolerance_kg"]))
    destination_lane = "PASS" if abs(actual_weight_kg - expected_weight_kg) <= tolerance_kg else "NG"

    return RuntimeIntent.command(
        device_role="DIVERT_CONVEYOR",
        action="DIVERT_TOTE",
        payload={"tote_id": tote_id, "destination_lane": destination_lane},
        destination=Destination.role("DIVERT_CONVEYOR"),
        timeout_seconds=120,
    )


__all__ = ["handle_tote_arrived", "handle_weigh_result"]
