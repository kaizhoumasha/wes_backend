"""RuntimeInbox 失败状态机真实 service/repository 合同。"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from src.app.runtime.orchestration.repositories.runtime_inbox_repository import RuntimeInboxRepository
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.services.runtime_inbox import RuntimeInboxService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def _processing_inbox(
    db: AsyncSession,
    *,
    source_event_id: str,
    attempt_count: int,
    max_retries: int,
    token: str = "lease-1",  # noqa: S107  (测试 lease token，不是密码)
) -> RuntimeInbox:
    inbox = RuntimeInbox(
        kind="DEVICE_EVENT",
        provider_code="TEST",
        event_type="DEVICE_EVENT",
        source_event_id=source_event_id,
        payload_hash=f"sha256:{source_event_id}",
        payload_json={},
        payload_schema_version=1,
        status="PROCESSING",
        processor_token=token,
        claim_bucket_key=f"bucket:{source_event_id}",
        received_at=1,
        attempt_count=attempt_count,
        max_retries=max_retries,
    )
    db.add(inbox)
    await db.commit()
    await db.refresh(inbox)
    return inbox


@pytest.mark.asyncio
async def test_nonretryable_failure_becomes_dead_letter(db_session: AsyncSession) -> None:
    inbox = await _processing_inbox(db_session, source_event_id="nonretry", attempt_count=1, max_retries=3)
    service = RuntimeInboxService(repository=RuntimeInboxRepository())

    updated = await service.mark_failed(
        db_session,
        inbox_id=inbox.id,  # type: ignore[arg-type]
        lease_token="lease-1",
        error_message="invalid payload",
        retryable=False,
    )
    await db_session.commit()
    await db_session.refresh(inbox)

    assert updated is True
    assert inbox.status == "DEAD_LETTER"
    assert inbox.next_retry_at is None
    assert inbox.last_error_message == "invalid payload"
    assert inbox.failed_at is not None
    assert inbox.processor_token is None
    assert inbox.lease_until is None


@pytest.mark.asyncio
async def test_retryable_failure_schedules_retry_before_exhaustion(db_session: AsyncSession) -> None:
    inbox = await _processing_inbox(db_session, source_event_id="retryable", attempt_count=1, max_retries=3)
    service = RuntimeInboxService(repository=RuntimeInboxRepository())

    updated = await service.mark_failed(
        db_session,
        inbox_id=inbox.id,  # type: ignore[arg-type]
        lease_token="lease-1",
        error_message="temporary failure",
        retryable=True,
    )
    await db_session.commit()
    await db_session.refresh(inbox)

    assert updated is True
    assert inbox.status == "FAILED"
    assert inbox.attempt_count == 1
    assert inbox.next_retry_at is not None
    assert inbox.processor_token is None
    assert inbox.lease_until is None


@pytest.mark.asyncio
async def test_retryable_failure_becomes_dead_letter_when_exhausted(db_session: AsyncSession) -> None:
    inbox = await _processing_inbox(db_session, source_event_id="exhausted", attempt_count=3, max_retries=3)
    service = RuntimeInboxService(repository=RuntimeInboxRepository())

    updated = await service.mark_failed(
        db_session,
        inbox_id=inbox.id,  # type: ignore[arg-type]
        lease_token="lease-1",
        error_message="still failing",
        retryable=True,
    )
    await db_session.commit()
    await db_session.refresh(inbox)

    assert updated is True
    assert inbox.status == "DEAD_LETTER"
    assert inbox.next_retry_at is None
    assert inbox.processor_token is None
    assert inbox.lease_until is None


@pytest.mark.asyncio
async def test_zero_retry_budget_is_already_exhausted(db_session: AsyncSession) -> None:
    inbox = await _processing_inbox(db_session, source_event_id="zero-budget", attempt_count=0, max_retries=0)
    service = RuntimeInboxService(repository=RuntimeInboxRepository())

    updated = await service.mark_failed(
        db_session,
        inbox_id=inbox.id,  # type: ignore[arg-type]
        lease_token="lease-1",
        error_message="no retry budget",
        retryable=True,
    )
    await db_session.commit()
    await db_session.refresh(inbox)

    assert updated is True
    assert inbox.status == "DEAD_LETTER"


@pytest.mark.asyncio
async def test_resource_wait_does_not_consume_attempt_across_repeated_claims(db_session: AsyncSession) -> None:
    inbox = RuntimeInbox(
        kind="DEVICE_EVENT",
        provider_code="TEST",
        event_type="DEVICE_EVENT",
        source_event_id="resource-wait",
        payload_hash="sha256:resource-wait",
        payload_json={},
        payload_schema_version=1,
        status="RECEIVED",
        claim_bucket_key="bucket:resource-wait",
        received_at=1,
        attempt_count=0,
        max_retries=2,
    )
    db_session.add(inbox)
    await db_session.commit()
    await db_session.refresh(inbox)
    service = RuntimeInboxService(repository=RuntimeInboxRepository())

    for attempt in range(4):
        token = f"resource-worker-{attempt}"
        claims = await service.claim_for_processing(
            db_session,
            limit=1,
            processor_token=token,
            stale_after_seconds=30,
        )
        assert len(claims) == 1
        await db_session.commit()

        updated = await service.mark_failed(
            db_session,
            inbox_id=inbox.id,  # type: ignore[arg-type]
            lease_token=token,
            error_message="RESOURCE_WAIT",
            retryable=True,
            consume_attempt=False,
        )
        await db_session.commit()
        await db_session.refresh(inbox)

        assert updated is True
        assert inbox.status == "FAILED"
        assert inbox.attempt_count == 0
        assert inbox.next_retry_at is not None
        assert inbox.processor_token is None
        assert inbox.lease_until is None
        inbox.next_retry_at = 0
        await db_session.commit()


@pytest.mark.asyncio
async def test_stale_owner_cannot_overwrite_failure_state(db_session: AsyncSession) -> None:
    inbox = await _processing_inbox(db_session, source_event_id="fenced", attempt_count=1, max_retries=3)
    service = RuntimeInboxService(repository=RuntimeInboxRepository())

    updated = await service.mark_failed(
        db_session,
        inbox_id=inbox.id,  # type: ignore[arg-type]
        lease_token="stale-owner",
        error_message="must not win",
        retryable=False,
    )
    await db_session.commit()
    await db_session.refresh(inbox)

    assert updated is False
    assert inbox.status == "PROCESSING"
    assert inbox.last_error_message is None


@pytest.mark.asyncio
async def test_atomic_recovery_respects_limit_active_lease_and_retry_budget(db_session: AsyncSession) -> None:
    rows = [
        await _processing_inbox(
            db_session,
            source_event_id="stale-retryable",
            attempt_count=1,
            max_retries=3,
            token="stale-1",
        ),
        await _processing_inbox(
            db_session,
            source_event_id="stale-exhausted",
            attempt_count=3,
            max_retries=3,
            token="stale-2",
        ),
        await _processing_inbox(
            db_session,
            source_event_id="active-lease",
            attempt_count=1,
            max_retries=3,
            token="active",
        ),
    ]
    rows[0].received_at = 1
    rows[0].lease_until = 0
    rows[1].received_at = 2
    rows[1].lease_until = 0
    rows[2].received_at = 3
    rows[2].lease_until = 9_999_999_999_999
    await db_session.commit()
    service = RuntimeInboxService(repository=RuntimeInboxRepository())

    recovered = await service.recover_stale_leases(db_session, stale_after_seconds=30, limit=2)
    await db_session.commit()
    for row in rows:
        await db_session.refresh(row)

    assert recovered == 2
    assert rows[0].status == "RECEIVED"
    assert rows[0].attempt_count == 1
    assert rows[0].processor_token is None and rows[0].lease_until is None
    assert rows[1].status == "DEAD_LETTER"
    assert rows[1].attempt_count == 3
    assert rows[1].processor_token is None and rows[1].lease_until is None
    assert rows[1].failed_at is not None
    assert rows[1].last_error_code == "INBOX_RETRY_EXHAUSTED"
    assert rows[1].last_error_message == "PROCESSING_LEASE_EXPIRED_RETRY_EXHAUSTED"
    assert rows[2].status == "PROCESSING"
    assert rows[2].processor_token == "active"


@pytest.mark.asyncio
async def test_last_budget_crash_recovers_to_dead_letter_and_unblocks_bucket(db_session: AsyncSession) -> None:
    first = RuntimeInbox(
        kind="DEVICE_EVENT",
        provider_code="TEST",
        event_type="DEVICE_EVENT",
        source_event_id="last-budget",
        payload_hash="sha256:last-budget",
        payload_json={},
        payload_schema_version=1,
        status="RECEIVED",
        claim_bucket_key="bucket:last-budget",
        received_at=1,
        attempt_count=1,
        max_retries=2,
    )
    following = RuntimeInbox(
        kind="DEVICE_EVENT",
        provider_code="TEST",
        event_type="DEVICE_EVENT",
        source_event_id="after-last-budget",
        payload_hash="sha256:after-last-budget",
        payload_json={},
        payload_schema_version=1,
        status="RECEIVED",
        claim_bucket_key="bucket:last-budget",
        received_at=2,
        attempt_count=0,
        max_retries=2,
    )
    db_session.add_all([first, following])
    await db_session.commit()
    service = RuntimeInboxService(repository=RuntimeInboxRepository())

    claims = await service.claim_for_processing(
        db_session,
        limit=1,
        processor_token="last-worker",
        stale_after_seconds=30,
    )
    await db_session.commit()
    assert [claim["source_event_id"] for claim in claims] == ["last-budget"]
    await db_session.refresh(first)
    assert first.status == "PROCESSING" and first.attempt_count == 2

    first.lease_until = 0
    await db_session.commit()
    recovered = await service.recover_stale_leases(db_session, stale_after_seconds=30, limit=1)
    await db_session.commit()
    await db_session.refresh(first)
    assert recovered == 1
    assert first.status == "DEAD_LETTER"

    next_claims = await service.claim_for_processing(
        db_session,
        limit=1,
        processor_token="next-worker",
        stale_after_seconds=30,
    )
    assert [claim["source_event_id"] for claim in next_claims] == ["after-last-budget"]
