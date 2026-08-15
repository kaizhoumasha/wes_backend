"""Runtime observability contract registry."""

from __future__ import annotations

import logging
import os
import queue
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from math import isfinite
from types import MappingProxyType
from typing import Protocol

from src.app.wms_integration.operation_registry import WMS_OPERATIONS

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RuntimeObservabilitySignal:
    """严格声明属性、低基数标签和数值测量的观测信号。"""

    name: str
    signal_type: str
    required_attributes: frozenset[str]
    allowed_attributes: frozenset[str]
    fixed_attributes: Mapping[str, object]
    metric_label_attributes: frozenset[str]
    metric_measurement_attributes: frozenset[str]
    allowed_values: Mapping[str, frozenset[object]]

    def __post_init__(self) -> None:
        fixed_attributes = MappingProxyType(dict(self.fixed_attributes))
        allowed_values = MappingProxyType({name: frozenset(values) for name, values in self.allowed_values.items()})
        object.__setattr__(self, "fixed_attributes", fixed_attributes)
        object.__setattr__(self, "allowed_values", allowed_values)

        declared = self.allowed_attributes | frozenset(fixed_attributes)
        metric_attributes = self.metric_label_attributes | self.metric_measurement_attributes
        if not self.required_attributes <= declared:
            raise ValueError("required observability attributes must be declared")
        if not metric_attributes <= declared:
            raise ValueError("metric attributes must be declared")
        if not self.metric_label_attributes <= frozenset(allowed_values):
            raise ValueError("every metric label must declare a closed allowed value set")
        if set(allowed_values) - declared:
            raise ValueError("allowed observability values must reference declared attributes")
        if "metric" not in self.signal_type.split("+") and metric_attributes:
            raise ValueError("non-metric signal must not declare metric attributes")


@dataclass(frozen=True, slots=True)
class RuntimeObservabilityValidation:
    """Validation result for an emitted signal."""

    valid: bool
    missing_attributes: tuple[str, ...] = ()
    unexpected_attributes: tuple[str, ...] = ()
    invalid_attributes: tuple[str, ...] = ()
    reason: str = "OK"


@dataclass(frozen=True, slots=True)
class RuntimeObservabilityEvent:
    """Validated runtime observability event ready for metric/log/span adapters."""

    name: str
    signal_type: str
    attributes: Mapping[str, object]
    metric_attributes: Mapping[str, object]


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
                self._exporter.emit_metric(event.name, event.metric_attributes)
            elif signal_kind == "log":
                self._exporter.emit_log_event(event.name, event.attributes)
            else:
                raise ValueError(f"unsupported runtime observability signal kind: {signal_kind}")

    def close(self) -> None:
        close = getattr(self._exporter, "close", None)
        if callable(close):
            _ = close()


class RuntimeQueuedObservabilityObserver:
    """用有界后台队列隔离同步 exporter，避免阻塞 async 业务热路径。"""

    def __init__(
        self,
        observer: RuntimeObservabilityObserver,
        *,
        max_queue_size: int = 1024,
    ) -> None:
        if max_queue_size <= 0:
            raise ValueError("runtime observability queue size must be positive")
        self._observer = observer
        self._queue: queue.Queue[RuntimeObservabilityEvent] = queue.Queue(maxsize=max_queue_size)
        self._stopping = threading.Event()
        self._state_lock = threading.Lock()
        self._accepting = True
        self._dropped_count = 0
        self._worker = threading.Thread(
            target=self._run,
            name="runtime-observability-exporter",
            daemon=True,
        )
        self._worker.start()

    @property
    def dropped_count(self) -> int:
        return self._dropped_count

    @property
    def pending_count(self) -> int:
        return self._queue.qsize()

    def __call__(self, event: RuntimeObservabilityEvent) -> None:
        with self._state_lock:
            if not self._accepting:
                self._dropped_count += 1
                return
            try:
                self._queue.put_nowait(event)
            except queue.Full:
                # 观测不能反压或改变业务结果；队列满时只丢弃观测事件并保留可检查计数。
                self._dropped_count += 1

    def close(self) -> None:
        """停止接收后让 daemon worker 排空已有事件并退出。"""

        with self._state_lock:
            self._accepting = False
            self._stopping.set()
        self._queue.join()
        self._worker.join()

    def _run(self) -> None:
        while not self._stopping.is_set() or not self._queue.empty():
            try:
                event = self._queue.get(timeout=0.1)
            except queue.Empty:
                event = None
            if event is None:
                continue
            try:
                self._observer(event)
            except Exception:
                # 生产观测是 best-effort，exporter 故障不能改变 runtime operation 的业务结果。
                logger.warning("runtime observability exporter failed", exc_info=True)
            finally:
                self._queue.task_done()
        close = getattr(self._observer, "close", None)
        if callable(close):
            _ = close()


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
        self._post_json = post_json or _RuntimeOpenTelemetryHttpPoster()

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

    def close(self) -> None:
        close = getattr(self._post_json, "close", None)
        if callable(close):
            _ = close()


