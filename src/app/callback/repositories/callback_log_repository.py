"""
回调日志 Repository
"""

from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from src.app.callback.models import CallbackLog
from src.database.base_repository import BaseRepository


class CallbackLogRepository(BaseRepository[CallbackLog]):
    """回调日志 Repository"""

    def __init__(self) -> None:
        super().__init__(CallbackLog)

    async def get_by_request_id(self, db: AsyncSession, request_id: str) -> CallbackLog | None:
        """根据 request_id 查询回调日志"""
        result = await db.execute(select(CallbackLog).where(CallbackLog.request_id == request_id))
        return result.scalar_one_or_none()

    async def get_by_trace_id(self, db: AsyncSession, trace_id: str) -> list[CallbackLog]:
        """根据 trace_id 查询所有相关的回调日志"""
        columns = cast("Any", CallbackLog).__table__.c
        result = await db.execute(select(CallbackLog).where(columns.trace_id == trace_id).order_by(columns.created_at))
        return list(result.scalars().all())

    async def get_by_subject_code(self, db: AsyncSession, subject_code: str, limit: int = 100) -> list[CallbackLog]:
        """根据回调主体编码查询最近的回调日志。"""
        columns = cast("Any", CallbackLog).__table__.c
        result = await db.execute(
            select(CallbackLog)
            .where(columns.subject_code == subject_code)
            .order_by(columns.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())


# 创建单例
callback_log_repository = CallbackLogRepository()


__all__ = [
    "CallbackLogRepository",
    "callback_log_repository",
]
