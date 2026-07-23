"""SystemOutbox 公平桶策略与有界背压。"""

from __future__ import annotations

from datetime import datetime, timedelta
from importlib import import_module, util
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from src.app.device.models.command import DeviceCommand
from src.app.sys.canonical_dispatch import CanonicalPayload
from src.app.sys.models.outbox import (
    SystemOutbox,
    SystemOutboxDispatchType,
    SystemOutboxStatus,
    SystemOutboxTargetType,
)
from src.app.sys.repositories.outbox_repository import SystemOutboxRepository
from src.utils.timezone import timezone
from tests.support.external_http import frozen_external_http_binding


def _concurrency_module() -> Any:
    spec = util.find_spec("src.app.sys.dispatch_concurrency")
    assert spec is not None, "T8e dispatch concurrency module must exist"
    return import_module("src.app.sys.dispatch_concurrency")


class _Repository:
    def __init__(self, states: dict[Any, Any], *, expired_http_lease_count: int = 0) -> None:
        self.states = states
        self.expired_http_lease_count = expired_http_lease_count
        self.locked: set[Any] = set()
        self.claims: list[dict[str, Any]] = []
        self.claim_sequence = 0

    async def fence_expired_external_http_leases(self, _db: object, **_kwargs: Any) -> int:
        return self.expired_http_lease_count

    async def list_dispatch_bucket_keys(self, _db: object, **_kwargs: Any) -> tuple[Any, ...]:
        return tuple(sorted(self.states))

    async def try_lock_dispatch_bucket(self, _db: object, *, bucket: Any) -> bool:
        if bucket in self.locked:
            return False
        self.locked.add(bucket)
        return True

    async def get_dispatch_bucket_state(self, _db: object, *, bucket: Any, **_kwargs: Any) -> Any:
        return self.states[bucket]

    async def claim_next_in_bucket(self, _db: object, **kwargs: Any) -> object | None:
        bucket = kwargs["bucket"]
        state = self.states[bucket]
        if state.backlog_count <= 0:
            return None
        self.claims.append(kwargs)
        self.claim_sequence += 1
        self.states[bucket] = type(state)(
            key=state.key,
            backlog_count=state.backlog_count - 1,
            active_lease_count=state.active_lease_count + 1,
            recent_attempt_count=state.recent_attempt_count + 1,
            oldest_created_at=state.oldest_created_at,
            unknown_count=state.unknown_count,
        )
        return SimpleNamespace(
            id=self.claim_sequence,
            provider_profile_identity=bucket.provider_profile_identity,
            operation_identity=bucket.operation_identity,
            lease_owner_token=kwargs["lease_owner_token"],
            lease_expires_at=kwargs["now"] + timedelta(seconds=kwargs["lease_seconds"]),
        )


def _types() -> tuple[Any, Any, Any, Any]:
    module = _concurrency_module()
    return (
        module.DispatchBucketKey,
        module.DispatchBucketPolicy,
        module.DispatchBucketState,
        module.DispatchPolicyRegistry,
    )


def test_bucket_policy_rejects_unbounded_or_invalid_limits() -> None:
    _key_type, policy_type, _state_type, _registry_type = _types()

    with pytest.raises(ValueError, match="max_concurrency"):
        policy_type(max_concurrency=0)
    with pytest.raises(ValueError, match="rate_limit"):
        policy_type(rate_limit=-1)
    with pytest.raises(ValueError, match="batch_size"):
        policy_type(batch_size=0)
    with pytest.raises(ValueError, match="retry_budget"):
        policy_type(retry_budget=-1)
    with pytest.raises(ValueError, match="lease_seconds"):
        policy_type(lease_seconds=0)


@pytest.mark.asyncio
async def test_rate_limited_bucket_does_not_starve_another_bucket() -> None:
    module = _concurrency_module()
    key_type, policy_type, state_type, registry_type = _types()
    now = datetime(2026, 7, 23, 1, 0, 0)
    limited = key_type("wms.profile-a", "inventory.confirm")
    available = key_type("wms.profile-b", "fulfillment.notify")
    repository = _Repository(
        {
            limited: state_type(limited, 9, 0, 2, now - timedelta(seconds=30), 0),
            available: state_type(available, 3, 0, 0, now - timedelta(seconds=10), 0),
        }
    )
    registry = registry_type(
        default_policy=policy_type(max_concurrency=2, rate_limit=4, batch_size=2),
        policies={limited: policy_type(max_concurrency=2, rate_limit=2, batch_size=2)},
    )
    scheduler = module.FairDispatchScheduler(
        repository=repository, policy_registry=registry, worker_identity="worker-a"
    )

    batch = await scheduler.claim(object(), limit=2, now=now)

    assert [claim.bucket for claim in batch.claims] == [available, available]
    assert batch.metrics.rate_limited_buckets == (limited,)
    assert batch.metrics.backlog_count == 12
    assert batch.metrics.oldest_queue_age_seconds == 30


