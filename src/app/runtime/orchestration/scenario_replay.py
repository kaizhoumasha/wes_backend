"""Phase 3 deterministic scenario recording and replay contracts."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any


@dataclass(frozen=True, slots=True)
class ScenarioEvent:
    """One sanitized runtime event in a replay scenario."""

    event_id: str
    kind: str
    occurred_at: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ScenarioRecording:
    """Deterministic scenario recording."""

    scenario_id: str
    schema_version: str
    events: tuple[ScenarioEvent, ...]


@dataclass(frozen=True, slots=True)
class ScenarioReplayResult:
    """Replay result used by Phase 3 gates."""

    scenario_id: str
    timeline: tuple[str, ...]
    projection_hash: str
    outbox_effect_keys: tuple[str, ...]
    reconciliation_reasons: tuple[str, ...]


class ScenarioRecorder:
    """Build sanitized scenario recordings."""

    def record(self, *, scenario_id: str, events: list[ScenarioEvent]) -> ScenarioRecording:
        sanitized = tuple(
            ScenarioEvent(
                event_id=event.event_id,
                kind=event.kind,
                occurred_at=event.occurred_at,
                payload=self._sanitize_payload(event.payload),
            )
            for event in sorted(events, key=lambda item: (item.occurred_at, item.event_id))
        )
        return ScenarioRecording(scenario_id=scenario_id, schema_version="scenario.v1", events=sanitized)

    def _sanitize_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        sanitized: dict[str, Any] = {}
        for key, value in payload.items():
            if key == "pkg_code" and isinstance(value, str):
                sanitized[key] = f"***{value[-4:]}"
            elif key == "bin_code" and isinstance(value, str):
                sanitized[key] = f"{value[:3]}***"
            else:
                sanitized[key] = value
        return sanitized


class ScenarioReplayRunner:
    """Deterministic replay for projection, timeline, outbox and reconciliation."""

    _RECONCILING_KINDS = frozenset({"runtime_conflict", "wms_reject", "device_timeout", "callback_out_of_order"})

    def replay(self, recording: ScenarioRecording) -> ScenarioReplayResult:
        timeline: list[str] = []
        outbox_effect_keys: list[str] = []
        reconciliation_reasons: list[str] = []
        projection_state: dict[str, Any] = {}

        for event in recording.events:
            timeline.append(f"{event.kind}:{event.event_id}")
            effect_key = event.payload.get("effect_key")
            if isinstance(effect_key, str) and effect_key not in outbox_effect_keys:
                outbox_effect_keys.append(effect_key)
            object_key = event.payload.get("object_key")
            if isinstance(object_key, str):
                projection_state[object_key] = event.payload.get("state", event.kind)
            if event.kind in self._RECONCILING_KINDS:
                reason = event.payload.get("reason")
                reconciliation_reasons.append(str(reason or event.kind))

        projection_items = sorted(f"{key}={value}" for key, value in projection_state.items())
        projection_hash = sha256("|".join(projection_items).encode("utf-8")).hexdigest()
        return ScenarioReplayResult(
            scenario_id=recording.scenario_id,
            timeline=tuple(timeline),
            projection_hash=projection_hash,
            outbox_effect_keys=tuple(outbox_effect_keys),
            reconciliation_reasons=tuple(reconciliation_reasons),
        )


scenario_recorder = ScenarioRecorder()
scenario_replay_runner = ScenarioReplayRunner()


__all__ = [
    "ScenarioEvent",
    "ScenarioRecorder",
    "ScenarioRecording",
    "ScenarioReplayResult",
    "ScenarioReplayRunner",
    "scenario_recorder",
    "scenario_replay_runner",
]
