"""WMS EFFECT 六类稳定观测信号合同。"""

from __future__ import annotations

import hashlib
from typing import Any


def test_registry_registers_all_wms_effect_signals_with_closed_metric_projection() -> None:
    from src.app.runtime.orchestration.observability import RuntimeObservabilityRegistry

    registry = RuntimeObservabilityRegistry()
    expected = {
        "wms_effect.submit",
        "wms_effect.status_query",
        "wms_effect.status_backlog",
        "wms_effect.status_backpressure",
        "wms_effect.recovery",
        "wms_effect.callback_hint",
    }

    assert expected <= set(registry.signals)
    for name in expected:
        signal = registry.signals[name]
        assert {"capability_identity", "policy_version"} <= signal.metric_label_attributes
        assert "sample_count" in signal.metric_measurement_attributes
        assert "dispatch_key_hash" not in signal.metric_label_attributes
        assert "dispatch_key_hash" not in signal.metric_measurement_attributes

    assert registry.signals["wms_effect.submit"].allowed_values["outcome"] == {
        "ACCEPTED",
        "AMBIGUOUS",
        "NOT_SENT",
    }
    assert registry.signals["wms_effect.status_query"].allowed_values["state"] == {
        "ACCEPTED",
        "PROCESSING",
        "COMPLETED",
        "REJECTED",
        "NOT_FOUND",
    }
    assert registry.signals["wms_effect.callback_hint"].allowed_values["outcome"] == {
        "RECEIVED",
        "REJECTED",
        "DUPLICATE",
        "QUERY_TRIGGERED",
        "ENQUEUE_DEGRADED",
    }
    assert (
        "wms.inventory.confirm_inbound@v1"
        not in registry.signals["wms_effect.status_query"].allowed_values["operation_identity"]
    )
    backpressure = registry.signals["wms_effect.status_backpressure"]
    assert "breaker_state" not in backpressure.required_attributes
    assert "breaker_state" not in backpressure.metric_label_attributes
    assert "breaker_state" in backpressure.allowed_attributes


def test_projection_hashes_dispatch_key_for_event_but_excludes_it_from_metric() -> None:
    from src.app.runtime.orchestration.observability import RuntimeObservabilityRegistry
    from src.app.runtime.orchestration.wms_effect_observability import emit_wms_effect_observation

    registry = RuntimeObservabilityRegistry()
    dispatch_key = "business-dispatch-key-must-not-be-a-label"

    event = emit_wms_effect_observation(
        "wms_effect.submit",
        operation_identity="wms.fulfillment.request_rack_transport@v1",
        dispatch_key=dispatch_key,
        attributes={
            "outcome": "ACCEPTED",
            "latency_ms": 12.5,
            "retry_count": 1,
        },
        registry=registry,
    )

    assert event is not None
    assert event.attributes["dispatch_key_hash"] == hashlib.sha256(dispatch_key.encode()).hexdigest()[:16]
    assert dispatch_key not in str(dict(event.attributes))
    assert "dispatch_key_hash" not in event.metric_attributes
    assert dict(event.metric_attributes) == {
        "capability_identity": "wms_effect.submit@v1",
        "operation_identity": "wms.fulfillment.request_rack_transport@v1",
        "outcome": "ACCEPTED",
        "policy_version": "northbound-observability.v1",
        "latency_ms": 12.5,
        "retry_count": 1,
        "sample_count": 1,
    }


def test_projection_is_best_effort_and_does_not_leak_dispatch_key_on_failure(caplog) -> None:
    from src.app.runtime.orchestration.wms_effect_observability import emit_wms_effect_observation

    class FailingRegistry:
        def emit(self, _name: str, _attributes: dict[str, object]) -> Any:
            raise RuntimeError("export failed")

    dispatch_key = "sensitive-business-dispatch-key"
    event = emit_wms_effect_observation(
        "wms_effect.submit",
        operation_identity="wms.fulfillment.request_rack_transport@v1",
        dispatch_key=dispatch_key,
        attributes={
            "outcome": "ACCEPTED",
            "latency_ms": 1,
            "retry_count": 0,
        },
        registry=FailingRegistry(),
    )

    assert event is None
    assert dispatch_key not in caplog.text


def test_all_wms_effect_signal_shapes_emit_with_low_cardinality_metrics() -> None:
    from src.app.runtime.orchestration.observability import RuntimeObservabilityRegistry
    from src.app.runtime.orchestration.wms_effect_observability import emit_wms_effect_observation

    operation_identity = "wms.fulfillment.request_rack_transport@v1"
    cases = {
        "wms_effect.submit": {"outcome": "AMBIGUOUS", "latency_ms": 3, "retry_count": 1},
        "wms_effect.status_query": {
            "state": "PROCESSING",
            "latency_ms": 5,
            "retry_count": 2,
            "age_ms": 50,
        },
        "wms_effect.status_backlog": {
            "backlog_count": 7,
            "max_overdue_age_ms": 500,
            "max_confirmation_age_ms": 5_000,
            "claimed_count": 3,
            "duration_ms": 20,
        },
        "wms_effect.status_backpressure": {
            "outcome": "RATE_LIMITED",
            "retry_after_ms": 1_000,
            "actual_backoff_ms": 1_500,
        },
        "wms_effect.recovery": {"outcome": "RECONCILIATION_OPENED", "age_ms": 2_000},
        "wms_effect.callback_hint": {"outcome": "ENQUEUE_DEGRADED"},
    }
    registry = RuntimeObservabilityRegistry()

    for name, attributes in cases.items():
        event = emit_wms_effect_observation(
            name,
            operation_identity=None if name == "wms_effect.status_backlog" else operation_identity,
            dispatch_key=None if name == "wms_effect.status_backlog" else "dispatch-key",
            attributes=attributes,
            registry=registry,
        )

        assert event is not None
        assert "dispatch_key_hash" not in event.metric_attributes
        assert event.metric_attributes["sample_count"] == 1
        assert all(isinstance(value, (str, int, float)) for value in event.metric_attributes.values())

    circuit_open = emit_wms_effect_observation(
        "wms_effect.status_backpressure",
        operation_identity=operation_identity,
        dispatch_key="dispatch-key",
        attributes={
            "outcome": "CIRCUIT_OPEN",
            "breaker_state": "OPEN",
            "retry_after_ms": 1_000,
            "actual_backoff_ms": 1_500,
        },
        registry=registry,
    )
    assert circuit_open is not None
    assert circuit_open.attributes["breaker_state"] == "OPEN"
    assert "breaker_state" not in circuit_open.metric_attributes
