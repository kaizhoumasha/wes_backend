"""Workline dispatcher 的 EXTERNAL_HTTP typed result 映射。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from importlib import import_module
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.app.runtime.orchestration.services.inbox.outbox_dispatch_service import OutboxDispatchService
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
from src.app.sys.models import SystemOutboxDispatchType, SystemOutboxStatus


class _Repository:
    def __init__(self) -> None:
        self.outbox = SimpleNamespace(id=1, status=SystemOutboxStatus.DISPATCHING)
        self.calls: list[str] = []

    async def mark_as_sent(self, _db: Any, _outbox_id: int, *, lease_owner_token: str) -> SimpleNamespace:
        assert lease_owner_token
        self.calls.append("sent")
        self.outbox.status = SystemOutboxStatus.SENT
        return self.outbox

    async def mark_as_failed(
        self,
        _db: Any,
        _outbox_id: int,
        _error: str,
        _max_retries: int,
        *,
        lease_owner_token: str,
    ) -> SimpleNamespace:
        assert lease_owner_token
        self.calls.append("retry_wait")
        self.outbox.status = SystemOutboxStatus.RETRY_WAIT
        return self.outbox

    async def mark_as_unknown(
        self, _db: Any, _outbox_id: int, _error: str, *, lease_owner_token: str
    ) -> SimpleNamespace:
        assert lease_owner_token
        self.calls.append("unknown")
        self.outbox.status = SystemOutboxStatus.UNKNOWN
        return self.outbox

    async def mark_as_terminal_failure(
        self, _db: Any, _outbox_id: int, _error: str, *, lease_owner_token: str
    ) -> SimpleNamespace:
        assert lease_owner_token
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
        lease_owner_token="owner-1",
        retry_budget=3,
    )

    assert updated is repository.outbox
    assert repository.calls == [expected_finalization]
    assert attempt_service.finalized == [
        {
            "attempt": attempt,
            "lease_owner_token": "owner-1",
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


class _DispatchRepository(_Repository):
    def __init__(self, outbox: SimpleNamespace) -> None:
        super().__init__()
        self.outbox = outbox
        self.retry_calls = 0

    async def get_pending_messages(self, _db: Any, **_kwargs: Any) -> list[SimpleNamespace]:
        if self.outbox.status in {SystemOutboxStatus.NEW, SystemOutboxStatus.RETRY_WAIT}:
            return [self.outbox]
        return []

    async def mark_as_dispatching(self, _db: Any, _outbox_id: int) -> SimpleNamespace | None:
        if self.outbox.status not in {SystemOutboxStatus.NEW, SystemOutboxStatus.RETRY_WAIT}:
            return None
        self.outbox.status = SystemOutboxStatus.DISPATCHING
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

    async def mark_evidence_persistence_unknown(
        self,
        _db: Any,
        _outbox_id: int,
        error: str,
        *,
        lease_owner_token: str,
    ) -> SimpleNamespace:
        assert lease_owner_token == self.outbox.lease_owner_token
        self.calls.append("evidence_unknown")
        self.outbox.status = SystemOutboxStatus.UNKNOWN
        self.outbox.next_retry_at = None
        self.outbox.last_error = error
        return self.outbox


class _DispatchAttemptService(_AttemptService):
    def __init__(self, *, missing_attempt: bool = False) -> None:
        super().__init__()
        self.attempt = SimpleNamespace(status="DISPATCHING")
        self.missing_attempt = missing_attempt

    async def create_attempt(self, _db: Any, **_kwargs: Any) -> SimpleNamespace | None:
        return None if self.missing_attempt else self.attempt

    async def finalize_attempt_record(self, _db: Any, **kwargs: Any) -> SimpleNamespace:
        return kwargs["attempt"]


class _CommitFailsAfterSendDatabase:
    def __init__(self, outbox: SimpleNamespace, *, fail_commit_number: int | None = 3) -> None:
        self.outbox = outbox
        self.fail_commit_number = fail_commit_number
        self.commit_count = 0
        self.rollback_count = 0

    async def commit(self) -> None:
        self.commit_count += 1
        if self.commit_count == self.fail_commit_number:
            raise RuntimeError("typed evidence commit unavailable")

    async def rollback(self) -> None:
        self.rollback_count += 1
        self.outbox.status = SystemOutboxStatus.DISPATCHING


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("missing_attempt", "recovery_fails"),
    [(False, False), (False, True), (True, False)],
    ids=["commit_recovered", "commit_recovery_failed", "attempt_missing_recovered"],
)
async def test_workline_evidence_persistence_failure_never_reopens_sendable_state(
    monkeypatch: pytest.MonkeyPatch,
    missing_attempt: bool,
    recovery_fails: bool,
) -> None:
    canonical = CanonicalPayload.from_projection({"request_id": "REQ-WORKLINE-EVIDENCE-FAIL"})
    outbox = SimpleNamespace(
        id=91,
        dispatch_key="workline-evidence-fail-91",
        dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
        target_type="HTTP_ENDPOINT",
        target_code="WMS_TEST",
        canonical_payload_bytes=canonical.body,
        payload_hash=canonical.sha256,
        payload_json={"request_id": "REQ-WORKLINE-EVIDENCE-FAIL"},
        status=SystemOutboxStatus.NEW,
        attempt_count=0,
        next_retry_at=None,
        last_error=None,
        operation_domain="WORKLINE",
        workline_id=None,
        session_id=None,
        device_id=None,
        provider_profile_identity="wms.profile-test",
        operation_identity="wms.effect-test@v1",
        lease_owner_token="owner-workline-91",
        lease_expires_at=datetime(2027, 1, 1),
    )
    repository = _DispatchRepository(outbox)
    attempt_service = _DispatchAttemptService(missing_attempt=missing_attempt)
    current_db = _CommitFailsAfterSendDatabase(outbox, fail_commit_number=None if missing_attempt else 2)
    recovery_db = SimpleNamespace(
        commit=AsyncMock(side_effect=RuntimeError("isolated recovery unavailable") if recovery_fails else None)
    )
    sender = AsyncMock(
        return_value=ExternalHttpTransportResult.accepted(
            http_status_code=202,
            protocol_result=ExternalHttpProtocolResult.ACCEPTED,
        )
    )

    @asynccontextmanager
    async def recovery_context():
        yield recovery_db

    outbox.status = SystemOutboxStatus.DISPATCHING
    claim = DispatchLeaseClaim(
        outbox=outbox,
        bucket=DispatchBucketKey(outbox.provider_profile_identity, outbox.operation_identity),
        lease_owner_token=outbox.lease_owner_token,
        lease_expires_at=outbox.lease_expires_at,
        policy=DispatchBucketPolicy(),
    )
    scheduler = SimpleNamespace(
        claim=AsyncMock(
            side_effect=[
                DispatchClaimBatch(
                    claims=(claim,),
                    metrics=DispatchClaimMetrics(1, 1, 0, 0, (), (), ()),
                ),
                DispatchClaimBatch(
                    claims=(),
                    metrics=DispatchClaimMetrics(0, 0, 0, None, (), (), ()),
                ),
            ]
        )
    )

    service = OutboxDispatchService(
        external_http_recovery_context_factory=recovery_context,
        effect_transport_bridge=SimpleNamespace(record_result=AsyncMock()),
        outbox_repository=repository,
        dispatch_scheduler=scheduler,
        dispatch_attempt_service=attempt_service,
    )
    dispatch_module = import_module("src.app.runtime.orchestration.services.inbox.outbox_dispatch_service")
    event_stream_module = import_module("src.app.sys.services.event_stream_service")
    monkeypatch.setattr(service, "_dispatch_blocked_resource_heads", AsyncMock(return_value=(set(), set())))
    monkeypatch.setattr(service, "_should_dispatch_to_sandbox", AsyncMock(return_value=False))
    monkeypatch.setattr(service, "_dispatch_external_http", sender)
    monkeypatch.setattr(dispatch_module, "_repair_orphaned_device_busy_dispatches", AsyncMock(return_value=0))
    monkeypatch.setattr(dispatch_module, "_repair_self_blocked_device_busy_dispatches", AsyncMock(return_value=0))
    monkeypatch.setattr(dispatch_module, "_record_diagnostic", AsyncMock())
    monkeypatch.setattr(dispatch_module, "_mark_device_command_failed_if_dispatch_exhausted", AsyncMock())
    monkeypatch.setattr(event_stream_module, "publish_deferred_sse_events", AsyncMock())

    if recovery_fails:
        with pytest.raises(ExternalHttpEvidenceRecoveryError):
            await service.dispatch(current_db, limit=1)
        assert repository.retry_calls == 0
        assert sender.await_count == 1
        assert current_db.rollback_count == 1
        recovery_db.commit.assert_awaited_once()
        return

    first = await service.dispatch(current_db, limit=1)
    second = await service.dispatch(current_db, limit=1)

    assert first == {"dispatched": 1, "success": 0, "failed": 1, "skipped": 0}
    assert second == {"dispatched": 0, "success": 0, "failed": 0, "skipped": 0}
    assert outbox.status is SystemOutboxStatus.UNKNOWN
    assert "EXTERNAL_HTTP_EVIDENCE_PERSISTENCE_FAILED" in outbox.last_error
    assert repository.retry_calls == 0
    assert sender.await_count == 1
    assert current_db.rollback_count == 1
    recovery_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_workline_dispatcher_emits_ordered_external_http_fault_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canonical = CanonicalPayload.from_projection({"request_id": "REQ-WORKLINE-FAULT-BOUNDARIES"})
    outbox = SimpleNamespace(
        id=92,
        dispatch_key="workline-fault-boundaries-92",
        dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
        target_type="HTTP_ENDPOINT",
        target_code="WMS_TEST",
        canonical_payload_bytes=canonical.body,
        payload_hash=canonical.sha256,
        payload_json={"request_id": "REQ-WORKLINE-FAULT-BOUNDARIES"},
        status=SystemOutboxStatus.DISPATCHING,
        attempt_count=0,
        next_retry_at=None,
        last_error=None,
        operation_domain="WORKLINE",
        workline_id=None,
        session_id=None,
        device_id=None,
        provider_profile_identity="wms.profile-test",
        operation_identity="wms.effect-test@v1",
        lease_owner_token="owner-workline-92",
        lease_expires_at=datetime(2027, 1, 1),
    )
    repository = _DispatchRepository(outbox)
    attempt_service = _DispatchAttemptService()
    transport_result = ExternalHttpTransportResult.accepted(
        http_status_code=202,
        protocol_result=ExternalHttpProtocolResult.ACCEPTED,
    )
    sender = AsyncMock(return_value=transport_result)
    observed: list[tuple[str, int | None]] = []

    async def fault_hook(point: object, current_outbox: Any | None) -> None:
        observed.append((str(point), getattr(current_outbox, "id", None)))

    claim = DispatchLeaseClaim(
        outbox=outbox,
        bucket=DispatchBucketKey(outbox.provider_profile_identity, outbox.operation_identity),
        lease_owner_token=outbox.lease_owner_token,
        lease_expires_at=outbox.lease_expires_at,
        policy=DispatchBucketPolicy(),
    )
    scheduler = SimpleNamespace(
        claim=AsyncMock(
            return_value=DispatchClaimBatch(
                claims=(claim,),
                metrics=DispatchClaimMetrics(1, 1, 0, 0, (), (), ()),
            )
        )
    )
    bridge = SimpleNamespace(record_result=AsyncMock())
    service = OutboxDispatchService(
        effect_transport_bridge=bridge,
        outbox_repository=repository,
        dispatch_scheduler=scheduler,
        dispatch_attempt_service=attempt_service,
        external_http_fault_hook=fault_hook,
    )
    dispatch_module = import_module("src.app.runtime.orchestration.services.inbox.outbox_dispatch_service")
    event_stream_module = import_module("src.app.sys.services.event_stream_service")
    monkeypatch.setattr(service, "_dispatch_blocked_resource_heads", AsyncMock(return_value=(set(), set())))
    monkeypatch.setattr(service, "_should_dispatch_to_sandbox", AsyncMock(return_value=False))
    monkeypatch.setattr(service, "_dispatch_external_http", sender)
    monkeypatch.setattr(dispatch_module, "_repair_orphaned_device_busy_dispatches", AsyncMock(return_value=0))
    monkeypatch.setattr(dispatch_module, "_repair_self_blocked_device_busy_dispatches", AsyncMock(return_value=0))
    monkeypatch.setattr(event_stream_module, "publish_deferred_sse_events", AsyncMock())

    stats = await service.dispatch(SimpleNamespace(commit=AsyncMock()), limit=1)

    assert stats == {"dispatched": 1, "success": 1, "failed": 0, "skipped": 0}
    assert observed == [
        ("BEFORE_CLAIM", None),
        ("AFTER_CLAIM_COMMIT", 92),
        ("BEFORE_SEND", 92),
        ("AFTER_SEND", 92),
        ("BEFORE_OUTBOX_EVIDENCE", 92),
        ("AFTER_OUTBOX_EVIDENCE", 92),
        ("BEFORE_ATTEMPT_EVIDENCE", 92),
        ("AFTER_ATTEMPT_EVIDENCE", 92),
        ("BEFORE_REDUCER_EVIDENCE", 92),
        ("AFTER_REDUCER_EVIDENCE", 92),
        ("AFTER_EVIDENCE_COMMIT", 92),
    ]
