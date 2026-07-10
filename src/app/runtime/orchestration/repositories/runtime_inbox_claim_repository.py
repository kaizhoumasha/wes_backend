"""RuntimeInbox claim + write-back 仓库 (Task 3 主计划 §3).

提供 5 态状态机所需的原子 claim、stale 回收、fenced 终态写回。
底层全部基于 RuntimeInbox 表 (wes_runtime.runtime_inbox)。

用法: Task 4 producer 写入 → Task 6 Celery task claim + 处理 + 终态写回。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import select, update

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
        import time as _time

        from src.utils.timezone import timezone

        _ = timezone.now_for_db()  # ensure tz helper is bound; keep the import for symmetry
        now_ms = int(_time.time() * 1000)
        stale_cutoff = now_ms + (stale_after_seconds * 1000)
        columns = cast("Any", RuntimeInbox).__table__.c

        # 原子 claim: 用 UPDATE ... RETURNING 把 RECEIVED/FAILED/stale PROCESSING
        # 转为 PROCESSING + processor_token + lease_until
        result = await db.execute(
            update(RuntimeInbox)
            .where(
                columns.status.in_(["RECEIVED", "PROCESSING", "FAILED"]),
            )
            .where(
                (columns.status == "RECEIVED")
                | ((columns.status == "FAILED") & (columns.next_retry_at <= now_ms))
                | ((columns.status == "PROCESSING") & (columns.lease_until <= now_ms))
            )
            .where(columns.attempt_count < columns.max_retries)
            .values(
                status="PROCESSING",
                processor_token=processor_token,
                lease_until=stale_cutoff,
                attempt_count=columns.attempt_count + 1,
            )
            .returning(columns.id)
            .execution_options(synchronize_session=False)
        )
        ids = [row[0] for row in result.fetchall()][:limit]

        if not ids:
            return []

        # 读出 claim 行 (轻量 dict)
        rows = (await db.execute(select(RuntimeInbox).where(columns.id.in_(ids)))).scalars().all()

        return [
            {
                "id": row.id,
                "processor_token": processor_token,
                "provider_code": row.provider_code,
                "event_type": row.event_type,
                "source_event_id": row.source_event_id,
                "payload_json": dict(row.payload_json or {}),
                "correlation_id": row.correlation_id,
                "execution_session_id": row.execution_session_id,
                "workline_id": row.workline_id,
                "device_id": row.device_id,
                "command_id": row.command_id,
                "kind": row.kind,
                "trace_id": row.trace_id,
                "event_id": row.event_id,
                "causation_id": row.causation_id,
            }
            for row in rows
        ]

    async def find_stale_processing(
        self,
        db: AsyncSession,
        *,
        stale_after_seconds: int,
        limit: int,
    ) -> list[RuntimeInbox]:
        """查找 stale PROCESSING 行 (lease_until < now).

        用于 health-check + 异常回收.
        """
        import time as _time

        now_ms = _time.time() * 1000
        columns = cast("Any", RuntimeInbox).__table__.c

        result = await db.execute(
            select(RuntimeInbox)
            .where(columns.status == "PROCESSING")
            .where(columns.lease_until <= now_ms - stale_after_seconds * 1000)
            .limit(limit)
        )
        return list(result.scalars().all())

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
