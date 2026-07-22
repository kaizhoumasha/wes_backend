from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from src.app.device.models.command import DeviceCommand
from src.app.runtime.orchestration.models.session import RunMode, SessionStatus, WorklineSession
from src.app.runtime.orchestration.repositories.runtime_inbox_repository import runtime_inbox_repository
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.sys.canonical_dispatch import CanonicalPayload
from src.app.sys.dispatch_concurrency import DispatchBucketKey
from src.app.sys.models import SystemOutboxStatus
from src.app.sys.models.outbox import (
    SystemOutbox,
    SystemOutboxDispatchType,
    SystemOutboxTargetType,
)
from src.app.sys.repositories.outbox_repository import SystemOutboxRepository
from src.utils.timezone import timezone


class _FakeResult:
    def __init__(self, outboxes: list[Any]) -> None:
        self._outboxes = outboxes

    def scalars(self) -> _FakeResult:
        return self

    def all(self) -> list[Any]:
        return self._outboxes

    def scalar_one_or_none(self) -> Any | None:
        return self._outboxes[0] if self._outboxes else None


class _CapturingDb:
    def __init__(self, outboxes: list[Any]) -> None:
        self.outboxes = outboxes
        self.statement: Any | None = None

    async def execute(self, statement: Any) -> _FakeResult:
        self.statement = statement
        return _FakeResult(self.outboxes)

    async def flush(self) -> None:
        return None


def _compiled_status_values(statement: Any) -> set[SystemOutboxStatus]:
    values: set[SystemOutboxStatus] = set()
    for value in statement.compile().params.values():
        if isinstance(value, list):
            values.update(item for item in value if isinstance(item, SystemOutboxStatus))
    return values


@pytest.mark.asyncio
async def test_cancel_active_by_session_treats_retry_wait_as_active() -> None:
    blocked_outbox = SimpleNamespace(
        status=SystemOutboxStatus.RETRY_WAIT,
        last_error=None,
        finished_at=None,
    )
    db = _CapturingDb([blocked_outbox])

    count = await SystemOutboxRepository().cancel_active_by_session(
        db,
        session_id=7001,
        reason="MANUAL_CANCEL_REQUESTED",
    )

    assert db.statement is not None
    assert SystemOutboxStatus.RETRY_WAIT in _compiled_status_values(db.statement)
    assert count == 1
    assert blocked_outbox.status == SystemOutboxStatus.CANCELLED
    assert blocked_outbox.last_error == "MANUAL_CANCEL_REQUESTED"
    assert blocked_outbox.finished_at is not None


@pytest.mark.asyncio
async def test_repository_rejects_dispatch_key_updates_before_loading_row() -> None:
    with pytest.raises(ValueError, match=r"dispatch_key.*不可变"):
        await SystemOutboxRepository().update(  # type: ignore[arg-type]
            SimpleNamespace(),
            7,
            {"dispatch_key": "replacement"},
        )


@pytest.mark.asyncio
async def test_resource_wait_rejects_uncontrolled_retry_reason_before_loading_row() -> None:
    with pytest.raises(ValueError, match="不受控"):
        await SystemOutboxRepository().block_for_resource_wait(  # type: ignore[arg-type]
            SimpleNamespace(),
            7,
            reason="HTTP_503_BACKOFF",
            blocked_device_id=9,
        )


def test_shared_retry_wait_transition_releases_dispatch_lease_expiry() -> None:
    outbox = SimpleNamespace(
        status=SystemOutboxStatus.DISPATCHING,
        lease_owner_token="resource-wait-owner",
        lease_expires_at=timezone.now_for_db() + timedelta(minutes=5),
    )

    SystemOutboxRepository._transition_to_retry_wait(outbox)  # type: ignore[arg-type]

    assert outbox.status is SystemOutboxStatus.RETRY_WAIT
    assert outbox.lease_expires_at is None
    assert outbox.lease_owner_token == "resource-wait-owner"


