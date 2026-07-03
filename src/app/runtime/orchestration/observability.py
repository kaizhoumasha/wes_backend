"""Phase 3 runtime observability contract registry."""

from __future__ import annotations

import os
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
RuntimeOpenTelemetryPostJson = Callable[[str, Mapping[str, object], Mapping[str, str], float], None]


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


class RuntimeOpenTelemetryHttpExporter:
    """Best-effort HTTP JSON exporter for the production OpenTelemetry backend adapter."""

    def __init__(
        self,
        *,
        endpoint: str,
        service_name: str,
        environment: str,
        headers: Mapping[str, str] | None = None,
        timeout_seconds: float = 0.5,
        post_json: RuntimeOpenTelemetryPostJson | None = None,
    ) -> None:
        self._endpoint = endpoint.strip()
        if not self._endpoint:
            raise ValueError("runtime OpenTelemetry endpoint is required")
        self._service_name = service_name
        self._environment = environment
        self._timeout_seconds = timeout_seconds
        merged_headers = {"content-type": "application/json"}
        merged_headers.update(dict(headers or {}))
        self._headers = MappingProxyType(merged_headers)
        self._post_json = post_json or _post_runtime_open_telemetry_json

    def emit_span(self, name: str, attributes: Mapping[str, object]) -> None:
        """Export a span payload."""

        self._emit("span", name, attributes)

    def emit_metric(self, name: str, attributes: Mapping[str, object]) -> None:
        """Export a metric payload."""

        self._emit("metric", name, attributes)

    def emit_log_event(self, name: str, attributes: Mapping[str, object]) -> None:
        """Export a log event payload."""

        self._emit("log", name, attributes)

    def _emit(self, signal_kind: str, name: str, attributes: Mapping[str, object]) -> None:
        payload: dict[str, object] = {
            "service_name": self._service_name,
            "environment": self._environment,
            "signal_kind": signal_kind,
            "name": name,
            "attributes": dict(attributes),
        }
        self._post_json(self._endpoint, payload, self._headers, self._timeout_seconds)


class RuntimeObservabilityRegistry:
    """Registry for stable Phase 3 span/metric/log event contracts."""

    def __init__(
        self,
        signals: dict[str, RuntimeObservabilitySignal] | None = None,
        *,
        observers: Iterable[RuntimeObservabilityObserver] | None = None,
    ) -> None:
        self.signals = signals or default_runtime_observability_signals()
        self._observers = {f"anonymous:{index}": observer for index, observer in enumerate(observers or ())}

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

    def register_observer(self, observer: RuntimeObservabilityObserver, *, key: str | None = None) -> None:
        """Register or replace an observer by key so production backends are idempotent."""

        observer_key = key or f"anonymous:{len(self._observers)}"
        self._observers[observer_key] = observer

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
        for observer in tuple(self._observers.values()):
            observer(event)
        return event


_RUNTIME_OTEL_OBSERVER_KEY = "runtime-open-telemetry-backend"


def configure_runtime_open_telemetry_backend(
    *,
    registry: RuntimeObservabilityRegistry | None = None,
    enabled: bool | None = None,
    endpoint: str | None = None,
    service_name: str | None = None,
    environment: str | None = None,
    headers: Mapping[str, str] | None = None,
    timeout_seconds: float | None = None,
    post_json: RuntimeOpenTelemetryPostJson | None = None,
) -> bool:
    """Attach the configured production OpenTelemetry backend observer."""

    resolved_enabled = _env_bool("WES_RUNTIME_OTEL_ENABLED", default=False) if enabled is None else enabled
    if not resolved_enabled:
        return False

    resolved_endpoint = _first_text(endpoint, os.getenv("WES_RUNTIME_OTEL_ENDPOINT"))
    if resolved_endpoint is None:
        raise ValueError("WES_RUNTIME_OTEL_ENDPOINT is required when WES_RUNTIME_OTEL_ENABLED=true")

    exporter = RuntimeOpenTelemetryHttpExporter(
        endpoint=resolved_endpoint,
        service_name=_first_text(service_name, os.getenv("WES_RUNTIME_OTEL_SERVICE_NAME")) or "wes_backend",
        environment=_first_text(environment, os.getenv("APP_ENV")) or "unknown",
        headers=headers,
        timeout_seconds=timeout_seconds
        if timeout_seconds is not None
        else _env_float("WES_RUNTIME_OTEL_TIMEOUT_SECONDS", default=0.5),
        post_json=post_json,
    )
    (registry or runtime_observability_registry).register_observer(
        RuntimeOpenTelemetryBridge(exporter),
        key=_RUNTIME_OTEL_OBSERVER_KEY,
    )
    return True


def _first_text(*values: object) -> str | None:
    for value in values:
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                return stripped
    return None


def _env_bool(name: str, *, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, *, default: float) -> float:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _post_runtime_open_telemetry_json(
    endpoint: str,
    payload: Mapping[str, object],
    headers: Mapping[str, str],
    timeout_seconds: float,
) -> None:
    import httpx

    with httpx.Client(timeout=timeout_seconds, trust_env=False) as client:
        response = client.post(endpoint, json=dict(payload), headers=dict(headers))
        response.raise_for_status()


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
            common | {"command_code", "provider_code", "ack_age_ms"},
        ),
        "device_command.dispatch_policy": RuntimeObservabilitySignal(
            "device_command.dispatch_policy",
            "span+metric",
            common
            | {
                "command_code",
                "device_code",
                "provider_code",
                "policy_decision",
                "reason",
                "dispatch_allowed",
                "runtime_hold_required",
            },
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
    "RuntimeOpenTelemetryHttpExporter",
    "RuntimeOpenTelemetryPostJson",
    "configure_runtime_open_telemetry_backend",
    "default_runtime_observability_signals",
    "runtime_observability_registry",
]
