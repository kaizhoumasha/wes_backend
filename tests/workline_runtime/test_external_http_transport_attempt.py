"""EXTERNAL_HTTP typed transport result 的 attempt 证据合同。"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.effect_ledger_status import DispatchAttemptStatus
from src.app.runtime.orchestration.models.dispatch_attempt import WorklineDispatchAttempt
from src.app.runtime.orchestration.services.inbox.dispatch_attempt_service import (
    OutboxLeaseLost,
    workline_dispatch_attempt_service,
)
from src.app.runtime.orchestration.services.inbox.external_http_lease_loss_service import (
    ExternalHttpLeaseLossService,
)
from src.app.sys.external_http_transport import (
    ExternalHttpProtocolResult,
    ExternalHttpTransportOutcome,
    ExternalHttpTransportPhase,
    ExternalHttpTransportResult,
)
from src.utils.timezone import timezone


def test_dispatch_attempt_model_exposes_typed_external_http_evidence_columns() -> None:
    columns = WorklineDispatchAttempt.__table__.c

    assert {
        "transport_outcome",
        "transport_phase",
        "protocol_result",
        "safe_to_retry",
        "http_status_code",
    } <= set(columns.keys())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("transport_result", "expected_status", "outbox_finalization"),
    [
        (
            ExternalHttpTransportResult.accepted(
                http_status_code=409,
                protocol_result=ExternalHttpProtocolResult.REJECTED,
                error_code="HTTP_REJECTED",
                error_message="HTTP 409 explicitly rejected request",
            ),
            DispatchAttemptStatus.SENT,
            "sent",
        ),
        (
            ExternalHttpTransportResult.not_sent(
                phase=ExternalHttpTransportPhase.CONNECTING,
                safe_to_retry=True,
                error_code="CONNECT_ERROR",
                error_message="connection refused",
            ),
            DispatchAttemptStatus.FAILED,
            "retry_wait",
        ),
        (
            ExternalHttpTransportResult.ambiguous(
                phase=ExternalHttpTransportPhase.AWAITING_RESPONSE,
                error_code="READ_TIMEOUT",
                error_message="response timeout",
            ),
            DispatchAttemptStatus.UNKNOWN,
            "unknown",
        ),
    ],
)
async def test_finalize_external_http_attempt_record_persists_typed_evidence(
    transport_result: ExternalHttpTransportResult,
    expected_status: DispatchAttemptStatus,
    outbox_finalization: str,
) -> None:
    attempt = SimpleNamespace(
        status=DispatchAttemptStatus.DISPATCHING,
        lease_token="attempt-owner",
        lease_expires_at=timezone.now_for_db() + timedelta(minutes=5),
    )
    db = SimpleNamespace(flush=AsyncMock(), commit=AsyncMock())

    finalized = await workline_dispatch_attempt_service.finalize_external_http_attempt_record(
        db,
        attempt=attempt,
        lease_owner_token="attempt-owner",
        result=transport_result,
        outbox_finalization=outbox_finalization,
    )

    assert finalized.status is expected_status
    assert finalized.transport_outcome == transport_result.outcome.value
    assert finalized.transport_phase == transport_result.phase.value
    assert finalized.protocol_result == transport_result.protocol_result.value
    assert finalized.safe_to_retry is transport_result.safe_to_retry
    assert finalized.http_status_code == transport_result.http_status_code
    assert finalized.finalized_at is not None
    assert finalized.error_message == transport_result.error_message
    assert finalized.response_json == {
        "transport": transport_result.evidence_json(),
        "outbox_finalization": outbox_finalization,
    }
    db.flush.assert_awaited_once()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_finalize_external_http_attempt_rejects_second_terminal_write() -> None:
    attempt = SimpleNamespace(status=DispatchAttemptStatus.SENT)
    db = SimpleNamespace(flush=AsyncMock(), commit=AsyncMock())
    result = ExternalHttpTransportResult.ambiguous(
        phase=ExternalHttpTransportPhase.AWAITING_RESPONSE,
        error_code="READ_TIMEOUT",
    )

    with pytest.raises(OutboxLeaseLost, match="OUTBOX_LEASE_LOST"):
        await workline_dispatch_attempt_service.finalize_external_http_attempt_record(
            db,
            attempt=attempt,
            lease_owner_token="attempt-owner",
            result=result,
            outbox_finalization="unknown",
        )

    assert result.outcome is ExternalHttpTransportOutcome.AMBIGUOUS
    db.flush.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_callback_completed_outbox_recovers_orphaned_dispatch_attempt_after_sender_crash() -> None:
    """callback 已提交而 sender 崩溃时，过期 attempt 仍必须有独立恢复路径。"""

    now = timezone.now_for_db()
    attempt = SimpleNamespace(
        status=DispatchAttemptStatus.DISPATCHING,
        lease_expires_at=now - timedelta(seconds=1),
        finalized_at=None,
        error_message=None,
        response_json={},
    )
    outbox_repository = SimpleNamespace(
        fence_expired_external_http_leases=AsyncMock(return_value=()),
    )
    attempt_repository = SimpleNamespace(
        list_expired_dispatching_for_finished_outboxes_for_update=AsyncMock(return_value=(attempt,)),
    )
    bridge = SimpleNamespace(record_result=AsyncMock())
    db = SimpleNamespace(flush=AsyncMock())
    service = ExternalHttpLeaseLossService(
        outbox_repository=outbox_repository,
        dispatch_attempt_repository=attempt_repository,
        effect_transport_bridge=bridge,
    )

    recovered = await service.fence_expired_leases(
        db,
        now=now,
        operation_domains=("WMS_INVENTORY", "WMS_FULFILLMENT"),
    )

    assert recovered == 1
    assert attempt.status is DispatchAttemptStatus.CANCELLED
    assert attempt.finalized_at == now
    assert attempt.error_message == "OUTBOX_FINISHED_BEFORE_TRANSPORT_EVIDENCE"
    assert attempt.response_json == {
        "outbox_finished": True,
        "sender_crash_recovery": True,
    }
    bridge.record_result.assert_not_awaited()
