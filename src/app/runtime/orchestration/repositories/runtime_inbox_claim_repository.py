"""RuntimeInbox claim + write-back 仓库 (Task 3 主计划 §3).

提供 5 态状态机所需的原子 claim、stale 回收、fenced 终态写回。
底层全部基于 RuntimeInbox 表 (wes_runtime.runtime_inbox)。

用法: Task 4 producer 写入 → Task 6 Celery task claim + 处理 + 终态写回。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import BigInteger, exists, literal, select, tuple_, update

from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.database.base_repository import BaseRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class RuntimeInboxClaimRepository(BaseRepository[RuntimeInbox]):
    """RuntimeInbox 原子 claim + 终态写回仓库."""

    def __init__(self) -> None:
        super().__init__(RuntimeInbox)

    async def claim_received_with_token(
        self,
        db: AsyncSession,
        *,
        limit: int,
        processor_token: str,
        stale_after_seconds: int,
    ) -> list[dict[str, Any]]:
        """原子 claim RECEIVED / stale PROCESSING / due FAILED 行.

        返回轻量 dict (不跨 session 传递 ORM 对象).
        """
        if limit <= 0:
            return []

        import time as _time

        from src.utils.timezone import timezone

        _ = timezone.now_for_db()  # ensure tz helper is bound; keep the import for symmetry
        now_ms = int(_time.time() * 1000)
        lease_until = now_ms + (max(stale_after_seconds, 1) * 1000)
        now_value = literal(now_ms, type_=BigInteger())
        lease_until_value = literal(lease_until, type_=BigInteger())
        table = cast("Any", RuntimeInbox).__table__
        columns = table.c
        claim_candidate = table.alias("claim_candidate")
        earlier_inbox = table.alias("earlier_inbox")
        candidate_columns = claim_candidate.c
        earlier_columns = earlier_inbox.c
        candidate_claimable = (
            (candidate_columns.status == "RECEIVED")
            | ((candidate_columns.status == "FAILED") & (candidate_columns.next_retry_at <= now_value))
            | ((candidate_columns.status == "PROCESSING") & (candidate_columns.lease_until <= now_value))
        ) & (candidate_columns.attempt_count < candidate_columns.max_retries)
        earlier_message_in_bucket = exists(
            select(1)
            .select_from(earlier_inbox)
            .where(
                earlier_columns.status.in_(["RECEIVED", "FAILED", "PROCESSING"]),
                earlier_columns.claim_bucket_key == candidate_columns.claim_bucket_key,
                tuple_(earlier_columns.received_at, earlier_columns.id)
                < tuple_(candidate_columns.received_at, candidate_columns.id),
            )
        )
        claimable_ids = (
            select(candidate_columns.id)
            .select_from(claim_candidate)
            .where(candidate_claimable, ~earlier_message_in_bucket)
            .order_by(candidate_columns.received_at, candidate_columns.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )

        # 候选选择和状态变更保持在同一条 UPDATE ... RETURNING 中；DB 层 limit
        # 避免 Python 切片留下已更新但未返回给 processor 的 lease。
        result = await db.execute(
            update(table)
            .where(columns.id.in_(claimable_ids))
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

    async def find_stale_processing(
        self,
        db: AsyncSession,
        *,
        stale_after_seconds: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        """查找 stale PROCESSING 行 (lease_until < now).

        用于 health-check + 异常回收. 返回轻量 dict 而非 ORM 对象
        避免 inbound-normalizer 误判 (RuntimeInbox 是 SQLModel 表 model,
        不是 inbound normalizer interface).
        """
        import time as _time

        now_ms = _time.time() * 1000
        columns = cast("Any", RuntimeInbox).__table__.c

        result = await db.execute(
            select(
                columns.id,
                columns.status,
                columns.lease_until,
                columns.attempt_count,
            )
            .where(columns.status == "PROCESSING")
            .where(columns.lease_until <= now_ms - stale_after_seconds * 1000)
            .limit(limit)
        )
        return [
            {
                "id": row.id,
                "status": row.status,
                "lease_until": row.lease_until,
                "attempt_count": row.attempt_count,
            }
            for row in result.fetchall()
        ]

    async def update_terminal_state(
        self,
        db: AsyncSession,
        *,
        inbox_id: int,
        lease_token: str,
        target_state: str,
        extra_values: dict[str, Any] | None = None,
    ) -> bool:
        """原子 fenced 终态写回 (status + processor_token 匹配).

        返回影响行数: 1=成功, 0=token 不匹配 (旧 owner 已回收).
        """
        columns = cast("Any", RuntimeInbox).__table__.c

        values: dict[str, Any] = {"status": target_state}
        if extra_values:
            values.update(extra_values)

        result = await db.execute(
            update(RuntimeInbox)
            .where(columns.id == inbox_id)
            .where(columns.status == "PROCESSING")
            .where(columns.processor_token == lease_token)
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        return result.rowcount > 0


runtime_inbox_claim_repository = RuntimeInboxClaimRepository()


__all__ = [
    "RuntimeInboxClaimRepository",
    "runtime_inbox_claim_repository",
]