@pytest.mark.asyncio
async def test_round_robin_cursor_rotates_first_bucket_across_calls() -> None:
    module = _concurrency_module()
    key_type, policy_type, state_type, registry_type = _types()
    now = datetime(2026, 7, 23, 1, 0, 0)
    first = key_type("profile-a", "operation-a")
    second = key_type("profile-b", "operation-b")
    repository = _Repository(
        {
            first: state_type(first, 2, 0, 0, now, 0),
            second: state_type(second, 2, 0, 0, now, 0),
        }
    )
    registry = registry_type(default_policy=policy_type(max_concurrency=2, rate_limit=10, batch_size=2))
    scheduler = module.FairDispatchScheduler(
        repository=repository, policy_registry=registry, worker_identity="worker-b"
    )

    first_batch = await scheduler.claim(object(), limit=1, now=now)
    repository.locked.clear()
    second_batch = await scheduler.claim(object(), limit=1, now=now)

    assert first_batch.claims[0].bucket == first
    assert second_batch.claims[0].bucket == second


@pytest.mark.asyncio
async def test_concurrency_batch_pause_and_retry_budget_bound_prefetch() -> None:
    module = _concurrency_module()
    key_type, policy_type, state_type, registry_type = _types()
    now = datetime(2026, 7, 23, 1, 0, 0)
    bounded = key_type("profile-a", "operation-a")
    paused_bucket = key_type("profile-a", "operation-b")
    paused_profile_bucket = key_type("profile-paused", "operation-c")
    repository = _Repository(
        {
            bounded: state_type(bounded, 20, 1, 0, now, 0),
            paused_bucket: state_type(paused_bucket, 5, 0, 0, now, 0),
            paused_profile_bucket: state_type(paused_profile_bucket, 5, 0, 0, now, 0),
        }
    )
    bounded_policy = policy_type(
        max_concurrency=3,
        rate_limit=100,
        batch_size=7,
        retry_budget=5,
        lease_seconds=17,
    )
    registry = registry_type(
        default_policy=bounded_policy,
        paused_profiles={"profile-paused"},
        paused_buckets={paused_bucket},
    )
    scheduler = module.FairDispatchScheduler(
        repository=repository, policy_registry=registry, worker_identity="worker-c"
    )

    batch = await scheduler.claim(object(), limit=50, now=now)

    assert len(batch.claims) == 2
    assert {claim.bucket for claim in batch.claims} == {bounded}
    assert all(call["retry_budget"] == 5 for call in repository.claims)
    assert all(call["lease_seconds"] == 17 for call in repository.claims)
    assert batch.metrics.paused_buckets == (paused_bucket, paused_profile_bucket)
    assert batch.metrics.active_lease_count == 1
    assert repository.states[bounded].backlog_count == 18


@pytest.mark.asyncio
async def test_bucket_lock_contention_preserves_backlog_and_reports_metric() -> None:
    module = _concurrency_module()
    key_type, policy_type, state_type, registry_type = _types()
    now = datetime(2026, 7, 23, 1, 0, 0)
    bucket = key_type("profile-a", "operation-a")
    repository = _Repository({bucket: state_type(bucket, 4, 0, 0, now, 1)})
    repository.locked.add(bucket)
    scheduler = module.FairDispatchScheduler(
        repository=repository,
        policy_registry=registry_type(default_policy=policy_type()),
        worker_identity="worker-d",
    )

    batch = await scheduler.claim(object(), limit=4, now=now)

    assert batch.claims == ()
    assert batch.metrics.lease_contended_buckets == (bucket,)
    assert batch.metrics.unknown_count == 1
    assert repository.states[bucket].backlog_count == 4


@pytest.mark.asyncio
async def test_claim_metrics_report_expired_http_and_reclaimable_lease_loss() -> None:
    module = _concurrency_module()
    key_type, policy_type, state_type, registry_type = _types()
    now = datetime(2026, 7, 23, 1, 0, 0)
    bucket = key_type("profile-a", "operation-a")
    repository = _Repository(
        {bucket: state_type(bucket, 1, 0, 0, now, 0, expired_lease_count=2)},
        expired_http_lease_count=1,
    )
    scheduler = module.FairDispatchScheduler(
        repository=repository,
        policy_registry=registry_type(default_policy=policy_type()),
        worker_identity="worker-lease-loss",
    )

    batch = await scheduler.claim(object(), limit=1, now=now)

    assert batch.metrics.lease_loss_count == 3


