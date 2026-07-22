"""Workline dispatcher 的 EXTERNAL_HTTP typed result 映射。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.app.runtime.orchestration.services.inbox.outbox_dispatch_service import OutboxDispatchService
from src.app.sys.external_http_transport import (
    ExternalHttpProtocolResult,
    ExternalHttpTransportOutcome,
    ExternalHttpTransportPhase,
    ExternalHttpTransportResult,
)
from src.app.sys.models import SystemOutboxDispatchType, SystemOutboxStatus


class _Repository:
    def __init__(self) -> None:
        self.outbox = SimpleNamespace(id=1, status=SystemOutboxStatus.DISPATCHING)
        self.calls: list[str] = []

    async def mark_as_sent(self, _db: Any, _outbox_id: int) -> SimpleNamespace:
        self.calls.append("sent")
        self.outbox.status = SystemOutboxStatus.SENT
        return self.outbox

    async def mark_as_failed(self, _db: Any, _outbox_id: int, _error: str, _max_retries: int) -> SimpleNamespace:
        self.calls.append("retry_wait")
        self.outbox.status = SystemOutboxStatus.RETRY_WAIT
        return self.outbox

    async def mark_as_unknown(self, _db: Any, _outbox_id: int, _error: str) -> SimpleNamespace:
        self.calls.append("unknown")
        self.outbox.status = SystemOutboxStatus.UNKNOWN
        return self.outbox

    async def mark_as_terminal_failure(self, _db: Any, _outbox_id: int, _error: str) -> SimpleNamespace:
        self.calls.append("failed")
        self.outbox.status = SystemOutboxStatus.FAILED
        return self.outbox


class _AttemptService:
    def __init__(self) -> None:
        self.finalized: list[dict[str, Any]] = []

    async def finalize_external_http_attempt_record(self, _db: Any, **kwargs: Any) -> SimpleNamespace:
        self.finalized.append(kwargs)
        return kwargs["attempt"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("transport_result", "expected_finalization"),
    [
        (
            ExternalHttpTransportResult.accepted(
                http_status_code=409,
                protocol_result=ExternalHttpProtocolResult.REJECTED,
                error_code="HTTP_REJECTED",
            ),
            "sent",
        ),
        (
            ExternalHttpTransportResult.not_sent(
                phase=ExternalHttpTransportPhase.CONNECTING,
                safe_to_retry=True,
                error_code="CONNECT_ERROR",
            ),
            "retry_wait",
        ),
        (
            ExternalHttpTransportResult.not_sent(
                phase=ExternalHttpTransportPhase.PREPARING,
                safe_to_retry=False,
                error_code="INVALID_URL",
            ),
            "failed",
        ),
        (
            ExternalHttpTransportResult.ambiguous(
                phase=ExternalHttpTransportPhase.AWAITING_RESPONSE,
                error_code="READ_TIMEOUT",
            ),
            "unknown",
        ),
    ],
)
async def test_workline_dispatcher_uses_same_typed_result_mapping_and_attempt_evidence(
    transport_result: ExternalHttpTransportResult,
    expected_finalization: str,
) -> None:
    repository = _Repository()
    attempt_service = _AttemptService()
    attempt = SimpleNamespace(status="DISPATCHING")

    updated = await OutboxDispatchService()._finalize_external_http_result(
        object(),
        outbox_repo=repository,
        outbox_id=1,
        dispatch_attempt=attempt,
        attempt_service=attempt_service,
        result=transport_result,
    )

    assert updated is repository.outbox
    assert repository.calls == [expected_finalization]
    assert attempt_service.finalized == [
        {
            "attempt": attempt,
            "result": transport_result,
            "outbox_finalization": expected_finalization,
            "auto_commit": False,
        }
    ]


@pytest.mark.asyncio
async def test_external_http_sandbox_returns_typed_accepted_result() -> None:
    outbox = SimpleNamespace(
        dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
        dispatch_key="sandbox-http-1",
        session_id=1,
    )

    result = await OutboxDispatchService()._dispatch_sandbox(object(), outbox)

    assert isinstance(result, ExternalHttpTransportResult)
    assert result.outcome is ExternalHttpTransportOutcome.ACCEPTED
    assert result.phase is ExternalHttpTransportPhase.SANDBOX
    assert result.protocol_result is ExternalHttpProtocolResult.NOT_AVAILABLE
    assert result.http_status_code is None