@pytest.mark.asyncio
async def test_external_http_evidence_persistence_failure_fences_current_unexpired_owner_to_unknown() -> None:
    """仅当前有效 lease owner 可在独立事务中保守收口 UNKNOWN。"""

    outbox = SimpleNamespace(
        dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
        status=SystemOutboxStatus.DISPATCHING,
        attempt_count=2,
        next_retry_at=timezone.now_for_db(),
        sent_at=timezone.now_for_db(),
        last_error=None,
        finished_at=None,
        blocked_by_runtime_hold_id=1,
        blocked_by_reconciliation_session_id=2,
        blocked_device_id=3,
        blocked_workline_id=4,
        blocked_reason="DEVICE_BUSY",
        blocked_at=timezone.now_for_db(),
        last_blocked_check_at=timezone.now_for_db(),
        blocked_check_count=1,
        blocked_detail_json={"reason": "busy"},
        lease_owner_token="evidence-owner-91",
        lease_expires_at=timezone.now_for_db() + timedelta(minutes=5),
    )
    db = _CapturingDb([outbox])

    updated = await SystemOutboxRepository().mark_evidence_persistence_unknown(
        db,  # type: ignore[arg-type]
        91,
        "EXTERNAL_HTTP_EVIDENCE_PERSISTENCE_FAILED outcome=ACCEPTED",
        lease_owner_token="evidence-owner-91",
    )

    assert updated is outbox
    assert outbox.status is SystemOutboxStatus.UNKNOWN
    assert outbox.attempt_count == 3
    assert outbox.next_retry_at is None
    assert outbox.sent_at is None
    assert outbox.finished_at is not None
    assert outbox.last_error == "EXTERNAL_HTTP_EVIDENCE_PERSISTENCE_FAILED outcome=ACCEPTED"
    assert outbox.blocked_reason is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "stored_owner", "lease_delta", "existing_error"),
    [
        (SystemOutboxStatus.DISPATCHING, "evidence-owner-91", timedelta(seconds=-1), "EXPIRED_OWNER"),
        (SystemOutboxStatus.UNKNOWN, "evidence-owner-91", None, "STALE_EXTERNAL_HTTP_DISPATCH_LEASE_EXPIRED"),
        (SystemOutboxStatus.DISPATCHING, "replacement-owner-92", timedelta(minutes=5), "NEW_OWNER_ACTIVE"),
    ],
)
async def test_external_http_evidence_persistence_failure_rejects_lost_fence_without_overwriting_evidence(
    status: SystemOutboxStatus,
    stored_owner: str,
    lease_delta: timedelta | None,
    existing_error: str,
) -> None:
    """过期、已收口或异主 lease 都必须保持现有证据不变。"""

    now = timezone.now_for_db()
    outbox = SimpleNamespace(
        dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
        status=status,
        attempt_count=4,
        next_retry_at=None,
        sent_at=None,
        last_error=existing_error,
        finished_at=now if status is SystemOutboxStatus.UNKNOWN else None,
        blocked_by_runtime_hold_id=None,
        blocked_by_reconciliation_session_id=None,
        blocked_device_id=None,
        blocked_workline_id=None,
        blocked_reason=None,
        blocked_at=None,
        last_blocked_check_at=None,
        blocked_check_count=0,
        blocked_detail_json={"fence": existing_error},
        lease_owner_token=stored_owner,
        lease_expires_at=now + lease_delta if lease_delta is not None else None,
    )
    before = vars(outbox).copy()

    updated = await SystemOutboxRepository().mark_evidence_persistence_unknown(
        _CapturingDb([outbox]),  # type: ignore[arg-type]
        91,
        "LATE_RECOVERY_MUST_NOT_OVERWRITE",
        lease_owner_token="evidence-owner-91",
    )

    assert updated is None
    assert vars(outbox) == before


