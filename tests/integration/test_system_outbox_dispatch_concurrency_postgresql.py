"""SystemOutbox SKIP LOCKED、lease/fencing 与公平桶 PostgreSQL 合同。"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from sqlalchemy import delete

from src.app.effect_ledger_status import DispatchAttemptStatus
from src.app.runtime.orchestration.models.dispatch_attempt import WorklineDispatchAttempt
from src.app.runtime.orchestration.services.inbox.dispatch_attempt_service import (
    OutboxLeaseLost,
    WorklineDispatchAttemptService,
)
from src.app.sys.dispatch_concurrency import (
    DispatchBucketKey,
    DispatchBucketPolicy,
    DispatchPolicyRegistry,
    FairDispatchScheduler,
)
from src.app.sys.models import (
    SystemOutbox,
    SystemOutboxDispatchType,
    SystemOutboxStatus,
    SystemOutboxTargetType,
)
from src.app.sys.repositories import SystemOutboxRepository
from src.utils.timezone import timezone
from src.utils.value_normalization import enum_value

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


def _outbox(
    *, dispatch_key: str, bucket: DispatchBucketKey, status: SystemOutboxStatus = SystemOutboxStatus.NEW
) -> SystemOutbox:
    return SystemOutbox(
        operation_domain="T8E_INTEGRATION",
        dispatch_type=SystemOutboxDispatchType.INTERNAL_SIGNAL,
        dispatch_key=dispatch_key,
        target_type=SystemOutboxTargetType.INTERNAL_SERVICE,
        target_code="t8e-integration",
        provider_profile_identity=bucket.provider_profile_identity,
        operation_identity=bucket.operation_identity,
        payload_json={},
        status=status,
    )


async def _cleanup(session_factory: async_sessionmaker[AsyncSession], prefix: str) -> None:
    async with session_factory() as db:
        await db.execute(delete(WorklineDispatchAttempt).where(WorklineDispatchAttempt.dispatch_key.like(f"{prefix}%")))
        await db.execute(delete(SystemOutbox).where(SystemOutbox.dispatch_key.like(f"{prefix}%")))
        await db.commit()


@pytest.mark.asyncio
async def test_postgresql_skip_locked_claims_distinct_rows_without_waiting(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    prefix = f"t8e-skip-locked:{uuid4().hex}:"
    bucket = DispatchBucketKey(f"profile.{uuid4().hex}", "operation.skip-locked")
    repository = SystemOutboxRepository()
    async with integration_session_factory() as setup:
        setup.add_all([_outbox(dispatch_key=f"{prefix}{index}", bucket=bucket) for index in range(2)])
        await setup.commit()

    first = integration_session_factory()
    second = integration_session_factory()
    try:
        now = timezone.now_for_db()
        first_claim = await repository.claim_next_in_bucket(
            first,
            bucket=bucket,
            lease_owner_token="worker-a",
            lease_seconds=60,
            retry_budget=3,
            now=now,
        )
        second_claim = await repository.claim_next_in_bucket(
            second,
            bucket=bucket,
            lease_owner_token="worker-b",
            lease_seconds=60,
            retry_budget=3,
            now=now,
        )

        assert first_claim is not None
        assert second_claim is not None
        assert first_claim.id != second_claim.id
        assert {first_claim.lease_owner_token, second_claim.lease_owner_token} == {"worker-a", "worker-b"}
    finally:
        await first.rollback()
        await second.rollback()
        await first.close()
        await second.close()
        await _cleanup(integration_session_factory, prefix)


@pytest.mark.asyncio
async def test_postgresql_lease_steal_fences_previous_owner_terminal_write(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    prefix = f"t8e-fencing:{uuid4().hex}:"
    bucket = DispatchBucketKey(f"profile.{uuid4().hex}", "operation.fencing")
    repository = SystemOutboxRepository()
    now = timezone.now_for_db()
    async with integration_session_factory() as setup:
        outbox = _outbox(dispatch_key=f"{prefix}1", bucket=bucket, status=SystemOutboxStatus.DISPATCHING)
        outbox.lease_owner_token = "expired-owner"
        outbox.lease_expires_at = now - timedelta(seconds=1)
        setup.add(outbox)
        await setup.commit()
        outbox_id = outbox.id

    try:
        async with integration_session_factory() as claimant:
            claimed = await repository.claim_next_in_bucket(
                claimant,
                bucket=bucket,
                lease_owner_token="replacement-owner",
                lease_seconds=60,
                retry_budget=3,
                now=now,
            )
            assert claimed is not None
            await claimant.commit()

        async with integration_session_factory() as stale_worker:
            assert (
                await repository.mark_as_sent(
                    stale_worker,
                    outbox_id,
                    lease_owner_token="expired-owner",
                )
                is None
            )

        async with integration_session_factory() as current_worker:
            updated = await repository.mark_as_sent(
                current_worker,
                outbox_id,
                lease_owner_token="replacement-owner",
            )
            assert updated is not None
            assert enum_value(updated.status) == SystemOutboxStatus.SENT.value
            await current_worker.commit()
    finally:
        await _cleanup(integration_session_factory, prefix)


@pytest.mark.asyncio
async def test_postgresql_lease_steal_fences_previous_attempt_owner(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    prefix = f"t8e-attempt-fencing:{uuid4().hex}:"
    bucket = DispatchBucketKey(f"profile.{uuid4().hex}", "operation.attempt-fencing")
    repository = SystemOutboxRepository()
    attempt_service = WorklineDispatchAttemptService()
    now = timezone.now_for_db()
    expired_owner = f"expired-owner:{uuid4().hex}"
    replacement_owner = f"replacement-owner:{uuid4().hex}"
    async with integration_session_factory() as setup:
        outbox = _outbox(dispatch_key=f"{prefix}1", bucket=bucket, status=SystemOutboxStatus.DISPATCHING)
        outbox.lease_owner_token = expired_owner
        outbox.lease_expires_at = now - timedelta(seconds=1)
        setup.add(outbox)
        await setup.flush()
        setup.add(
            WorklineDispatchAttempt(
                outbox_id=outbox.id,
                dispatch_key=outbox.dispatch_key,
                attempt_no=1,
                lease_token=expired_owner,
                lease_expires_at=outbox.lease_expires_at,
                status=DispatchAttemptStatus.DISPATCHING,
                started_at=now - timedelta(minutes=1),
            )
        )
        await setup.commit()

    try:
        async with integration_session_factory() as claimant:
            claimed = await repository.claim_next_in_bucket(
                claimant,
                bucket=bucket,
                lease_owner_token=replacement_owner,
                lease_seconds=60,
                retry_budget=3,
                now=now,
            )
            assert claimed is not None
            replacement_attempt = await attempt_service.create_attempt(claimant, outbox=claimed, auto_commit=False)
            await claimant.commit()

            sent = await repository.mark_as_sent(
                claimant,
                claimed.id,
                lease_owner_token=replacement_owner,
            )
            assert sent is not None
            finalized = await attempt_service.finalize_attempt_record(
                claimant,
                attempt=replacement_attempt,
                lease_owner_token=replacement_owner,
                success=True,
                auto_commit=False,
            )
            assert enum_value(finalized.status) == DispatchAttemptStatus.SENT.value
            await claimant.commit()

        async with integration_session_factory() as stale_worker:
            with pytest.raises(OutboxLeaseLost, match="OUTBOX_LEASE_LOST"):
                await attempt_service.finalize_attempt(
                    stale_worker,
                    lease_token=expired_owner,
                    success=True,
                    auto_commit=False,
                )
    finally:
        await _cleanup(integration_session_factory, prefix)


@pytest.mark.asyncio
async def test_postgresql_fair_scheduler_claims_one_from_each_bucket_before_second_item(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    prefix = f"t8e-fair:{uuid4().hex}:"
    first_bucket = DispatchBucketKey(f"profile.{uuid4().hex}", "operation.slow")
    second_bucket = DispatchBucketKey(f"profile.{uuid4().hex}", "operation.fast")
    policy = DispatchBucketPolicy(max_concurrency=4, rate_limit=20, batch_size=4, lease_seconds=60)
    scheduler = FairDispatchScheduler(
        repository=SystemOutboxRepository(),
        policy_registry=DispatchPolicyRegistry(default_policy=policy),
        worker_identity="fair-worker",
    )
    async with integration_session_factory() as setup:
        setup.add_all(
            [_outbox(dispatch_key=f"{prefix}slow-{index}", bucket=first_bucket) for index in range(3)]
            + [_outbox(dispatch_key=f"{prefix}fast-1", bucket=second_bucket)]
        )
        await setup.commit()

    try:
        async with integration_session_factory() as db:
            batch = await scheduler.claim(
                db,
                limit=2,
                operation_domains=("T8E_INTEGRATION",),
            )
            assert {claim.bucket for claim in batch.claims} == {first_bucket, second_bucket}
            assert batch.metrics.backlog_count == 4
            await db.rollback()
    finally:
        await _cleanup(integration_session_factory, prefix)


@pytest.mark.asyncio
async def test_postgresql_bucket_concurrency_limit_preserves_durable_backlog(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    prefix = f"t8e-backpressure:{uuid4().hex}:"
    bucket = DispatchBucketKey(f"profile.{uuid4().hex}", "operation.backpressure")
    policy = DispatchBucketPolicy(max_concurrency=1, rate_limit=20, batch_size=4, lease_seconds=60)
    scheduler = FairDispatchScheduler(
        repository=SystemOutboxRepository(),
        policy_registry=DispatchPolicyRegistry(default_policy=policy),
        worker_identity="backpressure-worker",
    )
    now = timezone.now_for_db()
    async with integration_session_factory() as setup:
        active = _outbox(dispatch_key=f"{prefix}active", bucket=bucket, status=SystemOutboxStatus.DISPATCHING)
        active.lease_owner_token = "active-owner"
        active.lease_expires_at = now + timedelta(minutes=5)
        setup.add_all([active, _outbox(dispatch_key=f"{prefix}backlog", bucket=bucket)])
        await setup.commit()

    try:
        async with integration_session_factory() as db:
            batch = await scheduler.claim(
                db,
                limit=10,
                now=now,
                operation_domains=("T8E_INTEGRATION",),
            )
            assert batch.claims == ()
            assert batch.metrics.backlog_count == 1
            assert batch.metrics.active_lease_count == 1
            await db.rollback()
    finally:
        await _cleanup(integration_session_factory, prefix)


@pytest.mark.asyncio
async def test_postgresql_bucket_concurrency_budget_is_global_across_dispatcher_domains(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    prefix = f"t8e-cross-domain-budget:{uuid4().hex}:"
    bucket = DispatchBucketKey(f"profile.{uuid4().hex}", "operation.cross-domain")
    policy = DispatchBucketPolicy(max_concurrency=1, rate_limit=20, batch_size=4, lease_seconds=60)
    scheduler = FairDispatchScheduler(
        repository=SystemOutboxRepository(),
        policy_registry=DispatchPolicyRegistry(default_policy=policy),
        worker_identity="cross-domain-worker",
    )
    now = timezone.now_for_db()
    async with integration_session_factory() as setup:
        active = _outbox(dispatch_key=f"{prefix}active", bucket=bucket, status=SystemOutboxStatus.DISPATCHING)
        active.operation_domain = "OTHER_DISPATCHER_DOMAIN"
        active.lease_owner_token = "other-dispatcher-owner"
        active.lease_expires_at = now + timedelta(minutes=5)
        setup.add_all([active, _outbox(dispatch_key=f"{prefix}backlog", bucket=bucket)])
        await setup.commit()

    try:
        async with integration_session_factory() as db:
            batch = await scheduler.claim(
                db,
                limit=10,
                now=now,
                operation_domains=("T8E_INTEGRATION",),
            )
            assert batch.claims == ()
            assert batch.metrics.backlog_count == 1
            assert batch.metrics.active_lease_count == 1
            await db.rollback()
    finally:
        await _cleanup(integration_session_factory, prefix)
