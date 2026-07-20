"""RuntimeInbox 跨域只读 Repository 实现。"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import BigInteger, case, exists, func, literal, select, tuple_, update

from src.app.contracts.runtime_inbox_query import (
    RUNTIME_INBOX_UNFINISHED_STATUSES,
    RuntimeInboxEvidence,
    RuntimeInboxProjection,
    RuntimeInboxWorkloadSample,
)
from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation
from src.app.runtime.orchestration.runtime_inbox import PRE_CUTOVER_AUDIT_ONLY, RuntimeInbox
from src.core.conf import settings
from src.core.logger import logger
from src.database.base_repository import BaseRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class RuntimeInboxPayloadTooLarge(Exception):
    """canonical payload 的 UTF-8 JSON bytes 超过 RuntimeInbox 持久化上限。"""

    status_code = 413

    def __init__(self, *, actual_bytes: int, max_bytes: int) -> None:
        super().__init__(f"runtime inbox payload too large: actual_bytes={actual_bytes}, max_bytes={max_bytes}")
        self.actual_bytes = actual_bytes
        self.max_bytes = max_bytes


@dataclass(frozen=True, slots=True)
class RuntimeInboxSliSnapshot:
    """RuntimeInbox 当前状态与可推进 backlog 的数据库 SLI 快照。"""

    status_counts: dict[str, int]
    oldest_claimable_age_ms: int | None
    stale_processing_count: int
    resource_wait_count: int


@dataclass(frozen=True, slots=True)
class RuntimeInboxRetryMetadata:
    """失败状态判定所需的最小重试元数据。"""

    attempt_count: int
    max_retries: int


@dataclass(frozen=True, slots=True)
class RuntimeInboxManualHoldEvidence:
    """当前 Session 最新 Timeline 及其关联 Inbox 的最小重放证据。"""

    session_id: int
    action_type: str
    timeline_status: str
    reason_code: str | None
    related_inbox_id: int | None
    source_session_id: int | None
    source_status: str | None


def _emit_runtime_inbox_sli(name: str, attributes: dict[str, object]) -> None:
    """观测链路失败不得改变 Inbox 事务结果。"""

    from src.app.runtime.orchestration.observability import runtime_observability_registry

    try:
        _ = runtime_observability_registry.emit(name, attributes)
    except Exception as exc:  # pragma: no cover - 观测 backend 故障不反向影响事实源
        logger.warning(f"RuntimeInbox SLI 发射失败: signal={name}, error={exc}")


def validate_canonical_payload_size(payload_json: object) -> None:
    """在 db.add/flush/ACK 前校验 canonical JSON 的 UTF-8 bytes。"""

    if not isinstance(payload_json, dict):
        raise TypeError("runtime inbox payload_json must be a dict")
    encoded = json.dumps(
        payload_json,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    max_bytes = settings.runtime_inbox_payload_max_bytes
    if len(encoded) > max_bytes:
        raise RuntimeInboxPayloadTooLarge(actual_bytes=len(encoded), max_bytes=max_bytes)


class RuntimeInboxRepository(BaseRepository[RuntimeInbox]):
    """集中持有 RuntimeInbox ORM，并向业务 repository 暴露 typed query DTO。"""

    def __init__(self) -> None:
        super().__init__(RuntimeInbox)

    @staticmethod
    def _to_projection(record: RuntimeInbox) -> RuntimeInboxProjection:
        if record.id is None:
            raise ValueError("RuntimeInbox projection requires persisted id")
        return RuntimeInboxProjection(
            id=record.id,
            kind=record.kind,
            provider_code=record.provider_code,
            event_type=record.event_type,
            source_event_id=record.source_event_id,
            payload_json=deepcopy(record.payload_json or {}),
            payload_hash=record.payload_hash,
            payload_schema_version=record.payload_schema_version,
            workline_session_ref=record.workline_session_id,
            execution_session_id=record.execution_session_id,
            workline_id=record.workline_id,
            device_id=record.device_id,
            command_id=record.command_id,
            correlation_id=record.correlation_id,
            trace_id=record.trace_id,
            event_id=record.event_id,
            causation_id=record.causation_id,
            status=record.status,
            attempt_count=record.attempt_count,
            max_retries=record.max_retries,
            next_retry_at=record.next_retry_at,
            received_at=record.received_at,
            processed_at=record.processed_at,
            failed_at=record.failed_at,
            last_error_code=record.last_error_code,
            last_error_message=record.last_error_message,
        )

    async def get_by_source_event_identity(
        self,
        db: AsyncSession,
        *,
        provider_code: str,
        event_type: str,
        source_event_id: str,
    ) -> RuntimeInbox | None:
        """按入站事件幂等身份读取 RuntimeInbox。"""

        columns = cast("Any", RuntimeInbox).__table__.c
        result = await db.execute(
            select(RuntimeInbox).where(
                columns.provider_code == provider_code,
                columns.event_type == event_type,
                columns.source_event_id == source_event_id,
            )
        )
        return result.scalar_one_or_none()

    async def resolve_unique_correlation_id_by_trace(self, db: AsyncSession, *, trace_id: str) -> str | None:
        """按 trace_id 查唯一关联；零条或多条命中均返回未关联。"""

        columns = cast("Any", ExecutionCorrelation).__table__.c
        correlation_ids = (
            (
                await db.execute(
                    select(columns.correlation_id).where(columns.trace_id == trace_id).order_by(columns.id).limit(2)
                )
            )
            .scalars()
            .all()
        )
        return str(correlation_ids[0]) if len(correlation_ids) == 1 else None

    async def resolve_unique_correlation_context_by_trace(
        self,
        db: AsyncSession,
        *,
        trace_id: str,
    ) -> tuple[str, int | None] | None:
        """按 trace 返回唯一 correlation 及其 runtime ExecutionSession 归属。"""

        columns = cast("Any", ExecutionCorrelation).__table__.c
        rows = (
            (
                await db.execute(
                    select(columns.correlation_id, columns.execution_session_id)
                    .where(columns.trace_id == trace_id)
                    .order_by(columns.id)
                    .limit(2)
                )
            )
            .tuples()
            .all()
        )
        if len(rows) != 1:
            return None
        correlation_id, execution_session_id = rows[0]
        return str(correlation_id), int(execution_session_id) if execution_session_id is not None else None

    async def resolve_correlation_context_by_id(
        self,
        db: AsyncSession,
        *,
        correlation_id: str,
    ) -> tuple[str, int | None] | None:
        """按权威 correlation 读取其 runtime ExecutionSession 归属。"""

        columns = cast("Any", ExecutionCorrelation).__table__.c
        row = (
            await db.execute(
                select(columns.correlation_id, columns.execution_session_id)
                .where(columns.correlation_id == correlation_id)
                .limit(1)
            )
        ).one_or_none()
        if row is None:
            return None
        correlation_id, execution_session_id = row
        return str(correlation_id), int(execution_session_id) if execution_session_id is not None else None

    async def correlation_id_exists(self, db: AsyncSession, *, correlation_id: str) -> bool:
        """检查 RuntimeInbox 外键目标是否已持久化。"""

        columns = cast("Any", ExecutionCorrelation).__table__.c
        result = await db.execute(
            select(columns.correlation_id).where(columns.correlation_id == correlation_id).limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def get_retry_metadata(self, db: AsyncSession, *, inbox_id: int) -> RuntimeInboxRetryMetadata | None:
        """读取失败状态判定所需的 attempt/max_retries。"""

        columns = cast("Any", RuntimeInbox).__table__.c
        row = (
            await db.execute(select(columns.attempt_count, columns.max_retries).where(columns.id == inbox_id).limit(1))
        ).one_or_none()
        if row is None:
            return None
        return RuntimeInboxRetryMetadata(
            attempt_count=int(row[0] or 0),
            max_retries=int(row[1] or 0),
        )

    async def get_by_id_for_update(
        self,
        db: AsyncSession,
        inbox_id: int,
        *,
        populate_existing: bool = False,
    ) -> RuntimeInbox | None:
        """锁定读取单条 RuntimeInbox，用于人工重放。"""

        columns = cast("Any", RuntimeInbox).__table__.c
        statement = select(RuntimeInbox).where(columns.id == inbox_id).with_for_update()
        if populate_existing:
            statement = statement.execution_options(populate_existing=True)
        result = await db.execute(statement)
        return result.scalar_one_or_none()

    async def get_by_id_refreshed(self, db: AsyncSession, inbox_id: int) -> RuntimeInbox | None:
        """非锁定刷新读取单条 RuntimeInbox，避免消费事务持 root 锁跨越编排写回。"""

        columns = cast("Any", RuntimeInbox).__table__.c
        statement = select(RuntimeInbox).where(columns.id == inbox_id).execution_options(populate_existing=True)
        result = await db.execute(statement)
        return result.scalar_one_or_none()

    async def get_latest_manual_hold_evidence(
        self,
        db: AsyncSession,
        *,
        session_id: int,
    ) -> RuntimeInboxManualHoldEvidence | None:
        """先读取 Session 最新状态迁移 Timeline，再返回其关联 Inbox 证据。"""

        from src.app.runtime.orchestration.models.timeline import WorklineTimeline

        timeline = cast("Any", WorklineTimeline).__table__.alias("latest_timeline")
        source = cast("Any", RuntimeInbox).__table__.alias("hold_source_inbox")
        row = (
            await db.execute(
                select(
                    timeline.c.session_id,
                    timeline.c.action_type,
                    timeline.c.status,
                    timeline.c.payload_json,
                    timeline.c.related_inbox_id,
                    source.c.workline_session_id,
                    source.c.status,
                )
                .select_from(timeline.outerjoin(source, source.c.id == timeline.c.related_inbox_id))
                .where(
                    timeline.c.session_id == session_id,
                    timeline.c.to_status.is_not(None),
                )
                .order_by(timeline.c.seq_no.desc(), timeline.c.id.desc())
                .limit(1)
            )
        ).one_or_none()
        if row is None:
            return None
        payload = row[3] if isinstance(row[3], dict) else {}
        reason_code = payload.get("reason_code")
        return RuntimeInboxManualHoldEvidence(
            session_id=int(row[0]),
            action_type=str(getattr(row[1], "value", row[1])),
            timeline_status=str(getattr(row[2], "value", row[2])),
            reason_code=reason_code if isinstance(reason_code, str) else None,
            related_inbox_id=int(row[4]) if row[4] is not None else None,
            source_session_id=int(row[5]) if row[5] is not None else None,
            source_status=str(getattr(row[6], "value", row[6])) if row[6] is not None else None,
        )

    async def add_received(self, db: AsyncSession, data: dict[str, Any]) -> RuntimeInbox:
        """新建 RECEIVED RuntimeInbox 并 flush，调用方控制事务提交。"""

        validate_canonical_payload_size(data.get("payload_json"))
        record = RuntimeInbox(**data)
        db.add(record)
        await db.flush()
        await db.refresh(record)
        return record

    async def get_evidence_by_id(self, db: AsyncSession, inbox_id: int) -> RuntimeInboxEvidence | None:
        """按显式主键返回跨域只读 evidence DTO。"""

        record = await self.get_by_id(db, inbox_id)
        if record is None or record.id is None:
            return None
        return RuntimeInboxEvidence(
            id=record.id,
            status=record.status,
            event_id=record.event_id,
            attempt_count=record.attempt_count,
            max_retries=record.max_retries,
            next_retry_at=record.next_retry_at,
            processed_at=record.processed_at,
            failed_at=record.failed_at,
            last_error_code=record.last_error_code,
            last_error_message=record.last_error_message,
        )

    async def count_unfinished_by_workline(self, db: AsyncSession, workline_id: int) -> int:
        """按显式 workline_id 统计非终态 RuntimeInbox。"""

        columns = cast("Any", RuntimeInbox).__table__.c
        result = await db.execute(
            select(func.count())
            .select_from(RuntimeInbox)
            .where(
                columns.workline_id == workline_id,
                columns.status.in_(RUNTIME_INBOX_UNFINISHED_STATUSES),
            )
        )
        return int(result.scalar_one() or 0)

    async def first_unfinished_by_workline(
        self,
        db: AsyncSession,
        workline_id: int,
    ) -> RuntimeInboxWorkloadSample | None:
        """按主键稳定顺序返回首条非终态 RuntimeInbox。"""

        columns = cast("Any", RuntimeInbox).__table__.c
        result = await db.execute(
            select(columns.id, columns.status)
            .where(
                columns.workline_id == workline_id,
                columns.status.in_(RUNTIME_INBOX_UNFINISHED_STATUSES),
            )
            .order_by(columns.id.asc())
            .limit(1)
        )
        row = result.first()
        if row is None:
            return None
        return RuntimeInboxWorkloadSample(id=int(row[0]), status=str(row[1]))

    async def claim_received_with_token(
        self,
        db: AsyncSession,
        *,
        limit: int,
        processor_token: str,
        stale_after_seconds: int,
        now_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        """原子 claim RECEIVED、到期 FAILED 与过期 PROCESSING，并保持同桶 FIFO。"""

        if limit <= 0:
            return []
        statement = self.build_claim_received_statement(
            limit=limit,
            processor_token=processor_token,
            stale_after_seconds=stale_after_seconds,
            now_ms=now_ms,
        )
        result = await db.execute(statement)
        claims = [
            {
                **dict(row),
                "processor_token": str(row["processor_token"]),
                "payload_json": dict(row["payload_json"] or {}),
            }
            for row in result.mappings().all()
        ]
        claims.sort(key=lambda row: (row["received_at"] is None, row["received_at"] or 0, row["id"]))
        return claims

    def build_claim_received_statement(
        self,
        *,
        limit: int,
        processor_token: str,
        stale_after_seconds: int,
        now_ms: int | None = None,
    ) -> Any:
        """构建生产 claim statement，供执行路径与 PostgreSQL 计划门禁共用。"""

        import time as _time

        effective_now_ms = int(_time.time() * 1000) if now_ms is None else now_ms
        lease_until = effective_now_ms + (max(stale_after_seconds, 1) * 1000)
        now_value = literal(effective_now_ms, type_=BigInteger())
        lease_until_value = literal(lease_until, type_=BigInteger())
        table = cast("Any", RuntimeInbox).__table__
        columns = table.c
        candidate = table.alias("claim_candidate")
        earlier = table.alias("earlier_inbox")
        cc = candidate.c
        ec = earlier.c
        candidate_has_canonical_envelope = (
            cc.kind.is_not(None)
            & cc.provider_code.is_not(None)
            & cc.event_type.is_not(None)
            & cc.source_event_id.is_not(None)
            & cc.payload_json.is_not(None)
            & cc.payload_hash.is_not(None)
            & cc.payload_schema_version.is_not(None)
            & cc.claim_bucket_key.is_not(None)
            & cc.received_at.is_not(None)
            & cc.last_error_code.is_distinct_from(PRE_CUTOVER_AUDIT_ONLY)
        )
        earlier_has_canonical_envelope = (
            ec.kind.is_not(None)
            & ec.provider_code.is_not(None)
            & ec.event_type.is_not(None)
            & ec.source_event_id.is_not(None)
            & ec.payload_json.is_not(None)
            & ec.payload_hash.is_not(None)
            & ec.payload_schema_version.is_not(None)
            & ec.claim_bucket_key.is_not(None)
            & ec.received_at.is_not(None)
            & ec.last_error_code.is_distinct_from(PRE_CUTOVER_AUDIT_ONLY)
        )
        # 候选 -> canonical envelope -> 状态/重试预算 -> FIFO anti-join -> 原子 UPDATE。
        # audit-only 或 malformed 行既不能被 claim，也不能作为同桶队首阻塞行动型消息。
        claimable = (
            (
                (cc.status == "RECEIVED")
                | ((cc.status == "FAILED") & (cc.next_retry_at <= now_value))
                | ((cc.status == "PROCESSING") & (cc.lease_until <= now_value))
            )
            & (cc.attempt_count < cc.max_retries)
            & candidate_has_canonical_envelope
        )
        earlier_can_advance = earlier_has_canonical_envelope & (
            (ec.status == "RECEIVED")
            | (ec.status == "PROCESSING")
            | ((ec.status == "FAILED") & ec.next_retry_at.is_not(None) & (ec.attempt_count < ec.max_retries))
        )
        earlier_in_bucket = exists(
            select(1)
            .select_from(earlier)
            .where(
                earlier_can_advance,
                ec.claim_bucket_key == cc.claim_bucket_key,
                tuple_(ec.received_at, ec.id) < tuple_(cc.received_at, cc.id),
            )
        )
        candidate_ids = (
            select(cc.id)
            .select_from(candidate)
            .where(claimable, ~earlier_in_bucket)
            .order_by(cc.received_at, cc.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return (
            update(table)
            .where(columns.id.in_(candidate_ids))
            .values(
                status="PROCESSING",
                processor_token=processor_token,
                lease_until=lease_until_value,
                attempt_count=columns.attempt_count + 1,
            )
            .returning(
                columns.id,
                columns.processor_token,
                columns.provider_code,
                columns.event_type,
                columns.source_event_id,
                columns.payload_json,
                columns.correlation_id,
                columns.execution_session_id,
                columns.workline_session_id,
                columns.workline_id,
                columns.device_id,
                columns.command_id,
                columns.kind,
                columns.trace_id,
                columns.event_id,
                columns.causation_id,
                columns.claim_bucket_key,
                columns.received_at,
            )
            .execution_options(synchronize_session=False)
        )

    async def recover_stale_leases(
        self,
        db: AsyncSession,
        *,
        stale_after_seconds: int,
        limit: int,
    ) -> int:
        """原子恢复 stale lease；耗尽预算的记录直接进入 DEAD_LETTER。"""

        if limit <= 0:
            return 0
        import time as _time

        _ = stale_after_seconds
        now_value = literal(int(_time.time() * 1000), type_=BigInteger())
        table = cast("Any", RuntimeInbox).__table__
        columns = table.c
        candidate = table.alias("stale_candidate")
        cc = candidate.c
        stale_ids = (
            select(cc.id)
            .select_from(candidate)
            .where(cc.status == "PROCESSING", cc.lease_until <= now_value)
            .order_by(cc.received_at, cc.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        exhausted = columns.attempt_count >= columns.max_retries
        result = await db.execute(
            update(table)
            .where(columns.id.in_(stale_ids), columns.status == "PROCESSING", columns.lease_until <= now_value)
            .values(
                status=case((exhausted, "DEAD_LETTER"), else_="RECEIVED"),
                processor_token=None,
                lease_until=None,
                next_retry_at=None,
                failed_at=case((exhausted, now_value), else_=columns.failed_at),
                last_error_code=case((exhausted, "INBOX_RETRY_EXHAUSTED"), else_=columns.last_error_code),
                last_error_message=case(
                    (exhausted, "PROCESSING_LEASE_EXPIRED_RETRY_EXHAUSTED"),
                    else_=columns.last_error_message,
                ),
            )
            .returning(columns.id, columns.status)
            .execution_options(synchronize_session=False)
        )
        return len(result.mappings().all())

    async def update_terminal_state(
        self,
        db: AsyncSession,
        *,
        inbox_id: int,
        lease_token: str,
        target_state: str,
        extra_values: dict[str, Any] | None = None,
    ) -> bool:
        """按 PROCESSING + processor_token 围栏写回结果并释放 worker 所有权。"""

        columns = cast("Any", RuntimeInbox).__table__.c
        values: dict[str, Any] = {"status": target_state}
        if extra_values:
            values.update(extra_values)
        # FAILED 等待重试、PROCESSED/DEAD_LETTER 终态都已离开 PROCESSING，
        # processor token 与 lease 必须同步释放，且不允许 extra_values 覆盖该不变量。
        values["processor_token"] = None
        values["lease_until"] = None
        result = await db.execute(
            update(RuntimeInbox)
            .where(
                columns.id == inbox_id,
                columns.status == "PROCESSING",
                columns.processor_token == lease_token,
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        updated = result.rowcount > 0
        if not updated:
            _emit_runtime_inbox_sli(
                "runtime_inbox.fencing_reject",
                {"inbox_id": inbox_id, "target_state": target_state},
            )
        elif target_state == "DEAD_LETTER":
            _emit_runtime_inbox_sli("runtime_inbox.dead_letter", {"inbox_id": inbox_id})
        elif target_state == "FAILED" and values.get("last_error_code") == "RESOURCE_WAIT":
            _emit_runtime_inbox_sli("runtime_inbox.resource_wait", {"inbox_id": inbox_id})
        return updated

    async def get_sli_snapshot(self, db: AsyncSession, *, now_ms: int) -> RuntimeInboxSliSnapshot:
        """读取五态数量、最老可 claim 年龄及关键积压分类。"""

        table = cast("Any", RuntimeInbox).__table__
        columns = table.c
        now_value = literal(now_ms, type_=BigInteger())
        status_rows = await db.execute(
            select(columns.status, func.count())
            .where(columns.last_error_code.is_distinct_from(PRE_CUTOVER_AUDIT_ONLY))
            .group_by(columns.status)
        )
        status_counts: dict[str, int] = dict.fromkeys(
            ("RECEIVED", "PROCESSING", "PROCESSED", "FAILED", "DEAD_LETTER"), 0
        )
        status_counts.update({str(status): int(count) for status, count in status_rows.all()})
        candidate = table.alias("sli_claim_candidate")
        earlier = table.alias("sli_earlier_inbox")
        cc = candidate.c
        ec = earlier.c
        candidate_has_canonical_envelope = (
            cc.kind.is_not(None)
            & cc.provider_code.is_not(None)
            & cc.event_type.is_not(None)
            & cc.source_event_id.is_not(None)
            & cc.payload_json.is_not(None)
            & cc.payload_hash.is_not(None)
            & cc.payload_schema_version.is_not(None)
            & cc.claim_bucket_key.is_not(None)
            & cc.received_at.is_not(None)
            & cc.last_error_code.is_distinct_from(PRE_CUTOVER_AUDIT_ONLY)
        )
        earlier_has_canonical_envelope = (
            ec.kind.is_not(None)
            & ec.provider_code.is_not(None)
            & ec.event_type.is_not(None)
            & ec.source_event_id.is_not(None)
            & ec.payload_json.is_not(None)
            & ec.payload_hash.is_not(None)
            & ec.payload_schema_version.is_not(None)
            & ec.claim_bucket_key.is_not(None)
            & ec.received_at.is_not(None)
            & ec.last_error_code.is_distinct_from(PRE_CUTOVER_AUDIT_ONLY)
        )
        claimable = (
            (
                (cc.status == "RECEIVED")
                | ((cc.status == "FAILED") & (cc.next_retry_at <= now_value))
                | ((cc.status == "PROCESSING") & (cc.lease_until <= now_value))
            )
            & (cc.attempt_count < cc.max_retries)
            & candidate_has_canonical_envelope
        )
        earlier_can_advance = earlier_has_canonical_envelope & (
            (ec.status == "RECEIVED")
            | (ec.status == "PROCESSING")
            | ((ec.status == "FAILED") & ec.next_retry_at.is_not(None) & (ec.attempt_count < ec.max_retries))
        )
        earlier_in_bucket = exists(
            select(1)
            .select_from(earlier)
            .where(
                earlier_can_advance,
                ec.claim_bucket_key == cc.claim_bucket_key,
                tuple_(ec.received_at, ec.id) < tuple_(cc.received_at, cc.id),
            )
        )
        oldest_received_at = await db.scalar(
            select(func.min(cc.received_at)).select_from(candidate).where(claimable, ~earlier_in_bucket)
        )
        aggregates = (
            await db.execute(
                select(
                    func.count().filter((columns.status == "PROCESSING") & (columns.lease_until <= now_value)),
                    func.count().filter((columns.status == "FAILED") & (columns.last_error_code == "RESOURCE_WAIT")),
                )
            )
        ).one()
        return RuntimeInboxSliSnapshot(
            status_counts=status_counts,
            oldest_claimable_age_ms=(
                max(0, now_ms - int(oldest_received_at)) if oldest_received_at is not None else None
            ),
            stale_processing_count=int(aggregates[0] or 0),
            resource_wait_count=int(aggregates[1] or 0),
        )

    async def count_by_statuses(self, db: AsyncSession, statuses: set[str]) -> int:
        """按 RuntimeInbox 五态统计记录。"""

        columns = cast("Any", RuntimeInbox).__table__.c
        result = await db.execute(select(func.count()).select_from(RuntimeInbox).where(columns.status.in_(statuses)))
        return int(result.scalar_one() or 0)

    async def latest_by_workline_session_refs(
        self,
        db: AsyncSession,
        *,
        workline_session_refs: list[int],
        kind: str | None = None,
    ) -> dict[int, RuntimeInboxProjection]:
        """按显式 WorklineSession FK 返回各会话最新 RuntimeInbox。"""

        if not workline_session_refs:
            return {}
        columns = cast("Any", RuntimeInbox).__table__.c
        filters = [columns.workline_session_id.in_(workline_session_refs)]
        if kind is not None:
            filters.append(columns.kind == kind)
        ranked = (
            select(
                columns.id.label("id"),
                func.row_number()
                .over(
                    partition_by=columns.workline_session_id,
                    order_by=(columns.received_at.desc(), columns.id.desc()),
                )
                .label("rn"),
            )
            .where(*filters)
            .subquery()
        )
        result = await db.execute(select(RuntimeInbox).join(ranked, columns.id == ranked.c.id).where(ranked.c.rn == 1))
        return {
            item.workline_session_id: self._to_projection(item)
            for item in result.scalars().all()
            if isinstance(item.workline_session_id, int)
        }

    async def list_by_trace_id(self, db: AsyncSession, trace_id: str) -> list[RuntimeInboxProjection]:
        """按显式 trace_id 稳定返回 RuntimeInbox。"""

        columns = cast("Any", RuntimeInbox).__table__.c
        result = await db.execute(
            select(RuntimeInbox)
            .where(columns.trace_id == trace_id)
            .order_by(columns.received_at.asc(), columns.id.asc())
        )
        return [self._to_projection(item) for item in result.scalars().all()]

    async def list_by_workline_session_ref(
        self, db: AsyncSession, workline_session_ref: int
    ) -> list[RuntimeInboxProjection]:
        """按显式 WorklineSession FK 稳定返回 RuntimeInbox。"""

        columns = cast("Any", RuntimeInbox).__table__.c
        result = await db.execute(
            select(RuntimeInbox)
            .where(columns.workline_session_id == workline_session_ref)
            .order_by(columns.received_at.asc(), columns.id.asc())
        )
        return [self._to_projection(item) for item in result.scalars().all()]

    def workline_session_ref_exists_for_device(self, device_id: int, outer_ref_column: Any) -> Any:
        """返回可组合的设备/WorklineSession 相关 EXISTS 表达式。"""

        columns = cast("Any", RuntimeInbox).__table__.c
        return exists(
            select(literal(1)).where(
                columns.device_id == device_id,
                columns.workline_session_id == outer_ref_column,
            )
        )


runtime_inbox_repository = RuntimeInboxRepository()


__all__ = [
    "RuntimeInboxPayloadTooLarge",
    "RuntimeInboxRepository",
    "RuntimeInboxSliSnapshot",
    "runtime_inbox_repository",
    "validate_canonical_payload_size",
]