@pytest.mark.asyncio
async def test_stale_external_http_claim_explicitly_clears_retry_schedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outbox = SimpleNamespace(
        dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
        status=SystemOutboxStatus.DISPATCHING,
        attempt_count=0,
        next_retry_at=None,
        lease_owner_token="expired-http-owner",
        lease_expires_at=timezone.now_for_db() - timedelta(seconds=1),
        sent_at=None,
        last_error=None,
        finished_at=None,
    )
    db = _CapturingDb([outbox])
    repository = SystemOutboxRepository()
    # 单独验证 claim fence 自己拥有 retry 终态，不依赖通用 block 清理器的附带行为。
    monkeypatch.setattr(repository, "_clear_block", lambda _outbox: None)

    claimed = await repository.mark_as_dispatching(  # type: ignore[arg-type]
        db,
        91,
        lease_owner_token="replacement-owner",
    )

    assert claimed is None
    assert outbox.status is SystemOutboxStatus.UNKNOWN
    assert outbox.next_retry_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("dispatch_type", "target_type", "target_code"),
    [
        (SystemOutboxDispatchType.DEVICE_COMMAND, SystemOutboxTargetType.DEVICE, "DEVICE-LEASE-1"),
        (SystemOutboxDispatchType.INTERNAL_SIGNAL, SystemOutboxTargetType.INTERNAL_SERVICE, "core"),
    ],
)
async def test_non_http_stale_dispatching_lease_remains_reclaimable(
    db_session: Any,
    dispatch_type: SystemOutboxDispatchType,
    target_type: SystemOutboxTargetType,
    target_code: str,
) -> None:
    now = timezone.now_for_db()
    outbox = SystemOutbox(
        operation_domain="HANDLING",
        dispatch_type=dispatch_type,
        dispatch_key=f"non-http-stale-lease-{dispatch_type.value}",
        target_type=target_type,
        target_code=target_code,
        provider_profile_identity="test.non-http.v1",
        operation_identity=f"test.{dispatch_type.value.lower()}",
        payload_json={},
        status=SystemOutboxStatus.DISPATCHING,
        lease_owner_token="expired-owner",
        lease_expires_at=now - timedelta(seconds=1),
    )
    db_session.add(outbox)
    await db_session.commit()
    await db_session.refresh(outbox)

    claimed = await SystemOutboxRepository().claim_next_in_bucket(
        db_session,
        bucket=DispatchBucketKey("test.non-http.v1", f"test.{dispatch_type.value.lower()}"),
        lease_owner_token="replacement-owner",
        lease_seconds=300,
        retry_budget=3,
        now=now,
    )
    await db_session.refresh(outbox)

    assert claimed.id == outbox.id
    assert outbox.status is SystemOutboxStatus.DISPATCHING
    assert outbox.lease_owner_token == "replacement-owner"
    assert outbox.lease_expires_at == now + timedelta(seconds=300)


@pytest.mark.asyncio
async def test_sandbox_completed_messages_join_runtime_inbox_by_explicit_workline_session(
    db_session: Any,
) -> None:
    """沙箱历史用独立 WorklineSession FK 关联 RuntimeInbox，不读取旧 inbox。"""

    session = WorklineSession(
        session_code="sandbox-runtime-inbox-1",
        workline_id=901,
        plugin_key="test",
        run_mode=RunMode.SIMULATION,
        status=SessionStatus.COMPLETED,
    )
    db_session.add(session)
    await db_session.flush()
    canonical = CanonicalPayload.from_projection({})
    outbox = SystemOutbox(
        session_id=session.id,
        workline_id=901,
        dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
        dispatch_key="sandbox-runtime-inbox-dispatch-1",
        target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
        target_code="TEST",
        provider_profile_identity="test.sandbox.v1",
        operation_identity="test.sandbox.external-http",
        payload_json={},
        canonical_payload_bytes=canonical.body,
        payload_hash=canonical.sha256,
        status=SystemOutboxStatus.SENT,
    )
    inbox = RuntimeInbox(
        workline_session_id=session.id,
        provider_code="TEST",
        event_type="DEVICE_EVENT",
        source_event_id="sandbox-runtime-inbox-event-1",
        kind="DEVICE_EVENT",
        payload_json={"event_type": "SCAN_COMPLETED", "data": {"session_id": session.id}},
        payload_hash="sha256:sandbox-runtime-inbox-event-1",
        payload_schema_version=1,
        status="PROCESSED",
        claim_bucket_key=f"workline-session:{session.id}",
        received_at=1,
    )
    db_session.add_all([outbox, inbox])
    await db_session.commit()

    rows = await SystemOutboxRepository().get_sandbox_completed_messages(
        db_session,
        inbox_query=runtime_inbox_repository,
        limit=10,
    )

    assert rows[0]["session"]["id"] == session.id
    assert rows[0]["session"]["event_type"] == "SCAN_COMPLETED"
    assert rows[0]["session"]["event_payload"]["data"]["session_id"] == session.id
