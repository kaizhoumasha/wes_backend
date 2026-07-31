"""SystemOutbox Repository 层。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import and_, case, exists, func, or_, select, true, update

from src.app.effect_ledger_status import DispatchAttemptStatus
from src.app.runtime.orchestration.effect_state_contract import (
    transition_dispatch_attempt,
    transition_system_outbox,
)
from src.app.sys.dispatch_concurrency import DispatchBucketKey, DispatchBucketState
from src.app.sys.models.outbox import (
    SYSTEM_OUTBOX_RESOURCE_WAIT_REASONS,
    SystemOutbox,
    SystemOutboxDispatchType,
    SystemOutboxStatus,
    system_outbox_resource_wait_clause,
)
from src.database.base_repository import BaseRepository
from src.utils.timezone import timezone
from src.utils.value_normalization import enum_value

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from src.app.contracts.runtime_inbox_query import RuntimeInboxQueryPort


@dataclass(frozen=True, slots=True)
class ExpiredExternalHttpLeaseFence:
    """同事务证据闭环所需的过期 HTTP lease 冻结快照。"""

    outbox_id: int
    dispatch_key: str
    lease_owner_token: str
    lease_expires_at: datetime
    attempt_no_hint: int
    dispatch_started: bool
    operation_identity: str | None = None


@dataclass(frozen=True, slots=True)
class CancelledSystemOutbox:
    """取消服务写入 reducer 所需的最小冻结事实。"""

    dispatch_key: str
    previous_status: SystemOutboxStatus


class SystemOutboxRepository(BaseRepository[SystemOutbox]):
    """系统级发件箱数据访问层。"""

    DISPATCH_LEASE_SECONDS = 300
    RESOURCE_WAIT_PROBE_MIN_INTERVAL_SECONDS = 2
    DEVICE_RESOURCE_WAIT_REASONS = tuple(sorted(SYSTEM_OUTBOX_RESOURCE_WAIT_REASONS))

    def __init__(self) -> None:
        super().__init__(SystemOutbox)

    async def update(self, db: AsyncSession, id: int, data: dict[str, Any]) -> SystemOutbox | None:
        """拒绝绕过专用状态方法改写稳定派发身份或冻结请求体。"""

        immutable_fields = {
            "dispatch_key",
            "idempotency_key",
            "target_code",
            "provider_profile_identity",
            "provider_profile_hash",
            "operation_identity",
            "binding_revision",
            "target_snapshot_json",
            "target_snapshot_hash",
            "auth_scheme",
            "network_trust_mode",
            "credential_reference",
            "payload_json",
            "canonical_payload_bytes",
            "payload_hash",
            "lease_owner_token",
            "lease_expires_at",
            "dispatch_started_at",
        }
        attempted_fields = immutable_fields.intersection(data)
        if attempted_fields:
            raise ValueError(f"SystemOutbox {', '.join(sorted(attempted_fields))} 持久化后不可变")
        return await super().update(db, id, data)

    async def get_by_dispatch_key(self, db: AsyncSession, dispatch_key: str) -> SystemOutbox | None:
        columns = cast("Any", SystemOutbox).__table__.c
        result = await db.execute(select(SystemOutbox).where(columns.dispatch_key == dispatch_key))
        return result.scalar_one_or_none()

    async def get_by_id_for_update(self, db: AsyncSession, outbox_id: int) -> SystemOutbox | None:
        """锁定并强制刷新，避免 callback 事务已提交而 identity map 仍保留旧派发状态。"""

        columns = cast("Any", SystemOutbox).__table__.c
        statement = (
            select(SystemOutbox)
            .where(columns.id == outbox_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        result = await db.execute(statement)
        return result.scalar_one_or_none()

    async def get_by_dispatch_key_for_update(self, db: AsyncSession, dispatch_key: str) -> SystemOutbox | None:
        columns = cast("Any", SystemOutbox).__table__.c
        statement = (
            select(SystemOutbox)
            .where(columns.dispatch_key == dispatch_key)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        result = await db.execute(statement)
        return result.scalar_one_or_none()

    async def begin_physical_dispatch(
        self,
        db: AsyncSession,
        outbox_id: int,
        *,
        lease_owner_token: str,
        lease_seconds: int,
    ) -> SystemOutbox | None:
        """原子建立物理发送边界并续租；调用方必须在网络 I/O 前立即提交。"""

        columns = cast("Any", SystemOutbox).__table__.c
        statement = (
            select(SystemOutbox)
            .where(
                columns.id == outbox_id,
                columns.status == SystemOutboxStatus.DISPATCHING,
                columns.lease_owner_token == lease_owner_token,
                columns.dispatch_started_at.is_(None),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        result = await db.execute(statement)
        outbox = result.scalar_one_or_none()
        if outbox is None:
            return None
        from src.app.runtime.orchestration.models.dispatch_attempt import WorklineDispatchAttempt

        attempt_columns = cast("Any", WorklineDispatchAttempt).__table__.c
        attempt_result = await db.execute(
            select(WorklineDispatchAttempt)
            .where(
                attempt_columns.outbox_id == outbox_id,
                attempt_columns.lease_token == lease_owner_token,
                attempt_columns.status == DispatchAttemptStatus.DISPATCHING,
            )
            .with_for_update()
        )
        attempt = attempt_result.scalar_one_or_none()
        if attempt is None:
            return None
        now = timezone.now_for_db()
        lease_expires_at = now + timedelta(seconds=lease_seconds)
        outbox.dispatch_started_at = now
        outbox.lease_expires_at = lease_expires_at
        attempt.lease_expires_at = lease_expires_at
        await db.flush()
        return outbox

    async def fence_expired_external_http_leases(
        self,
        db: AsyncSession,
        *,
        now: Any,
        operation_domains: Sequence[str] | None = None,
        exclude_operation_domains: Sequence[str] | None = None,
        operation_identities: Sequence[str] | None = None,
        exclude_operation_identities: Sequence[str] | None = None,
        limit: int = 100,
    ) -> tuple[ExpiredExternalHttpLeaseFence, ...]:
        """锁定并收口过期 HTTP lease，返回同事务证据闭环快照。"""

        if limit <= 0:
            return ()
        columns = cast("Any", SystemOutbox).__table__.c
        result = await db.execute(
            select(SystemOutbox)
            .where(
                columns.status == SystemOutboxStatus.DISPATCHING,
                columns.dispatch_type == SystemOutboxDispatchType.EXTERNAL_HTTP,
                columns.lease_expires_at.is_not(None),
                columns.lease_expires_at <= now,
                *self._operation_domain_predicates(
                    columns,
                    operation_domains=operation_domains,
                    exclude_operation_domains=exclude_operation_domains,
                ),
                *self._operation_identity_predicates(
                    columns,
                    operation_identities=operation_identities,
                    exclude_operation_identities=exclude_operation_identities,
                ),
            )
            .order_by(columns.lease_expires_at, columns.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        fences: list[ExpiredExternalHttpLeaseFence] = []
        for outbox in result.scalars().all():
            outbox_id = getattr(outbox, "id", None)
            lease_owner_token = getattr(outbox, "lease_owner_token", None)
            lease_expires_at = getattr(outbox, "lease_expires_at", None)
            if not isinstance(outbox_id, int) or not isinstance(lease_owner_token, str) or not lease_owner_token:
                continue
            if not isinstance(lease_expires_at, datetime):
                continue
            dispatch_started = isinstance(getattr(outbox, "dispatch_started_at", None), datetime)
            fences.append(
                ExpiredExternalHttpLeaseFence(
                    outbox_id=outbox_id,
                    dispatch_key=str(outbox.dispatch_key),
                    lease_owner_token=lease_owner_token,
                    lease_expires_at=lease_expires_at,
                    attempt_no_hint=max(1, int(outbox.attempt_count or 0) + 1),
                    dispatch_started=dispatch_started,
                    operation_identity=getattr(outbox, "operation_identity", None),
                )
            )
            self._clear_block(outbox)
            outbox.lease_expires_at = None
            if dispatch_started:
                outbox.attempt_count += 1
                transition_system_outbox(outbox, SystemOutboxStatus.UNKNOWN)
                outbox.last_error = (
                    "STALE_EXTERNAL_HTTP_DISPATCH_LEASE_EXPIRED: delivery evidence unavailable; automatic replay fenced"
                )
                outbox.sent_at = None
                outbox.next_retry_at = None
                outbox.finished_at = now
            else:
                # claim 仅在本地排队且尚未越过物理发送边界，lease 过期可确定安全回队。
                transition_system_outbox(outbox, SystemOutboxStatus.RETRY_WAIT)
                outbox.last_error = "STALE_EXTERNAL_HTTP_QUEUE_LEASE_EXPIRED: safely requeued before send"
                outbox.next_retry_at = now
                outbox.finished_at = None
        await db.flush()
        return tuple(fences)

    async def list_dispatch_bucket_keys(
        self,
        db: AsyncSession,
        *,
        now: Any,
        operation_domains: Sequence[str] | None = None,
        exclude_operation_domains: Sequence[str] | None = None,
        operation_identities: Sequence[str] | None = None,
        exclude_operation_identities: Sequence[str] | None = None,
    ) -> tuple[DispatchBucketKey, ...]:
        """只按显式调度列返回当前有 durable backlog 的活跃桶。"""

        columns = cast("Any", SystemOutbox).__table__.c
        result = await db.execute(
            select(columns.provider_profile_identity, columns.operation_identity)
            .where(
                self._dispatch_claimable_clause(columns, now=now, retry_budget=None),
                *self._operation_domain_predicates(
                    columns,
                    operation_domains=operation_domains,
                    exclude_operation_domains=exclude_operation_domains,
                ),
                *self._operation_identity_predicates(
                    columns,
                    operation_identities=operation_identities,
                    exclude_operation_identities=exclude_operation_identities,
                ),
            )
            .group_by(columns.provider_profile_identity, columns.operation_identity)
            .order_by(columns.provider_profile_identity, columns.operation_identity)
        )
        return tuple(DispatchBucketKey(str(row[0]), str(row[1])) for row in result.all())

    async def try_lock_dispatch_bucket(self, db: AsyncSession, *, bucket: DispatchBucketKey) -> bool:
        """用 PostgreSQL transaction advisory lock 串行化单桶预算核算。"""

        bind = db.get_bind()
        if bind.dialect.name != "postgresql":
            raise NotImplementedError("SystemOutbox fair bucket claim requires PostgreSQL")
        lock_identity = f"system-outbox\x1f{bucket.provider_profile_identity}\x1f{bucket.operation_identity}"
        locked = await db.scalar(select(func.pg_try_advisory_xact_lock(func.hashtextextended(lock_identity, 0))))
        return bool(locked)

    async def get_dispatch_bucket_state(
        self,
        db: AsyncSession,
        *,
        bucket: DispatchBucketKey,
        now: Any,
        rate_window_seconds: int = 60,
        operation_domains: Sequence[str] | None = None,
        exclude_operation_domains: Sequence[str] | None = None,
        operation_identities: Sequence[str] | None = None,
        exclude_operation_identities: Sequence[str] | None = None,
    ) -> DispatchBucketState:
        """从索引列与 attempt ledger 汇总 backlog/rate/lease/UNKNOWN SLI。"""

        from src.app.runtime.orchestration.models.dispatch_attempt import WorklineDispatchAttempt

        columns = cast("Any", SystemOutbox).__table__.c
        attempt_columns = cast("Any", WorklineDispatchAttempt).__table__.c
        bucket_identity_filters = (
            columns.provider_profile_identity == bucket.provider_profile_identity,
            columns.operation_identity == bucket.operation_identity,
        )
        scoped_bucket_filters = (
            *bucket_identity_filters,
            *self._operation_domain_predicates(
                columns,
                operation_domains=operation_domains,
                exclude_operation_domains=exclude_operation_domains,
            ),
            *self._operation_identity_predicates(
                columns,
                operation_identities=operation_identities,
                exclude_operation_identities=exclude_operation_identities,
            ),
        )
        claimable = self._dispatch_claimable_clause(columns, now=now, retry_budget=None)
        backlog_count = await db.scalar(
            select(func.count()).select_from(SystemOutbox).where(*scoped_bucket_filters, claimable)
        )
        active_lease_count = await db.scalar(
            select(func.count())
            .select_from(SystemOutbox)
            .where(
                *bucket_identity_filters,
                columns.status == SystemOutboxStatus.DISPATCHING,
                columns.lease_expires_at > now,
            )
        )
        expired_lease_count = await db.scalar(
            select(func.count())
            .select_from(SystemOutbox)
            .where(
                *scoped_bucket_filters,
                columns.status == SystemOutboxStatus.DISPATCHING,
                columns.lease_expires_at.is_not(None),
                columns.lease_expires_at <= now,
            )
        )
        unknown_count = await db.scalar(
            select(func.count())
            .select_from(SystemOutbox)
            .where(*scoped_bucket_filters, columns.status == SystemOutboxStatus.UNKNOWN)
        )
        oldest_created_at = await db.scalar(
            select(func.min(columns.created_at)).select_from(SystemOutbox).where(*scoped_bucket_filters, claimable)
        )
        recent_attempt_count = await db.scalar(
            select(func.count())
            .select_from(WorklineDispatchAttempt)
            .join(SystemOutbox, attempt_columns.outbox_id == columns.id)
            .where(
                *bucket_identity_filters,
                attempt_columns.started_at >= now - timedelta(seconds=rate_window_seconds),
            )
        )
        return DispatchBucketState(
            key=bucket,
            backlog_count=int(backlog_count or 0),
            active_lease_count=int(active_lease_count or 0),
            recent_attempt_count=int(recent_attempt_count or 0),
            oldest_created_at=oldest_created_at,
            unknown_count=int(unknown_count or 0),
            expired_lease_count=int(expired_lease_count or 0),
        )

    def build_claim_next_in_bucket_statement(
        self,
        *,
        bucket: DispatchBucketKey,
        lease_owner_token: str,
        lease_seconds: int,
        retry_budget: int,
        now: Any,
        operation_domains: Sequence[str] | None = None,
        exclude_operation_domains: Sequence[str] | None = None,
        operation_identities: Sequence[str] | None = None,
        exclude_operation_identities: Sequence[str] | None = None,
    ) -> Any:
        """构建单桶原子 claim；候选选择只读取可索引的 typed columns。"""

        table = cast("Any", SystemOutbox).__table__
        columns = table.c
        candidate = table.alias("dispatch_claim_candidate")
        cc = candidate.c
        current_device = self._device_resolution_alias("dispatch_claim_candidate_device")
        older_outbox = table.alias("older_dispatch_claim_device_outbox")
        older_device = self._device_resolution_alias("older_dispatch_claim_device")
        candidate_from = candidate.outerjoin(
            current_device,
            self._device_resolution_join_condition(cc, current_device),
        )
        older_device_from = older_outbox.outerjoin(
            older_device,
            self._device_resolution_join_condition(older_outbox.c, older_device),
        )
        earlier_active_device_outbox_exists = exists(
            select(1)
            .select_from(older_device_from)
            .where(
                older_outbox.c.dispatch_type == SystemOutboxDispatchType.DEVICE_COMMAND,
                older_outbox.c.status.in_(
                    [
                        SystemOutboxStatus.NEW,
                        SystemOutboxStatus.DISPATCHING,
                        SystemOutboxStatus.RETRY_WAIT,
                    ]
                ),
                self._same_physical_device_predicate(
                    current_columns=cc,
                    current_device=current_device,
                    other_columns=older_outbox.c,
                    other_device=older_device,
                ),
                or_(
                    older_outbox.c.created_at < cc.created_at,
                    and_(older_outbox.c.created_at == cc.created_at, older_outbox.c.id < cc.id),
                ),
            )
        )
        candidate_ids = (
            select(cc.id)
            .select_from(candidate_from)
            .where(
                cc.provider_profile_identity == bucket.provider_profile_identity,
                cc.operation_identity == bucket.operation_identity,
                self._dispatch_claimable_clause(cc, now=now, retry_budget=retry_budget),
                or_(
                    cc.dispatch_type != SystemOutboxDispatchType.DEVICE_COMMAND,
                    ~earlier_active_device_outbox_exists,
                ),
                *self._operation_domain_predicates(
                    cc,
                    operation_domains=operation_domains,
                    exclude_operation_domains=exclude_operation_domains,
                ),
                *self._operation_identity_predicates(
                    cc,
                    operation_identities=operation_identities,
                    exclude_operation_identities=exclude_operation_identities,
                ),
            )
            .order_by(cc.created_at, cc.id)
            .limit(1)
            .with_for_update(skip_locked=True, of=candidate)
            # PostgreSQL 的自更新语句可能为每个外层行重求值 SKIP LOCKED 子查询；
            # MATERIALIZED 固定本次事务唯一候选，避免 LIMIT 1 仍更新整桶。
            .cte("dispatch_claim_candidate_ids")
            .prefix_with("MATERIALIZED")
        )
        return (
            update(SystemOutbox)
            .where(columns.id == candidate_ids.c.id)
            .values(
                status=SystemOutboxStatus.DISPATCHING,
                attempt_count=case(
                    (
                        and_(
                            columns.status == SystemOutboxStatus.DISPATCHING,
                            columns.dispatch_type != SystemOutboxDispatchType.EXTERNAL_HTTP,
                        ),
                        columns.attempt_count + 1,
                    ),
                    else_=columns.attempt_count,
                ),
                lease_owner_token=lease_owner_token,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                dispatch_started_at=None,
                next_retry_at=None,
                finished_at=None,
            )
            .returning(SystemOutbox)
            .execution_options(synchronize_session=False)
        )

    async def claim_next_in_bucket(
        self,
        db: AsyncSession,
        *,
        bucket: DispatchBucketKey,
        lease_owner_token: str,
        lease_seconds: int,
        retry_budget: int,
        now: Any,
        operation_domains: Sequence[str] | None = None,
        exclude_operation_domains: Sequence[str] | None = None,
        operation_identities: Sequence[str] | None = None,
        exclude_operation_identities: Sequence[str] | None = None,
    ) -> SystemOutbox | None:
        """在已持有 bucket transaction lock 时领取一条消息。"""

        result = await db.execute(
            self.build_claim_next_in_bucket_statement(
                bucket=bucket,
                lease_owner_token=lease_owner_token,
                lease_seconds=lease_seconds,
                retry_budget=retry_budget,
                now=now,
                operation_domains=operation_domains,
                exclude_operation_domains=exclude_operation_domains,
                operation_identities=operation_identities,
                exclude_operation_identities=exclude_operation_identities,
            )
        )
        return result.scalar_one_or_none()

    async def fence_exhausted_non_http_leases_in_bucket(
        self,
        db: AsyncSession,
        *,
        bucket: DispatchBucketKey,
        retry_budget: int,
        now: Any,
        operation_domains: Sequence[str] | None = None,
        exclude_operation_domains: Sequence[str] | None = None,
        operation_identities: Sequence[str] | None = None,
        exclude_operation_identities: Sequence[str] | None = None,
        limit: int = 100,
    ) -> tuple[SystemOutbox, ...]:
        """锁定并终结单桶内耗尽预算的非 HTTP 过期 lease。"""

        if limit <= 0:
            return ()
        columns = cast("Any", SystemOutbox).__table__.c
        result = await db.execute(
            select(SystemOutbox)
            .where(
                columns.provider_profile_identity == bucket.provider_profile_identity,
                columns.operation_identity == bucket.operation_identity,
                columns.status == SystemOutboxStatus.DISPATCHING,
                columns.dispatch_type != SystemOutboxDispatchType.EXTERNAL_HTTP,
                columns.attempt_count >= retry_budget,
                columns.lease_expires_at.is_not(None),
                columns.lease_expires_at <= now,
                *self._operation_domain_predicates(
                    columns,
                    operation_domains=operation_domains,
                    exclude_operation_domains=exclude_operation_domains,
                ),
                *self._operation_identity_predicates(
                    columns,
                    operation_identities=operation_identities,
                    exclude_operation_identities=exclude_operation_identities,
                ),
            )
            .order_by(columns.lease_expires_at, columns.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        outboxes = tuple(result.scalars().all())
        for outbox in outboxes:
            transition_system_outbox(outbox, SystemOutboxStatus.FAILED)
            self._clear_block(outbox)
            outbox.last_error = "NON_HTTP_DISPATCH_RETRY_BUDGET_EXHAUSTED"
            outbox.next_retry_at = None
            outbox.lease_expires_at = None
            outbox.finished_at = now
        if outboxes:
            await db.flush()
        return outboxes

    @staticmethod
    def _dispatch_claimable_clause(columns: Any, *, now: Any, retry_budget: int | None) -> Any:
        retry_budget_clause = true() if retry_budget is None else columns.attempt_count <= retry_budget
        expired_lease_budget_clause = true() if retry_budget is None else columns.attempt_count < retry_budget
        return and_(
            retry_budget_clause,
            or_(
                and_(
                    columns.status == SystemOutboxStatus.NEW,
                    or_(columns.next_retry_at.is_(None), columns.next_retry_at <= now),
                ),
                and_(
                    columns.status == SystemOutboxStatus.RETRY_WAIT,
                    columns.next_retry_at.is_not(None),
                    columns.next_retry_at <= now,
                ),
                and_(
                    columns.status == SystemOutboxStatus.DISPATCHING,
                    columns.dispatch_type != SystemOutboxDispatchType.EXTERNAL_HTTP,
                    expired_lease_budget_clause,
                    columns.lease_expires_at.is_not(None),
                    columns.lease_expires_at <= now,
                ),
            ),
        )

    async def get_pending_messages(
        self,
        db: AsyncSession,
        limit: int = 50,
        *,
        operation_domains: Sequence[str] | None = None,
        exclude_operation_domains: Sequence[str] | None = None,
    ) -> list[SystemOutbox]:
        """获取可派发消息。

        设备命令按物理设备 FIFO：优先使用 device_id，没有 device_id 时使用 target_code。
        Rack、Handling、Workline 共享同一物理设备时必须互相串行。
        """

        columns = cast("Any", SystemOutbox).__table__.c
        current_device = self._device_resolution_alias("current_pending_outbox_device")
        older_outbox = cast("Any", SystemOutbox).__table__.alias("older_device_outbox")
        older_device = self._device_resolution_alias("older_pending_outbox_device")
        now = timezone.now_for_db()
        active_device_statuses = [
            SystemOutboxStatus.NEW,
            SystemOutboxStatus.DISPATCHING,
            SystemOutboxStatus.RETRY_WAIT,
        ]
        same_physical_device = self._same_physical_device_predicate(
            current_columns=columns,
            current_device=current_device,
            other_columns=older_outbox.c,
            other_device=older_device,
        )
        older_device_from = older_outbox.outerjoin(
            older_device, self._device_resolution_join_condition(older_outbox.c, older_device)
        )
        earlier_active_device_outbox_exists = exists(
            select(1)
            .select_from(older_device_from)
            .where(
                older_outbox.c.dispatch_type == SystemOutboxDispatchType.DEVICE_COMMAND,
                older_outbox.c.status.in_(active_device_statuses),
                same_physical_device,
                or_(
                    older_outbox.c.created_at < columns.created_at,
                    and_(older_outbox.c.created_at == columns.created_at, older_outbox.c.id < columns.id),
                ),
            )
        )

        domain_predicates = self._operation_domain_predicates(
            columns,
            operation_domains=operation_domains,
            exclude_operation_domains=exclude_operation_domains,
        )

        result = await db.execute(
            select(SystemOutbox)
            .outerjoin(current_device, self._device_resolution_join_condition(columns, current_device))
            .where(
                or_(
                    and_(
                        columns.status == SystemOutboxStatus.NEW,
                        (columns.next_retry_at.is_(None)) | (columns.next_retry_at <= now),
                    ),
                    and_(
                        columns.status == SystemOutboxStatus.DISPATCHING,
                        columns.next_retry_at.isnot(None),
                        columns.next_retry_at <= now,
                    ),
                    and_(
                        columns.status == SystemOutboxStatus.RETRY_WAIT,
                        columns.next_retry_at.isnot(None),
                        columns.next_retry_at <= now,
                    ),
                ),
                or_(
                    columns.dispatch_type != SystemOutboxDispatchType.DEVICE_COMMAND,
                    ~earlier_active_device_outbox_exists,
                ),
                *domain_predicates,
            )
            .order_by(columns.created_at.asc(), columns.id.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_probeable_blocked_device_heads(
        self,
        db: AsyncSession,
        limit: int = 50,
        *,
        min_probe_interval_seconds: int = 2,
        operation_domains: Sequence[str] | None = None,
        exclude_operation_domains: Sequence[str] | None = None,
    ) -> list[SystemOutbox]:
        """获取可重新探测 ECS admission 的 blocked 设备队首 outbox。"""

        columns = cast("Any", SystemOutbox).__table__.c
        current_device = self._device_resolution_alias("current_blocked_outbox_device")
        older_outbox = cast("Any", SystemOutbox).__table__.alias("older_blocked_device_outbox")
        older_device = self._device_resolution_alias("older_blocked_outbox_device")
        now = timezone.now_for_db()
        probe_cutoff = now - timedelta(seconds=min_probe_interval_seconds)
        active_device_statuses = [
            SystemOutboxStatus.NEW,
            SystemOutboxStatus.DISPATCHING,
            SystemOutboxStatus.RETRY_WAIT,
        ]
        same_physical_device = self._same_physical_device_predicate(
            current_columns=columns,
            current_device=current_device,
            other_columns=older_outbox.c,
            other_device=older_device,
        )
        older_device_from = older_outbox.outerjoin(
            older_device, self._device_resolution_join_condition(older_outbox.c, older_device)
        )
        earlier_active_device_outbox_exists = exists(
            select(1)
            .select_from(older_device_from)
            .where(
                older_outbox.c.dispatch_type == SystemOutboxDispatchType.DEVICE_COMMAND,
                older_outbox.c.status.in_(active_device_statuses),
                same_physical_device,
                or_(
                    older_outbox.c.created_at < columns.created_at,
                    and_(older_outbox.c.created_at == columns.created_at, older_outbox.c.id < columns.id),
                ),
            )
        )
        domain_predicates = self._operation_domain_predicates(
            columns,
            operation_domains=operation_domains,
            exclude_operation_domains=exclude_operation_domains,
        )

        result = await db.execute(
            select(SystemOutbox)
            .outerjoin(current_device, self._device_resolution_join_condition(columns, current_device))
            .where(
                system_outbox_resource_wait_clause(columns),
                or_(columns.last_blocked_check_at.is_(None), columns.last_blocked_check_at <= probe_cutoff),
                ~earlier_active_device_outbox_exists,
                *domain_predicates,
            )
            .order_by(columns.created_at.asc(), columns.id.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def mark_as_dispatching(
        self,
        db: AsyncSession,
        outbox_id: int,
        *,
        lease_owner_token: str,
        lease_seconds: int = DISPATCH_LEASE_SECONDS,
    ) -> SystemOutbox | None:
        """显式 owner 领取单条 outbox；dispatcher 批量路径统一使用 fair scheduler。"""

        columns = cast("Any", SystemOutbox).__table__.c
        result = await db.execute(select(SystemOutbox).where(columns.id == outbox_id).with_for_update())
        outbox = result.scalar_one_or_none()
        if outbox is None:
            return None

        now = timezone.now_for_db()
        stale_dispatching = (
            outbox.status == SystemOutboxStatus.DISPATCHING
            and outbox.lease_expires_at is not None
            and outbox.lease_expires_at <= now
        )
        if stale_dispatching and outbox.dispatch_type == SystemOutboxDispatchType.EXTERNAL_HTTP:
            # HTTP 请求在 worker 失联前可能已经越过本地边界；
            # 过期 lease 只能保守收口，绝不能直接重放。
            outbox.attempt_count += 1
            self._clear_block(outbox)
            transition_system_outbox(outbox, SystemOutboxStatus.UNKNOWN)
            outbox.last_error = (
                "STALE_EXTERNAL_HTTP_DISPATCH_LEASE_EXPIRED: delivery evidence unavailable; automatic replay fenced"
            )
            outbox.sent_at = None
            outbox.next_retry_at = None
            outbox.lease_expires_at = None
            outbox.finished_at = now
            await db.flush()
            return None
        if outbox.status not in {SystemOutboxStatus.NEW, SystemOutboxStatus.RETRY_WAIT} and not stale_dispatching:
            return None

        if outbox.status != SystemOutboxStatus.DISPATCHING:
            transition_system_outbox(outbox, SystemOutboxStatus.DISPATCHING)
        outbox.next_retry_at = None
        outbox.lease_owner_token = lease_owner_token
        outbox.lease_expires_at = now + timedelta(seconds=lease_seconds)
        outbox.dispatch_started_at = None
        await db.flush()
        return outbox

    async def claim_blocked_resource_wait_for_dispatch(
        self,
        db: AsyncSession,
        outbox_id: int,
        expected_reason: str,
        *,
        min_probe_interval_seconds: int = 2,
        operation_domains: Sequence[str] | None = None,
        exclude_operation_domains: Sequence[str] | None = None,
        lease_owner_token: str,
        lease_seconds: int,
    ) -> SystemOutbox | None:
        columns = cast("Any", SystemOutbox).__table__.c
        current_device = self._device_resolution_alias("claim_blocked_outbox_device")
        older_outbox = cast("Any", SystemOutbox).__table__.alias("older_claim_blocked_device_outbox")
        older_device = self._device_resolution_alias("older_claim_blocked_outbox_device")
        now = timezone.now_for_db()
        probe_cutoff = now - timedelta(seconds=min_probe_interval_seconds)
        active_device_statuses = [
            SystemOutboxStatus.NEW,
            SystemOutboxStatus.DISPATCHING,
            SystemOutboxStatus.RETRY_WAIT,
        ]
        same_physical_device = self._same_physical_device_predicate(
            current_columns=columns,
            current_device=current_device,
            other_columns=older_outbox.c,
            other_device=older_device,
        )
        older_device_from = older_outbox.outerjoin(
            older_device, self._device_resolution_join_condition(older_outbox.c, older_device)
        )
        earlier_active_device_outbox_exists = exists(
            select(1)
            .select_from(older_device_from)
            .where(
                older_outbox.c.dispatch_type == SystemOutboxDispatchType.DEVICE_COMMAND,
                older_outbox.c.status.in_(active_device_statuses),
                same_physical_device,
                or_(
                    older_outbox.c.created_at < columns.created_at,
                    and_(older_outbox.c.created_at == columns.created_at, older_outbox.c.id < columns.id),
                ),
            )
        )
        domain_predicates = self._operation_domain_predicates(
            columns,
            operation_domains=operation_domains,
            exclude_operation_domains=exclude_operation_domains,
        )
        result = await db.execute(
            select(SystemOutbox)
            .outerjoin(current_device, self._device_resolution_join_condition(columns, current_device))
            .where(
                columns.id == outbox_id,
                system_outbox_resource_wait_clause(columns),
                columns.blocked_reason == expected_reason,
                or_(columns.last_blocked_check_at.is_(None), columns.last_blocked_check_at <= probe_cutoff),
                ~earlier_active_device_outbox_exists,
                *domain_predicates,
            )
            .with_for_update(of=SystemOutbox)
        )
        outbox = result.scalar_one_or_none()
        if outbox is None:
            return None

        transition_system_outbox(outbox, SystemOutboxStatus.DISPATCHING)
        self._clear_block(outbox)
        outbox.lease_owner_token = lease_owner_token
        outbox.lease_expires_at = now + timedelta(seconds=lease_seconds)
        outbox.dispatch_started_at = None
        await db.flush()
        return outbox

    async def mark_as_sent(
        self,
        db: AsyncSession,
        outbox_id: int,
        *,
        lease_owner_token: str,
    ) -> SystemOutbox | None:
        return await self._mark_as_delivered(
            db,
            outbox_id,
            lease_owner_token=lease_owner_token,
            terminal_error=None,
        )

    async def mark_as_protocol_rejected(
        self,
        db: AsyncSession,
        outbox_id: int,
        error: str,
        *,
        lease_owner_token: str,
    ) -> SystemOutbox | None:
        """保留已送达事实，同时终结明确协议拒绝并释放资源占用。"""

        normalized_error = error.strip()
        if not normalized_error:
            raise ValueError("protocol rejection requires an error reason")
        return await self._mark_as_delivered(
            db,
            outbox_id,
            lease_owner_token=lease_owner_token,
            terminal_error=normalized_error,
        )

    async def _mark_as_delivered(
        self,
        db: AsyncSession,
        outbox_id: int,
        *,
        lease_owner_token: str,
        terminal_error: str | None,
    ) -> SystemOutbox | None:
        columns = cast("Any", SystemOutbox).__table__.c
        now = timezone.now_for_db()
        result = await db.execute(
            select(SystemOutbox)
            .where(
                columns.id == outbox_id,
                columns.status == SystemOutboxStatus.DISPATCHING,
                columns.lease_owner_token == lease_owner_token,
                columns.lease_expires_at > now,
            )
            .with_for_update()
        )
        outbox = result.scalar_one_or_none()
        if outbox is None or getattr(outbox, "finished_at", None) is not None:
            return None
        transition_system_outbox(outbox, SystemOutboxStatus.SENT)
        outbox.sent_at = now
        outbox.next_retry_at = None
        outbox.lease_expires_at = None
        outbox.last_error = terminal_error
        if terminal_error is not None:
            outbox.finished_at = now
        await db.flush()
        return outbox

    async def finish_sent_external_by_dispatch_key(self, db: AsyncSession, dispatch_key: str) -> SystemOutbox | None:
        """按外部派发键闭环已发送或已回调 outbox，释放 station lease。"""

        columns = cast("Any", SystemOutbox).__table__.c
        result = await db.execute(
            select(SystemOutbox)
            .where(
                columns.dispatch_key == dispatch_key,
                columns.dispatch_type == SystemOutboxDispatchType.EXTERNAL_HTTP,
                columns.status.in_(
                    [
                        SystemOutboxStatus.DISPATCHING,
                        SystemOutboxStatus.SENT,
                    ]
                ),
                columns.finished_at.is_(None),
            )
            .with_for_update()
        )
        outbox = result.scalar_one_or_none()
        if outbox is None:
            return None
        if outbox.status == SystemOutboxStatus.DISPATCHING:
            transition_system_outbox(outbox, SystemOutboxStatus.SENT)
        outbox.sent_at = outbox.sent_at or timezone.now_for_db()
        outbox.next_retry_at = None
        # lease_owner_token 作为最近 owner 的审计证据保留；非 DISPATCHING 必须清 expiry。
        outbox.lease_expires_at = None
        outbox.last_error = None
        outbox.finished_at = timezone.now_for_db()
        await db.flush()
        return outbox

    async def isolate_for_reconciliation_by_dispatch_key(
        self,
        db: AsyncSession,
        dispatch_key: str,
        *,
        reason: str,
    ) -> SystemOutbox | None:
        """业务关联冲突时隔离 outbox，禁止继续派发并释放 station lease。"""

        columns = cast("Any", SystemOutbox).__table__.c
        result = await db.execute(
            select(SystemOutbox)
            .where(
                columns.dispatch_key == dispatch_key,
                columns.dispatch_type == SystemOutboxDispatchType.EXTERNAL_HTTP,
            )
            .with_for_update()
        )
        outbox = result.scalar_one_or_none()
        if outbox is None:
            return None
        if outbox.status in {
            SystemOutboxStatus.NEW,
            SystemOutboxStatus.RETRY_WAIT,
            SystemOutboxStatus.DISPATCHING,
        }:
            transition_system_outbox(outbox, SystemOutboxStatus.CANCELLED)
        self._clear_block(outbox)
        outbox.last_error = reason
        outbox.next_retry_at = None
        outbox.lease_expires_at = None
        outbox.finished_at = outbox.finished_at or timezone.now_for_db()
        await db.flush()
        return outbox

    async def mark_as_failed(
        self,
        db: AsyncSession,
        outbox_id: int,
        error: str,
        max_retries: int = 3,
        *,
        lease_owner_token: str,
    ) -> SystemOutbox | None:
        columns = cast("Any", SystemOutbox).__table__.c
        now = timezone.now_for_db()
        result = await db.execute(
            select(SystemOutbox)
            .where(
                columns.id == outbox_id,
                columns.status == SystemOutboxStatus.DISPATCHING,
                columns.lease_owner_token == lease_owner_token,
                columns.lease_expires_at > now,
            )
            .with_for_update()
        )
        outbox = result.scalar_one_or_none()
        if outbox is None:
            return None

        outbox.attempt_count += 1
        self._clear_block(outbox)
        outbox.last_error = error
        if outbox.attempt_count > max_retries:
            transition_system_outbox(outbox, SystemOutboxStatus.FAILED)
            outbox.next_retry_at = None
            outbox.finished_at = now
        else:
            transition_system_outbox(outbox, SystemOutboxStatus.RETRY_WAIT)
            outbox.next_retry_at = now + timedelta(seconds=2 ** (outbox.attempt_count - 1))
        outbox.lease_expires_at = None
        await db.flush()
        return outbox

    async def mark_as_unknown(
        self,
        db: AsyncSession,
        outbox_id: int,
        error: str,
        *,
        lease_owner_token: str,
    ) -> SystemOutbox | None:
        """将送达结果不确定的 attempt 终止为 UNKNOWN，禁止自动重试。"""

        columns = cast("Any", SystemOutbox).__table__.c
        now = timezone.now_for_db()
        result = await db.execute(
            select(SystemOutbox)
            .where(
                columns.id == outbox_id,
                columns.status == SystemOutboxStatus.DISPATCHING,
                columns.lease_owner_token == lease_owner_token,
                columns.lease_expires_at > now,
            )
            .with_for_update()
        )
        outbox = result.scalar_one_or_none()
        if outbox is None or outbox.status != SystemOutboxStatus.DISPATCHING:
            return None

        outbox.attempt_count += 1
        self._clear_block(outbox)
        transition_system_outbox(outbox, SystemOutboxStatus.UNKNOWN)
        outbox.last_error = error
        outbox.next_retry_at = None
        outbox.lease_expires_at = None
        outbox.finished_at = now
        await db.flush()
        return outbox

    async def mark_evidence_persistence_unknown(
        self,
        db: AsyncSession,
        outbox_id: int,
        error: str,
        *,
        lease_owner_token: str,
    ) -> SystemOutbox | None:
        """证据事务结果不确定时，仅由当前有效 owner 独立收口 UNKNOWN。

        独立恢复事务不能越过已过期或已被替换的 fence，也不能覆盖 scheduler 已写入的
        UNKNOWN 证据。仅仍处于 DISPATCHING 的同 owner 有效 lease 可以执行恢复。
        """

        columns = cast("Any", SystemOutbox).__table__.c
        now = timezone.now_for_db()
        result = await db.execute(
            select(SystemOutbox)
            .where(
                columns.id == outbox_id,
                columns.dispatch_type == SystemOutboxDispatchType.EXTERNAL_HTTP,
                columns.status == SystemOutboxStatus.DISPATCHING,
                columns.lease_owner_token == lease_owner_token,
                columns.lease_expires_at > now,
            )
            .with_for_update()
        )
        outbox = result.scalar_one_or_none()
        lease_expires_at = getattr(outbox, "lease_expires_at", None)
        if (
            outbox is None
            or outbox.dispatch_type != SystemOutboxDispatchType.EXTERNAL_HTTP
            or outbox.status != SystemOutboxStatus.DISPATCHING
            or getattr(outbox, "lease_owner_token", None) != lease_owner_token
            or lease_expires_at is None
            or lease_expires_at <= now
        ):
            return None

        outbox.attempt_count += 1
        self._clear_block(outbox)
        transition_system_outbox(outbox, SystemOutboxStatus.UNKNOWN)
        outbox.last_error = error
        outbox.sent_at = None
        outbox.next_retry_at = None
        outbox.lease_expires_at = None
        outbox.finished_at = now
        await db.flush()
        return outbox

    async def mark_as_terminal_failure(
        self,
        db: AsyncSession,
        outbox_id: int,
        error: str,
        *,
        lease_owner_token: str,
    ) -> SystemOutbox | None:
        """将不可重试且明确未发送的 attempt 终止为 FAILED。"""

        columns = cast("Any", SystemOutbox).__table__.c
        now = timezone.now_for_db()
        result = await db.execute(
            select(SystemOutbox)
            .where(
                columns.id == outbox_id,
                columns.status == SystemOutboxStatus.DISPATCHING,
                columns.lease_owner_token == lease_owner_token,
                columns.lease_expires_at > now,
            )
            .with_for_update()
        )
        outbox = result.scalar_one_or_none()
        if outbox is None or outbox.status != SystemOutboxStatus.DISPATCHING:
            return None

        outbox.attempt_count += 1
        self._clear_block(outbox)
        transition_system_outbox(outbox, SystemOutboxStatus.FAILED)
        outbox.last_error = error
        outbox.next_retry_at = None
        outbox.lease_expires_at = None
        outbox.finished_at = now
        await db.flush()
        return outbox

    async def mark_as_blocked_by_workline_estop(
        self,
        db: AsyncSession,
        outbox_id: int,
        *,
        lease_owner_token: str | None = None,
    ) -> SystemOutbox | None:
        return await self._block_or_fail(
            db,
            outbox_id,
            status=SystemOutboxStatus.CANCELLED,
            reason="BLOCKED_BY_WORKLINE_ESTOP",
            lease_owner_token=lease_owner_token,
        )

    async def mark_as_blocked_by_workline_state(
        self,
        db: AsyncSession,
        outbox_id: int,
        *,
        owner_session_id: int,
        reason: str,
        blocked_device_id: int | None = None,
        blocked_workline_id: int | None = None,
        lease_owner_token: str | None = None,
    ) -> SystemOutbox | None:
        outbox = await self._get_active_for_block(db, outbox_id, lease_owner_token=lease_owner_token)
        if outbox is None:
            return None
        self._transition_to_retry_wait(outbox)
        outbox.blocked_by_reconciliation_session_id = owner_session_id
        outbox.blocked_device_id = blocked_device_id
        outbox.blocked_workline_id = blocked_workline_id or outbox.workline_id
        outbox.blocked_reason = reason
        outbox.last_error = reason
        outbox.next_retry_at = None
        outbox.lease_expires_at = None
        outbox.finished_at = None
        await db.flush()
        return outbox

    async def mark_as_blocked_by_workline_stopped(
        self,
        db: AsyncSession,
        outbox_id: int,
        *,
        blocked_workline_id: int | None = None,
        lease_owner_token: str | None = None,
    ) -> SystemOutbox | None:
        outbox = await self._get_active_for_block(db, outbox_id, lease_owner_token=lease_owner_token)
        if outbox is None:
            return None
        self._transition_to_retry_wait(outbox)
        outbox.blocked_by_runtime_hold_id = None
        outbox.blocked_by_reconciliation_session_id = None
        outbox.blocked_workline_id = blocked_workline_id or outbox.workline_id
        outbox.blocked_reason = "WORKLINE_STOPPED_WAITING_START"
        outbox.last_error = "WORKLINE_STOPPED_WAITING_START"
        outbox.next_retry_at = None
        outbox.lease_expires_at = None
        outbox.finished_at = None
        await db.flush()
        return outbox

    async def block_by_runtime_hold(
        self,
        db: AsyncSession,
        outbox_id: int,
        *,
        runtime_hold_id: int,
        reason: str,
        owner_session_id: int | None = None,
        blocked_device_id: int | None = None,
        blocked_workline_id: int | None = None,
        lease_owner_token: str | None = None,
    ) -> SystemOutbox | None:
        outbox = await self._get_active_for_block(db, outbox_id, lease_owner_token=lease_owner_token)
        if outbox is None:
            return None
        self._transition_to_retry_wait(outbox)
        outbox.blocked_by_runtime_hold_id = runtime_hold_id
        outbox.blocked_by_reconciliation_session_id = owner_session_id
        outbox.blocked_device_id = blocked_device_id
        outbox.blocked_workline_id = blocked_workline_id or outbox.workline_id
        outbox.blocked_reason = reason
        outbox.last_error = reason
        outbox.next_retry_at = None
        outbox.lease_expires_at = None
        outbox.finished_at = None
        await db.flush()
        return outbox

    async def mark_as_blocked_by_device_busy(
        self,
        db: AsyncSession,
        outbox_id: int,
        *,
        blocked_device_id: int | None,
        blocked_workline_id: int | None = None,
        reason: str = "DEVICE_BUSY",
        last_error: str | None = None,
        detail: dict[str, Any] | None = None,
        lease_owner_token: str | None = None,
    ) -> SystemOutbox | None:
        return await self.block_for_resource_wait(
            db,
            outbox_id,
            reason=reason,
            blocked_device_id=blocked_device_id,
            blocked_workline_id=blocked_workline_id,
            last_error=last_error,
            detail=detail,
            lease_owner_token=lease_owner_token,
        )

    async def update_resource_wait_detail(
        self,
        db: AsyncSession,
        outbox_id: int,
        *,
        expected_reason: str,
        detail: dict[str, Any],
        last_error: str | None = None,
    ) -> SystemOutbox | None:
        """仅更新资源等待诊断，不递增 blocked_check_count。"""

        columns = cast("Any", SystemOutbox).__table__.c
        result = await db.execute(select(SystemOutbox).where(columns.id == outbox_id).with_for_update())
        outbox = result.scalar_one_or_none()
        if outbox is None:
            return None
        if outbox.status != SystemOutboxStatus.RETRY_WAIT or outbox.blocked_reason != expected_reason:
            return None
        if expected_reason not in self.DEVICE_RESOURCE_WAIT_REASONS:
            return None
        outbox.blocked_detail_json = dict(detail)
        if last_error is not None:
            outbox.last_error = last_error
        await db.flush()
        return outbox

    async def block_for_resource_wait(
        self,
        db: AsyncSession,
        outbox_id: int,
        *,
        reason: str,
        blocked_device_id: int | None,
        blocked_workline_id: int | None = None,
        last_error: str | None = None,
        detail: dict[str, Any] | None = None,
        lease_owner_token: str | None = None,
    ) -> SystemOutbox | None:
        if reason not in self.DEVICE_RESOURCE_WAIT_REASONS:
            raise ValueError(f"不受控的设备资源等待原因: {reason}")
        outbox = await self._get_active_for_resource_wait(
            db,
            outbox_id,
            reason=reason,
            lease_owner_token=lease_owner_token,
        )
        if outbox is None:
            return None
        now = timezone.now_for_db()
        existing_detail = dict(outbox.blocked_detail_json or {})
        if (
            outbox.status == SystemOutboxStatus.RETRY_WAIT
            and outbox.blocked_reason in self.DEVICE_RESOURCE_WAIT_REASONS
            and outbox.last_blocked_check_at is not None
            and outbox.last_blocked_check_at > now - timedelta(seconds=self.RESOURCE_WAIT_PROBE_MIN_INTERVAL_SECONDS)
        ):
            return None
        if outbox.status != SystemOutboxStatus.RETRY_WAIT:
            self._transition_to_retry_wait(outbox)
        outbox.blocked_by_reconciliation_session_id = None
        outbox.blocked_device_id = blocked_device_id
        outbox.blocked_workline_id = blocked_workline_id or outbox.workline_id
        outbox.blocked_reason = reason
        outbox.last_error = last_error or reason
        outbox.next_retry_at = None
        outbox.lease_expires_at = None
        outbox.finished_at = None
        if outbox.blocked_at is None:
            outbox.blocked_at = now
        outbox.last_blocked_check_at = now
        outbox.blocked_check_count = (outbox.blocked_check_count or 0) + 1
        next_detail = dict(detail or {})
        if existing_detail.get("last_probe_result") == "escalated" and existing_detail.get("diagnostic_key"):
            next_detail.update(
                {
                    "last_probe_result": "escalated",
                    "escalated_at": existing_detail.get("escalated_at"),
                    "diagnostic_key": existing_detail.get("diagnostic_key"),
                }
            )
        outbox.blocked_detail_json = next_detail
        await db.flush()
        return outbox

    async def get_dispatching_device_messages(
        self,
        db: AsyncSession,
        limit: int = 50,
        *,
        operation_domains: Sequence[str] | None = None,
        exclude_operation_domains: Sequence[str] | None = None,
    ) -> list[SystemOutbox]:
        columns = cast("Any", SystemOutbox).__table__.c
        domain_predicates = self._operation_domain_predicates(
            columns,
            operation_domains=operation_domains,
            exclude_operation_domains=exclude_operation_domains,
        )
        result = await db.execute(
            select(SystemOutbox)
            .where(
                columns.status == SystemOutboxStatus.DISPATCHING,
                columns.dispatch_type == SystemOutboxDispatchType.DEVICE_COMMAND,
                columns.sent_at.is_(None),
                columns.finished_at.is_(None),
                *domain_predicates,
            )
            .order_by(columns.created_at.asc(), columns.id.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_active_external_station_dispatch(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        position_code: str,
    ) -> SystemOutbox | None:
        """查询占用单层 Station 的 active 外部派发 lease。"""

        columns = cast("Any", SystemOutbox).__table__.c
        active_statuses = [
            SystemOutboxStatus.NEW,
            SystemOutboxStatus.DISPATCHING,
            SystemOutboxStatus.SENT,
            SystemOutboxStatus.RETRY_WAIT,
        ]
        payload = columns.payload_json
        result = await db.execute(
            select(SystemOutbox)
            .where(
                columns.workline_id == workline_id,
                columns.dispatch_type == SystemOutboxDispatchType.EXTERNAL_HTTP,
                columns.status.in_(active_statuses),
                columns.finished_at.is_(None),
                or_(
                    payload["station"]["position_code"].as_string() == position_code,
                    payload["position_code"].as_string() == position_code,
                    payload["source"]["position_code"].as_string() == position_code,
                    payload["source_position_code"].as_string() == position_code,
                    payload["target_position_code"].as_string() == position_code,
                    payload["rack_operation"]["target_position_code"].as_string() == position_code,
                    payload["rack_operation"]["work_position_code"].as_string() == position_code,
                ),
            )
            .order_by(columns.created_at.asc(), columns.id.asc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_blocked_device_busy_messages(
        self,
        db: AsyncSession,
        limit: int = 50,
        *,
        operation_domains: Sequence[str] | None = None,
        exclude_operation_domains: Sequence[str] | None = None,
    ) -> list[SystemOutbox]:
        columns = cast("Any", SystemOutbox).__table__.c
        domain_predicates = self._operation_domain_predicates(
            columns,
            operation_domains=operation_domains,
            exclude_operation_domains=exclude_operation_domains,
        )
        result = await db.execute(
            select(SystemOutbox)
            .where(
                system_outbox_resource_wait_clause(columns),
                columns.blocked_reason == "DEVICE_BUSY",
                *domain_predicates,
            )
            .order_by(columns.created_at.asc(), columns.id.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def cancel_active_by_workline(
        self, db: AsyncSession, workline_id: int, *, incident_id: int
    ) -> tuple[CancelledSystemOutbox, ...]:
        columns = cast("Any", SystemOutbox).__table__.c
        return await self._cancel_active(
            db,
            scope_clause=columns.workline_id == workline_id,
            reason=f"CANCELLED_BY_ESTOP:incident_id={incident_id}",
        )

    async def cancel_active_by_session(
        self, db: AsyncSession, *, session_id: int, reason: str
    ) -> tuple[CancelledSystemOutbox, ...]:
        columns = cast("Any", SystemOutbox).__table__.c
        return await self._cancel_active(
            db,
            scope_clause=columns.session_id == session_id,
            reason=reason,
        )

    async def _cancel_active(
        self,
        db: AsyncSession,
        *,
        scope_clause: Any,
        reason: str,
    ) -> tuple[CancelledSystemOutbox, ...]:
        columns = cast("Any", SystemOutbox).__table__.c
        active_statuses = [
            SystemOutboxStatus.NEW,
            SystemOutboxStatus.DISPATCHING,
            SystemOutboxStatus.RETRY_WAIT,
            SystemOutboxStatus.SENT,
        ]
        result = await db.execute(
            select(SystemOutbox)
            .where(
                scope_clause,
                columns.status.in_(active_statuses),
                columns.finished_at.is_(None),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        outboxes = list(result.scalars().all())
        now = timezone.now_for_db()
        await self._finalize_cancelled_dispatching_attempts(db, outboxes=outboxes, reason=reason, now=now)
        cancelled: list[CancelledSystemOutbox] = []
        for outbox in outboxes:
            previous_status = SystemOutboxStatus(outbox.status)
            # SENT 已证明 transport 接受，不能伪造为未发送；这里只封存等待 callback 的 EFFECT，
            # 后续由同事务 reducer 将配对 intent 推入 reconciliation。
            dispatch_started = (
                previous_status is SystemOutboxStatus.DISPATCHING and outbox.dispatch_started_at is not None
            )
            if dispatch_started:
                transition_system_outbox(outbox, SystemOutboxStatus.UNKNOWN)
            elif previous_status is not SystemOutboxStatus.SENT:
                transition_system_outbox(outbox, SystemOutboxStatus.CANCELLED)
            outbox.last_error = reason
            # 保留最近 owner token 供审计，离开 DISPATCHING 时释放有限 lease。
            outbox.lease_expires_at = None
            outbox.finished_at = now
            cancelled.append(
                CancelledSystemOutbox(
                    dispatch_key=outbox.dispatch_key,
                    previous_status=previous_status,
                )
            )
        return tuple(cancelled)

    @staticmethod
    async def _finalize_cancelled_dispatching_attempts(
        db: AsyncSession,
        *,
        outboxes: Sequence[SystemOutbox],
        reason: str,
        now: datetime,
    ) -> None:
        dispatching_ids = tuple(
            outbox.id
            for outbox in outboxes
            if outbox.id is not None and SystemOutboxStatus(outbox.status) is SystemOutboxStatus.DISPATCHING
        )
        if not dispatching_ids:
            return
        from src.app.runtime.orchestration.models.dispatch_attempt import WorklineDispatchAttempt

        columns = cast("Any", WorklineDispatchAttempt).__table__.c
        result = await db.execute(
            select(WorklineDispatchAttempt)
            .where(
                columns.outbox_id.in_(dispatching_ids),
                columns.status == DispatchAttemptStatus.DISPATCHING,
            )
            .with_for_update()
        )
        for attempt in result.scalars().all():
            outbox = next(item for item in outboxes if item.id == attempt.outbox_id)
            target = (
                DispatchAttemptStatus.UNKNOWN
                if getattr(outbox, "dispatch_started_at", None) is not None
                else DispatchAttemptStatus.CANCELLED
            )
            transition_dispatch_attempt(attempt, target)
            attempt.finalized_at = now
            attempt.error_message = reason

    async def release_blocked_by_reconciliation_session(self, db: AsyncSession, owner_session_id: int) -> int:
        columns = cast("Any", SystemOutbox).__table__.c
        return await self._release_blocked(
            db,
            columns.blocked_by_reconciliation_session_id == owner_session_id,
        )

    async def release_blocked_by_runtime_hold(self, db: AsyncSession, runtime_hold_id: int) -> int:
        columns = cast("Any", SystemOutbox).__table__.c
        return await self._release_blocked(db, columns.blocked_by_runtime_hold_id == runtime_hold_id)

    async def release_blocked_by_runtime_hold_or_workline(
        self,
        db: AsyncSession,
        *,
        runtime_hold_id: int,
        workline_id: int,
        release_workline_scope: bool,
    ) -> int:
        released_count = await self.release_blocked_by_runtime_hold(db, runtime_hold_id)
        if release_workline_scope:
            released_count += await self.release_blocked_by_workline(db, workline_id)
        return released_count

    async def park_blocked_by_runtime_hold_until_start(
        self,
        db: AsyncSession,
        *,
        runtime_hold_id: int,
        workline_id: int,
    ) -> int:
        columns = cast("Any", SystemOutbox).__table__.c
        result = await db.execute(
            select(SystemOutbox)
            .where(
                columns.status == SystemOutboxStatus.RETRY_WAIT,
                columns.blocked_by_runtime_hold_id == runtime_hold_id,
                columns.workline_id == workline_id,
            )
            .with_for_update()
        )
        outboxes = list(result.scalars().all())
        now = timezone.now_for_db()
        for outbox in outboxes:
            outbox.blocked_by_runtime_hold_id = None
            outbox.blocked_by_reconciliation_session_id = None
            outbox.blocked_workline_id = workline_id
            outbox.blocked_reason = "WORKLINE_STOPPED_WAITING_START"
            outbox.last_error = "WORKLINE_STOPPED_WAITING_START"
            outbox.next_retry_at = None
            outbox.finished_at = now
        await db.flush()
        return len(outboxes)

    async def release_blocked_by_workline(self, db: AsyncSession, workline_id: int) -> int:
        columns = cast("Any", SystemOutbox).__table__.c
        return await self._release_blocked(
            db,
            columns.workline_id == workline_id,
            columns.blocked_reason.notin_(self.DEVICE_RESOURCE_WAIT_REASONS),
            columns.blocked_by_runtime_hold_id.is_(None),
            or_(
                columns.blocked_workline_id == workline_id,
                columns.blocked_by_reconciliation_session_id.isnot(None),
            ),
        )

    async def release_parked_after_workline_start(self, db: AsyncSession, workline_id: int) -> int:
        columns = cast("Any", SystemOutbox).__table__.c
        return await self._release_blocked(
            db,
            columns.workline_id == workline_id,
            columns.blocked_reason.notin_(self.DEVICE_RESOURCE_WAIT_REASONS),
            or_(
                columns.blocked_workline_id == workline_id,
                columns.blocked_by_reconciliation_session_id.isnot(None),
            ),
        )

    async def release_blocked_by_device(
        self,
        db: AsyncSession,
        *,
        device_id: int,
        workline_id: int | None = None,
    ) -> int:
        _ = (db, device_id, workline_id)
        return 0

    async def get_sandbox_pending_messages(
        self,
        db: AsyncSession,
        limit: int = 50,
        workline_id: int | None = None,
        device_id: int | None = None,
    ) -> list[SystemOutbox]:
        from src.app.device.models import Device
        from src.app.runtime.orchestration.models.session import RunMode, SessionStatus, WorklineSession

        columns = cast("Any", SystemOutbox).__table__.c
        session_columns = cast("Any", WorklineSession).__table__.c
        open_session_statuses = [
            SessionStatus.NEW,
            SessionStatus.RUNNING,
            SessionStatus.WAITING_DEVICE_RESULT,
            SessionStatus.WAITING_EXTERNAL,
            SessionStatus.MANUAL_HOLD,
        ]
        query = (
            select(SystemOutbox)
            .join(WorklineSession, columns.session_id == session_columns.id)
            .where(
                session_columns.run_mode == RunMode.SIMULATION,
                session_columns.status.in_(open_session_statuses),
                columns.dispatch_type.in_(
                    [SystemOutboxDispatchType.DEVICE_COMMAND, SystemOutboxDispatchType.EXTERNAL_HTTP]
                ),
                columns.status.in_(
                    [
                        SystemOutboxStatus.NEW,
                        SystemOutboxStatus.DISPATCHING,
                        SystemOutboxStatus.SENT,
                        SystemOutboxStatus.RETRY_WAIT,
                        SystemOutboxStatus.FAILED,
                        SystemOutboxStatus.UNKNOWN,
                    ]
                ),
            )
        )
        if workline_id is not None:
            query = query.where(columns.workline_id == workline_id)
        if device_id is not None:
            device_columns = cast("Any", Device).__table__.c
            query = query.join(Device, columns.target_code == device_columns.device_code).where(
                device_columns.id == device_id
            )
        result = await db.execute(query.order_by(columns.created_at.asc()).limit(limit))
        return list(result.scalars().all())

    async def get_sandbox_completed_messages(
        self,
        db: AsyncSession,
        *,
        inbox_query: RuntimeInboxQueryPort,
        limit: int = 50,
        workline_id: int | None = None,
        device_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """获取沙箱已完成 outbox，按 Session 分组。"""

        from src.app.device.models import Device
        from src.app.runtime.orchestration.models.session import RunMode, SessionStatus, WorklineSession

        columns = cast("Any", SystemOutbox).__table__.c
        session_columns = cast("Any", WorklineSession).__table__.c
        query = (
            select(SystemOutbox, WorklineSession)
            .join(WorklineSession, columns.session_id == session_columns.id)
            .where(
                session_columns.run_mode == RunMode.SIMULATION,
                session_columns.status.in_([SessionStatus.COMPLETED, SessionStatus.FAILED, SessionStatus.CANCELLED]),
                columns.status.in_([SystemOutboxStatus.SENT, SystemOutboxStatus.CANCELLED, SystemOutboxStatus.FAILED]),
                columns.dispatch_type.in_(
                    [SystemOutboxDispatchType.DEVICE_COMMAND, SystemOutboxDispatchType.EXTERNAL_HTTP]
                ),
            )
        )
        if workline_id is not None:
            query = query.where(columns.workline_id == workline_id)
        if device_id is not None:
            device_columns = cast("Any", Device).__table__.c
            query = query.join(Device, columns.target_code == device_columns.device_code).where(
                device_columns.id == device_id
            )
        result = await db.execute(
            query.order_by(session_columns.created_at.desc(), columns.created_at.asc()).limit(limit * 3)
        )
        rows = result.all()
        session_refs = sorted({session.id for _outbox, session in rows if isinstance(session.id, int)})
        latest_inboxes = await inbox_query.latest_by_workline_session_refs(
            db,
            workline_session_refs=session_refs,
            kind="DEVICE_EVENT",
        )
        sessions: dict[int, dict[str, Any]] = {}
        for outbox, session in rows:
            sid = session.id
            inbox = latest_inboxes.get(sid)
            if sid not in sessions:
                event_payload: dict[str, Any] | None = None
                event_type: str | None = None
                if inbox is not None and isinstance(inbox.payload_json, dict):
                    event_payload = dict(inbox.payload_json)
                    event_type = inbox.payload_json.get("event_type")
                sessions[sid] = {
                    "history_group_key": f"session:{sid}",
                    "session": {
                        "id": session.id,
                        "session_code": session.session_code,
                        "status": session.status.value if hasattr(session.status, "value") else session.status,
                        "awaiting_device_command_code": session.awaiting_device_command_code,
                        "barcode": session.barcode,
                        "created_at": session.created_at.isoformat() if session.created_at else None,
                        "started_at": session.started_at.isoformat() if session.started_at else None,
                        "ended_at": session.ended_at.isoformat() if session.ended_at else None,
                        "event_type": event_type,
                        "event_payload": event_payload,
                        "failure_domain": session.failure_domain,
                        "failure_code": session.failure_code,
                        "failure_message": session.failure_message,
                    },
                    "outbox_items": [],
                }
            payload = dict(outbox.payload_json) if isinstance(outbox.payload_json, dict) else {}
            sessions[sid]["outbox_items"].append(
                {
                    "id": outbox.id,
                    "session_id": outbox.session_id,
                    "workline_id": outbox.workline_id,
                    "dispatch_key": outbox.dispatch_key,
                    "dispatch_type": enum_value(outbox.dispatch_type),
                    "target_type": enum_value(outbox.target_type),
                    "target_code": outbox.target_code,
                    "status": enum_value(outbox.status),
                    "last_error": outbox.last_error,
                    "is_actionable": False,
                    "runtime_hold_id": outbox.blocked_by_runtime_hold_id,
                    "payload_json": payload,
                    "source_device": None,
                    "failure_summary": {
                        "code": session.failure_code or outbox.last_error,
                        "message": session.failure_message or outbox.last_error,
                        "runtime_hold_id": outbox.blocked_by_runtime_hold_id,
                    },
                    "history_group_key": f"session:{sid}",
                }
            )
        return list(sessions.values())[:limit]

    async def _block_or_fail(
        self,
        db: AsyncSession,
        outbox_id: int,
        *,
        status: SystemOutboxStatus,
        reason: str,
        lease_owner_token: str | None,
    ) -> SystemOutbox | None:
        outbox = await self._get_active_for_block(db, outbox_id, lease_owner_token=lease_owner_token)
        if outbox is None:
            return None
        transition_system_outbox(outbox, status)
        outbox.last_error = reason
        outbox.next_retry_at = None
        outbox.lease_expires_at = None
        outbox.finished_at = timezone.now_for_db()
        await db.flush()
        return outbox

    async def _get_active_for_block(
        self,
        db: AsyncSession,
        outbox_id: int,
        *,
        lease_owner_token: str | None,
    ) -> SystemOutbox | None:
        columns = cast("Any", SystemOutbox).__table__.c
        result = await db.execute(select(SystemOutbox).where(columns.id == outbox_id).with_for_update())
        outbox = result.scalar_one_or_none()
        if outbox is None:
            return None
        if outbox.status == SystemOutboxStatus.NEW and lease_owner_token is None:
            return outbox
        if (
            outbox.status == SystemOutboxStatus.DISPATCHING
            and isinstance(lease_owner_token, str)
            and outbox.lease_owner_token == lease_owner_token
            and outbox.lease_expires_at is not None
            and outbox.lease_expires_at > timezone.now_for_db()
        ):
            return outbox
        return None

    async def _get_active_for_resource_wait(
        self,
        db: AsyncSession,
        outbox_id: int,
        *,
        reason: str,
        lease_owner_token: str | None,
    ) -> SystemOutbox | None:
        columns = cast("Any", SystemOutbox).__table__.c
        result = await db.execute(select(SystemOutbox).where(columns.id == outbox_id).with_for_update())
        outbox = result.scalar_one_or_none()
        if outbox is None:
            return None
        if outbox.status == SystemOutboxStatus.NEW and lease_owner_token is None:
            return outbox
        if (
            outbox.status == SystemOutboxStatus.DISPATCHING
            and isinstance(lease_owner_token, str)
            and outbox.lease_owner_token == lease_owner_token
            and outbox.lease_expires_at is not None
            and outbox.lease_expires_at > timezone.now_for_db()
        ):
            return outbox
        if (
            outbox.status == SystemOutboxStatus.RETRY_WAIT
            and outbox.blocked_reason in self.DEVICE_RESOURCE_WAIT_REASONS
            and reason in self.DEVICE_RESOURCE_WAIT_REASONS
        ):
            return outbox
        return None

    async def _release_blocked(self, db: AsyncSession, *conditions: Any) -> int:
        columns = cast("Any", SystemOutbox).__table__.c
        result = await db.execute(
            select(SystemOutbox).where(columns.status == SystemOutboxStatus.RETRY_WAIT, *conditions).with_for_update()
        )
        outboxes = list(result.scalars().all())
        for outbox in outboxes:
            self._release_blocked_outbox(outbox)
        await db.flush()
        return len(outboxes)

    @staticmethod
    def _release_blocked_outbox(outbox: SystemOutbox) -> None:
        outbox.attempt_count = 0
        SystemOutboxRepository._clear_block(outbox)
        outbox.next_retry_at = timezone.now_for_db()

    @staticmethod
    def _transition_to_retry_wait(outbox: SystemOutbox) -> None:
        """把未发送 outbox 经由合法 ATTEMPT_STARTED 边进入 RETRY_WAIT。"""

        if outbox.status == SystemOutboxStatus.NEW:
            transition_system_outbox(outbox, SystemOutboxStatus.DISPATCHING)
        transition_system_outbox(outbox, SystemOutboxStatus.RETRY_WAIT)
        # 所有共享 block/park 路径离开 DISPATCHING 时都释放 expiry；保留 owner token 供审计。
        outbox.lease_expires_at = None
        outbox.dispatch_started_at = None

    @staticmethod
    def _clear_block(outbox: SystemOutbox) -> None:
        outbox.next_retry_at = None
        outbox.last_error = None
        outbox.finished_at = None
        outbox.blocked_by_runtime_hold_id = None
        outbox.blocked_by_reconciliation_session_id = None
        outbox.blocked_device_id = None
        outbox.blocked_workline_id = None
        outbox.blocked_reason = None
        outbox.blocked_at = None
        outbox.last_blocked_check_at = None
        outbox.blocked_check_count = 0
        outbox.blocked_detail_json = {}

    @staticmethod
    def _device_resolution_alias(name: str) -> Any:
        from src.app.device.models import Device

        return cast("Any", Device).__table__.alias(name)

    @staticmethod
    def _device_resolution_join_condition(outbox_columns: Any, device_alias: Any) -> Any:
        return and_(
            outbox_columns.device_id.is_(None),
            outbox_columns.target_code == device_alias.c.device_code,
            device_alias.c.is_deleted.is_(False),
        )

    @staticmethod
    def _same_physical_device_predicate(
        *,
        current_columns: Any,
        current_device: Any,
        other_columns: Any,
        other_device: Any,
    ) -> Any:
        current_device_id = func.coalesce(current_columns.device_id, current_device.c.id)
        other_device_id = func.coalesce(other_columns.device_id, other_device.c.id)
        return or_(
            and_(current_device_id.isnot(None), other_device_id == current_device_id),
            and_(
                current_device_id.is_(None),
                other_device_id.is_(None),
                other_columns.target_code == current_columns.target_code,
            ),
        )

    @staticmethod
    def _operation_domain_predicates(
        columns: Any,
        *,
        operation_domains: Sequence[str] | None,
        exclude_operation_domains: Sequence[str] | None,
    ) -> list[Any]:
        domain_predicates: list[Any] = []
        if operation_domains:
            domain_predicates.append(columns.operation_domain.in_(tuple(operation_domains)))
        if exclude_operation_domains:
            domain_predicates.append(columns.operation_domain.not_in(tuple(exclude_operation_domains)))
        return domain_predicates

    @staticmethod
    def _operation_identity_predicates(
        columns: Any,
        *,
        operation_identities: Sequence[str] | None,
        exclude_operation_identities: Sequence[str] | None,
    ) -> list[Any]:
        """在既有 operation_identity 索引列上构造显式 include/exclude scope。"""

        identity_predicates: list[Any] = []
        if operation_identities:
            identity_predicates.append(columns.operation_identity.in_(tuple(operation_identities)))
        if exclude_operation_identities:
            identity_predicates.append(columns.operation_identity.not_in(tuple(exclude_operation_identities)))
        return identity_predicates


system_outbox_repository = SystemOutboxRepository()
outbox_repository = system_outbox_repository

__all__ = [
    "CancelledSystemOutbox",
    "ExpiredExternalHttpLeaseFence",
    "SystemOutboxRepository",
    "outbox_repository",
    "system_outbox_repository",
]
