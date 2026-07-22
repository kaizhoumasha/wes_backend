"""Generic SystemOutboxEngine 的 typed EXTERNAL_HTTP 状态映射。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.app.sys.canonical_dispatch import CanonicalPayload
from src.app.sys.external_http_transport import (
    ExternalHttpProtocolResult,
    ExternalHttpTransportOutcome,
    ExternalHttpTransportPhase,
    ExternalHttpTransportResult,
)
from src.app.sys.models import SystemOutboxDispatchType, SystemOutboxStatus, SystemOutboxTargetType
from src.app.sys.services.outbox_engine import SystemOutboxEngine


def _outbox() -> SimpleNamespace:
    canonical = CanonicalPayload.from_projection({"request_id": "REQ-001"})
    return SimpleNamespace(
        id=1,
        dispatch_key="dispatch-001",
        dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
        target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
        target_code="WMS_RCS_BIN_OPERATION",
        payload_json={"request_id": "REQ-001"},
        canonical_payload_bytes=canonical.body,
        payload_hash=canonical.sha256,
        status=SystemOutboxStatus.NEW,
        attempt_count=0,
        next_retry_at=None,
        last_error=None,
        finished_at=None,
        operation_domain="HANDLING",
    )


class _Repository:
    def __init__(self, outbox: SimpleNamespace) -> None:
        self.outbox = outbox
        self.sent_calls = 0
        self.retry_calls = 0
        self.unknown_calls = 0
        self.terminal_failure_calls = 0

    async def get_pending_messages(self, _db: Any, **_kwargs: Any) -> list[SimpleNamespace]:
        if self.outbox.status in {SystemOutboxStatus.NEW, SystemOutboxStatus.RETRY_WAIT}:
            return [self.outbox]
        return []

    async def mark_as_dispatching(self, _db: Any, _outbox_id: int) -> SimpleNamespace | None:
        if self.outbox.status not in {SystemOutboxStatus.NEW, SystemOutboxStatus.RETRY_WAIT}:
            return None
        self.outbox.status = SystemOutboxStatus.DISPATCHING
        return self.outbox

    async def mark_as_sent(self, _db: Any, _outbox_id: int) -> SimpleNamespace:
        self.sent_calls += 1
        self.outbox.status = SystemOutboxStatus.SENT
        return self.outbox

    async def mark_as_failed(
        self,
        _db: Any,
        _outbox_id: int,
        error: str,
        _max_retries: int,
    ) -> SimpleNamespace:
        self.retry_calls += 1
        self.outbox.status = SystemOutboxStatus.RETRY_WAIT
        self.outbox.last_error = error
        return self.outbox

    async def mark_as_unknown(self, _db: Any, _outbox_id: int, error: str) -> SimpleNamespace:
        self.unknown_calls += 1
        self.outbox.status = SystemOutboxStatus.UNKNOWN
        self.outbox.last_error = error
        return self.outbox

    async def mark_as_terminal_failure(self, _db: Any, _outbox_id: int, error: str) -> SimpleNamespace:
        self.terminal_failure_calls += 1
        self.outbox.status = SystemOutboxStatus.FAILED
        self.outbox.last_error = error
        return self.outbox


class _AttemptService:
    def __init__(self) -> None:
        self.attempt = SimpleNamespace(status="DISPATCHING")
        self.created: list[SimpleNamespace] = []
        self.finalized: list[dict[str, Any]] = []

    async def create_attempt(self, _db: Any, *, outbox: SimpleNamespace, auto_commit: bool) -> SimpleNamespace:
        assert auto_commit is False
        self.created.append(outbox)
        return self.attempt

    async def finalize_external_http_attempt_record(self, _db: Any, **kwargs: Any) -> SimpleNamespace:
        assert kwargs["auto_commit"] is False
        self.finalized.append(kwargs)
        return kwargs["attempt"]


async def _no_workline_messages(_db: Any, _limit: int) -> dict[str, int]:
    return {"dispatched": 0, "success": 0, "failed": 0, "skipped": 0}


async def _dispatch(
    result: ExternalHttpTransportResult,
) -> tuple[dict[str, int], _Repository, _AttemptService, AsyncMock]:
    outbox = _outbox()
    repository = _Repository(outbox)
    attempt_service = _AttemptService()
    sender = AsyncMock(return_value=result)
    engine = SystemOutboxEngine(
        outbox_repository=repository,  # type: ignore[arg-type]
        external_http_sender=sender,
        dispatch_attempt_service=attempt_service,
        workline_domain_dispatcher=_no_workline_messages,
    )
    stats = await engine.dispatch(SimpleNamespace(commit=AsyncMock()), limit=1)
    return stats, repository, attempt_service, sender


@pytest.mark.asyncio
async def test_accepted_explicit_protocol_reject_is_transport_sent_without_retry() -> None:
    transport_result = ExternalHttpTransportResult.accepted(
        http_status_code=409,
        protocol_result=ExternalHttpProtocolResult.REJECTED,
        error_code="HTTP_REJECTED",
    )
    stats, repository, attempt_service, _sender = await _dispatch(transport_result)

    assert stats == {"dispatched": 1, "success": 1, "failed": 0, "skipped": 0}
    assert repository.outbox.status is SystemOutboxStatus.SENT
    assert repository.sent_calls == 1
    assert repository.retry_calls == 0
    assert attempt_service.created == [repository.outbox]
    assert attempt_service.finalized == [
        {
            "attempt": attempt_service.attempt,
            "result": transport_result,
            "outbox_finalization": "sent",
            "auto_commit": False,
        }
    ]


@pytest.mark.asyncio
async def test_retry_safe_not_sent_enters_bounded_retry() -> None:
    transport_result = ExternalHttpTransportResult.not_sent(
        phase=ExternalHttpTransportPhase.CONNECTING,
        safe_to_retry=True,
        error_code="CONNECT_ERROR",
        error_message="connection refused",
    )
    stats, repository, attempt_service, _sender = await _dispatch(transport_result)

    assert stats == {"dispatched": 1, "success": 0, "failed": 1, "skipped": 0}
    assert repository.outbox.status is SystemOutboxStatus.RETRY_WAIT
    assert repository.retry_calls == 1
    assert repository.sent_calls == 0
    assert attempt_service.finalized[0]["outbox_finalization"] == "retry_wait"


@pytest.mark.asyncio
async def test_unsafe_not_sent_is_terminal_failed_without_retry() -> None:
    transport_result = ExternalHttpTransportResult.not_sent(
        phase=ExternalHttpTransportPhase.PREPARING,
        safe_to_retry=False,
        error_code="INVALID_URL",
        error_message="invalid URL",
    )
    stats, repository, attempt_service, _sender = await _dispatch(transport_result)

    assert stats == {"dispatched": 1, "success": 0, "failed": 1, "skipped": 0}
    assert repository.outbox.status is SystemOutboxStatus.FAILED
    assert repository.terminal_failure_calls == 1
    assert repository.retry_calls == 0
    assert attempt_service.finalized[0]["outbox_finalization"] == "failed"


@pytest.mark.asyncio
async def test_ambiguous_result_enters_unknown_and_is_not_dispatched_again() -> None:
    result = ExternalHttpTransportResult.ambiguous(
        phase=ExternalHttpTransportPhase.AWAITING_RESPONSE,
        error_code="READ_TIMEOUT",
        error_message="response timeout",
    )
    outbox = _outbox()
    repository = _Repository(outbox)
    attempt_service = _AttemptService()
    sender = AsyncMock(return_value=result)
    engine = SystemOutboxEngine(
        outbox_repository=repository,  # type: ignore[arg-type]
        external_http_sender=sender,
        dispatch_attempt_service=attempt_service,
        workline_domain_dispatcher=_no_workline_messages,
    )
    db = SimpleNamespace(commit=AsyncMock())

    first = await engine.dispatch(db, limit=1)
    second = await engine.dispatch(db, limit=1)

    assert first == {"dispatched": 1, "success": 0, "failed": 1, "skipped": 0}
    assert second == {"dispatched": 0, "success": 0, "failed": 0, "skipped": 0}
    assert repository.outbox.status is SystemOutboxStatus.UNKNOWN
    assert repository.unknown_calls == 1
    assert sender.await_count == 1
    assert result.outcome is ExternalHttpTransportOutcome.AMBIGUOUS
    assert attempt_service.finalized[0]["outbox_finalization"] == "unknown"
