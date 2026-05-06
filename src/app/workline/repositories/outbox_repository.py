"""WorklineOutbox Repository 层"""

from typing import Any, cast

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.workline.models.outbox import DispatchType, OutboxStatus, WorklineOutbox
from src.database.base_repository import BaseRepository
from src.utils.timezone import timezone


class WorklineOutboxRepository(BaseRepository[WorklineOutbox]):
    """作业线发件箱数据访问层"""

    def __init__(self) -> None:
        """初始化发件箱仓库"""
        super().__init__(WorklineOutbox)

    async def get_pending_messages(
        self,
        db: AsyncSession,
        limit: int = 50,
    ) -> list[WorklineOutbox]:
        """获取待派发的消息

        只查询 NEW 状态且未到重试时间或重试时间已过的消息。

        Args:
            db: 数据库会话
            limit: 最大返回数量

        Returns:
            待派发的消息列表
        """
        columns = cast("Any", WorklineOutbox).__table__.c
        now = timezone.now_for_db()

        result = await db.execute(
            select(WorklineOutbox)
            .where(
                columns.status == OutboxStatus.NEW,
                # next_retry_at 为空或已过重试时间
                (columns.next_retry_at.is_(None)) | (columns.next_retry_at <= now),
            )
            .order_by(columns.created_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list(result.scalars().all())

    async def get_sandbox_pending_messages(
        self,
        db: AsyncSession,
        limit: int = 50,
        workline_id: int | None = None,
        device_id: int | None = None,
    ) -> list[WorklineOutbox]:
        """获取沙箱待处理消息。

        SIMULATION 模式下，Outbox 会正常进入派发链路并标记为 SENT；
        SENT 但未 ACKED 的消息即等待调试人员手工 callback/result 回传。

        Args:
            db: 数据库会话
            limit: 最大返回数量
            workline_id: 工作线 ID 过滤（可选）
            device_id: 设备 ID 过滤（可选）
        """
        from src.app.workline.models.session import RunMode, SessionStatus, WorklineSession

        columns = cast("Any", WorklineOutbox).__table__.c
        session_columns = cast("Any", WorklineSession).__table__.c

        # 包含等待派发的 Outbox (NEW, DISPATCHING, SENT)
        # 以及等待 Result 回传的 ACKED Outbox (session 在 WAITING_DEVICE_RESULT 状态)
        open_session_statuses = [
            SessionStatus.NEW,
            SessionStatus.RUNNING,
            SessionStatus.WAITING_DEVICE_RESULT,
            SessionStatus.WAITING_EXTERNAL,
            SessionStatus.MANUAL_HOLD,
        ]
        query = (
            select(WorklineOutbox)
            .join(WorklineSession, columns.session_id == session_columns.id)
            .where(
                session_columns.run_mode == RunMode.SIMULATION,
                session_columns.status.in_(open_session_statuses),
                columns.dispatch_type.in_([DispatchType.DEVICE_COMMAND, DispatchType.EXTERNAL_HTTP]),
                or_(
                    # 等待派发
                    columns.status.in_([OutboxStatus.NEW, OutboxStatus.DISPATCHING, OutboxStatus.SENT]),
                    # 已 ACK 但等待 Result 回传
                    and_(
                        columns.status == OutboxStatus.ACKED,
                        session_columns.status == SessionStatus.WAITING_DEVICE_RESULT,
                    ),
                ),
            )
        )

        if workline_id is not None:
            query = query.where(columns.workline_id == workline_id)

        if device_id is not None:
            from src.app.device.models import Device

            device_columns = cast("Any", Device).__table__.c
            query = query.join(Device, columns.target_code == device_columns.device_code).where(
                device_columns.id == device_id
            )

        result = await db.execute(query.order_by(columns.created_at.asc()).limit(limit))
        return list(result.scalars().all())

    async def get_sandbox_completed_messages(
        self,
        db: AsyncSession,
        limit: int = 50,
        workline_id: int | None = None,
        device_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """获取沙箱已完成的 outbox，按 Session 分组。

        SIMULATION 模式下，Session 进入终端态（COMPLETED/FAILED/CANCELLED）且
        Outbox 状态为 ACKED 的记录。按 Session 分组返回，用于 Sandbox 页面
        以 Event 为维度展示用户已处理过的命令。

        同时关联触发该 Session 的 DEVICE_EVENT inbox，返回 event_payload。

        Args:
            db: 数据库会话
            limit: 最大返回 Session 数量
            workline_id: 工作线 ID 过滤（可选）
            device_id: 设备 ID 过滤（可选）
        """
        from src.app.workline.models.inbox import InboxKind, WorklineInbox
        from src.app.workline.models.session import RunMode, SessionStatus, WorklineSession

        columns = cast("Any", WorklineOutbox).__table__.c
        session_columns = cast("Any", WorklineSession).__table__.c
        inbox_columns = cast("Any", WorklineInbox).__table__.c

        terminal_session_statuses = [
            SessionStatus.COMPLETED,
            SessionStatus.FAILED,
            SessionStatus.CANCELLED,
        ]

        query = (
            select(WorklineOutbox, WorklineSession, WorklineInbox)
            .join(WorklineSession, columns.session_id == session_columns.id)
            .outerjoin(
                WorklineInbox,
                (columns.session_id == inbox_columns.session_id) & (inbox_columns.kind == InboxKind.DEVICE_EVENT),
            )
            .where(
                session_columns.run_mode == RunMode.SIMULATION,
                session_columns.status.in_(terminal_session_statuses),
                columns.status == OutboxStatus.ACKED,
                columns.dispatch_type.in_([DispatchType.DEVICE_COMMAND, DispatchType.EXTERNAL_HTTP]),
            )
        )

        if workline_id is not None:
            query = query.where(columns.workline_id == workline_id)

        if device_id is not None:
            from src.app.device.models import Device

            device_columns = cast("Any", Device).__table__.c
            query = query.join(Device, columns.target_code == device_columns.device_code).where(
                device_columns.id == device_id
            )

        query = query.order_by(session_columns.created_at.desc(), columns.created_at.asc()).limit(limit * 3)
        result = await db.execute(query)
        rows = result.all()

        # Group by session
        sessions: dict[int, dict[str, Any]] = {}
        for outbox, session, inbox in rows:
            sid = session.id
            if sid not in sessions:
                # Extract event payload from the first matching inbox
                event_payload: dict[str, Any] | None = None
                event_type: str | None = None
                if inbox is not None:
                    raw = inbox.payload_json
                    if isinstance(raw, dict):
                        event_payload = dict(raw)
                        event_type = raw.get("event_type")

                sessions[sid] = {
                    "session": {
                        "id": session.id,
                        "session_code": session.session_code,
                        "status": session.status.value if hasattr(session.status, "value") else session.status,
                        "step_code": session.step_code,
                        "barcode": session.barcode,
                        "created_at": session.created_at.isoformat() if session.created_at else None,
                        "started_at": session.started_at.isoformat() if session.started_at else None,
                        "ended_at": session.ended_at.isoformat() if session.ended_at else None,
                        "event_type": event_type,
                        "event_payload": event_payload,
                    },
                    "outbox_items": [],
                }
            raw_payload = outbox.payload_json
            payload = dict(raw_payload) if isinstance(raw_payload, dict) else {}
            sessions[sid]["outbox_items"].append(
                {
                    "id": outbox.id,
                    "session_id": outbox.session_id,
                    "workline_id": outbox.workline_id,
                    "dispatch_key": outbox.dispatch_key,
                    "dispatch_type": (
                        outbox.dispatch_type.value if hasattr(outbox.dispatch_type, "value") else outbox.dispatch_type
                    ),
                    "target_type": (
                        outbox.target_type.value if hasattr(outbox.target_type, "value") else outbox.target_type
                    ),
                    "target_code": outbox.target_code,
                    "status": (outbox.status.value if hasattr(outbox.status, "value") else outbox.status),
                    "payload_json": payload,
                    "source_device": None,
                }
            )

        return list(sessions.values())[:limit]

    async def mark_as_dispatching(
        self,
        db: AsyncSession,
        outbox_id: int,
    ) -> WorklineOutbox | None:
        """标记消息为派发中

        原子更新，用于并发控制。

        Args:
            db: 数据库会话
            outbox_id: 消息 ID

        Returns:
            更新后的消息，如果已被其他进程处理则返回 None
        """
        columns = cast("Any", WorklineOutbox).__table__.c

        # 先检查当前状态
        result = await db.execute(select(WorklineOutbox).where(columns.id == outbox_id).with_for_update())
        outbox = result.scalar_one_or_none()

        if not outbox:
            return None

        if outbox.status != OutboxStatus.NEW:
            # 已被其他进程处理
            return None

        # 更新状态
        outbox.status = OutboxStatus.DISPATCHING
        await db.flush()
        return outbox

    async def mark_as_sent(
        self,
        db: AsyncSession,
        outbox_id: int,
    ) -> WorklineOutbox | None:
        """标记消息为已发送

        Args:
            db: 数据库会话
            outbox_id: 消息 ID

        Returns:
            更新后的消息
        """
        result = await db.execute(select(WorklineOutbox).where(cast("Any", WorklineOutbox).__table__.c.id == outbox_id))
        outbox = result.scalar_one_or_none()

        if not outbox:
            return None

        outbox.status = OutboxStatus.SENT
        outbox.sent_at = timezone.now_for_db()
        outbox.next_retry_at = None
        outbox.last_error = None
        await db.flush()
        return outbox

    async def mark_as_acked_by_dispatch_key(
        self,
        db: AsyncSession,
        dispatch_key: str,
    ) -> WorklineOutbox | None:
        """按 dispatch_key 标记消息为已确认。

        用于设备执行结果通过 callback/result 回到 WES 后，
        将对应的 DEVICE_COMMAND outbox 从 SENT 闭环到 ACKED。
        """
        columns = cast("Any", WorklineOutbox).__table__.c
        result = await db.execute(select(WorklineOutbox).where(columns.dispatch_key == dispatch_key))
        outbox = result.scalar_one_or_none()

        if not outbox:
            return None

        if outbox.status in {OutboxStatus.FAILED, OutboxStatus.CANCELLED}:
            return outbox

        outbox.status = OutboxStatus.ACKED
        outbox.finished_at = timezone.now_for_db()
        outbox.next_retry_at = None
        outbox.last_error = None
        await db.flush()
        return outbox

    async def get_by_dispatch_key(
        self,
        db: AsyncSession,
        dispatch_key: str,
    ) -> WorklineOutbox | None:
        """按 dispatch_key 查询 Outbox。

        Args:
            db: 数据库会话
            dispatch_key: Dispatch Key

        Returns:
            Outbox 对象或 None
        """
        columns = cast("Any", WorklineOutbox).__table__.c
        result = await db.execute(select(WorklineOutbox).where(columns.dispatch_key == dispatch_key))
        return result.scalar_one_or_none()

    async def mark_as_failed(
        self,
        db: AsyncSession,
        outbox_id: int,
        error: str,
        max_retries: int = 3,
    ) -> WorklineOutbox | None:
        """标记消息为失败，设置重试或永久失败

        Args:
            db: 数据库会话
            outbox_id: 消息 ID
            error: 错误信息
            max_retries: 最大重试次数

        Returns:
            更新后的消息
        """
        from datetime import timedelta

        result = await db.execute(select(WorklineOutbox).where(cast("Any", WorklineOutbox).__table__.c.id == outbox_id))
        outbox = result.scalar_one_or_none()

        if not outbox:
            return None

        outbox.attempt_count += 1
        outbox.last_error = error

        if outbox.attempt_count >= max_retries:
            # 达到最大重试次数，标记为失败
            outbox.status = OutboxStatus.FAILED
            outbox.finished_at = timezone.now_for_db()
        else:
            # 设置重试时间（指数退避）
            retry_delay = timedelta(seconds=30 * (2**outbox.attempt_count))
            outbox.next_retry_at = timezone.now_for_db() + retry_delay
            outbox.status = OutboxStatus.NEW  # 重置为 NEW 以便重试

        await db.flush()
        return outbox


# 创建单例
outbox_repository = WorklineOutboxRepository()