class RuntimeObservabilityRegistry:
    """Registry for stable runtime span/metric/log event contracts."""

    def __init__(
        self,
        signals: dict[str, RuntimeObservabilitySignal] | None = None,
        *,
        observers: Iterable[RuntimeObservabilityObserver] | None = None,
    ) -> None:
        self.signals = signals or default_runtime_observability_signals()
        self._observers = {f"anonymous:{index}": observer for index, observer in enumerate(observers or ())}
        self._observer_lock = threading.Lock()

    def validate(  # noqa: PLR0911 - 每个 fail-closed 原因保留独立、稳定诊断码。
        self,
        name: str,
        attributes: dict[str, object],
    ) -> RuntimeObservabilityValidation:
        signal = self.signals.get(name)
        if signal is None:
            return RuntimeObservabilityValidation(valid=False, reason="UNKNOWN_SIGNAL")

        sensitive_attributes = tuple(sorted(attr for attr in attributes if _is_sensitive_attribute_name(attr)))
        if sensitive_attributes:
            return RuntimeObservabilityValidation(
                valid=False,
                invalid_attributes=sensitive_attributes,
                reason="SENSITIVE_ATTRIBUTE",
            )
        fixed_overrides = tuple(sorted(frozenset(attributes) & frozenset(signal.fixed_attributes)))
        if fixed_overrides:
            return RuntimeObservabilityValidation(
                valid=False,
                invalid_attributes=fixed_overrides,
                reason="FIXED_ATTRIBUTE_OVERRIDE",
            )
        unexpected = tuple(sorted(frozenset(attributes) - signal.allowed_attributes))
        if unexpected:
            return RuntimeObservabilityValidation(
                valid=False,
                unexpected_attributes=unexpected,
                reason="UNEXPECTED_ATTRIBUTES",
            )
        merged_attributes = {**signal.fixed_attributes, **attributes}
        missing = tuple(
            sorted(
                attr
                for attr in signal.required_attributes
                if (value := merged_attributes.get(attr)) is None or value == ""
            )
        )
        if missing:
            return RuntimeObservabilityValidation(
                valid=False,
                missing_attributes=missing,
                reason="MISSING_REQUIRED_ATTRIBUTES",
            )
        non_scalar = tuple(
            sorted(attr for attr, value in merged_attributes.items() if not isinstance(value, (str, int, float, bool)))
        )
        if non_scalar:
            return RuntimeObservabilityValidation(
                valid=False,
                invalid_attributes=non_scalar,
                reason="NON_SCALAR_ATTRIBUTE",
            )
        sensitive_values = tuple(
            sorted(
                attr
                for attr, value in merged_attributes.items()
                if isinstance(value, str) and _contains_sensitive_observability_value(value)
            )
        )
        if sensitive_values:
            return RuntimeObservabilityValidation(
                valid=False,
                invalid_attributes=sensitive_values,
                reason="SENSITIVE_VALUE",
            )
        invalid_values = tuple(
            sorted(
                attr
                for attr, allowed in signal.allowed_values.items()
                if attr in merged_attributes and merged_attributes[attr] not in allowed
            )
        )
        if invalid_values:
            return RuntimeObservabilityValidation(
                valid=False,
                invalid_attributes=invalid_values,
                reason="ATTRIBUTE_VALUE_NOT_ALLOWED",
            )
        invalid_measurements = tuple(
            sorted(
                attr
                for attr in signal.metric_measurement_attributes
                if not _is_non_negative_finite_number(merged_attributes.get(attr))
            )
        )
        if invalid_measurements:
            return RuntimeObservabilityValidation(
                valid=False,
                invalid_attributes=invalid_measurements,
                reason="INVALID_METRIC_MEASUREMENT",
            )
        return RuntimeObservabilityValidation(valid=True)

    def register_observer(self, observer: RuntimeObservabilityObserver, *, key: str | None = None) -> None:
        """Register or replace an observer by key so production backends are idempotent."""

        observer_key = key or f"anonymous:{len(self._observers)}"
        with self._observer_lock:
            previous = self._observers.get(observer_key)
            self._observers[observer_key] = observer
        close = getattr(previous, "close", None)
        if callable(close):
            _ = close()

    def close(self) -> None:
        """停止接收新观测，排空并关闭所有 exporter；registry 可在下一次 lifespan 重新配置。"""

        with self._observer_lock:
            observers = tuple(self._observers.values())
            self._observers.clear()
        for observer in observers:
            close = getattr(observer, "close", None)
            if callable(close):
                _ = close()

    def emit(self, name: str, attributes: dict[str, object]) -> RuntimeObservabilityEvent:
        """Validate and publish a stable observability event to configured adapters."""

        validation = self.validate(name, attributes)
        if not validation.valid:
            raise RuntimeObservabilityEmissionError(name, validation)

        signal = self.signals[name]
        merged_attributes = {**signal.fixed_attributes, **attributes}
        metric_attribute_names = signal.metric_label_attributes | signal.metric_measurement_attributes
        event = RuntimeObservabilityEvent(
            name=signal.name,
            signal_type=signal.signal_type,
            attributes=MappingProxyType(merged_attributes),
            metric_attributes=MappingProxyType({name: merged_attributes[name] for name in metric_attribute_names}),
        )
        with self._observer_lock:
            observers = tuple(self._observers.values())
        for observer in observers:
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
    queued_observer = RuntimeQueuedObservabilityObserver(RuntimeOpenTelemetryBridge(exporter))
    (registry or runtime_observability_registry).register_observer(
        queued_observer,
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


class _RuntimeOpenTelemetryHttpPoster:
    """后台 worker 专用的持久 HTTP client，复用连接池发送多种 signal。"""

    def __init__(self) -> None:
        import httpx

        self._client = httpx.Client(trust_env=False)

    def __call__(
        self,
        endpoint: str,
        payload: Mapping[str, object],
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> None:
        response = self._client.post(
            endpoint,
            json=dict(payload),
            headers=dict(headers),
            timeout=timeout_seconds,
        )
        _ = response.raise_for_status()

    def close(self) -> None:
        self._client.close()


_SENSITIVE_ATTRIBUTE_FRAGMENTS = (
    "authorization",
    "canonical_payload",
    "credential_reference",
    "header",
    "password",
    "payload",
    "secret",
    "signature",
    "token",
)
_SENSITIVE_VALUE_FRAGMENTS = (
    "authorization:",
    "bearer ",
    "secret://",
    "x-wes-signature",
)


def _is_sensitive_attribute_name(name: str) -> bool:
    normalized = name.strip().lower()
    return any(fragment in normalized for fragment in _SENSITIVE_ATTRIBUTE_FRAGMENTS)


def _contains_sensitive_observability_value(value: str) -> bool:
    normalized = value.strip().lower()
    return any(fragment in normalized for fragment in _SENSITIVE_VALUE_FRAGMENTS)


def _is_non_negative_finite_number(value: object) -> bool:
    return (
        isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(float(value)) and float(value) >= 0
    )


_OBSERVABILITY_POLICY_VERSION = "northbound-observability.v1"
_RUNTIME_OUTCOMES = frozenset({"success", "failed", "skipped", "resource_wait"})
_NORTHBOUND_OUTCOMES = frozenset(
    {
        "SUCCESS",
        "BUSINESS_REJECT",
        "TECHNICAL_FAILURE",
        "CONTRACT_FAILURE",
        "UNKNOWN",
        "RECONCILING",
    }
)
_NORTHBOUND_TRACE_STAGES = frozenset(
    {
        "QUERY_EVIDENCE",
        "POLICY_DECISION",
        "RUNTIME_INTENT_LOG",
        "DISPATCH_ATTEMPT",
        "CALLBACK",
        "RECONCILIATION",
    }
)
_WMS_PROVIDER_PROFILE_IDENTITIES = frozenset({"wms.2026-07-28.full-factory"})
_WMS_EFFECT_OPERATION_IDENTITIES = frozenset(
    operation.identity for operation in WMS_OPERATIONS if operation.mode.value == "EFFECT"
)
_WMS_ASYNC_EFFECT_OPERATION_IDENTITIES = frozenset(
    operation.identity for operation in WMS_OPERATIONS if operation.supports_status_query
)
_WMS_EFFECT_SUBMIT_OUTCOMES = frozenset({"ACCEPTED", "AMBIGUOUS", "NOT_SENT"})
_WMS_EFFECT_STATUS_STATES = frozenset({"ACCEPTED", "PROCESSING", "COMPLETED", "REJECTED", "NOT_FOUND"})
_WMS_EFFECT_BACKPRESSURE_OUTCOMES = frozenset({"RATE_LIMITED", "TIMEOUT", "RETRYABLE_FAILURE", "CIRCUIT_OPEN"})
_WMS_EFFECT_RECOVERY_OUTCOMES = frozenset(
    {
        "NOT_FOUND_GRACE_EXHAUSTED",
        "QUERY_BUDGET_EXHAUSTED",
        "IDEMPOTENCY_CONFLICT",
        "RECONCILIATION_OPENED",
    }
)
_WMS_EFFECT_CALLBACK_HINT_OUTCOMES = frozenset(
    {"RECEIVED", "REJECTED", "DUPLICATE", "QUERY_TRIGGERED", "ENQUEUE_DEGRADED"}
)
_WMS_BREAKER_STATES = frozenset({"OPEN", "HALF_OPEN", "CLOSED"})


def _runtime_signal(
    name: str,
    signal_type: str,
    required_attributes: frozenset[str],
    *,
    optional_attributes: frozenset[str] = frozenset(),
    metric_labels: frozenset[str] = frozenset(),
    metric_measurements: frozenset[str] = frozenset(),
    allowed_values: Mapping[str, frozenset[object]] | None = None,
) -> RuntimeObservabilitySignal:
    fixed_attributes: dict[str, object] = {
        "capability_identity": f"{name}@v1",
        "policy_version": _OBSERVABILITY_POLICY_VERSION,
    }
    if "metric" in signal_type.split("+"):
        fixed_attributes["sample_count"] = 1
        metric_measurements |= {"sample_count"}
        metric_labels |= {"capability_identity", "policy_version"}
    values = {
        "capability_identity": frozenset({fixed_attributes["capability_identity"]}),
        "policy_version": frozenset({_OBSERVABILITY_POLICY_VERSION}),
        **dict(allowed_values or {}),
    }
    return RuntimeObservabilitySignal(
        name=name,
        signal_type=signal_type,
        required_attributes=required_attributes | frozenset(fixed_attributes),
        allowed_attributes=required_attributes | optional_attributes,
        fixed_attributes=fixed_attributes,
        metric_label_attributes=metric_labels,
        metric_measurement_attributes=metric_measurements,
        allowed_values=values,
    )


def _northbound_operation_signal(
    *,
    name: str,
    operation_identity: str,
) -> RuntimeObservabilitySignal:
    return RuntimeObservabilitySignal(
        name=name,
        signal_type="span+metric+log",
        required_attributes=frozenset(
            {
                "capability_identity",
                "operation_identity",
                "provider_profile_identity",
                "outcome",
                "policy_version",
                "latency_ms",
                "sample_count",
                "unknown_count",
                "trace_id",
                "correlation_id",
                "evidence_ref",
                "stage",
            }
        ),
        allowed_attributes=frozenset(
            {
                "provider_profile_identity",
                "outcome",
                "latency_ms",
                "sample_count",
                "unknown_count",
                "trace_id",
                "correlation_id",
                "evidence_ref",
                "stage",
            }
        ),
        fixed_attributes={
            "capability_identity": operation_identity,
            "operation_identity": operation_identity,
            "policy_version": _OBSERVABILITY_POLICY_VERSION,
        },
        metric_label_attributes=frozenset(
            {
                "capability_identity",
                "operation_identity",
                "provider_profile_identity",
                "outcome",
                "policy_version",
            }
        ),
        metric_measurement_attributes=frozenset({"latency_ms", "sample_count", "unknown_count"}),
        allowed_values={
            "capability_identity": frozenset({operation_identity}),
            "operation_identity": frozenset({operation_identity}),
            "provider_profile_identity": _WMS_PROVIDER_PROFILE_IDENTITIES,
            "outcome": _NORTHBOUND_OUTCOMES,
            "policy_version": frozenset({_OBSERVABILITY_POLICY_VERSION}),
            "stage": _NORTHBOUND_TRACE_STAGES,
        },
    )


def default_runtime_observability_signals() -> dict[str, RuntimeObservabilitySignal]:
    common = frozenset({"trace_id", "correlation_id"})
    return {
        "callback.normalize": _runtime_signal(
            "callback.normalize",
            "span+metric+log",
            common | {"provider_code", "source_event_id"},
        ),
        "wms_effect.submit": _runtime_signal(
            "wms_effect.submit",
            "span+metric+log",
            frozenset({"operation_identity", "outcome", "latency_ms", "retry_count", "dispatch_key_hash"}),
            metric_labels=frozenset({"operation_identity", "outcome"}),
            metric_measurements=frozenset({"latency_ms", "retry_count"}),
            allowed_values={
                "operation_identity": _WMS_EFFECT_OPERATION_IDENTITIES,
                "outcome": _WMS_EFFECT_SUBMIT_OUTCOMES,
            },
        ),
        "wms_effect.status_query": _runtime_signal(
            "wms_effect.status_query",
            "span+metric+log",
            frozenset(
                {
                    "operation_identity",
                    "state",
                    "latency_ms",
                    "retry_count",
                    "age_ms",
                    "dispatch_key_hash",
                }
            ),
            metric_labels=frozenset({"operation_identity", "state"}),
            metric_measurements=frozenset({"latency_ms", "retry_count", "age_ms"}),
            allowed_values={
                "operation_identity": _WMS_ASYNC_EFFECT_OPERATION_IDENTITIES,
                "state": _WMS_EFFECT_STATUS_STATES,
            },
        ),
        "wms_effect.status_backlog": _runtime_signal(
            "wms_effect.status_backlog",
            "metric+log",
            frozenset(
                {
                    "backlog_count",
                    "max_overdue_age_ms",
                    "max_confirmation_age_ms",
                    "claimed_count",
                    "duration_ms",
                }
            ),
            metric_measurements=frozenset(
                {
                    "backlog_count",
                    "max_overdue_age_ms",
                    "max_confirmation_age_ms",
                    "claimed_count",
                    "duration_ms",
                }
            ),
        ),
        "wms_effect.status_backpressure": _runtime_signal(
            "wms_effect.status_backpressure",
            "span+metric+log",
            frozenset(
                {
                    "operation_identity",
                    "outcome",
                    "retry_after_ms",
                    "actual_backoff_ms",
                    "dispatch_key_hash",
                }
            ),
            optional_attributes=frozenset({"breaker_state"}),
            metric_labels=frozenset({"operation_identity", "outcome"}),
            metric_measurements=frozenset({"retry_after_ms", "actual_backoff_ms"}),
            allowed_values={
                "operation_identity": _WMS_ASYNC_EFFECT_OPERATION_IDENTITIES,
                "outcome": _WMS_EFFECT_BACKPRESSURE_OUTCOMES,
                "breaker_state": _WMS_BREAKER_STATES,
            },
        ),
        "wms_effect.recovery": _runtime_signal(
            "wms_effect.recovery",
            "span+metric+log",
            frozenset({"operation_identity", "outcome", "age_ms", "dispatch_key_hash"}),
            metric_labels=frozenset({"operation_identity", "outcome"}),
            metric_measurements=frozenset({"age_ms"}),
            allowed_values={
                "operation_identity": _WMS_ASYNC_EFFECT_OPERATION_IDENTITIES,
                "outcome": _WMS_EFFECT_RECOVERY_OUTCOMES,
            },
        ),
        "wms_effect.callback_hint": _runtime_signal(
            "wms_effect.callback_hint",
            "span+metric+log",
            frozenset({"outcome"}),
            optional_attributes=frozenset({"operation_identity", "dispatch_key_hash"}),
            metric_labels=frozenset({"outcome"}),
            allowed_values={
                "operation_identity": _WMS_ASYNC_EFFECT_OPERATION_IDENTITIES,
                "outcome": _WMS_EFFECT_CALLBACK_HINT_OUTCOMES,
            },
        ),
        "runtime_inbox.claim_batch": _runtime_signal(
            "runtime_inbox.claim_batch",
            "metric",
            frozenset({"claimed_count", "duration_ms"}),
            metric_measurements=frozenset({"claimed_count", "duration_ms"}),
        ),
        "runtime_inbox.processing": _runtime_signal(
            "runtime_inbox.processing",
            "span+metric",
            frozenset({"inbox_id", "duration_ms", "outcome"}),
            metric_labels=frozenset({"outcome"}),
            metric_measurements=frozenset({"duration_ms"}),
            allowed_values={"outcome": _RUNTIME_OUTCOMES},
        ),
        "runtime_inbox.lease_reclaim": _runtime_signal(
            "runtime_inbox.lease_reclaim",
            "metric",
            frozenset({"reclaimed_count"}),
            metric_measurements=frozenset({"reclaimed_count"}),
        ),
        "runtime_inbox.fencing_reject": _runtime_signal(
            "runtime_inbox.fencing_reject",
            "metric+log",
            frozenset({"inbox_id", "target_state"}),
        ),
        "runtime_inbox.resource_wait": _runtime_signal(
            "runtime_inbox.resource_wait",
            "metric",
            frozenset({"inbox_id"}),
        ),
        "runtime_inbox.dead_letter": _runtime_signal(
            "runtime_inbox.dead_letter",
            "metric+log",
            frozenset({"inbox_id"}),
        ),
        "runtime_intent.dispatch": _runtime_signal(
            "runtime_intent.dispatch",
            "span+metric+log",
            common | {"provider_code", "operation_kind"},
        ),
        "device_command.ack": _runtime_signal(
            "device_command.ack",
            "span+metric",
            common | {"command_code", "provider_code", "ack_age_ms"},
            metric_measurements=frozenset({"ack_age_ms"}),
        ),
        "device_command.dispatch_policy": _runtime_signal(
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
        "device_command.result": _runtime_signal(
            "device_command.result",
            "span+metric",
            common | {"command_code", "source_event_id"},
        ),
        "wms_breaker.transition": _runtime_signal(
            "wms_breaker.transition",
            "metric+log",
            frozenset({"trace_id", "provider_code", "operation_kind", "breaker_state"}),
        ),
        "wms_evidence.persistence_failure": _runtime_signal(
            "wms_evidence.persistence_failure",
            "metric+log",
            frozenset({"trace_id", "provider_code", "operation_kind", "evidence_key", "reason_code"}),
            optional_attributes=frozenset({"request_id", "http_status"}),
        ),
        "scenario_replay.run": _runtime_signal(
            "scenario_replay.run",
            "span+metric",
            frozenset({"trace_id", "scenario_id", "operation_kind"}),
        ),
        "northbound.dispatch.health": RuntimeObservabilitySignal(
            name="northbound.dispatch.health",
            signal_type="metric+log",
            required_attributes=frozenset(
                {
                    "capability_identity",
                    "policy_version",
                    "backlog_count",
                    "active_lease_count",
                    "unknown_count",
                    "oldest_queue_age_seconds",
                    "rate_limited_bucket_count",
                    "paused_bucket_count",
                    "lease_contended_bucket_count",
                    "lease_loss_count",
                }
            ),
            allowed_attributes=frozenset(
                {
                    "backlog_count",
                    "active_lease_count",
                    "unknown_count",
                    "oldest_queue_age_seconds",
                    "rate_limited_bucket_count",
                    "paused_bucket_count",
                    "lease_contended_bucket_count",
                    "lease_loss_count",
                }
            ),
            fixed_attributes={
                "capability_identity": "runtime.northbound.dispatch@v1",
                "policy_version": _OBSERVABILITY_POLICY_VERSION,
            },
            metric_label_attributes=frozenset({"capability_identity", "policy_version"}),
            metric_measurement_attributes=frozenset(
                {
                    "backlog_count",
                    "active_lease_count",
                    "unknown_count",
                    "oldest_queue_age_seconds",
                    "rate_limited_bucket_count",
                    "paused_bucket_count",
                    "lease_contended_bucket_count",
                    "lease_loss_count",
                }
            ),
            allowed_values={
                "capability_identity": frozenset({"runtime.northbound.dispatch@v1"}),
                "policy_version": frozenset({_OBSERVABILITY_POLICY_VERSION}),
            },
        ),
        "northbound.credential.resolve": RuntimeObservabilitySignal(
            name="northbound.credential.resolve",
            signal_type="metric+log",
            required_attributes=frozenset(
                {
                    "capability_identity",
                    "policy_version",
                    "provider_kind",
                    "outcome",
                    "sample_count",
                }
            ),
            allowed_attributes=frozenset({"provider_kind", "outcome", "sample_count"}),
            fixed_attributes={
                "capability_identity": "runtime.external-http-credential@v1",
                "policy_version": _OBSERVABILITY_POLICY_VERSION,
            },
            metric_label_attributes=frozenset({"capability_identity", "policy_version", "provider_kind", "outcome"}),
            metric_measurement_attributes=frozenset({"sample_count"}),
            allowed_values={
                "capability_identity": frozenset({"runtime.external-http-credential@v1"}),
                "policy_version": frozenset({_OBSERVABILITY_POLICY_VERSION}),
                "provider_kind": frozenset({"environment", "custom"}),
                "outcome": frozenset({"RESOLVED", "REVOKED", "RESOLUTION_FAILED", "PROVIDER_ERROR"}),
            },
        ),
        **{
            signal_name: _northbound_operation_signal(
                name=signal_name,
                operation_identity=operation.identity,
            )
            for operation in WMS_OPERATIONS
            if (signal_name := f"northbound.operation.{operation.identity.partition('@')[0].rsplit('.', 1)[-1]}")
        },
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
