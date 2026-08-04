"""北向 operation 的 SLO、低基数指标、trace 与凭据审计合同。"""

from __future__ import annotations

import pytest


def test_slo_catalog_covers_all_authored_wms_operations_and_is_operable() -> None:
    from src.app.runtime.orchestration.operation_observability import (
        NORTHBOUND_OPERATION_SLO_CATALOG,
        NORTHBOUND_OPERATION_SLO_CATALOG_VERSION,
    )
    from src.app.runtime.system_capabilities.wms.generated_operation_index import WMS_OPERATION_IDENTITIES

    assert NORTHBOUND_OPERATION_SLO_CATALOG_VERSION == "northbound-operation-slo.v1"
    assert set(NORTHBOUND_OPERATION_SLO_CATALOG) == set(WMS_OPERATION_IDENTITIES)
    for operation_identity, objective in NORTHBOUND_OPERATION_SLO_CATALOG.items():
        assert objective.operation_identity == operation_identity
        assert objective.window_minutes > 0
        assert objective.latency_p95_ms > 0
        assert 0 < objective.availability_target <= 1
        assert objective.burn_rate_thresholds
        assert objective.dashboard_id
        assert objective.alert_owner
        assert objective.runbook_anchor


def test_missing_slo_catalog_entry_blocks_provider_binding_activation() -> None:
    from src.app.runtime.orchestration.operation_observability import require_northbound_operation_slo

    with pytest.raises(ValueError, match="SLO catalog"):
        require_northbound_operation_slo(
            "wms.inventory.query_inventory@v1",
            catalog={},
        )


def test_northbound_metric_projects_only_closed_labels_and_measurements() -> None:
    from src.app.runtime.orchestration.observability import RuntimeObservabilityRegistry, RuntimeOpenTelemetryBridge
    from src.app.runtime.orchestration.operation_observability import emit_northbound_operation_observation

    class RecordingExporter:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, dict[str, object]]] = []

        def emit_span(self, name, attributes) -> None:
            self.calls.append(("span", name, dict(attributes)))

        def emit_metric(self, name, attributes) -> None:
            self.calls.append(("metric", name, dict(attributes)))

        def emit_log_event(self, name, attributes) -> None:
            self.calls.append(("log", name, dict(attributes)))

    exporter = RecordingExporter()
    registry = RuntimeObservabilityRegistry(observers=(RuntimeOpenTelemetryBridge(exporter),))

    event = emit_northbound_operation_observation(
        operation_identity="wms.inventory.query_inventory@v1",
        provider_profile_identity="wms.2026-07-28.full-factory",
        outcome="SUCCESS",
        latency_ms=12.5,
        trace_id="trace-high-cardinality-1",
        correlation_id="corr-high-cardinality-1",
        evidence_ref="evidence-high-cardinality-1",
        stage="QUERY_EVIDENCE",
        registry=registry,
    )

    metric = next(attributes for kind, _name, attributes in exporter.calls if kind == "metric")
    span = next(attributes for kind, _name, attributes in exporter.calls if kind == "span")
    assert metric == {
        "capability_identity": "wms.inventory.query_inventory@v1",
        "operation_identity": "wms.inventory.query_inventory@v1",
        "provider_profile_identity": "wms.2026-07-28.full-factory",
        "outcome": "SUCCESS",
        "policy_version": "northbound-observability.v1",
        "latency_ms": 12.5,
        "sample_count": 1,
        "unknown_count": 0,
    }
    assert span["trace_id"] == "trace-high-cardinality-1"
    assert span["evidence_ref"] == "evidence-high-cardinality-1"
    assert "trace_id" not in metric
    assert "correlation_id" not in metric
    assert "evidence_ref" not in metric
    assert dict(event.metric_attributes) == metric


