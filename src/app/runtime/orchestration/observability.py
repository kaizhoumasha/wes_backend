"""Phase 3 runtime observability contract registry."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RuntimeObservabilitySignal:
    """Stable runtime observability signal definition."""

    name: str
    signal_type: str
    required_attributes: frozenset[str]


@dataclass(frozen=True, slots=True)
class RuntimeObservabilityValidation:
    """Validation result for an emitted signal."""

    valid: bool
    missing_attributes: tuple[str, ...] = ()
    reason: str = "OK"


class RuntimeObservabilityRegistry:
    """Registry for stable Phase 3 span/metric/log event contracts."""

    def __init__(self, signals: dict[str, RuntimeObservabilitySignal] | None = None) -> None:
        self.signals = signals or default_runtime_observability_signals()

    def validate(self, name: str, attributes: dict[str, object]) -> RuntimeObservabilityValidation:
        signal = self.signals.get(name)
        if signal is None:
            return RuntimeObservabilityValidation(valid=False, reason="UNKNOWN_SIGNAL")
        missing = tuple(sorted(attr for attr in signal.required_attributes if attributes.get(attr) in {None, ""}))
        if missing:
            return RuntimeObservabilityValidation(
                valid=False,
                missing_attributes=missing,
                reason="MISSING_REQUIRED_ATTRIBUTES",
            )
        return RuntimeObservabilityValidation(valid=True)


def default_runtime_observability_signals() -> dict[str, RuntimeObservabilitySignal]:
    common = frozenset({"trace_id", "correlation_id"})
    return {
        "callback.normalize": RuntimeObservabilitySignal(
            "callback.normalize",
            "span+metric+log",
            common | {"provider_code", "operation_kind"},
        ),
        "runtime_inbox.claim": RuntimeObservabilitySignal(
            "runtime_inbox.claim",
            "span+metric",
            common | {"source_event_id", "operation_kind"},
        ),
        "runtime_intent.dispatch": RuntimeObservabilitySignal(
            "runtime_intent.dispatch",
            "span+metric+log",
            common | {"operation_kind"},
        ),
        "device_command.ack": RuntimeObservabilitySignal(
            "device_command.ack",
            "span+metric",
            common | {"command_code"},
        ),
        "device_command.result": RuntimeObservabilitySignal(
            "device_command.result",
            "span+metric",
            common | {"command_code"},
        ),
        "wms_breaker.transition": RuntimeObservabilitySignal(
            "wms_breaker.transition",
            "metric+log",
            frozenset({"trace_id", "provider_code", "operation_kind"}),
        ),
        "scenario_replay.run": RuntimeObservabilitySignal(
            "scenario_replay.run",
            "span+metric",
            frozenset({"trace_id", "scenario_id", "operation_kind"}),
        ),
    }


runtime_observability_registry = RuntimeObservabilityRegistry()


__all__ = [
    "RuntimeObservabilityRegistry",
    "RuntimeObservabilitySignal",
    "RuntimeObservabilityValidation",
    "default_runtime_observability_signals",
    "runtime_observability_registry",
]
