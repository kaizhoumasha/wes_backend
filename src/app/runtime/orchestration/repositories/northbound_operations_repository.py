"""北向 operation 只读聚合 Repository。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import case, func, select, tuple_

from src.app.runtime.orchestration.operation_observability import NORTHBOUND_OPERATION_SLO_CATALOG
from src.app.runtime.orchestration.reconciliation_case import ReconciliationCase, ReconciliationCaseStatus
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog
from src.app.runtime.system_capabilities.shadow_models import QueryShadowReadinessReportRecord
from src.app.sys.models.outbox import SystemOutbox, SystemOutboxStatus
from src.app.workline.models import WorkLine
from src.utils.timezone import timezone


@dataclass(frozen=True, slots=True)
class NorthboundOperationHealthRow:
    """数据库聚合后的安全运维行。"""

    provider_profile_identity: str
    operation_identity: str
    backlog_count: int
    active_lease_count: int
    unknown_count: int
    oldest_queue_age_seconds: int
    rate_limited_count: int
    lease_loss_count: int
    reconciliation_open_count: int
    readiness: str


class NorthboundOperationsRepository:
    """只从 typed columns 聚合 SLI；不读取 payload、header、trace 或 secret ref。"""

    async def workline_is_owned_by(self, db: Any, *, workline_id: int, tenant_id: int) -> bool:
        workline_columns = cast("Any", WorkLine).__table__.c
        owned = await db.scalar(
            select(func.count())
            .select_from(WorkLine)
            .where(
                workline_columns.id == workline_id,
                workline_columns.created_by == tenant_id,
            )
        )
        return bool(owned)

    async def load_snapshot(
        self,
        db: Any,
        *,
        tenant_id: int | None,
        workline_id: int | None,
    ) -> tuple[NorthboundOperationHealthRow, ...]:
        now = timezone.now_for_db()
        outbox_columns = cast("Any", SystemOutbox).__table__.c
        workline_columns = cast("Any", WorkLine).__table__.c
        scope_filters = [
            outbox_columns.operation_identity.in_(tuple(NORTHBOUND_OPERATION_SLO_CATALOG)),
            outbox_columns.workline_id.is_not(None),
        ]
        if tenant_id is not None:
            scope_filters.append(workline_columns.created_by == tenant_id)
        if workline_id is not None:
            scope_filters.append(outbox_columns.workline_id == workline_id)

        backlog_predicate = outbox_columns.status.in_((SystemOutboxStatus.NEW, SystemOutboxStatus.RETRY_WAIT))
        result = await db.execute(
            select(
                outbox_columns.provider_profile_identity,
                outbox_columns.operation_identity,
                func.count().filter(backlog_predicate).label("backlog_count"),
                func.count()
                .filter(
                    outbox_columns.status == SystemOutboxStatus.DISPATCHING,
                    outbox_columns.lease_expires_at > now,
                )
                .label("active_lease_count"),
                func.count().filter(outbox_columns.status == SystemOutboxStatus.UNKNOWN).label("unknown_count"),
                func.min(case((backlog_predicate, outbox_columns.created_at), else_=None)).label("oldest_created_at"),
                func.count()
                .filter(
                    outbox_columns.status == SystemOutboxStatus.UNKNOWN,
                    outbox_columns.last_error.like("STALE_EXTERNAL_HTTP_DISPATCH_LEASE_EXPIRED:%"),
                )
                .label("lease_loss_count"),
            )
            .select_from(SystemOutbox)
            .join(WorkLine, workline_columns.id == outbox_columns.workline_id)
            .where(*scope_filters)
            .group_by(outbox_columns.provider_profile_identity, outbox_columns.operation_identity)
            .order_by(outbox_columns.provider_profile_identity, outbox_columns.operation_identity)
        )
        aggregate_rows = tuple(result.all())
        if not aggregate_rows:
            return ()

        keys = tuple((str(row[0]), str(row[1])) for row in aggregate_rows)
        reconciliation_counts = await self._load_reconciliation_counts(
            db,
            tenant_id=tenant_id,
            workline_id=workline_id,
            keys=keys,
        )
        readiness = await self._load_readiness(db, keys=keys)
        rows: list[NorthboundOperationHealthRow] = []
        for row in aggregate_rows:
            provider_profile_identity = str(row[0])
            operation_identity = str(row[1])
            oldest_created_at = row[5]
            oldest_queue_age_seconds = (
                max(0, int((now - oldest_created_at).total_seconds())) if oldest_created_at is not None else 0
            )
            key = (provider_profile_identity, operation_identity)
            rows.append(
                NorthboundOperationHealthRow(
                    provider_profile_identity=provider_profile_identity,
                    operation_identity=operation_identity,
                    backlog_count=int(row[2] or 0),
                    active_lease_count=int(row[3] or 0),
                    unknown_count=int(row[4] or 0),
                    oldest_queue_age_seconds=oldest_queue_age_seconds,
                    # 实时 rate-limit 命中由 dispatcher signal 提供；读模型不推导策略。
                    rate_limited_count=0,
                    lease_loss_count=int(row[6] or 0),
                    reconciliation_open_count=reconciliation_counts.get(key, 0),
                    readiness=readiness.get(key, "UNKNOWN"),
                )
            )
        return tuple(rows)

    async def _load_reconciliation_counts(
        self,
        db: Any,
        *,
        tenant_id: int | None,
        workline_id: int | None,
        keys: tuple[tuple[str, str], ...],
    ) -> dict[tuple[str, str], int]:
        reconciliation_columns = cast("Any", ReconciliationCase).__table__.c
        intent_columns = cast("Any", RuntimeIntentLog).__table__.c
        outbox_columns = cast("Any", SystemOutbox).__table__.c
        workline_columns = cast("Any", WorkLine).__table__.c
        filters = [
            tuple_(outbox_columns.provider_profile_identity, outbox_columns.operation_identity).in_(keys),
            reconciliation_columns.status == ReconciliationCaseStatus.OPEN,
            outbox_columns.workline_id.is_not(None),
        ]
        if tenant_id is not None:
            filters.append(workline_columns.created_by == tenant_id)
        if workline_id is not None:
            filters.append(outbox_columns.workline_id == workline_id)
        result = await db.execute(
            select(
                outbox_columns.provider_profile_identity,
                outbox_columns.operation_identity,
                func.count(),
            )
            .select_from(ReconciliationCase)
            .join(RuntimeIntentLog, intent_columns.id == reconciliation_columns.runtime_intent_log_id)
            .join(SystemOutbox, outbox_columns.dispatch_key == intent_columns.dispatch_key)
            .join(WorkLine, workline_columns.id == outbox_columns.workline_id)
            .where(*filters)
            .group_by(outbox_columns.provider_profile_identity, outbox_columns.operation_identity)
        )
        return {(str(row[0]), str(row[1])): int(row[2] or 0) for row in result.all()}

    async def _load_readiness(
        self,
        db: Any,
        *,
        keys: tuple[tuple[str, str], ...],
    ) -> dict[tuple[str, str], str]:
        report_columns = cast("Any", QueryShadowReadinessReportRecord).__table__.c
        result = await db.execute(
            select(
                report_columns.provider_profile_identity,
                report_columns.operation_identity,
                report_columns.verdict,
                report_columns.generated_at,
                report_columns.report_id,
            )
            .where(
                tuple_(
                    report_columns.provider_profile_identity,
                    report_columns.operation_identity,
                ).in_(keys)
            )
            .order_by(
                report_columns.provider_profile_identity,
                report_columns.operation_identity,
                report_columns.generated_at.desc(),
                report_columns.report_id.desc(),
            )
        )
        latest: dict[tuple[str, str], str] = {}
        for row in result.all():
            key = (str(row[0]), str(row[1]))
            latest.setdefault(key, str(row[2]))
        return latest


northbound_operations_repository = NorthboundOperationsRepository()


__all__ = [
    "NorthboundOperationHealthRow",
    "NorthboundOperationsRepository",
    "northbound_operations_repository",
]
