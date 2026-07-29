"""北向 operation 只读聚合 Repository。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, cast

from sqlalchemy import case, column, func, select, table, tuple_

from src.app.runtime.orchestration.operation_observability import NORTHBOUND_OPERATION_SLO_CATALOG
from src.app.runtime.orchestration.reconciliation_case import ReconciliationCase, ReconciliationCaseStatus
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog
from src.app.sys.models.outbox import SystemOutbox, SystemOutboxStatus
from src.app.workline.models import WorkLine
from src.database.schema_conf import SchemaType
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from src.app.runtime.system_capabilities.wms.provider_catalog import WmsProviderCatalog

_WMS_CALL_EVIDENCE = table(
    "wms_call_evidence",
    column("provider_profile_identity"),
    column("operation_name"),
    schema=SchemaType.BIZ.value,
)


@dataclass(frozen=True, slots=True)
class NorthboundOperationHealthRow:
    """数据库聚合后的安全运维行。"""

    provider_profile_identity: str
    operation_identity: str
    mode: Literal["QUERY", "EFFECT"]
    backlog_count: int
    active_lease_count: int
    unknown_count: int
    oldest_queue_age_seconds: int
    rate_limited_count: int
    lease_loss_count: int
    reconciliation_open_count: int


def _active_catalog_operations(
    catalog: WmsProviderCatalog,
) -> tuple[tuple[str, str, Literal["QUERY", "EFFECT"]], ...]:
    """从当前运行环境的 provider binding 生成空账本运维基线和 operation mode。"""

    return tuple(
        (
            catalog.profile_identity,
            binding.operation.identity,
            cast("Literal['QUERY', 'EFFECT']", binding.operation.mode.value),
        )
        for binding in catalog.bindings
        if binding.operation.identity in NORTHBOUND_OPERATION_SLO_CATALOG
    )


class NorthboundOperationsRepository:
    """只从 typed columns 聚合 SLI；不读取 payload、header、trace 或 secret ref。"""

    def __init__(self, *, provider_catalog: WmsProviderCatalog | None = None) -> None:
        self._provider_catalog = provider_catalog

    def bind_provider_catalog(self, provider_catalog: WmsProviderCatalog) -> None:
        """由启动 composition root 注入本进程唯一 compiled catalog。"""

        self._provider_catalog = provider_catalog

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
        keys = tuple((str(row[0]), str(row[1])) for row in aggregate_rows)
        # 同步 QUERY 没有 outbox；平台视图从 typed evidence 补充 operation identity。
        # owner/workline 视图仍只使用可归属的 outbox keys，避免泄漏无 workline 归属的全局证据。
        evidence_keys = (
            await self._load_sync_query_evidence_keys(db) if tenant_id is None and workline_id is None else ()
        )
        if self._provider_catalog is None:
            raise RuntimeError("compiled WMS provider catalog must be injected before northbound queries")
        catalog_operations = _active_catalog_operations(self._provider_catalog)
        operation_modes: dict[str, Literal["QUERY", "EFFECT"]] = {
            operation_identity: mode for _, operation_identity, mode in catalog_operations
        }
        visible_keys = tuple(sorted(set(keys) | set(evidence_keys)))
        if not visible_keys and tenant_id is None and workline_id is None:
            visible_keys = tuple(sorted((profile, operation) for profile, operation, _ in catalog_operations))
        if not visible_keys:
            return ()
        reconciliation_counts = (
            await self._load_reconciliation_counts(
                db,
                tenant_id=tenant_id,
                workline_id=workline_id,
                keys=keys,
            )
            if keys
            else {}
        )
        aggregates = {(str(row[0]), str(row[1])): row for row in aggregate_rows}
        rows: list[NorthboundOperationHealthRow] = []
        for key in visible_keys:
            provider_profile_identity, operation_identity = key
            row = aggregates.get(key)
            oldest_created_at = row[5] if row is not None else None
            oldest_queue_age_seconds = (
                max(0, int((now - oldest_created_at).total_seconds())) if oldest_created_at is not None else 0
            )
            rows.append(
                NorthboundOperationHealthRow(
                    provider_profile_identity=provider_profile_identity,
                    operation_identity=operation_identity,
                    mode=operation_modes[operation_identity],
                    backlog_count=int(row[2] or 0) if row is not None else 0,
                    active_lease_count=int(row[3] or 0) if row is not None else 0,
                    unknown_count=int(row[4] or 0) if row is not None else 0,
                    oldest_queue_age_seconds=oldest_queue_age_seconds,
                    # 实时 rate-limit 命中由 dispatcher signal 提供；读模型不推导策略。
                    rate_limited_count=0,
                    lease_loss_count=int(row[6] or 0) if row is not None else 0,
                    reconciliation_open_count=reconciliation_counts.get(key, 0),
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

    async def _load_sync_query_evidence_keys(self, db: Any) -> tuple[tuple[str, str], ...]:
        evidence_columns = _WMS_CALL_EVIDENCE.c
        result = await db.execute(
            select(
                evidence_columns.provider_profile_identity,
                evidence_columns.operation_name,
            )
            .where(
                evidence_columns.provider_profile_identity.is_not(None),
                evidence_columns.operation_name.in_(tuple(NORTHBOUND_OPERATION_SLO_CATALOG)),
            )
            .distinct()
            .order_by(
                evidence_columns.provider_profile_identity,
                evidence_columns.operation_name,
            )
        )
        return tuple((str(row[0]), str(row[1])) for row in result.all())


northbound_operations_repository = NorthboundOperationsRepository()


__all__ = [
    "NorthboundOperationHealthRow",
    "NorthboundOperationsRepository",
    "northbound_operations_repository",
]
