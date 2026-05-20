"""WorklineSession Repository 层"""

from typing import Any, cast

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.device.models.command import CommandStatus, DeviceCommand
from src.app.workline.models.session import RuntimeReconciliationState, SessionStatus, WorklineSession
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

    async def list_latest_by_workline_id(
        self,
        db: AsyncSession,
        workline_id: int,
        *,
        limit: int = 50,
    ) -> list[WorklineSession]:
        """查询作业线最近会话。"""

        columns = cast("Any", WorklineSession).__table__.c
        result = await db.execute(
            select(WorklineSession).where(columns.workline_id == workline_id).order_by(columns.id.desc()).limit(limit)
        )
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

    async def get_by_trace_id(
        self,
        db: AsyncSession,
        trace_id: str,
    ) -> WorklineSession | None:
        """根据 Trace ID 查询会话

        Args:
            db: 数据库会话
            trace_id: Trace ID（串联业务流程）

        Returns:
            匹配的会话（如果有）
        """
        columns = cast("Any", WorklineSession).__table__.c
        result = await db.execute(
            select(WorklineSession).where(
                columns.trace_id == trace_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_for_update(
        self,
        db: AsyncSession,
        session_id: int,
    ) -> WorklineSession | None:
        """根据 ID 查询并锁定 Session。"""
        columns = cast("Any", WorklineSession).__table__.c
        result = await db.execute(select(WorklineSession).where(columns.id == session_id).with_for_update())
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

        查询 ACK_RECEIVED 后执行等待或外部系统等待已过期的 Session；
        no-ACK 派发失败不属于 TIMER_TIMEOUT。

        Args:
            db: 数据库会话
            limit: 最大返回数量

        Returns:
            超时的 Session 列表
        """
        columns = cast("Any", WorklineSession).__table__.c
        command_columns = cast("Any", DeviceCommand).__table__.c
        now = timezone.now_for_db()
        result = await db.execute(
            select(WorklineSession)
            .outerjoin(DeviceCommand, columns.awaiting_command_id == command_columns.id)
            .where(
                columns.deadline_at.isnot(None),
                columns.deadline_at < now,
                or_(
                    and_(
                        columns.status == SessionStatus.WAITING_DEVICE_RESULT,
                        columns.awaiting_command_id.isnot(None),
                        command_columns.status == CommandStatus.ACK_RECEIVED,
                        command_columns.ack_received_at.isnot(None),
                    ),
                    columns.status == SessionStatus.WAITING_EXTERNAL,
                ),
            )
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_pending_reconciliation_by_command_id(
        self,
        db: AsyncSession,
        command_id: int,
    ) -> WorklineSession | None:
        """查询指定 command 当前未解除的 runtime reconciliation owner session。"""

        columns = cast("Any", WorklineSession).__table__.c
        result = await db.execute(
            select(WorklineSession)
            .where(
                columns.reconciliation_command_id == command_id,
                columns.reconciliation_state == RuntimeReconciliationState.PENDING,
            )
            .order_by(columns.reconciliation_occurred_at.desc(), columns.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_pending_reconciliation_owner_for_workline(
        self,
        db: AsyncSession,
        workline_id: int,
    ) -> WorklineSession | None:
        """查询 WorkLine 当前 runtime reconciliation owner session。"""

        columns = cast("Any", WorklineSession).__table__.c
        result = await db.execute(
            select(WorklineSession)
            .where(
                columns.workline_id == workline_id,
                columns.reconciliation_state == RuntimeReconciliationState.PENDING,
            )
            .order_by(columns.reconciliation_occurred_at.asc(), columns.id.asc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def count_pending_reconciliations_for_workline(
        self,
        db: AsyncSession,
        workline_id: int,
    ) -> int:
        """统计 WorkLine 尚未解除的 runtime reconciliation 数量。"""

        columns = cast("Any", WorklineSession).__table__.c
        result = await db.execute(
            select(func.count(columns.id)).where(
                columns.workline_id == workline_id,
                columns.reconciliation_state == RuntimeReconciliationState.PENDING,
            )
        )
        return int(result.scalar_one() or 0)

    async def fail_open_by_workline(
        self,
        db: AsyncSession,
        workline_id: int,
        *,
        incident_id: int,
    ) -> int:
        """将 WorkLine 未完成 Session 终止为失败。"""

        columns = cast("Any", WorklineSession).__table__.c
        open_statuses = [
            SessionStatus.NEW,
            SessionStatus.RUNNING,
            SessionStatus.WAITING_DEVICE_RESULT,
            SessionStatus.WAITING_EXTERNAL,
            SessionStatus.MANUAL_HOLD,
        ]
        result = await db.execute(
            select(WorklineSession)
            .where(
                columns.workline_id == workline_id,
                columns.status.in_(open_statuses),
            )
            .with_for_update()
        )
        sessions = list(result.scalars().all())
        now = timezone.now_for_db()
        for session in sessions:
            session.status = SessionStatus.FAILED
            session.failure_domain = "SAFETY"
            session.failure_code = "WORKLINE_ESTOPPED"
            session.failure_message = f"WorkLine 急停冻结，incident_id={incident_id}"
            session.ended_at = now
        return len(sessions)


# 创建单例
workline_session_repository = WorklineSessionRepository()
