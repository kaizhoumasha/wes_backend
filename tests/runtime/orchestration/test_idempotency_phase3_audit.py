"""Phase 3 idempotency security audit contract tests."""

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
        "idempotency_key": "same-key",
        "existing_request_hash": "hash-old",
        "incoming_request_hash": "hash-new",
        "correlation_id": "corr-001",
    }