@pytest.mark.parametrize(
    ("attributes", "reason"),
    [
        ({"payload_json": {"secret": "x"}}, "SENSITIVE_ATTRIBUTE"),
        ({"dynamic_label": "tenant-42"}, "UNEXPECTED_ATTRIBUTES"),
        ({"outcome": "tenant-42"}, "ATTRIBUTE_VALUE_NOT_ALLOWED"),
        ({"provider_profile_identity": "wms.dynamic.tenant-42"}, "ATTRIBUTE_VALUE_NOT_ALLOWED"),
    ],
)
def test_northbound_metric_guard_fails_closed_for_dynamic_or_sensitive_attributes(
    attributes: dict[str, object],
    reason: str,
) -> None:
    from src.app.runtime.orchestration.observability import RuntimeObservabilityRegistry

    registry = RuntimeObservabilityRegistry()
    valid = {
        "provider_profile_identity": "wms.2026-07-28.full-factory",
        "outcome": "SUCCESS",
        "latency_ms": 3.0,
        "sample_count": 1,
        "unknown_count": 0,
        "trace_id": "trace-1",
        "correlation_id": "corr-1",
        "evidence_ref": "evidence-1",
        "stage": "QUERY_EVIDENCE",
    }
    valid.update(attributes)

    validation = registry.validate("northbound.operation.query_inventory", valid)

    assert validation.valid is False
    assert validation.reason == reason


def test_northbound_trace_stage_and_outcome_are_closed_sets() -> None:
    from src.app.runtime.orchestration.observability import RuntimeObservabilityRegistry

    registry = RuntimeObservabilityRegistry()
    signal = registry.signals["northbound.operation.query_inventory"]
    assert signal.allowed_values["stage"] == {
        "PLUGIN_EXECUTION",
        "QUERY_EVIDENCE",
        "POLICY_DECISION",
        "RUNTIME_INTENT_LOG",
        "DISPATCH_ATTEMPT",
        "CALLBACK",
        "RECONCILIATION",
    }
    attributes = {
        "provider_profile_identity": "wms.2026-07-28.full-factory",
        "outcome": "SUCCESS",
        "latency_ms": 1.0,
        "sample_count": 1,
        "unknown_count": 0,
        "trace_id": "trace-1",
        "correlation_id": "corr-1",
        "evidence_ref": "evidence-1",
        "stage": "CALLBACK",
    }
    assert registry.validate("northbound.operation.query_inventory", attributes).valid is True

    attributes["stage"] = "arbitrary-tenant-stage"
    invalid = registry.validate("northbound.operation.query_inventory", attributes)
    assert invalid.valid is False
    assert invalid.reason == "ATTRIBUTE_VALUE_NOT_ALLOWED"


def test_dispatch_health_metric_has_no_bucket_or_business_key_labels() -> None:
    from src.app.runtime.orchestration.observability import RuntimeObservabilityRegistry
    from src.app.runtime.orchestration.operation_observability import emit_dispatch_health_observation
    from src.app.sys.dispatch_concurrency import DispatchClaimMetrics

    registry = RuntimeObservabilityRegistry()
    event = emit_dispatch_health_observation(
        DispatchClaimMetrics(
            backlog_count=12,
            active_lease_count=3,
            unknown_count=2,
            oldest_queue_age_seconds=18,
            rate_limited_buckets=(),
            paused_buckets=(),
            lease_contended_buckets=(),
            lease_loss_count=1,
        ),
        registry=registry,
    )

    assert dict(event.metric_attributes) == {
        "capability_identity": "runtime.northbound.dispatch@v1",
        "policy_version": "northbound-observability.v1",
        "backlog_count": 12,
        "active_lease_count": 3,
        "unknown_count": 2,
        "oldest_queue_age_seconds": 18,
        "rate_limited_bucket_count": 0,
        "paused_bucket_count": 0,
        "lease_contended_bucket_count": 0,
        "lease_loss_count": 1,
    }


def test_alert_catalog_has_complete_executable_metadata() -> None:
    from src.app.runtime.orchestration.operation_observability import NORTHBOUND_OPERATION_ALERT_CATALOG

    required_procedures = {
        "pause-resume",
        "unknown-reconciliation",
        "credential-revoked",
        "lease-fencing",
        "callback-diagnostics",
    }

    assert required_procedures <= {alert.runbook_anchor for alert in NORTHBOUND_OPERATION_ALERT_CATALOG.values()}
    for alert in NORTHBOUND_OPERATION_ALERT_CATALOG.values():
        assert alert.owner
        assert alert.dashboard_id
        assert alert.burn_rate > 0
