"""Idempotency security audit contract tests."""

from __future__ import annotations


def test_idempotency_conflict_exposes_security_audit_payload() -> None:
    """同 key 不同 hash 冲突必须可转为稳定安全审计事件。"""

    from src.app.runtime.orchestration.services.idempotency_guard import IdempotencyConflict

    conflict = IdempotencyConflict(
        provider_code="WMS",
        operation_kind="fulfillment",
        idempotency_key="same-key",
        existing_request_hash="hash-old",
        incoming_request_hash="hash-new",
        correlation_id="corr-001",
    )

    assert conflict.status_code == 409
    assert conflict.to_audit_event() == {
        "event_type": "IDEMPOTENCY_CONFLICT",
        "provider_code": "WMS",
        "operation_kind": "fulfillment",
        "normalized_operation_kind": "fulfillment",
        "domain": "wms_integration",
        "idempotency_key": "same-key",
        "existing_request_hash": "hash-old",
        "incoming_request_hash": "hash-new",
        "correlation_id": "corr-001",
        "status_code": 409,
        "security_control": "idempotency_key_request_hash",
    }


def test_idempotency_audit_matrix_covers_runtime_domains() -> None:
    """ENG-009 跨域矩阵必须覆盖所有 runtime 幂等审计域。"""

    from src.app.runtime.orchestration.services.idempotency_guard import (
        default_idempotency_operation_matrix,
        get_idempotency_operation_spec,
    )

    matrix = default_idempotency_operation_matrix()

    assert set(matrix) == {"callback", "fulfillment", "reconciliation"}
    assert get_idempotency_operation_spec("FULFILLMENT").operation_kind == "fulfillment"
