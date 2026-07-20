"""Timeline seq_no 分配服务。"""

from __future__ import annotations

from typing import Any

from src.app.runtime.orchestration.repositories.timeline_sequence_repository import timeline_sequence_repository


async def allocate_timeline_seq_no(db: Any, *, session_id: int) -> int:
    """为同一 session 分配单调递增 seq_no。

    PostgreSQL 环境下先获取事务级 advisory lock，再读取 max(seq_no)。
    非 PostgreSQL 测试/本地内存数据库跳过 advisory lock，但保留同一查询路径。
    """

    return (await timeline_sequence_repository.allocate_many(db, session_id=session_id, count=1))[0]


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