def test_postgresql_claim_statement_uses_skip_locked_and_only_indexed_identity() -> None:
    module = _concurrency_module()
    now = datetime(2026, 7, 23, 1, 0, 0)
    statement = SystemOutboxRepository().build_claim_next_in_bucket_statement(
        bucket=module.DispatchBucketKey("wms.profile-a", "inventory.confirm"),
        lease_owner_token="worker-a:claim-1",
        lease_seconds=30,
        retry_budget=2,
        now=now,
        operation_domains=("WMS_INVENTORY",),
    )

    compiled = str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    normalized = " ".join(compiled.split()).upper()
    assert "AS MATERIALIZED" in normalized
    assert "FOR UPDATE" in normalized
    assert "SKIP LOCKED" in normalized
    assert "PROVIDER_PROFILE_IDENTITY" in normalized
    assert "OPERATION_IDENTITY" in normalized
    assert "LEASE_OWNER_TOKEN" in normalized
    assert "LEASE_EXPIRES_AT" in normalized
    assert "PAYLOAD_JSON ->" not in normalized
    assert "PAYLOAD_JSON[" not in normalized
    assert "SNAPSHOT_JSON ->" not in normalized


def _leased_outbox(*, owner: str, expires_at: datetime) -> SystemOutbox:
    return SystemOutbox(
        provider_profile_identity="ecs.device-command.v1",
        operation_identity="device.command",
        operation_domain="DEVICE",
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        dispatch_key=f"device-command:{owner}",
        target_type=SystemOutboxTargetType.DEVICE,
        target_code="ROBOT-1",
        payload_json={"command_code": owner},
        status=SystemOutboxStatus.DISPATCHING,
        lease_owner_token=owner,
        lease_expires_at=expires_at,
    )


@pytest.mark.asyncio
async def test_outbox_terminal_write_requires_matching_unexpired_owner(db_session: Any) -> None:
    repository = SystemOutboxRepository()
    owner = "worker-a:lease-current"
    outbox = _leased_outbox(owner=owner, expires_at=timezone.now_for_db() + timedelta(minutes=5))
    db_session.add(outbox)
    await db_session.flush()

    assert (
        await repository.mark_as_sent(
            db_session,
            outbox.id,
            lease_owner_token="worker-old:lease-lost",
        )
        is None
    )
    await db_session.refresh(outbox)
    assert outbox.status is SystemOutboxStatus.DISPATCHING

    updated = await repository.mark_as_sent(db_session, outbox.id, lease_owner_token=owner)
    assert updated is outbox
    assert getattr(outbox.status, "value", outbox.status) == SystemOutboxStatus.SENT.value
    assert outbox.lease_expires_at is None


@pytest.mark.asyncio
async def test_expired_owner_is_fenced_even_before_a_new_worker_steals_lease(db_session: Any) -> None:
    repository = SystemOutboxRepository()
    owner = "worker-a:lease-expired"
    outbox = _leased_outbox(owner=owner, expires_at=timezone.now_for_db() - timedelta(seconds=1))
    db_session.add(outbox)
    await db_session.flush()

    assert await repository.mark_as_sent(db_session, outbox.id, lease_owner_token=owner) is None
    await db_session.refresh(outbox)
    assert outbox.status is SystemOutboxStatus.DISPATCHING


@pytest.mark.asyncio
async def test_expired_external_http_lease_is_quarantined_without_reclaim(db_session: Any) -> None:
    repository = SystemOutboxRepository()
    canonical = CanonicalPayload.from_projection({"request_id": "http-expired"})
    frozen_binding = frozen_external_http_binding(
        target_code="WMS_CONFIRM_INBOUND",
        provider_profile_identity="wms.profile-a",
        operation_identity="inventory.confirm",
    )
    outbox = SystemOutbox(
        **frozen_binding.as_persisted_fields(),
        operation_domain="WMS_INVENTORY",
        dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
        dispatch_key="http-expired",
        target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
        payload_json={"request_id": "http-expired"},
        canonical_payload_bytes=canonical.body,
        payload_hash=canonical.sha256,
        status=SystemOutboxStatus.DISPATCHING,
        lease_owner_token="worker-old:http-expired",
        lease_expires_at=timezone.now_for_db() - timedelta(seconds=1),
    )
    db_session.add(outbox)
    await db_session.flush()

    fenced = await repository.fence_expired_external_http_leases(
        db_session,
        now=timezone.now_for_db(),
        operation_domains=("WMS_INVENTORY",),
    )

    assert len(fenced) == 1
    await db_session.refresh(outbox)
    assert getattr(outbox.status, "value", outbox.status) == SystemOutboxStatus.UNKNOWN.value
    assert outbox.lease_owner_token == "worker-old:http-expired"
    assert outbox.lease_expires_at is None
    assert outbox.next_retry_at is None
    assert "STALE_EXTERNAL_HTTP_DISPATCH_LEASE_EXPIRED" in str(outbox.last_error)
