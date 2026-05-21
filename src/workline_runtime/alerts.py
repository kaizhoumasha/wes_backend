"""Runtime alerts derived from append-only runtime facts."""

from __future__ import annotations

from dataclasses import dataclass

from src.workline_runtime.runtime_event import RuntimeEvent, RuntimeEventType


@dataclass(frozen=True)
class RuntimeAlert:
    trace_id: str
    workline_id: int
    device_id: int | None
    material_identity_key: str | None
    reason_code: str
    owner: str | None
    suggested_action: str | None


def build_alerts(events: list[RuntimeEvent]) -> list[RuntimeAlert]:
    alerts: list[RuntimeAlert] = []

    for event in events:
        if event.event_type != RuntimeEventType.PROCESS_BLOCKED:
            continue

        suggested_action = event.payload_json.get("suggested_action")
        if suggested_action is not None and not isinstance(suggested_action, str):
            raise ValueError("suggested_action must be a string")

        alerts.append(
            RuntimeAlert(
                trace_id=event.trace_id,
                workline_id=event.workline_id,
                device_id=event.device_id,
                material_identity_key=event.material_identity_key,
                reason_code="UNKNOWN" if event.reason_code is None else event.reason_code,
                owner=event.owner,
                suggested_action=suggested_action,
            )
        )

    return alerts


__all__ = ["RuntimeAlert", "build_alerts"]
