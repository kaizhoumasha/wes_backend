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
        """获取待派发候选消息

        只查询 NEW 状态且未到重试时间或重试时间已过的消息。这里不加行锁；
        真正的并发领取由 mark_as_dispatching 完成，避免 dispatcher 在外部 I/O 前
        长时间持有候选 outbox 锁。

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
        ACK 事实只写 DeviceCommand，Outbox 保持 SENT 等待调试人员手工 callback/result 回传。

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
        # 以及已 ACK 但等待 Result 回传的 SENT Outbox (ACK 事实在 DeviceCommand 上)
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
                    and_(
                        columns.status == OutboxStatus.SENT,
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
        Outbox 已派发或已终止的记录。按 Session 分组返回，用于 Sandbox 页面
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
                columns.status.in_([OutboxStatus.SENT, OutboxStatus.CANCELLED, OutboxStatus.FAILED]),
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

    async def mark_as_blocked_by_workline_estop(
        self,
        db: AsyncSession,
        outbox_id: int,
    ) -> WorklineOutbox | None:
        """因 WorkLine ESTOP 本地终止待派发 outbox，不进入普通重试路径。"""

        columns = cast("Any", WorklineOutbox).__table__.c
        result = await db.execute(select(WorklineOutbox).where(columns.id == outbox_id).with_for_update())
        outbox = result.scalar_one_or_none()

        if not outbox:
            return None

        if outbox.status not in {OutboxStatus.NEW, OutboxStatus.DISPATCHING}:
            return None

        outbox.status = OutboxStatus.FAILED
        outbox.last_error = "BLOCKED_BY_WORKLINE_ESTOP"
        outbox.next_retry_at = None
        outbox.finished_at = timezone.now_for_db()
        await db.flush()
        return outbox

    async def mark_as_blocked_by_workline_state(
        self,
        db: AsyncSession,
        outbox_id: int,
        *,
        owner_session_id: int,
        reason: str,
        blocked_device_id: int | None = None,
        blocked_workline_id: int | None = None,
    ) -> WorklineOutbox | None:
        """因 WorkLine runtime reconciliation 暂停派发 outbox。

        等待 owner session resolve 后释放。
        """

        columns = cast("Any", WorklineOutbox).__table__.c
        result = await db.execute(select(WorklineOutbox).where(columns.id == outbox_id).with_for_update())
        outbox = result.scalar_one_or_none()

        if not outbox:
            return None

        if outbox.status not in {OutboxStatus.NEW, OutboxStatus.DISPATCHING}:
            return None

        outbox.status = OutboxStatus.BLOCKED_RESOURCE
        outbox.blocked_by_reconciliation_session_id = owner_session_id
        outbox.blocked_device_id = blocked_device_id
        outbox.blocked_workline_id = blocked_workline_id or outbox.workline_id
        outbox.blocked_reason = reason
        outbox.last_error = reason
        outbox.next_retry_at = None
        outbox.finished_at = timezone.now_for_db()
        await db.flush()
        return outbox

    async def cancel_active_by_workline(
        self,
        db: AsyncSession,
        workline_id: int,
        *,
        incident_id: int,
    ) -> int:
        """取消 WorkLine 尚未闭环的 outbox 消息。"""

        columns = cast("Any", WorklineOutbox).__table__.c
        active_statuses = [
            OutboxStatus.NEW,
            OutboxStatus.DISPATCHING,
            OutboxStatus.SENT,
            OutboxStatus.BLOCKED_RESOURCE,
        ]
        result = await db.execute(
            select(WorklineOutbox)
            .where(
                columns.workline_id == workline_id,
                columns.status.in_(active_statuses),
            )
            .with_for_update()
        )
        outboxes = list(result.scalars().all())
        now = timezone.now_for_db()
        for outbox in outboxes:
            outbox.status = OutboxStatus.CANCELLED
            outbox.last_error = "CANCELLED_BY_ESTOP"
            outbox.finished_at = now
            outbox.payload_json = {
                **(outbox.payload_json or {}),
                "cancelled_by_safety_incident_id": incident_id,
            }
        return len(outboxes)

    async def cancel_active_by_session(
        self,
        db: AsyncSession,
        *,
        session_id: int,
        reason: str,
    ) -> int:
        """取消指定 Session 尚未 ACK 的 outbox，避免终态会话残留可操作项。"""

        columns = cast("Any", WorklineOutbox).__table__.c
        active_statuses = [OutboxStatus.NEW, OutboxStatus.DISPATCHING, OutboxStatus.SENT]
        result = await db.execute(
            select(WorklineOutbox)
            .where(
                columns.session_id == session_id,
                columns.status.in_(active_statuses),
            )
            .with_for_update()
        )
        outboxes = list(result.scalars().all())
        now = timezone.now_for_db()
        for outbox in outboxes:
            outbox.status = OutboxStatus.CANCELLED
            outbox.last_error = reason
            outbox.finished_at = now
        return len(outboxes)

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
        result = await db.execute(
            select(WorklineOutbox).where(cast("Any", WorklineOutbox).__table__.c.id == outbox_id).with_for_update()
        )
        outbox = result.scalar_one_or_none()

        if not outbox:
            return None
        if outbox.status != OutboxStatus.DISPATCHING:
            return None

        outbox.status = OutboxStatus.SENT
        outbox.sent_at = timezone.now_for_db()
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

        result = await db.execute(
            select(WorklineOutbox).where(cast("Any", WorklineOutbox).__table__.c.id == outbox_id).with_for_update()
        )
        outbox = result.scalar_one_or_none()

        if not outbox:
            return None
        if outbox.status not in {OutboxStatus.NEW, OutboxStatus.DISPATCHING}:
            return None

        outbox.attempt_count += 1
        outbox.last_error = error

        if outbox.attempt_count > max_retries:
            # 达到最大重试次数，标记为失败
            outbox.status = OutboxStatus.FAILED
            outbox.next_retry_at = None
            outbox.finished_at = timezone.now_for_db()
        else:
            # 白皮书通信 ACK 重试退避：1s, 2s, 4s；max_retries 不含首次尝试。
            retry_delay = timedelta(seconds=2 ** (outbox.attempt_count - 1))
            outbox.next_retry_at = timezone.now_for_db() + retry_delay
            outbox.status = OutboxStatus.NEW  # 重置为 NEW 以便重试

        await db.flush()
        return outbox

    async def release_blocked_by_reconciliation_session(
        self,
        db: AsyncSession,
        owner_session_id: int,
    ) -> int:
        """释放指定 runtime reconciliation owner 暂停的 outbox。

        保持原 outbox/command/dispatch_key 不变。
        """

        columns = cast("Any", WorklineOutbox).__table__.c
        result = await db.execute(
            select(WorklineOutbox)
            .where(
                columns.status == OutboxStatus.BLOCKED_RESOURCE,
                columns.blocked_by_reconciliation_session_id == owner_session_id,
            )
            .with_for_update()
        )
        outboxes = list(result.scalars().all())
        for outbox in outboxes:
            outbox.status = OutboxStatus.NEW
            outbox.attempt_count = 0
            outbox.next_retry_at = None
            outbox.last_error = None
            outbox.finished_at = None
            outbox.blocked_by_reconciliation_session_id = None
            outbox.blocked_device_id = None
            outbox.blocked_workline_id = None
            outbox.blocked_reason = None
        await db.flush()
        return len(outboxes)


# 创建单例
outbox_repository = WorklineOutboxRepository()
