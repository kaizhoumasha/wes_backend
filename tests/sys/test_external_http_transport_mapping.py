"""Generic SystemOutboxEngine 的 typed EXTERNAL_HTTP 状态映射。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.app.sys.canonical_dispatch import CanonicalPayload
from src.app.sys.dispatch_concurrency import (
    DispatchBucketKey,
    DispatchBucketPolicy,
    DispatchClaimBatch,
    DispatchClaimMetrics,
    DispatchLeaseClaim,
)
from src.app.sys.external_http_evidence import ExternalHttpEvidenceRecoveryError
from src.app.sys.external_http_transport import (
    ExternalHttpProtocolResult,
    ExternalHttpTransportOutcome,
    ExternalHttpTransportPhase,
    ExternalHttpTransportResult,
)
from src.app.sys.models import SystemOutbox, SystemOutboxDispatchType, SystemOutboxStatus, SystemOutboxTargetType
from src.app.sys.repositories import SystemOutboxRepository
from src.app.sys.services.outbox_engine import SystemOutboxEngine
from src.utils.timezone import timezone


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
        lease_owner_token="test-owner:1",
        lease_expires_at=datetime(2027, 1, 1),
    )


def _scheduler(outbox: Any) -> SimpleNamespace:
    outbox.status = SystemOutboxStatus.DISPATCHING
    claim = DispatchLeaseClaim(
        outbox=outbox,
        bucket=DispatchBucketKey("test.profile", "test.operation"),
        lease_owner_token=str(outbox.lease_owner_token),
        lease_expires_at=outbox.lease_expires_at,
        policy=DispatchBucketPolicy(),
    )
    first_batch = DispatchClaimBatch(
        claims=(claim,),
        metrics=DispatchClaimMetrics(1, 1, 0, 0, (), (), ()),
    )
    empty_batch = DispatchClaimBatch(
        claims=(),
        metrics=DispatchClaimMetrics(0, 0, 0, None, (), (), ()),
    )
    return SimpleNamespace(claim=AsyncMock(side_effect=[first_batch, empty_batch]))


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

    async def mark_as_sent(self, _db: Any, _outbox_id: int, *, lease_owner_token: str) -> SimpleNamespace:
        assert lease_owner_token == self.outbox.lease_owner_token
        self.sent_calls += 1
        self.outbox.status = SystemOutboxStatus.SENT
        return self.outbox

    async def mark_as_failed(
        self,
        _db: Any,
        _outbox_id: int,
        error: str,
        _max_retries: int,
        *,
        lease_owner_token: str,
    ) -> SimpleNamespace:
        assert lease_owner_token == self.outbox.lease_owner_token
        self.retry_calls += 1
        self.outbox.status = SystemOutboxStatus.RETRY_WAIT
        self.outbox.last_error = error
        return self.outbox

    async def mark_as_unknown(
        self, _db: Any, _outbox_id: int, error: str, *, lease_owner_token: str
    ) -> SimpleNamespace:
        assert lease_owner_token == self.outbox.lease_owner_token
        self.unknown_calls += 1
        self.outbox.status = SystemOutboxStatus.UNKNOWN
        self.outbox.last_error = error
        return self.outbox

    async def mark_as_terminal_failure(
        self, _db: Any, _outbox_id: int, error: str, *, lease_owner_token: str
    ) -> SimpleNamespace:
        assert lease_owner_token == self.outbox.lease_owner_token
        self.terminal_failure_calls += 1
        self.outbox.status = SystemOutboxStatus.FAILED
        self.outbox.last_error = error
        return self.outbox

    async def mark_evidence_persistence_unknown(
        self,
        _db: Any,
        _outbox_id: int,
        error: str,
        *,
        lease_owner_token: str,
    ) -> SimpleNamespace:
        assert lease_owner_token == self.outbox.lease_owner_token
        self.unknown_calls += 1
        self.outbox.status = SystemOutboxStatus.UNKNOWN
        self.outbox.last_error = error
        return self.outbox


class _AttemptService:
    def __init__(self, *, fail_finalize: bool = False) -> None:
        self.attempt = SimpleNamespace(status="DISPATCHING")
        self.created: list[SimpleNamespace] = []
        self.finalized: list[dict[str, Any]] = []
        self.fail_finalize = fail_finalize

    async def create_attempt(self, _db: Any, *, outbox: SimpleNamespace, auto_commit: bool) -> SimpleNamespace:
        assert auto_commit is False
        self.created.append(outbox)
        return self.attempt

    async def finalize_external_http_attempt_record(self, _db: Any, **kwargs: Any) -> SimpleNamespace:
        assert kwargs["auto_commit"] is False
        self.finalized.append(kwargs)
        if self.fail_finalize:
            raise RuntimeError("attempt evidence unavailable")
        return kwargs["attempt"]


class _FailOnceAttemptService(_AttemptService):
    def __init__(self) -> None:
        super().__init__()
        self.failures_remaining = 1

    async def finalize_external_http_attempt_record(self, _db: Any, **kwargs: Any) -> SimpleNamespace:
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise RuntimeError("attempt evidence unavailable")
        return await super().finalize_external_http_attempt_record(_db, **kwargs)


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
        dispatch_scheduler=_scheduler(outbox),
        external_http_sender=sender,
        dispatch_attempt_service=attempt_service,
        workline_domain_dispatcher=_no_workline_messages,
        effect_transport_bridge=SimpleNamespace(record_result=AsyncMock()),
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
            "lease_owner_token": "test-owner:1",
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
        dispatch_scheduler=_scheduler(outbox),
        external_http_sender=sender,
        dispatch_attempt_service=attempt_service,
        workline_domain_dispatcher=_no_workline_messages,
        effect_transport_bridge=SimpleNamespace(record_result=AsyncMock()),
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "transport_result",
    [
        ExternalHttpTransportResult.accepted(
            http_status_code=202,
            protocol_result=ExternalHttpProtocolResult.ACCEPTED,
        ),
        ExternalHttpTransportResult.ambiguous(
            phase=ExternalHttpTransportPhase.AWAITING_RESPONSE,
            error_code="READ_TIMEOUT",
        ),
        ExternalHttpTransportResult.not_sent(
            phase=ExternalHttpTransportPhase.CONNECTING,
            safe_to_retry=True,
            error_code="CONNECT_ERROR",
        ),
    ],
)
async def test_generic_attempt_evidence_failure_fail_closes_unknown_without_second_send(
    transport_result: ExternalHttpTransportResult,
) -> None:
    outbox = _outbox()
    repository = _Repository(outbox)
    attempt_service = _AttemptService(fail_finalize=True)
    sender = AsyncMock(return_value=transport_result)
    current_db = SimpleNamespace(
        commit=AsyncMock(),
        rollback=AsyncMock(side_effect=lambda: setattr(outbox, "status", SystemOutboxStatus.DISPATCHING)),
    )
    recovery_db = SimpleNamespace(commit=AsyncMock())

    @asynccontextmanager
    async def recovery_context():
        yield recovery_db

    engine = SystemOutboxEngine(
        outbox_repository=repository,  # type: ignore[arg-type]
        dispatch_scheduler=_scheduler(outbox),
        external_http_sender=sender,
        dispatch_attempt_service=attempt_service,
        external_http_recovery_context_factory=recovery_context,
        workline_domain_dispatcher=_no_workline_messages,
    )

    first = await engine.dispatch(current_db, limit=1)
    second = await engine.dispatch(current_db, limit=1)

    assert first == {"dispatched": 1, "success": 0, "failed": 1, "skipped": 0}
    assert second == {"dispatched": 0, "success": 0, "failed": 0, "skipped": 0}
    assert outbox.status is SystemOutboxStatus.UNKNOWN
    assert outbox.next_retry_at is None
    assert "EXTERNAL_HTTP_EVIDENCE_PERSISTENCE_FAILED" in outbox.last_error
    assert transport_result.outcome.value in outbox.last_error
    # NOT_SENT 的 RETRY_WAIT 写入发生在随后回滚的原事务内；隔离恢复不得再次走重试路径。
    expected_rolled_back_retry_call = int(
        transport_result.outcome is ExternalHttpTransportOutcome.NOT_SENT and transport_result.safe_to_retry
    )
    assert repository.retry_calls == expected_rolled_back_retry_call
    assert sender.await_count == 1
    current_db.rollback.assert_awaited_once()
    recovery_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_recovery_failure_then_expired_external_http_lease_never_sends_twice(db_session: Any) -> None:
    """首 worker 的 UNKNOWN 恢复失败后，下一 worker 只能收口旧 lease，不能重发 HTTP。"""

    canonical = CanonicalPayload.from_projection({"request_id": "REQ-RECOVERY-FAIL-LEASE"})
    outbox = SystemOutbox(
        operation_domain="HANDLING",
        dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
        dispatch_key="external-http-recovery-fail-lease",
        target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
        target_code="WMS_RCS_BIN_OPERATION",
        provider_profile_identity="wms.legacy-transport.production",
        operation_identity="wms.transport.handling@v1",
        payload_json={"request_id": "REQ-RECOVERY-FAIL-LEASE"},
        canonical_payload_bytes=canonical.body,
        payload_hash=canonical.sha256,
    )
    db_session.add(outbox)
    await db_session.commit()
    await db_session.refresh(outbox)
    outbox.status = SystemOutboxStatus.DISPATCHING
    outbox.lease_owner_token = "test-owner:recovery-failure"
    outbox.lease_expires_at = timezone.now_for_db() + timedelta(minutes=5)
    await db_session.commit()
    sender = AsyncMock(
        return_value=ExternalHttpTransportResult.accepted(
            http_status_code=202,
            protocol_result=ExternalHttpProtocolResult.ACCEPTED,
        )
    )

    @asynccontextmanager
    async def unavailable_recovery_context():
        yield SimpleNamespace(commit=AsyncMock())

    repository = SystemOutboxRepository()
    engine = SystemOutboxEngine(
        outbox_repository=repository,
        dispatch_scheduler=_scheduler(outbox),
        external_http_sender=sender,
        dispatch_attempt_service=_FailOnceAttemptService(),
        external_http_recovery_context_factory=unavailable_recovery_context,
        workline_domain_dispatcher=_no_workline_messages,
    )

    with pytest.raises(ExternalHttpEvidenceRecoveryError):
        await engine.dispatch(db_session, limit=1)
    await db_session.refresh(outbox)
    assert outbox.status is SystemOutboxStatus.DISPATCHING

    outbox.lease_expires_at = timezone.now_for_db() - timedelta(seconds=1)
    await db_session.commit()

    fenced = await repository.fence_expired_external_http_leases(
        db_session,
        now=timezone.now_for_db(),
    )
    second = await engine.dispatch(db_session, limit=1)
    await db_session.refresh(outbox)

    assert len(fenced) == 1
    assert second == {"dispatched": 0, "success": 0, "failed": 0, "skipped": 0}
    assert sender.await_count == 1
    assert outbox.status is SystemOutboxStatus.UNKNOWN
    assert outbox.next_retry_at is None
    assert outbox.finished_at is not None
    assert "STALE_EXTERNAL_HTTP_DISPATCH_LEASE" in outbox.last_error
