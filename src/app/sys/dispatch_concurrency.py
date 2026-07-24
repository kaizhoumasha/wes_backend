"""SystemOutbox Provider profile + operation 公平桶调度合同。"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from src.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping
    from datetime import datetime


@dataclass(frozen=True, order=True, slots=True)
class DispatchBucketKey:
    """完全由显式索引列组成的低基数调度桶 identity。"""

    provider_profile_identity: str
    operation_identity: str

    def __post_init__(self) -> None:
        for field_name, value in (
            ("provider_profile_identity", self.provider_profile_identity),
            ("operation_identity", self.operation_identity),
        ):
            if not isinstance(value, str) or not value.strip() or len(value) > 240:
                raise ValueError(f"{field_name} must be a non-empty string up to 240 characters")


@dataclass(frozen=True, slots=True)
class DispatchBucketPolicy:
    """单个 Provider profile + operation 的有界派发策略。"""

    max_concurrency: int = 4
    rate_limit: int = 60
    rate_window_seconds: int = 60
    batch_size: int = 10
    retry_budget: int = 3
    lease_seconds: int = 300

    def __post_init__(self) -> None:
        positive_fields = (
            ("max_concurrency", self.max_concurrency),
            ("rate_window_seconds", self.rate_window_seconds),
            ("batch_size", self.batch_size),
            ("lease_seconds", self.lease_seconds),
        )
        for field_name, value in positive_fields:
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{field_name} must be a positive integer")
        for field_name, value in (("rate_limit", self.rate_limit), ("retry_budget", self.retry_budget)):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class DispatchBucketState:
    """Repository 从索引列和 attempt ledger 汇总的桶压力。"""

    key: DispatchBucketKey
    backlog_count: int
    active_lease_count: int
    recent_attempt_count: int
    oldest_created_at: datetime | None
    unknown_count: int
    expired_lease_count: int = 0


@dataclass(frozen=True, slots=True)
class DispatchLeaseClaim:
    """dispatcher 唯一可消费的带 owner lease 领取结果。"""

    outbox: Any
    bucket: DispatchBucketKey
    lease_owner_token: str
    lease_expires_at: datetime
    policy: DispatchBucketPolicy


@dataclass(frozen=True, slots=True)
class DispatchClaimMetrics:
    """一次领取决策的有界背压与可观测摘要。"""

    backlog_count: int
    active_lease_count: int
    unknown_count: int
    oldest_queue_age_seconds: int | None
    rate_limited_buckets: tuple[DispatchBucketKey, ...]
    paused_buckets: tuple[DispatchBucketKey, ...]
    lease_contended_buckets: tuple[DispatchBucketKey, ...]
    lease_loss_count: int = 0


@dataclass(frozen=True, slots=True)
class DispatchClaimBatch:
    claims: tuple[DispatchLeaseClaim, ...]
    metrics: DispatchClaimMetrics


@dataclass(frozen=True, slots=True)
class DispatchPolicyRegistry:
    """显式策略与紧急暂停面；bucket policy 不从 outbox JSON 读取。"""

    default_policy: DispatchBucketPolicy = field(default_factory=DispatchBucketPolicy)
    policies: Mapping[DispatchBucketKey, DispatchBucketPolicy] = field(default_factory=dict)
    paused_profiles: Collection[str] = field(default_factory=frozenset)
    paused_buckets: Collection[DispatchBucketKey] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        object.__setattr__(self, "policies", MappingProxyType(dict(self.policies)))
        object.__setattr__(self, "paused_profiles", frozenset(self.paused_profiles))
        object.__setattr__(self, "paused_buckets", frozenset(self.paused_buckets))

    def policy_for(self, bucket: DispatchBucketKey) -> DispatchBucketPolicy:
        return self.policies.get(bucket, self.default_policy)

    def is_paused(self, bucket: DispatchBucketKey) -> bool:
        return bucket in self.paused_buckets or bucket.provider_profile_identity in self.paused_profiles


class FairDispatchScheduler:
    """按活跃桶轮转，并把跨 worker 预算串行化留给 Repository。"""

    def __init__(
        self,
        *,
        repository: Any,
        policy_registry: DispatchPolicyRegistry,
        worker_identity: str,
        expired_http_lease_loss_service: Any | None = None,
        exhausted_non_http_lease_service: Any | None = None,
    ) -> None:
        if not isinstance(worker_identity, str) or not worker_identity.strip():
            raise ValueError("worker_identity must be a non-empty string")
        self._repository = repository
        self._policy_registry = policy_registry
        self._worker_identity = worker_identity.strip()
        self._expired_http_lease_loss_service = expired_http_lease_loss_service
        self._exhausted_non_http_lease_service = exhausted_non_http_lease_service
        self._cursor = 0

    def _resolve_expired_http_lease_loss_service(self) -> Any:
        if self._expired_http_lease_loss_service is None:
            from src.app.runtime.orchestration.services.inbox.external_http_lease_loss_service import (
                external_http_lease_loss_service,
            )

            self._expired_http_lease_loss_service = external_http_lease_loss_service
        return self._expired_http_lease_loss_service

    def _resolve_exhausted_non_http_lease_service(self) -> Any:
        if self._exhausted_non_http_lease_service is None:
            from src.app.runtime.orchestration.services.inbox.non_http_lease_exhaustion_service import (
                non_http_lease_exhaustion_service,
            )

            self._exhausted_non_http_lease_service = non_http_lease_exhaustion_service
        return self._exhausted_non_http_lease_service

    async def claim(
        self,
        db: Any,
        *,
        limit: int,
        now: datetime | None = None,
        operation_domains: tuple[str, ...] | None = None,
        exclude_operation_domains: tuple[str, ...] | None = None,
    ) -> DispatchClaimBatch:
        effective_now = now or timezone.now_for_db()
        if limit <= 0:
            return DispatchClaimBatch(claims=(), metrics=self._empty_metrics())

        query_scope = {
            "now": effective_now,
            "operation_domains": operation_domains,
            "exclude_operation_domains": exclude_operation_domains,
        }
        recovered_lease_count = int(
            await self._resolve_expired_http_lease_loss_service().fence_expired_leases(
                db,
                outbox_repository=self._repository,
                **query_scope,
            )
            or 0
        )
        keys = tuple(
            await self._repository.list_dispatch_bucket_keys(
                db,
                **query_scope,
            )
        )
        if not keys:
            return DispatchClaimBatch(
                claims=(),
                metrics=replace(self._empty_metrics(), lease_loss_count=recovered_lease_count),
            )

        ordered = keys[self._cursor :] + keys[: self._cursor]
        self._cursor = (self._cursor + 1) % len(keys)
        initial_states = {
            bucket: await self._repository.get_dispatch_bucket_state(db, bucket=bucket, **query_scope)
            for bucket in ordered
        }
        metrics = self._metrics_from_states(effective_now, tuple(initial_states.values()))
        paused: list[DispatchBucketKey] = []
        rate_limited: list[DispatchBucketKey] = []
        contended: list[DispatchBucketKey] = []
        available: list[tuple[DispatchBucketKey, DispatchBucketPolicy, int]] = []

        for bucket in ordered:
            if self._policy_registry.is_paused(bucket):
                paused.append(bucket)
                continue
            if not await self._repository.try_lock_dispatch_bucket(db, bucket=bucket):
                contended.append(bucket)
                continue
            policy = self._policy_registry.policy_for(bucket)
            recovered_lease_count += int(
                await self._resolve_exhausted_non_http_lease_service().fence_exhausted_leases(
                    db,
                    repository=self._repository,
                    bucket=bucket,
                    retry_budget=policy.retry_budget,
                    **query_scope,
                )
                or 0
            )
            state = await self._repository.get_dispatch_bucket_state(
                db,
                bucket=bucket,
                rate_window_seconds=policy.rate_window_seconds,
                **query_scope,
            )
            concurrency_budget = max(0, policy.max_concurrency - state.active_lease_count)
            rate_budget = max(0, policy.rate_limit - state.recent_attempt_count)
            quota = min(policy.batch_size, concurrency_budget, rate_budget, state.backlog_count)
            if state.backlog_count > 0 and rate_budget == 0:
                rate_limited.append(bucket)
            if quota > 0:
                available.append((bucket, policy, quota))

        claims: list[DispatchLeaseClaim] = []
        claimed_per_bucket: dict[DispatchBucketKey, int] = {bucket: 0 for bucket, _policy, _quota in available}
        while len(claims) < limit:
            progressed = False
            for bucket, policy, quota in available:
                if len(claims) >= limit:
                    break
                if claimed_per_bucket[bucket] >= quota:
                    continue
                owner_token = f"outbox-lease:{self._worker_identity}:{uuid4().hex}"
                outbox = await self._repository.claim_next_in_bucket(
                    db,
                    bucket=bucket,
                    lease_owner_token=owner_token,
                    lease_seconds=policy.lease_seconds,
                    retry_budget=policy.retry_budget,
                    **query_scope,
                )
                claimed_per_bucket[bucket] += 1
                if outbox is None:
                    continue
                claims.append(
                    DispatchLeaseClaim(
                        outbox=outbox,
                        bucket=bucket,
                        lease_owner_token=owner_token,
                        lease_expires_at=outbox.lease_expires_at,
                        policy=policy,
                    )
                )
                progressed = True
            if not progressed:
                break

        return DispatchClaimBatch(
            claims=tuple(claims),
            metrics=DispatchClaimMetrics(
                backlog_count=metrics.backlog_count,
                active_lease_count=metrics.active_lease_count,
                unknown_count=metrics.unknown_count,
                oldest_queue_age_seconds=metrics.oldest_queue_age_seconds,
                rate_limited_buckets=tuple(rate_limited),
                paused_buckets=tuple(paused),
                lease_contended_buckets=tuple(contended),
                lease_loss_count=metrics.lease_loss_count + recovered_lease_count,
            ),
        )

    @staticmethod
    def _empty_metrics() -> DispatchClaimMetrics:
        return DispatchClaimMetrics(
            backlog_count=0,
            active_lease_count=0,
            unknown_count=0,
            oldest_queue_age_seconds=None,
            rate_limited_buckets=(),
            paused_buckets=(),
            lease_contended_buckets=(),
            lease_loss_count=0,
        )

    @classmethod
    def _metrics_from_states(
        cls,
        now: datetime,
        states: tuple[DispatchBucketState, ...],
    ) -> DispatchClaimMetrics:
        oldest = min(
            (state.oldest_created_at for state in states if state.oldest_created_at is not None),
            default=None,
        )
        return DispatchClaimMetrics(
            backlog_count=sum(state.backlog_count for state in states),
            active_lease_count=sum(state.active_lease_count for state in states),
            unknown_count=sum(state.unknown_count for state in states),
            oldest_queue_age_seconds=(max(0, int((now - oldest).total_seconds())) if oldest is not None else None),
            rate_limited_buckets=(),
            paused_buckets=(),
            lease_contended_buckets=(),
            lease_loss_count=sum(state.expired_lease_count for state in states),
        )


dispatch_policy_registry = DispatchPolicyRegistry()


__all__ = [
    "DispatchBucketKey",
    "DispatchBucketPolicy",
    "DispatchBucketState",
    "DispatchClaimBatch",
    "DispatchClaimMetrics",
    "DispatchLeaseClaim",
    "DispatchPolicyRegistry",
    "FairDispatchScheduler",
    "dispatch_policy_registry",
]
