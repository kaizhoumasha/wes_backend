"""WorklineSession Repository 层"""

from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.workline.models.session import WorklineSession
from src.database.base_repository import BaseRepository
from src.utils.timezone import timezone


class WorklineSessionRepository(BaseRepository[WorklineSession]):
    """作业线会话数据访问层"""

    def __init__(self) -> None:
        """初始化会话仓库"""
        super().__init__(WorklineSession)

    async def get_by_session_code(
        self,
        db: AsyncSession,
        session_code: str,
    ) -> WorklineSession | None:
        """根据会话编码查询"""
        columns = cast("Any", WorklineSession).__table__.c
        result = await db.execute(
            select(WorklineSession).where(
                columns.session_code == session_code,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_workline_id(
        self,
        db: AsyncSession,
        workline_id: int,
        status: str | None = None,
    ) -> list[WorklineSession]:
        """根据作业线 ID 查询会话列表

        Args:
            db: 数据库会话
            workline_id: 作业线 ID
            status: 可选的状态过滤

        Returns:
            会话列表
        """
        columns = cast("Any", WorklineSession).__table__.c
        query = select(WorklineSession).where(
            columns.workline_id == workline_id,
        )
        if status:
            query = query.where(columns.status == status)

        result = await db.execute(query)
        return list(result.scalars().all())

    async def get_open_session_by_business_key(
        self,
        db: AsyncSession,
        workline_id: int,
        business_key: str,
    ) -> WorklineSession | None:
        """根据业务键查询未结束的会话

        Args:
            db: 数据库会话
            workline_id: 作业线 ID
            business_key: 业务键

        Returns:
            未结束的会话（如果有）
        """
        columns = cast("Any", WorklineSession).__table__.c
        # 未结束状态: NEW, RUNNING, WAITING_*, MANUAL_HOLD
        open_statuses = [
            "NEW",
            "RUNNING",
            "WAITING_DEVICE_RESULT",
            "WAITING_EXTERNAL",
            "MANUAL_HOLD",
        ]
        result = await db.execute(
            select(WorklineSession).where(
                columns.workline_id == workline_id,
                columns.business_key == business_key,
                columns.status.in_(open_statuses),
            )
        )
        return result.scalar_one_or_none()

    async def get_latest_session_by_business_key(
        self,
        db: AsyncSession,
        workline_id: int,
        business_key: str,
    ) -> WorklineSession | None:
        """根据业务键查询最新的会话（无论状态）

        用于处理事件在 session 完成后立即到达的情况。

        Args:
            db: 数据库会话
            workline_id: 作业线 ID
            business_key: 业务键

        Returns:
            最新的会话（如果有）
        """
        columns = cast("Any", WorklineSession).__table__.c
        result = await db.execute(
            select(WorklineSession)
            .where(
                columns.workline_id == workline_id,
                columns.business_key == business_key,
            )
            .order_by(columns.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_by_correlation_id(
        self,
        db: AsyncSession,
        correlation_id: str,
    ) -> WorklineSession | None:
        """根据关联 ID 查询会话

        Args:
            db: 数据库会话
            correlation_id: 关联 ID（串联业务流程）

        Returns:
            匹配的会话（如果有）
        """
        columns = cast("Any", WorklineSession).__table__.c
        result = await db.execute(
            select(WorklineSession).where(
                columns.correlation_id == correlation_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_open_session_by_awaiting_command_id(
        self,
        db: AsyncSession,
        command_id: int,
    ) -> WorklineSession | None:
        """根据 awaiting_command_id 查询未结束的会话。"""
        columns = cast("Any", WorklineSession).__table__.c
        open_statuses = [
            "NEW",
            "RUNNING",
            "WAITING_DEVICE_RESULT",
            "WAITING_EXTERNAL",
            "MANUAL_HOLD",
        ]
        result = await db.execute(
            select(WorklineSession).where(
                columns.awaiting_command_id == command_id,
                columns.status.in_(open_statuses),
            )
        )
        return result.scalar_one_or_none()

    async def get_timed_out_sessions(
        self,
        db: AsyncSession,
        limit: int = 100,
    ) -> list[WorklineSession]:
        """获取已超时的 Session 列表

        只查询处于等待状态且 deadline_at 已过期的 Session。

        Args:
            db: 数据库会话
            limit: 最大返回数量

        Returns:
            超时的 Session 列表
        """
        columns = cast("Any", WorklineSession).__table__.c
        # 等待状态：可能超时的状态
        waiting_statuses = [
            "WAITING_DEVICE_RESULT",
            "WAITING_EXTERNAL",
        ]

        now = timezone.now_for_db()
        result = await db.execute(
            select(WorklineSession)
            .where(
                columns.status.in_(waiting_statuses),
                columns.deadline_at.isnot(None),
                columns.deadline_at < now,
            )
            .limit(limit)
        )
        return list(result.scalars().all())


# 创建单例
workline_session_repository = WorklineSessionRepository()
