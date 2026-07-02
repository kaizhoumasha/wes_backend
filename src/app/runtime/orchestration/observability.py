"""Phase 3 runtime observability contract registry."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol


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


class RuntimeOpenTelemetryExporter(Protocol):
    """Exporter port consumed by the runtime OpenTelemetry bridge."""

    def emit_span(self, name: str, attributes: Mapping[str, object]) -> None:
        """Export a validated span."""

    def emit_metric(self, name: str, attributes: Mapping[str, object]) -> None:
        """Export a validated metric."""

    def emit_log_event(self, name: str, attributes: Mapping[str, object]) -> None:
        """Export a validated log event."""


class RuntimeOpenTelemetryBridge:
    """Observer that fans validated runtime events out to OpenTelemetry-style exporters."""

    def __init__(self, exporter: RuntimeOpenTelemetryExporter) -> None:
        self._exporter = exporter

    def __call__(self, event: RuntimeObservabilityEvent) -> None:
        self.export(event)

    def export(self, event: RuntimeObservabilityEvent) -> None:
        """Export each signal kind declared by the stable contract."""

        for signal_kind in event.signal_type.split("+"):
            if signal_kind == "span":
                self._exporter.emit_span(event.name, event.attributes)
            elif signal_kind == "metric":
                self._exporter.emit_metric(event.name, event.attributes)
            elif signal_kind == "log":
                self._exporter.emit_log_event(event.name, event.attributes)
            else:
                raise ValueError(f"unsupported runtime observability signal kind: {signal_kind}")


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
        "wms_evidence.persistence_failure": RuntimeObservabilitySignal(
            "wms_evidence.persistence_failure",
            "metric+log",
            frozenset({"trace_id", "provider_code", "operation_kind", "evidence_key", "reason_code"}),
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
    "RuntimeOpenTelemetryBridge",
    "RuntimeOpenTelemetryExporter",
    "default_runtime_observability_signals",
    "runtime_observability_registry",
]
