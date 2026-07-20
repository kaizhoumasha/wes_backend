"""Recorded replay 的 Timeline 只读 Repository。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlmodel import select

from src.app.runtime.orchestration.models.timeline import TimelineActionType, WorklineTimeline

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class TimelineRecordedReplayRepository:
    """只从持久化 DECISION_MADE 读取 replay 来源，不接受调用者 envelope。"""

    async def list_recorded_decisions(
        self,
        db: AsyncSession,
        *,
        source_inbox_id: int,
    ) -> list[WorklineTimeline]:
        statement = (
            select(WorklineTimeline)
            .where(
                WorklineTimeline.related_inbox_id == source_inbox_id,
                WorklineTimeline.action_type == TimelineActionType.DECISION_MADE,
            )
            .order_by(WorklineTimeline.seq_no.asc())
        )
        return list((await db.scalars(statement)).all())


timeline_recorded_replay_repository = TimelineRecordedReplayRepository()

__all__ = ["TimelineRecordedReplayRepository", "timeline_recorded_replay_repository"]
