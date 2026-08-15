"""北向 operation 的版本化 SLO、告警与观测发射入口。"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal

from src.app.runtime.orchestration.observability import (
    RuntimeObservabilityEvent,
    RuntimeObservabilityRegistry,
    runtime_observability_registry,
)
from src.app.wms_integration.operation_contract import WmsCompletionMode, WmsOperationMode
from src.app.wms_integration.operation_registry import WMS_OPERATIONS

if TYPE_CHECKING:
    from collections.abc import Mapping

    from src.app.sys.dispatch_concurrency import DispatchClaimMetrics

NorthboundOutcome = Literal[
    "SUCCESS",
    "BUSINESS_REJECT",
    "TECHNICAL_FAILURE",
    "CONTRACT_FAILURE",
    "UNKNOWN",
    "RECONCILING",
]
NorthboundTraceStage = Literal[
    "QUERY_EVIDENCE",
    "POLICY_DECISION",
    "RUNTIME_INTENT_LOG",
    "DISPATCH_ATTEMPT",
    "CALLBACK",
    "RECONCILIATION",
]

NORTHBOUND_OPERATION_SLO_CATALOG_VERSION = "northbound-operation-slo.v1"
NORTHBOUND_OBSERVABILITY_POLICY_VERSION = "northbound-observability.v1"


@dataclass(frozen=True, slots=True)
class NorthboundOperationSlo:
    """单个 operation 的可执行 SLO 条目。"""

    operation_identity: str
    window_minutes: int
    availability_target: float
    latency_p95_ms: int
    unknown_ratio_limit: float
    reconciliation_age_seconds: int
    burn_rate_thresholds: tuple[float, ...]
    dashboard_id: str
    alert_owner: str
    runbook_anchor: str


@dataclass(frozen=True, slots=True)
class NorthboundOperationAlert:
    """告警与责任人、看板、Runbook 的静态绑定。"""

    alert_id: str
    slo_key: str
    burn_rate: float
    owner: str
    dashboard_id: str
    runbook_anchor: str


def _slo(
    operation_identity: str,
    *,
    latency_p95_ms: int,
    runbook_anchor: str,
) -> NorthboundOperationSlo:
    return NorthboundOperationSlo(
        operation_identity=operation_identity,
        window_minutes=30 * 24 * 60,
        availability_target=0.995,
        latency_p95_ms=latency_p95_ms,
        unknown_ratio_limit=0.001,
        reconciliation_age_seconds=900,
        burn_rate_thresholds=(14.4, 6.0, 3.0),
        dashboard_id="northbound-operation-day1",
        alert_owner="runtime-platform",
        runbook_anchor=runbook_anchor,
    )


NORTHBOUND_OPERATION_SLO_CATALOG = MappingProxyType(
    {
        operation.identity: _slo(
            operation.identity,
            latency_p95_ms=(
                1_500
                if operation.mode is WmsOperationMode.QUERY
                else 3_000
                if operation.completion_mode is WmsCompletionMode.ASYNC_TASK
                else 2_000
            ),
            runbook_anchor=(
                "unknown-reconciliation"
                if operation.mode is WmsOperationMode.QUERY
                else "pause-resume"
                if operation.completion_mode is WmsCompletionMode.ASYNC_TASK
                else "callback-diagnostics"
            ),
        )
        for operation in WMS_OPERATIONS
    }
)

NORTHBOUND_OPERATION_ALERT_CATALOG = MappingProxyType(
    {
        "northbound-slo-fast-burn": NorthboundOperationAlert(
            alert_id="northbound-slo-fast-burn",
            slo_key=NORTHBOUND_OPERATION_SLO_CATALOG_VERSION,
            burn_rate=14.4,
            owner="runtime-platform",
            dashboard_id="northbound-operation-day1",
            runbook_anchor="pause-resume",
        ),
        "northbound-unknown-ratio": NorthboundOperationAlert(
            alert_id="northbound-unknown-ratio",
            slo_key=NORTHBOUND_OPERATION_SLO_CATALOG_VERSION,
            burn_rate=6.0,
            owner="runtime-platform",
            dashboard_id="northbound-operation-day1",
            runbook_anchor="unknown-reconciliation",
        ),
        "northbound-credential-revoked": NorthboundOperationAlert(
            alert_id="northbound-credential-revoked",
            slo_key=NORTHBOUND_OPERATION_SLO_CATALOG_VERSION,
            burn_rate=14.4,
            owner="security-platform",
            dashboard_id="northbound-operation-day1",
            runbook_anchor="credential-revoked",
        ),
        "northbound-lease-loss": NorthboundOperationAlert(
            alert_id="northbound-lease-loss",
            slo_key=NORTHBOUND_OPERATION_SLO_CATALOG_VERSION,
            burn_rate=6.0,
            owner="runtime-platform",
            dashboard_id="northbound-operation-day1",
            runbook_anchor="lease-fencing",
        ),
        "northbound-callback-contradiction": NorthboundOperationAlert(
            alert_id="northbound-callback-contradiction",
            slo_key=NORTHBOUND_OPERATION_SLO_CATALOG_VERSION,
            burn_rate=3.0,
            owner="runtime-platform",
            dashboard_id="northbound-operation-day1",
            runbook_anchor="callback-diagnostics",
        ),
    }
)

_OPERATION_SIGNAL_NAMES = MappingProxyType(
    {
        operation.identity: f"northbound.operation.{operation.identity.partition('@')[0].rsplit('.', 1)[-1]}"
        for operation in WMS_OPERATIONS
    }
)


def require_northbound_operation_slo(
    operation_identity: str,
    *,
    catalog: Mapping[str, NorthboundOperationSlo] = NORTHBOUND_OPERATION_SLO_CATALOG,
) -> NorthboundOperationSlo:
    """Provider binding 激活前必须解析到完整版本化 SLO。"""

    objective = catalog.get(operation_identity)
    if objective is None:
        raise ValueError("northbound operation is missing from the versioned SLO catalog")
    return objective


def emit_northbound_operation_observation(
    *,
    operation_identity: str,
    provider_profile_identity: str,
    outcome: NorthboundOutcome,
    latency_ms: float,
    trace_id: str,
    correlation_id: str,
    evidence_ref: str,
    stage: NorthboundTraceStage,
    registry: RuntimeObservabilityRegistry = runtime_observability_registry,
) -> RuntimeObservabilityEvent:
    """发射 operation metric 与可串联 trace；业务 payload 永不进入参数面。"""

    signal_name = _OPERATION_SIGNAL_NAMES.get(operation_identity)
    if signal_name is None:
        raise ValueError("northbound operation is not authored in the SLO catalog")
    return registry.emit(
        signal_name,
        {
            "provider_profile_identity": provider_profile_identity,
            "outcome": outcome,
            "latency_ms": latency_ms,
            "sample_count": 1,
            "unknown_count": int(outcome == "UNKNOWN"),
            "trace_id": trace_id,
            "correlation_id": correlation_id,
            "evidence_ref": evidence_ref,
            "stage": stage,
        },
    )


def _observability_emit(
    *,
    operation_identity: str,
    provider_profile_identity: str,
    outcome: NorthboundOutcome,
    latency_ms: float,
    trace_id: str,
    correlation_id: str,
    evidence_ref: str,
    stage: NorthboundTraceStage,
    registry: RuntimeObservabilityRegistry = runtime_observability_registry,
) -> RuntimeObservabilityEvent:
    return emit_northbound_operation_observation(
        operation_identity=operation_identity,
        provider_profile_identity=provider_profile_identity,
        outcome=outcome,
        latency_ms=latency_ms,
        trace_id=trace_id,
        correlation_id=correlation_id,
        evidence_ref=evidence_ref,
        stage=stage,
        registry=registry,
    )


def emit_query_inventory_observation(
    *,
    provider_profile_identity: str,
    outcome: NorthboundOutcome,
    latency_ms: float,
    trace_id: str,
    correlation_id: str,
    evidence_ref: str,
    stage: NorthboundTraceStage,
    registry: RuntimeObservabilityRegistry = runtime_observability_registry,
) -> RuntimeObservabilityEvent:
    """`query_inventory` 的静态 metric identity 入口。"""

    return _observability_emit(
        operation_identity="wms.inventory.query_inventory@v1",
        provider_profile_identity=provider_profile_identity,
        outcome=outcome,
        latency_ms=latency_ms,
        trace_id=trace_id,
        correlation_id=correlation_id,
        evidence_ref=evidence_ref,
        stage=stage,
        registry=registry,
    )


def emit_confirm_inbound_observation(
    *,
    provider_profile_identity: str,
    outcome: NorthboundOutcome,
    latency_ms: float,
    trace_id: str,
    correlation_id: str,
    evidence_ref: str,
    stage: NorthboundTraceStage,
    registry: RuntimeObservabilityRegistry = runtime_observability_registry,
) -> RuntimeObservabilityEvent:
    """`confirm_inbound` 的静态 metric identity 入口。"""

    return _observability_emit(
        operation_identity="wms.inventory.confirm_inbound@v1",
        provider_profile_identity=provider_profile_identity,
        outcome=outcome,
        latency_ms=latency_ms,
        trace_id=trace_id,
        correlation_id=correlation_id,
        evidence_ref=evidence_ref,
        stage=stage,
        registry=registry,
    )


def emit_notify_pkg_binding_observation(
    *,
    provider_profile_identity: str,
    outcome: NorthboundOutcome,
    latency_ms: float,
    trace_id: str,
    correlation_id: str,
    evidence_ref: str,
    stage: NorthboundTraceStage,
    registry: RuntimeObservabilityRegistry = runtime_observability_registry,
) -> RuntimeObservabilityEvent:
    """`notify_pkg_binding` 的静态 metric identity 入口。"""

    return _observability_emit(
        operation_identity="wms.fulfillment.notify_pkg_binding@v1",
        provider_profile_identity=provider_profile_identity,
        outcome=outcome,
        latency_ms=latency_ms,
        trace_id=trace_id,
        correlation_id=correlation_id,
        evidence_ref=evidence_ref,
        stage=stage,
        registry=registry,
    )


def emit_dispatch_health_observation(
    metrics: DispatchClaimMetrics,
    *,
    registry: RuntimeObservabilityRegistry = runtime_observability_registry,
) -> RuntimeObservabilityEvent:
    """把公平调度摘要投影成无 bucket/business key 的平台级 SLI。"""

    return registry.emit(
        "northbound.dispatch.health",
        {
            "backlog_count": metrics.backlog_count,
            "active_lease_count": metrics.active_lease_count,
            "unknown_count": metrics.unknown_count,
            "oldest_queue_age_seconds": metrics.oldest_queue_age_seconds or 0,
            "rate_limited_bucket_count": len(metrics.rate_limited_buckets),
            "paused_bucket_count": len(metrics.paused_buckets),
            "lease_contended_bucket_count": len(metrics.lease_contended_buckets),
            "lease_loss_count": metrics.lease_loss_count,
        },
    )


def emit_credential_resolution_observation(
    *,
    provider_kind: Literal["environment", "custom"],
    outcome: Literal["RESOLVED", "REVOKED", "RESOLUTION_FAILED", "PROVIDER_ERROR"],
    registry: RuntimeObservabilityRegistry = runtime_observability_registry,
) -> RuntimeObservabilityEvent:
    """凭据解析审计只携带闭集 provider 类型和结果。"""

    return registry.emit(
        "northbound.credential.resolve",
        {
            "provider_kind": provider_kind,
            "outcome": outcome,
            "sample_count": 1,
        },
    )


__all__ = [
    "NORTHBOUND_OBSERVABILITY_POLICY_VERSION",
    "NORTHBOUND_OPERATION_ALERT_CATALOG",
    "NORTHBOUND_OPERATION_SLO_CATALOG",
    "NORTHBOUND_OPERATION_SLO_CATALOG_VERSION",
    "NorthboundOperationAlert",
    "NorthboundOperationSlo",
    "NorthboundOutcome",
    "NorthboundTraceStage",
    "emit_confirm_inbound_observation",
    "emit_credential_resolution_observation",
    "emit_dispatch_health_observation",
    "emit_northbound_operation_observation",
    "emit_notify_pkg_binding_observation",
    "emit_query_inventory_observation",
    "require_northbound_operation_slo",
]
