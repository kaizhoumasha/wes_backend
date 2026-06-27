"""Phase 1 CEO-007 RuntimeInbox + RuntimeIntentLog contract test。

主计划 §9.2 RuntimeInbox 处理契约 + RuntimeIntentLog effect ledger:
- RuntimeInbox 5 态状态机 + ACK-before-processing
- RuntimeIntentLog 5 态 dispatch + 崩溃重放 + idempotency_key/request_hash
- execution_session_id + correlation_id 可空 (callback 未解析前先 ACK)
- source_event_id + provider_code + event_type 唯一
"""

from __future__ import annotations

import pytest

from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog

# ---- RuntimeInbox ----


def test_runtime_inbox_table_name():
    assert RuntimeInbox.__tablename__ == "runtime_inbox"


def test_runtime_inbox_default_status_received():
    """新 inbox 默认 RECEIVED (主计划 §9.2 ACK-before-processing)。"""
    inbox = RuntimeInbox(
        provider_code="WMS",
        event_type="WMS_GRN_RECEIVED",
    )
    assert inbox.status == "RECEIVED"
    assert inbox.execution_session_id is None  # 未解析前可空
    assert inbox.correlation_id is None


def test_runtime_inbox_5_states_supported():
    """5 态: RECEIVED / PROCESSING / PROCESSED / FAILED / DEAD_LETTER。"""
    valid = {"RECEIVED", "PROCESSING", "PROCESSED", "FAILED", "DEAD_LETTER"}
    for state in valid:
        inbox = RuntimeInbox(provider_code="WMS", event_type="X", status=state)
        assert inbox.status == state


def test_runtime_inbox_retry_fields_defaults():
    """attempt_count=0, max_retries=5, next_retry_at/lease_until=None。"""
    inbox = RuntimeInbox(provider_code="ECS", event_type="DEVICE_EVENT")
    assert inbox.attempt_count == 0
    assert inbox.max_retries == 5
    assert inbox.next_retry_at is None
    assert inbox.lease_until is None


def test_runtime_inbox_source_event_id_optional():
    """source_event_id 可空 (缺 event_id 的离散事件只 ACK 不推进)。"""
    inbox = RuntimeInbox(
        provider_code="ECS",
        event_type="DEVICE_EVENT",
        source_event_id=None,
    )
    assert inbox.source_event_id is None


# ---- RuntimeIntentLog ----


def test_runtime_intent_log_table_name():
    assert RuntimeIntentLog.__tablename__ == "runtime_intent_logs"


def test_runtime_intent_log_default_dispatch_status_pending():
    """新 intent 默认 PENDING (主计划 §9.2 outbox)。"""
    log = RuntimeIntentLog(
        execution_session_id=1,
        correlation_id="corr-001",
        provider_code="WMS",
        target_domain="wms_integration",
        target_action="request_transport",
        idempotency_key="WES-FULFILLMENT-abc123",
        request_hash="sha256-hash",
    )
    assert log.dispatch_status == "PENDING"
    assert log.attempt_count == 0


def test_runtime_intent_log_5_dispatch_states():
    """5 态: PENDING / DISPATCHING / DISPATCHED / ACKED / FAILED。"""
    valid = {"PENDING", "DISPATCHING", "DISPATCHED", "ACKED", "FAILED"}
    for state in valid:
        log = RuntimeIntentLog(
            execution_session_id=1,
            correlation_id="corr-001",
            provider_code="WMS",
            target_domain="wms_integration",
            target_action="request_transport",
            idempotency_key="key",
            request_hash="hash",
            dispatch_status=state,
        )
        assert log.dispatch_status == state


def test_runtime_intent_log_required_fields():
    """必填: execution_session_id / correlation_id / provider_code /
    target_domain / target_action / idempotency_key / request_hash。"""
    log = RuntimeIntentLog(
        execution_session_id=42,
        correlation_id="corr-x",
        provider_code="ECS",
        target_domain="device",
        target_action="dispatch_command",
        idempotency_key="WES-DEVICE-xyz",
        request_hash="sha256-abc",
    )
    assert log.execution_session_id == 42
    assert log.correlation_id == "corr-x"
    assert log.provider_code == "ECS"
    assert log.target_domain == "device"
    assert log.target_action == "dispatch_command"
    assert log.idempotency_key == "WES-DEVICE-xyz"
    assert log.request_hash == "sha256-abc"
