"""Timeline seq_no 分配服务。"""

from __future__ import annotations

from inspect import isawaitable
from typing import Any, cast

from sqlalchemy import func, select, text

from src.app.runtime.orchestration.models.timeline import WorklineTimeline


def _dialect_name(db: Any) -> str | None:
    get_bind = getattr(db, "get_bind", None)
    bind = get_bind() if callable(get_bind) else getattr(db, "bind", None)
    if isawaitable(bind):
        close = getattr(bind, "close", None)
        if callable(close):
            _ = close()
        return None
    dialect = getattr(bind, "dialect", None)
    name = getattr(dialect, "name", None)
    return name if isinstance(name, str) else None


async def allocate_timeline_seq_no(db: Any, *, session_id: int) -> int:
    """为同一 session 分配单调递增 seq_no。

    PostgreSQL 环境下先获取事务级 advisory lock，再读取 max(seq_no)。
    非 PostgreSQL 测试/本地内存数据库跳过 advisory lock，但保留同一查询路径。
    """

    if _dialect_name(db) == "postgresql":
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
            {"lock_key": f"workline_timeline:{session_id}"},
        )

    columns = cast("Any", WorklineTimeline).__table__.c
    result = await db.execute(select(func.max(columns.seq_no)).where(columns.session_id == session_id))
    max_seq_no = result.scalar_one_or_none()
    return (max_seq_no or 0) + 1


async def add_timeline_with_sequence(db: Any, timeline: Any, *, seq_no: int | None = None) -> int:
    """设置 timeline.seq_no 并加入当前事务。"""

    assigned_seq_no = seq_no
    if assigned_seq_no is None:
        session_id = getattr(timeline, "session_id", None)
        if not isinstance(session_id, int):
            raise ValueError("Timeline 缺少有效 session_id")
        assigned_seq_no = await allocate_timeline_seq_no(db, session_id=session_id)

    timeline.seq_no = assigned_seq_no
    db.add(timeline)
    return assigned_seq_no


__all__ = ["add_timeline_with_sequence", "allocate_timeline_seq_no"]
