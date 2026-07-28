"""Generic SystemOutboxEngine 的 typed EXTERNAL_HTTP 状态映射。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.app.effect_ledger_status import DispatchAttemptStatus
from src.app.runtime.orchestration.models.dispatch_attempt import WorklineDispatchAttempt
from src.app.sys.canonical_dispatch import CanonicalPayload
from src.app.sys.dispatch_concurrency import (
    DispatchBucketKey,
    DispatchBucketPolicy,
    DispatchClaimBatch,
    DispatchClaimMetrics,
    DispatchLeaseClaim,
)
from src.app.sys.external_http_evidence import (
    ExternalHttpEvidenceRecoveryError,
    recover_external_http_evidence_failure_unknown,
)
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
from tests.support.external_http import (
    StaticTestCredentialProvider,
    frozen_external_http_binding,
    frozen_outbox_namespace,
)


def _outbox(*, operation_identity: str = "tests.external-http.effect@v1") -> SimpleNamespace:
    values: dict[str, Any] = {
        "id": 1,
        "dispatch_key": "dispatch-001",
        "dispatch_type": SystemOutboxDispatchType.EXTERNAL_HTTP,
        "target_type": SystemOutboxTargetType.HTTP_ENDPOINT,
        "status": SystemOutboxStatus.NEW,
        "attempt_count": 0,
        "next_retry_at": None,
        "last_error": None,
        "finished_at": None,
        "operation_domain": "HANDLING",
        "lease_owner_token": "test-owner:1",
        "lease_expires_at": datetime(2027, 1, 1),
    }
    return frozen_outbox_namespace(
        {"request_id": "REQ-001"},
        target_code="TEST_EXTERNAL_HTTP",
        target_url="https://external.test/effects",
        operation_identity=operation_identity,
        **values,
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
        self.protocol_rejected_calls = 0
        self.retry_calls = 0
        self.unknown_calls = 0
        self.terminal_failure_calls = 0

    async def get_pending_messages(self, _db: Any, **_kwargs: Any) -> list[SimpleNamespace]:
        if self.outbox.status in {SystemOutboxStatus.NEW, SystemOutboxStatus.RETRY_WAIT}:
            return [self.outbox]
        return []

    async def begin_physical_dispatch(
        self,
        _db: Any,
        _outbox_id: int,
        *,
        lease_owner_token: str,
        lease_seconds: int,
    ) -> SimpleNamespace | None:
        assert lease_seconds > 0
        if (
            self.outbox.status is SystemOutboxStatus.DISPATCHING
            and self.outbox.lease_owner_token == lease_owner_token
            and self.outbox.lease_expires_at > timezone.now_for_db()
        ):
            return self.outbox
        return None

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

    async def mark_as_protocol_rejected(
        self,
        _db: Any,
        _outbox_id: int,
        error: str,
        *,
        lease_owner_token: str,
    ) -> SimpleNamespace:
        assert lease_owner_token == self.outbox.lease_owner_token
        self.protocol_rejected_calls += 1
        self.outbox.status = SystemOutboxStatus.SENT
        self.outbox.last_error = error
        self.outbox.finished_at = datetime(2027, 1, 1)
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

    async def get_by_id_for_update(self, _db: Any, _outbox_id: int) -> SimpleNamespace:
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

    async def finalize_external_http_attempt(self, _db: Any, **kwargs: Any) -> SimpleNamespace:
        assert kwargs["auto_commit"] is False
        self.finalized.append(kwargs)
        self.attempt.status = "UNKNOWN"
        return self.attempt


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
        credential_provider=StaticTestCredentialProvider(),
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
    assert repository.sent_calls == 0
    assert repository.protocol_rejected_calls == 1
    assert repository.outbox.finished_at is not None
    assert repository.outbox.last_error == "HTTP_REJECTED"
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
@pytest.mark.parametrize(
    "transport_result",
    [
        ExternalHttpTransportResult.accepted(
            http_status_code=202,
            protocol_result=ExternalHttpProtocolResult.ACCEPTED,
        ),
        ExternalHttpTransportResult.accepted(
            http_status_code=409,
            protocol_result=ExternalHttpProtocolResult.REJECTED,
            protocol_error_code="IDEMPOTENCY_REQUEST_IN_PROGRESS",
            error_code="HTTP_REJECTED",
        ),
        ExternalHttpTransportResult.ambiguous(
            phase=ExternalHttpTransportPhase.AWAITING_RESPONSE,
            error_code="READ_TIMEOUT",
        ),
    ],
)
async def test_generic_transport_does_not_enqueue_wms_effect_status(
    transport_result: ExternalHttpTransportResult,
) -> None:
    outbox = _outbox()
    repository = _Repository(outbox)
    db = SimpleNamespace(commit=AsyncMock())
    enqueued: list[str] = []

    class QueueGateway:
        def enqueue_wms_effect_status(self, *, dispatch_key: str) -> None:
            assert db.commit.await_count >= 3, "claim、发送边界与 transport evidence 必须先提交"
            enqueued.append(dispatch_key)

    engine = SystemOutboxEngine(
        outbox_repository=repository,  # type: ignore[arg-type]
        dispatch_scheduler=_scheduler(outbox),
        external_http_sender=AsyncMock(return_value=transport_result),
        credential_provider=StaticTestCredentialProvider(),
        dispatch_attempt_service=_AttemptService(),
        workline_domain_dispatcher=_no_workline_messages,
        effect_transport_bridge=SimpleNamespace(record_result=AsyncMock()),
        task_queue_gateway=QueueGateway(),
    )

    await engine.dispatch(db, limit=1)

    assert enqueued == []


@pytest.mark.asyncio
async def test_generic_transport_commit_does_not_call_wms_status_queue() -> None:
    outbox = _outbox()
    repository = _Repository(outbox)
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())

    class ForbiddenQueueGateway:
        def enqueue_wms_effect_status(self, *, dispatch_key: str) -> None:
            raise AssertionError(f"generic transport must not enqueue WMS status: {dispatch_key}")

    engine = SystemOutboxEngine(
        outbox_repository=repository,  # type: ignore[arg-type]
        dispatch_scheduler=_scheduler(outbox),
        external_http_sender=AsyncMock(
            return_value=ExternalHttpTransportResult.accepted(
                http_status_code=202,
                protocol_result=ExternalHttpProtocolResult.ACCEPTED,
            )
        ),
        credential_provider=StaticTestCredentialProvider(),
        dispatch_attempt_service=_AttemptService(),
        workline_domain_dispatcher=_no_workline_messages,
        effect_transport_bridge=SimpleNamespace(record_result=AsyncMock()),
        task_queue_gateway=ForbiddenQueueGateway(),
    )

    stats = await engine.dispatch(db, limit=1)

    assert stats == {"dispatched": 1, "success": 1, "failed": 0, "skipped": 0}
    assert repository.outbox.status is SystemOutboxStatus.SENT
    db.rollback.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation_domain", ["TEST_INVENTORY", "TEST_FULFILLMENT"])
async def test_generic_callback_winning_transport_race_still_finalizes_attempt(
    operation_domain: str,
) -> None:
    transport_result = ExternalHttpTransportResult.accepted(
        http_status_code=202,
        protocol_result=ExternalHttpProtocolResult.ACCEPTED,
    )
    outbox = _outbox()
    outbox.operation_domain = operation_domain
    repository = _Repository(outbox)

    async def callback_wins_mark_as_sent(
        _db: Any,
        _outbox_id: int,
        *,
        lease_owner_token: str,
    ) -> None:
        assert lease_owner_token == outbox.lease_owner_token
        outbox.status = SystemOutboxStatus.SENT
        outbox.finished_at = timezone.now_for_db()
        outbox.lease_expires_at = None

    repository.mark_as_sent = callback_wins_mark_as_sent  # type: ignore[method-assign]
    attempt_service = _AttemptService()
    effect_bridge = SimpleNamespace(record_result=AsyncMock())
    engine = SystemOutboxEngine(
        outbox_repository=repository,  # type: ignore[arg-type]
        dispatch_scheduler=_scheduler(outbox),
        external_http_sender=AsyncMock(return_value=transport_result),
        credential_provider=StaticTestCredentialProvider(),
        dispatch_attempt_service=attempt_service,
        workline_domain_dispatcher=_no_workline_messages,
        effect_transport_bridge=effect_bridge,
    )

    stats = await engine.dispatch(SimpleNamespace(commit=AsyncMock()), limit=1)

    assert stats == {"dispatched": 1, "success": 1, "failed": 0, "skipped": 0}
    assert attempt_service.finalized[0]["outbox_finalization"] == "sent"
    effect_bridge.record_result.assert_awaited_once()


@pytest.mark.asyncio
async def test_callback_winning_evidence_recovery_preserves_completed_outbox() -> None:
    result = ExternalHttpTransportResult.accepted(
        http_status_code=202,
        protocol_result=ExternalHttpProtocolResult.ACCEPTED,
    )
    callback_completed = SimpleNamespace(
        status=SystemOutboxStatus.SENT,
        finished_at=timezone.now_for_db(),
    )
    repository = SimpleNamespace(
        get_by_id_for_update=AsyncMock(return_value=callback_completed),
        mark_evidence_persistence_unknown=AsyncMock(),
    )
    active_db = SimpleNamespace(rollback=AsyncMock())
    recovery_db = SimpleNamespace(commit=AsyncMock())
    attempt_service = SimpleNamespace(finalize_external_http_attempt=AsyncMock())
    bridge = SimpleNamespace(record_result=AsyncMock())

    @asynccontextmanager
    async def recovery_context():
        yield recovery_db

    recovered = await recover_external_http_evidence_failure_unknown(
        active_db,
        outbox_repository=repository,
        outbox_id=1,
        lease_owner_token="callback-race-owner",
        result=result,
        cause=RuntimeError("reducer evidence unavailable"),
        recovery_context_factory=recovery_context,
        attempt_service=attempt_service,
        effect_transport_bridge=bridge,
        dispatch_key="dispatch-callback-race",
        attempt_no=2,
        operation_identity="tests.external-http.callback-race@v1",
    )

    assert recovered is callback_completed
    repository.mark_evidence_persistence_unknown.assert_not_awaited()
    attempt_service.finalize_external_http_attempt.assert_awaited_once_with(
        recovery_db,
        lease_token="callback-race-owner",
        result=result,
        outbox_finalization="sent",
        auto_commit=False,
    )
    assert bridge.record_result.await_args.kwargs["result"] is result
    assert bridge.record_result.await_args.kwargs["operation_identity"] == "tests.external-http.callback-race@v1"
    recovery_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_late_transport_evidence_recovery_preserves_unknown_ledgers() -> None:
    result = ExternalHttpTransportResult.accepted(
        http_status_code=202,
        protocol_result=ExternalHttpProtocolResult.ACCEPTED,
    )
    fenced_unknown = SimpleNamespace(
        status=SystemOutboxStatus.UNKNOWN,
        dispatch_started_at=timezone.now_for_db(),
    )
    repository = SimpleNamespace(
        get_by_id_for_update=AsyncMock(return_value=fenced_unknown),
        mark_evidence_persistence_unknown=AsyncMock(),
    )
    active_db = SimpleNamespace(rollback=AsyncMock())
    recovery_db = SimpleNamespace(commit=AsyncMock())
    attempt_service = SimpleNamespace(finalize_external_http_attempt=AsyncMock())
    bridge = SimpleNamespace(record_result=AsyncMock())

    @asynccontextmanager
    async def recovery_context():
        yield recovery_db

    recovered = await recover_external_http_evidence_failure_unknown(
        active_db,
        outbox_repository=repository,
        outbox_id=2,
        lease_owner_token="late-result-owner",
        result=result,
        cause=RuntimeError("late reducer evidence unavailable"),
        recovery_context_factory=recovery_context,
        attempt_service=attempt_service,
        effect_transport_bridge=bridge,
        dispatch_key="dispatch-late-result",
        attempt_no=3,
    )

    assert recovered is fenced_unknown
    repository.mark_evidence_persistence_unknown.assert_not_awaited()
    attempt_service.finalize_external_http_attempt.assert_not_awaited()
    bridge.record_result.assert_awaited_once()
    assert bridge.record_result.await_args.kwargs["result"] is result
    recovery_db.commit.assert_awaited_once()


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
        credential_provider=StaticTestCredentialProvider(),
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
    effect_bridge = SimpleNamespace(record_result=AsyncMock())

    @asynccontextmanager
    async def recovery_context():
        yield recovery_db

    engine = SystemOutboxEngine(
        outbox_repository=repository,  # type: ignore[arg-type]
        dispatch_scheduler=_scheduler(outbox),
        external_http_sender=sender,
        credential_provider=StaticTestCredentialProvider(),
        dispatch_attempt_service=attempt_service,
        external_http_recovery_context_factory=recovery_context,
        workline_domain_dispatcher=_no_workline_messages,
        effect_transport_bridge=effect_bridge,
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
    assert attempt_service.attempt.status == "UNKNOWN"
    assert attempt_service.finalized[-1]["outbox_finalization"] == "unknown"
    recovery_result = attempt_service.finalized[-1]["result"]
    assert recovery_result.outcome is ExternalHttpTransportOutcome.AMBIGUOUS
    effect_bridge.record_result.assert_awaited_once()


@pytest.mark.asyncio
async def test_recovery_failure_then_expired_external_http_lease_never_sends_twice(db_session: Any) -> None:
    """首 worker 的 UNKNOWN 恢复失败后，下一 worker 只能收口旧 lease，不能重发 HTTP。"""

    canonical = CanonicalPayload.from_projection({"request_id": "REQ-RECOVERY-FAIL-LEASE"})
    frozen_binding = frozen_external_http_binding(
        target_code="TEST_RECOVERY_EXTERNAL_HTTP",
        target_url="https://external.test/recovery",
        provider_profile_identity="tests.external-http.recovery.v1",
        operation_identity="tests.external-http.recovery@v1",
    )
    outbox = SystemOutbox(
        **frozen_binding.as_persisted_fields(),
        operation_domain="HANDLING",
        dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
        dispatch_key="external-http-recovery-fail-lease",
        target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
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
    dispatch_attempt = WorklineDispatchAttempt(
        outbox_id=outbox.id,
        dispatch_key=outbox.dispatch_key,
        attempt_no=1,
        lease_token=outbox.lease_owner_token,
        lease_expires_at=outbox.lease_expires_at,
        status=DispatchAttemptStatus.DISPATCHING,
        target_type=SystemOutboxTargetType.HTTP_ENDPOINT.value,
        target_code=outbox.target_code,
        started_at=timezone.now_for_db(),
    )
    db_session.add(dispatch_attempt)
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
    attempt_service = _FailOnceAttemptService()
    attempt_service.attempt = dispatch_attempt
    engine = SystemOutboxEngine(
        outbox_repository=repository,
        dispatch_scheduler=_scheduler(outbox),
        external_http_sender=sender,
        credential_provider=StaticTestCredentialProvider(),
        dispatch_attempt_service=attempt_service,
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


@pytest.mark.asyncio
async def test_generic_dispatcher_emits_ordered_external_http_fault_boundaries() -> None:
    outbox = _outbox()
    repository = _Repository(outbox)
    attempt_service = _AttemptService()
    sender = AsyncMock(
        return_value=ExternalHttpTransportResult.accepted(
            http_status_code=202,
            protocol_result=ExternalHttpProtocolResult.ACCEPTED,
        )
    )
    bridge = SimpleNamespace(record_result=AsyncMock())
    observed: list[tuple[str, int | None]] = []

    async def fault_hook(point: object, current_outbox: Any | None) -> None:
        observed.append((str(point), getattr(current_outbox, "id", None)))

    engine = SystemOutboxEngine(
        outbox_repository=repository,  # type: ignore[arg-type]
        dispatch_scheduler=_scheduler(outbox),
        external_http_sender=sender,
        credential_provider=StaticTestCredentialProvider(),
        dispatch_attempt_service=attempt_service,
        workline_domain_dispatcher=_no_workline_messages,
        effect_transport_bridge=bridge,
        external_http_fault_hook=fault_hook,
    )

    stats = await engine.dispatch(SimpleNamespace(commit=AsyncMock()), limit=1)

    assert stats == {"dispatched": 1, "success": 1, "failed": 0, "skipped": 0}
    assert observed == [
        ("BEFORE_CLAIM", None),
        ("AFTER_CLAIM_COMMIT", 1),
        ("BEFORE_SEND", 1),
        ("AFTER_SEND", 1),
        ("BEFORE_OUTBOX_EVIDENCE", 1),
        ("AFTER_OUTBOX_EVIDENCE", 1),
        ("BEFORE_ATTEMPT_EVIDENCE", 1),
        ("AFTER_ATTEMPT_EVIDENCE", 1),
        ("BEFORE_REDUCER_EVIDENCE", 1),
        ("AFTER_REDUCER_EVIDENCE", 1),
        ("AFTER_EVIDENCE_COMMIT", 1),
    ]
