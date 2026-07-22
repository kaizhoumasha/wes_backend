"""EXTERNAL_HTTP typed transport result 的 attempt 证据合同。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.runtime.orchestration.models.dispatch_attempt import (
    DispatchAttemptStatus,
    WorklineDispatchAttempt,
)
from src.app.runtime.orchestration.services.inbox.dispatch_attempt_service import (
    workline_dispatch_attempt_service,
)
from src.app.sys.external_http_transport import (
    ExternalHttpProtocolResult,
    ExternalHttpTransportOutcome,
    ExternalHttpTransportPhase,
    ExternalHttpTransportResult,
)


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
    attempt = SimpleNamespace(status=DispatchAttemptStatus.DISPATCHING)
    db = SimpleNamespace(flush=AsyncMock(), commit=AsyncMock())

    finalized = await workline_dispatch_attempt_service.finalize_external_http_attempt_record(
        db,
        attempt=attempt,
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

    with pytest.raises(ValueError, match="非法状态转移"):
        await workline_dispatch_attempt_service.finalize_external_http_attempt_record(
            db,
            attempt=attempt,
            result=result,
            outbox_finalization="unknown",
        )

    assert result.outcome is ExternalHttpTransportOutcome.AMBIGUOUS
    db.flush.assert_not_awaited()
    db.commit.assert_not_awaited()
