"""RuntimeInbox 失败状态机真实 service/repository 合同。"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from src.app.runtime.orchestration.consumers.runtime_inbox_service import RuntimeInboxService
from src.app.runtime.orchestration.repositories.runtime_inbox_claim_repository import RuntimeInboxClaimRepository
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox

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
        provider_code="TEST",
        event_type="DEVICE_EVENT",
        source_event_id=source_event_id,
        payload_json={},
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
    service = RuntimeInboxService(claim_repo=RuntimeInboxClaimRepository())

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


@pytest.mark.asyncio
async def test_retryable_failure_schedules_retry_before_exhaustion(db_session: AsyncSession) -> None:
    inbox = await _processing_inbox(db_session, source_event_id="retryable", attempt_count=1, max_retries=3)
    service = RuntimeInboxService(claim_repo=RuntimeInboxClaimRepository())

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


@pytest.mark.asyncio
async def test_retryable_failure_becomes_dead_letter_when_exhausted(db_session: AsyncSession) -> None:
    inbox = await _processing_inbox(db_session, source_event_id="exhausted", attempt_count=3, max_retries=3)
    service = RuntimeInboxService(claim_repo=RuntimeInboxClaimRepository())

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


@pytest.mark.asyncio
async def test_zero_retry_budget_is_already_exhausted(db_session: AsyncSession) -> None:
    inbox = await _processing_inbox(db_session, source_event_id="zero-budget", attempt_count=0, max_retries=0)
    service = RuntimeInboxService(claim_repo=RuntimeInboxClaimRepository())

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
        provider_code="TEST",
        event_type="DEVICE_EVENT",
        source_event_id="resource-wait",
        payload_json={},
        status="RECEIVED",
        claim_bucket_key="bucket:resource-wait",
        received_at=1,
        attempt_count=0,
        max_retries=2,
    )
    db_session.add(inbox)
    await db_session.commit()
    await db_session.refresh(inbox)
    service = RuntimeInboxService(claim_repo=RuntimeInboxClaimRepository())

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
        inbox.next_retry_at = 0
        await db_session.commit()


@pytest.mark.asyncio
async def test_stale_owner_cannot_overwrite_failure_state(db_session: AsyncSession) -> None:
    inbox = await _processing_inbox(db_session, source_event_id="fenced", attempt_count=1, max_retries=3)
    service = RuntimeInboxService(claim_repo=RuntimeInboxClaimRepository())

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
