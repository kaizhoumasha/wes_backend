"""Timeline seq_no 的 PostgreSQL advisory owner。"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import func, select, text

from src.app.runtime.orchestration.models.timeline import WorklineTimeline
from src.database.dialect import dialect_name


class TimelineSequenceRepository:
    """同层 owner：持有 advisory lock 与 max(seq_no) 查询，不依赖 Service。"""

    async def acquire_lock(self, db: Any, *, session_id: int) -> None:
        if dialect_name(db) == "postgresql":
            await db.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
                {"lock_key": f"workline_timeline:{session_id}"},
            )

    async def allocate_many(
        self,
        db: Any,
        *,
        session_id: int,
        count: int,
        lock_already_held: bool = False,
    ) -> tuple[int, ...]:
        if count <= 0:
            raise ValueError("timeline sequence allocation count must be positive")
        if not lock_already_held:
            await self.acquire_lock(db, session_id=session_id)
        columns = cast("Any", WorklineTimeline).__table__.c
        result = await db.execute(select(func.max(columns.seq_no)).where(columns.session_id == session_id))
        max_seq_no = result.scalar_one_or_none()
        first = int(max_seq_no or 0) + 1
        return tuple(range(first, first + count))


timeline_sequence_repository = TimelineSequenceRepository()

__all__ = ["TimelineSequenceRepository", "timeline_sequence_repository"]
