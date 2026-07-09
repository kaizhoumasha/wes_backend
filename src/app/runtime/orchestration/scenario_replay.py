"""Deterministic runtime scenario recording and replay contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from src.utils.value_normalization import coerce_string_value


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
class ScenarioProjectionDiff:
    """One active projection state change observed during replay."""

    object_key: str
    from_state: str | None
    to_state: str


@dataclass(frozen=True, slots=True)
class ScenarioReplayResult:
    """Replay result used by runtime gates."""

    scenario_id: str
    timeline: tuple[str, ...]
    projection_hash: str
    outbox_effect_keys: tuple[str, ...]
    reconciliation_reasons: tuple[str, ...]
    projection_diff: tuple[ScenarioProjectionDiff, ...] = ()


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

    def record_simulator_events(
        self,
        *,
        scenario_id: str,
        simulator_events: list[Mapping[str, Any]],
    ) -> ScenarioRecording:
        """Build a replay recording from WMS/ECS simulator fixture events."""

        events: list[ScenarioEvent] = []
        for index, raw_event in enumerate(simulator_events, start=1):
            payload = raw_event.get("payload")
            event_payload = dict(payload) if isinstance(payload, Mapping) else {}
            source_system = raw_event.get("source_system") or raw_event.get("source")
            if source_system:
                event_payload.setdefault("source_system", str(source_system))
            occurred_at = raw_event.get("occurred_at")
            if not isinstance(occurred_at, str) or not occurred_at.strip():
                raise ValueError(f"simulator event missing occurred_at: index={index}")
            events.append(
                ScenarioEvent(
                    event_id=str(raw_event.get("event_id") or f"simulator-{index:04d}"),
                    kind=str(raw_event.get("kind") or "simulator_event"),
                    occurred_at=occurred_at,
                    payload=event_payload,
                )
            )
        return self.record(scenario_id=scenario_id, events=events)

    def record_trace_query_result(self, *, scenario_id: str, trace_result: Any) -> ScenarioRecording:
        """Build a replay recording from the production trace aggregation view."""

        trace = getattr(trace_result, "trace", None)
        trace_id = coerce_string_value(getattr(trace, "trace_id", None))
        request_id = coerce_string_value(getattr(trace, "request_id", None))
        events: list[ScenarioEvent] = []

        for inbox in getattr(trace_result, "inboxes", ()) or ():
            source_event_id = (
                coerce_string_value(getattr(inbox, "source_event_id", None))
                or f"inbox-{getattr(inbox, 'id', 'unknown')}"
            )
            payload = _payload_from(inbox)
            payload.setdefault("source_event_id", source_event_id)
            payload.setdefault("state", coerce_string_value(getattr(inbox, "status", None)) or "RECEIVED")
            _add_trace_payload(payload, trace_id=trace_id, request_id=request_id)
            events.append(
                ScenarioEvent(
                    event_id=source_event_id,
                    kind="runtime_inbox",
                    occurred_at=_occurred_at(inbox),
                    payload=payload,
                )
            )

        for command in getattr(trace_result, "commands", ()) or ():
            command_code = (
                coerce_string_value(getattr(command, "command_code", None))
                or f"command-{getattr(command, 'id', 'unknown')}"
            )
            payload = _payload_from(command)
            payload.setdefault("command_code", command_code)
            payload.setdefault("effect_key", f"device-command:{command_code}")
            payload.setdefault("state", coerce_string_value(getattr(command, "status", None)) or "UNKNOWN")
            provider_code = coerce_string_value(getattr(command, "provider_code", None))
            if provider_code:
                payload.setdefault("provider_code", provider_code)
            _add_trace_payload(payload, trace_id=trace_id, request_id=request_id)
            events.append(
                ScenarioEvent(
                    event_id=command_code,
                    kind="device_command",
                    occurred_at=_occurred_at(command),
                    payload=payload,
                )
            )

        for outbox in getattr(trace_result, "outboxes", ()) or ():
            dispatch_key = (
                coerce_string_value(getattr(outbox, "dispatch_key", None))
                or f"outbox-{getattr(outbox, 'id', 'unknown')}"
            )
            payload = _payload_from(outbox)
            payload.setdefault("effect_key", dispatch_key)
            payload.setdefault("state", coerce_string_value(getattr(outbox, "status", None)) or "UNKNOWN")
            dispatch_type = coerce_string_value(getattr(outbox, "dispatch_type", None))
            if dispatch_type:
                payload.setdefault("dispatch_type", dispatch_type)
            _add_trace_payload(payload, trace_id=trace_id, request_id=request_id)
            events.append(
                ScenarioEvent(
                    event_id=dispatch_key,
                    kind="runtime_outbox",
                    occurred_at=_occurred_at(outbox),
                    payload=payload,
                )
            )

        for timeline in getattr(trace_result, "timelines", ()) or ():
            timeline_id = (
                coerce_string_value(getattr(timeline, "event_id", None))
                or f"timeline-{getattr(timeline, 'id', 'unknown')}"
            )
            payload = _payload_from(timeline)
            action_type = coerce_string_value(getattr(timeline, "action_type", None)) or coerce_string_value(
                payload.get("canonical_event_type")
            )
            payload.setdefault(
                "state",
                coerce_string_value(getattr(timeline, "to_status", None))
                or coerce_string_value(getattr(timeline, "status", None)),
            )
            _add_trace_payload(payload, trace_id=trace_id, request_id=request_id)
            events.append(
                ScenarioEvent(
                    event_id=timeline_id,
                    kind=action_type or "runtime_timeline",
                    occurred_at=_occurred_at(timeline),
                    payload=payload,
                )
            )

        return self.record(scenario_id=scenario_id, events=events)

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


def _payload_from(record: Any) -> dict[str, Any]:
    for attr in ("payload_json", "payload", "trace_json"):
        payload = getattr(record, attr, None)
        if isinstance(payload, Mapping):
            return dict(payload)
    return {}


def _add_trace_payload(payload: dict[str, Any], *, trace_id: str | None, request_id: str | None) -> None:
    if trace_id:
        payload.setdefault("trace_id", trace_id)
    if request_id:
        payload.setdefault("request_id", request_id)


def _occurred_at(record: Any) -> str:
    for attr in ("occurred_at", "received_at", "created_at", "updated_at", "finished_at"):
        value = getattr(record, attr, None)
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=UTC)
            return value.isoformat().replace("+00:00", "Z")
        text = coerce_string_value(value)
        if text:
            return text
    return "1970-01-01T00:00:00Z"


class ScenarioReplayRunner:
    """Deterministic replay for projection, timeline, outbox and reconciliation."""

    _RECONCILING_KINDS = frozenset({"runtime_conflict", "wms_reject", "device_timeout", "callback_out_of_order"})

    def replay(self, recording: ScenarioRecording) -> ScenarioReplayResult:
        timeline: list[str] = []
        outbox_effect_keys: list[str] = []
        reconciliation_reasons: list[str] = []
        projection_state: dict[str, str] = {}
        projection_diff: list[ScenarioProjectionDiff] = []

        for event in recording.events:
            timeline.append(f"{event.kind}:{event.event_id}")
            effect_key = event.payload.get("effect_key")
            if isinstance(effect_key, str) and effect_key not in outbox_effect_keys:
                outbox_effect_keys.append(effect_key)
            object_key = event.payload.get("object_key")
            if isinstance(object_key, str):
                next_state = str(event.payload.get("state", event.kind))
                previous_state = projection_state.get(object_key)
                if previous_state != next_state:
                    projection_diff.append(
                        ScenarioProjectionDiff(
                            object_key=object_key,
                            from_state=previous_state,
                            to_state=next_state,
                        )
                    )
                projection_state[object_key] = next_state
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
            projection_diff=tuple(projection_diff),
        )


scenario_recorder = ScenarioRecorder()
scenario_replay_runner = ScenarioReplayRunner()


__all__ = [
    "ScenarioEvent",
    "ScenarioProjectionDiff",
    "ScenarioRecorder",
    "ScenarioRecording",
    "ScenarioReplayResult",
    "ScenarioReplayRunner",
    "scenario_recorder",
    "scenario_replay_runner",
]
