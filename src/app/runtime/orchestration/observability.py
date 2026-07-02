"""Phase 3 runtime observability contract registry."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType


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


@dataclass(frozen=True, slots=True)
class RuntimeObservabilityEvent:
    """Validated runtime observability event ready for metric/log/span adapters."""

    name: str
    signal_type: str
    attributes: Mapping[str, object]


class RuntimeObservabilityEmissionError(ValueError):
    """Raised when an emitted observability event violates the stable contract."""

    def __init__(self, name: str, validation: RuntimeObservabilityValidation) -> None:
        super().__init__(f"runtime observability event {name!r} invalid: {validation.reason}")
        self.name = name
        self.validation = validation


RuntimeObservabilityObserver = Callable[[RuntimeObservabilityEvent], None]


class RuntimeObservabilityRegistry:
    """Registry for stable Phase 3 span/metric/log event contracts."""

    def __init__(
        self,
        signals: dict[str, RuntimeObservabilitySignal] | None = None,
        *,
        observers: Iterable[RuntimeObservabilityObserver] | None = None,
    ) -> None:
        self.signals = signals or default_runtime_observability_signals()
        self._observers = tuple(observers or ())

    def validate(self, name: str, attributes: dict[str, object]) -> RuntimeObservabilityValidation:
        signal = self.signals.get(name)
        if signal is None:
            return RuntimeObservabilityValidation(valid=False, reason="UNKNOWN_SIGNAL")
        missing = tuple(
            sorted(
                attr for attr in signal.required_attributes if (value := attributes.get(attr)) is None or value == ""
            )
        )
        if missing:
            return RuntimeObservabilityValidation(
                valid=False,
                missing_attributes=missing,
                reason="MISSING_REQUIRED_ATTRIBUTES",
            )
        return RuntimeObservabilityValidation(valid=True)

    def emit(self, name: str, attributes: dict[str, object]) -> RuntimeObservabilityEvent:
        """Validate and publish a stable observability event to configured adapters."""

        validation = self.validate(name, attributes)
        if not validation.valid:
            raise RuntimeObservabilityEmissionError(name, validation)

        signal = self.signals[name]
        event = RuntimeObservabilityEvent(
            name=signal.name,
            signal_type=signal.signal_type,
            attributes=MappingProxyType(dict(attributes)),
        )
        for observer in self._observers:
            observer(event)
        return event


def default_runtime_observability_signals() -> dict[str, RuntimeObservabilitySignal]:
    common = frozenset({"trace_id", "correlation_id"})
    return {
        "callback.normalize": RuntimeObservabilitySignal(
            "callback.normalize",
            "span+metric+log",
            common | {"provider_code", "source_event_id"},
        ),
        "runtime_inbox.claim": RuntimeObservabilitySignal(
            "runtime_inbox.claim",
            "span+metric",
            common | {"operation_kind", "inbox_id"},
        ),
        "runtime_intent.dispatch": RuntimeObservabilitySignal(
            "runtime_intent.dispatch",
            "span+metric+log",
            common | {"provider_code", "operation_kind"},
        ),
        "device_command.ack": RuntimeObservabilitySignal(
            "device_command.ack",
            "span+metric",
            common | {"command_code", "provider_code"},
        ),
        "device_command.result": RuntimeObservabilitySignal(
            "device_command.result",
            "span+metric",
            common | {"command_code", "source_event_id"},
        ),
        "wms_breaker.transition": RuntimeObservabilitySignal(
            "wms_breaker.transition",
            "metric+log",
            frozenset({"trace_id", "provider_code", "operation_kind", "breaker_state"}),
        ),
        "scenario_replay.run": RuntimeObservabilitySignal(
            "scenario_replay.run",
            "span+metric",
            frozenset({"trace_id", "scenario_id", "operation_kind"}),
        ),
    }


runtime_observability_registry = RuntimeObservabilityRegistry()


__all__ = [
    "RuntimeObservabilityEmissionError",
    "RuntimeObservabilityEvent",
    "RuntimeObservabilityObserver",
    "RuntimeObservabilityRegistry",
    "RuntimeObservabilitySignal",
    "RuntimeObservabilityValidation",
    "default_runtime_observability_signals",
    "runtime_observability_registry",
]
