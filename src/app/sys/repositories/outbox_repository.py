"""SystemOutbox Repository 层。"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import and_, exists, or_, select

from src.app.sys.models.outbox import SystemOutbox, SystemOutboxDispatchType, SystemOutboxStatus
from src.database.base_repository import BaseRepository
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession


class SystemOutboxRepository(BaseRepository[SystemOutbox]):
    """系统级发件箱数据访问层。"""

    DISPATCH_LEASE_SECONDS = 300

    def __init__(self) -> None:
        super().__init__(SystemOutbox)

    async def get_by_dispatch_key(self, db: AsyncSession, dispatch_key: str) -> SystemOutbox | None:
        columns = cast("Any", SystemOutbox).__table__.c
        result = await db.execute(select(SystemOutbox).where(columns.dispatch_key == dispatch_key))
        return result.scalar_one_or_none()

    async def get_pending_messages(
        self,
        db: AsyncSession,
        limit: int = 50,
        *,
        operation_domains: Sequence[str] | None = None,
        exclude_operation_domains: Sequence[str] | None = None,
    ) -> list[SystemOutbox]:
        """获取可派发消息。

        设备命令按物理设备 FIFO：优先使用 device_id，没有 device_id 时使用 target_code。
        Rack、Handling、Workline 共享同一物理设备时必须互相串行。
        """

        columns = cast("Any", SystemOutbox).__table__.c
        older_outbox = cast("Any", SystemOutbox).__table__.alias("older_device_outbox")
        now = timezone.now_for_db()
        active_device_statuses = [
            SystemOutboxStatus.NEW,
            SystemOutboxStatus.DISPATCHING,
            SystemOutboxStatus.BLOCKED_RESOURCE,
        ]
        same_physical_device = or_(
            and_(columns.device_id.isnot(None), older_outbox.c.device_id == columns.device_id),
            and_(
                columns.device_id.is_(None),
                older_outbox.c.device_id.is_(None),
                older_outbox.c.target_code == columns.target_code,
            ),
        )
        earlier_active_device_outbox_exists = exists(
            select(1)
            .select_from(older_outbox)
            .where(
                older_outbox.c.dispatch_type == SystemOutboxDispatchType.DEVICE_COMMAND,
                older_outbox.c.status.in_(active_device_statuses),
                same_physical_device,
                or_(
                    older_outbox.c.created_at < columns.created_at,
                    and_(older_outbox.c.created_at == columns.created_at, older_outbox.c.id < columns.id),
                ),
            )
        )

        domain_predicates: list[Any] = []
        if operation_domains:
            domain_predicates.append(columns.operation_domain.in_(tuple(operation_domains)))
        if exclude_operation_domains:
            domain_predicates.append(columns.operation_domain.not_in(tuple(exclude_operation_domains)))

        result = await db.execute(
            select(SystemOutbox)
            .where(
                or_(
                    and_(
                        columns.status == SystemOutboxStatus.NEW,
                        (columns.next_retry_at.is_(None)) | (columns.next_retry_at <= now),
                    ),
                    and_(
                        columns.status == SystemOutboxStatus.DISPATCHING,
                        columns.next_retry_at.isnot(None),
                        columns.next_retry_at <= now,
                    ),
                ),
                or_(
                    columns.dispatch_type != SystemOutboxDispatchType.DEVICE_COMMAND,
                    ~earlier_active_device_outbox_exists,
                ),
                *domain_predicates,
            )
            .order_by(columns.created_at.asc(), columns.id.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def mark_as_dispatching(self, db: AsyncSession, outbox_id: int) -> SystemOutbox | None:
        columns = cast("Any", SystemOutbox).__table__.c
        result = await db.execute(select(SystemOutbox).where(columns.id == outbox_id).with_for_update())
        outbox = result.scalar_one_or_none()
        if outbox is None:
            return None

        now = timezone.now_for_db()
        stale_dispatching = (
            outbox.status == SystemOutboxStatus.DISPATCHING
            and outbox.next_retry_at is not None
            and outbox.next_retry_at <= now
        )
        if outbox.status != SystemOutboxStatus.NEW and not stale_dispatching:
            return None

        outbox.status = SystemOutboxStatus.DISPATCHING
        outbox.next_retry_at = now + timedelta(seconds=self.DISPATCH_LEASE_SECONDS)
        await db.flush()
        return outbox

    async def mark_as_sent(self, db: AsyncSession, outbox_id: int) -> SystemOutbox | None:
        columns = cast("Any", SystemOutbox).__table__.c
        result = await db.execute(select(SystemOutbox).where(columns.id == outbox_id).with_for_update())
        outbox = result.scalar_one_or_none()
        if outbox is None or outbox.status != SystemOutboxStatus.DISPATCHING:
            return None
        outbox.status = SystemOutboxStatus.SENT
        outbox.sent_at = timezone.now_for_db()
        outbox.next_retry_at = None
        outbox.last_error = None
        await db.flush()
        return outbox

    async def mark_as_failed(
        self,
        db: AsyncSession,
        outbox_id: int,
        error: str,
        max_retries: int = 3,
    ) -> SystemOutbox | None:
        columns = cast("Any", SystemOutbox).__table__.c
        result = await db.execute(select(SystemOutbox).where(columns.id == outbox_id).with_for_update())
        outbox = result.scalar_one_or_none()
        if outbox is None:
            return None
        if outbox.status not in {SystemOutboxStatus.NEW, SystemOutboxStatus.DISPATCHING}:
            return None

        outbox.attempt_count += 1
        outbox.last_error = error
        if outbox.attempt_count > max_retries:
            outbox.status = SystemOutboxStatus.FAILED
            outbox.next_retry_at = None
            outbox.finished_at = timezone.now_for_db()
        else:
            outbox.status = SystemOutboxStatus.NEW
            outbox.next_retry_at = timezone.now_for_db() + timedelta(seconds=2 ** (outbox.attempt_count - 1))
        await db.flush()
        return outbox

    async def mark_as_blocked_by_workline_estop(self, db: AsyncSession, outbox_id: int) -> SystemOutbox | None:
        return await self._block_or_fail(
            db,
            outbox_id,
            status=SystemOutboxStatus.FAILED,
            reason="BLOCKED_BY_WORKLINE_ESTOP",
        )

    async def mark_as_blocked_by_workline_state(
        self,
        db: AsyncSession,
        outbox_id: int,
        *,
        owner_session_id: int,
        reason: str,
        blocked_device_id: int | None = None,
        blocked_workline_id: int | None = None,
    ) -> SystemOutbox | None:
        outbox = await self._get_active_for_block(db, outbox_id)
        if outbox is None:
            return None
        outbox.status = SystemOutboxStatus.BLOCKED_RESOURCE
        outbox.blocked_by_reconciliation_session_id = owner_session_id
        outbox.blocked_device_id = blocked_device_id
        outbox.blocked_workline_id = blocked_workline_id or outbox.workline_id
        outbox.blocked_reason = reason
        outbox.last_error = reason
        outbox.next_retry_at = None
        outbox.finished_at = timezone.now_for_db()
        await db.flush()
        return outbox

    async def block_by_runtime_hold(
        self,
        db: AsyncSession,
        outbox_id: int,
        *,
        runtime_hold_id: int,
        reason: str,
        owner_session_id: int | None = None,
        blocked_device_id: int | None = None,
        blocked_workline_id: int | None = None,
    ) -> SystemOutbox | None:
        outbox = await self._get_active_for_block(db, outbox_id)
        if outbox is None:
            return None
        outbox.status = SystemOutboxStatus.BLOCKED_RESOURCE
        outbox.blocked_by_runtime_hold_id = runtime_hold_id
        outbox.blocked_by_reconciliation_session_id = owner_session_id
        outbox.blocked_device_id = blocked_device_id
        outbox.blocked_workline_id = blocked_workline_id or outbox.workline_id
        outbox.blocked_reason = reason
        outbox.last_error = reason
        outbox.next_retry_at = None
        outbox.finished_at = timezone.now_for_db()
        await db.flush()
        return outbox

    async def mark_as_blocked_by_device_busy(
        self,
        db: AsyncSession,
        outbox_id: int,
        *,
        blocked_device_id: int | None,
        blocked_workline_id: int | None = None,
        reason: str = "DEVICE_BUSY",
        last_error: str | None = None,
    ) -> SystemOutbox | None:
        outbox = await self._get_active_for_block(db, outbox_id)
        if outbox is None:
            return None
        outbox.status = SystemOutboxStatus.BLOCKED_RESOURCE
        outbox.blocked_by_reconciliation_session_id = None
        outbox.blocked_device_id = blocked_device_id
        outbox.blocked_workline_id = blocked_workline_id or outbox.workline_id
        outbox.blocked_reason = reason
        outbox.last_error = last_error or reason
        outbox.next_retry_at = None
        outbox.finished_at = timezone.now_for_db()
        await db.flush()
        return outbox

    async def mark_blocked_device_busy_as_sent(self, db: AsyncSession, outbox_id: int) -> SystemOutbox | None:
        columns = cast("Any", SystemOutbox).__table__.c
        result = await db.execute(select(SystemOutbox).where(columns.id == outbox_id).with_for_update())
        outbox = result.scalar_one_or_none()
        if outbox is None:
            return None
        if outbox.status != SystemOutboxStatus.BLOCKED_RESOURCE or outbox.blocked_reason != "DEVICE_BUSY":
            return None
        outbox.status = SystemOutboxStatus.SENT
        outbox.sent_at = outbox.sent_at or timezone.now_for_db()
        self._clear_block(outbox)
        await db.flush()
        return outbox

    async def get_dispatching_device_messages(self, db: AsyncSession, limit: int = 50) -> list[SystemOutbox]:
        columns = cast("Any", SystemOutbox).__table__.c
        result = await db.execute(
            select(SystemOutbox)
            .where(
                columns.status == SystemOutboxStatus.DISPATCHING,
                columns.dispatch_type == SystemOutboxDispatchType.DEVICE_COMMAND,
                columns.sent_at.is_(None),
                columns.finished_at.is_(None),
            )
            .order_by(columns.created_at.asc(), columns.id.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_blocked_device_busy_messages(self, db: AsyncSession, limit: int = 50) -> list[SystemOutbox]:
        columns = cast("Any", SystemOutbox).__table__.c
        result = await db.execute(
            select(SystemOutbox)
            .where(
                columns.status == SystemOutboxStatus.BLOCKED_RESOURCE,
                columns.dispatch_type == SystemOutboxDispatchType.DEVICE_COMMAND,
                columns.blocked_reason == "DEVICE_BUSY",
            )
            .order_by(columns.created_at.asc(), columns.id.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def cancel_active_by_workline(self, db: AsyncSession, workline_id: int, *, incident_id: int) -> int:
        columns = cast("Any", SystemOutbox).__table__.c
        active_statuses = [
            SystemOutboxStatus.NEW,
            SystemOutboxStatus.DISPATCHING,
            SystemOutboxStatus.SENT,
            SystemOutboxStatus.BLOCKED_RESOURCE,
        ]
        result = await db.execute(
            select(SystemOutbox)
            .where(columns.workline_id == workline_id, columns.status.in_(active_statuses))
            .with_for_update()
        )
        outboxes = list(result.scalars().all())
        now = timezone.now_for_db()
        for outbox in outboxes:
            outbox.status = SystemOutboxStatus.CANCELLED
            outbox.last_error = "CANCELLED_BY_ESTOP"
            outbox.finished_at = now
            outbox.payload_json = {
                **(outbox.payload_json or {}),
                "cancelled_by_safety_incident_id": incident_id,
            }
        return len(outboxes)

    async def cancel_active_by_session(self, db: AsyncSession, *, session_id: int, reason: str) -> int:
        columns = cast("Any", SystemOutbox).__table__.c
        active_statuses = [SystemOutboxStatus.NEW, SystemOutboxStatus.DISPATCHING, SystemOutboxStatus.SENT]
        result = await db.execute(
            select(SystemOutbox)
            .where(columns.session_id == session_id, columns.status.in_(active_statuses))
            .with_for_update()
        )
        outboxes = list(result.scalars().all())
        now = timezone.now_for_db()
        for outbox in outboxes:
            outbox.status = SystemOutboxStatus.CANCELLED
            outbox.last_error = reason
            outbox.finished_at = now
        return len(outboxes)

    async def release_blocked_by_reconciliation_session(self, db: AsyncSession, owner_session_id: int) -> int:
        columns = cast("Any", SystemOutbox).__table__.c
        return await self._release_blocked(
            db,
            columns.blocked_by_reconciliation_session_id == owner_session_id,
        )

    async def release_blocked_by_runtime_hold(self, db: AsyncSession, runtime_hold_id: int) -> int:
        columns = cast("Any", SystemOutbox).__table__.c
        return await self._release_blocked(db, columns.blocked_by_runtime_hold_id == runtime_hold_id)

    async def release_blocked_by_runtime_hold_or_workline(
        self,
        db: AsyncSession,
        *,
        runtime_hold_id: int,
        workline_id: int,
        release_workline_scope: bool,
    ) -> int:
        released_count = await self.release_blocked_by_runtime_hold(db, runtime_hold_id)
        if release_workline_scope:
            released_count += await self.release_blocked_by_workline(db, workline_id)
        return released_count

    async def release_blocked_by_workline(self, db: AsyncSession, workline_id: int) -> int:
        columns = cast("Any", SystemOutbox).__table__.c
        return await self._release_blocked(
            db,
            columns.workline_id == workline_id,
            columns.blocked_by_runtime_hold_id.is_(None),
            or_(
                columns.blocked_workline_id == workline_id,
                columns.blocked_by_reconciliation_session_id.isnot(None),
            ),
        )

    async def release_blocked_by_device(
        self,
        db: AsyncSession,
        *,
        device_id: int,
        workline_id: int | None = None,
    ) -> int:
        columns = cast("Any", SystemOutbox).__table__.c
        conditions: list[Any] = [
            columns.blocked_device_id == device_id,
            columns.blocked_reason == "DEVICE_BUSY",
        ]
        if workline_id is not None:
            conditions.append(columns.workline_id == workline_id)
        return await self._release_blocked(db, *conditions)

    async def get_sandbox_pending_messages(
        self,
        db: AsyncSession,
        limit: int = 50,
        workline_id: int | None = None,
        device_id: int | None = None,
    ) -> list[SystemOutbox]:
        from src.app.device.models import Device
        from src.app.workline.models.session import RunMode, SessionStatus, WorklineSession

        columns = cast("Any", SystemOutbox).__table__.c
        session_columns = cast("Any", WorklineSession).__table__.c
        open_session_statuses = [
            SessionStatus.NEW,
            SessionStatus.RUNNING,
            SessionStatus.WAITING_DEVICE_RESULT,
            SessionStatus.WAITING_EXTERNAL,
            SessionStatus.MANUAL_HOLD,
        ]
        query = (
            select(SystemOutbox)
            .join(WorklineSession, columns.session_id == session_columns.id)
            .where(
                session_columns.run_mode == RunMode.SIMULATION,
                session_columns.status.in_(open_session_statuses),
                columns.dispatch_type.in_(
                    [SystemOutboxDispatchType.DEVICE_COMMAND, SystemOutboxDispatchType.EXTERNAL_HTTP]
                ),
                columns.status.in_(
                    [
                        SystemOutboxStatus.NEW,
                        SystemOutboxStatus.DISPATCHING,
                        SystemOutboxStatus.SENT,
                        SystemOutboxStatus.BLOCKED_RESOURCE,
                        SystemOutboxStatus.FAILED,
                    ]
                ),
            )
        )
        if workline_id is not None:
            query = query.where(columns.workline_id == workline_id)
        if device_id is not None:
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
        """获取沙箱已完成 outbox，按 Session 分组。"""

        from src.app.device.models import Device
        from src.app.workline.models.inbox import InboxKind, WorklineInbox
        from src.app.workline.models.session import RunMode, SessionStatus, WorklineSession

        columns = cast("Any", SystemOutbox).__table__.c
        session_columns = cast("Any", WorklineSession).__table__.c
        inbox_columns = cast("Any", WorklineInbox).__table__.c
        query = (
            select(SystemOutbox, WorklineSession, WorklineInbox)
            .join(WorklineSession, columns.session_id == session_columns.id)
            .outerjoin(
                WorklineInbox,
                (columns.session_id == inbox_columns.session_id) & (inbox_columns.kind == InboxKind.DEVICE_EVENT),
            )
            .where(
                session_columns.run_mode == RunMode.SIMULATION,
                session_columns.status.in_([SessionStatus.COMPLETED, SessionStatus.FAILED, SessionStatus.CANCELLED]),
                columns.status.in_([SystemOutboxStatus.SENT, SystemOutboxStatus.CANCELLED, SystemOutboxStatus.FAILED]),
                columns.dispatch_type.in_(
                    [SystemOutboxDispatchType.DEVICE_COMMAND, SystemOutboxDispatchType.EXTERNAL_HTTP]
                ),
            )
        )
        if workline_id is not None:
            query = query.where(columns.workline_id == workline_id)
        if device_id is not None:
            device_columns = cast("Any", Device).__table__.c
            query = query.join(Device, columns.target_code == device_columns.device_code).where(
                device_columns.id == device_id
            )
        result = await db.execute(
            query.order_by(session_columns.created_at.desc(), columns.created_at.asc()).limit(limit * 3)
        )
        rows = result.all()
        sessions: dict[int, dict[str, Any]] = {}
        for outbox, session, inbox in rows:
            sid = session.id
            if sid not in sessions:
                event_payload: dict[str, Any] | None = None
                event_type: str | None = None
                if inbox is not None and isinstance(inbox.payload_json, dict):
                    event_payload = dict(inbox.payload_json)
                    event_type = inbox.payload_json.get("event_type")
                sessions[sid] = {
                    "history_group_key": f"session:{sid}",
                    "session": {
                        "id": session.id,
                        "session_code": session.session_code,
                        "status": session.status.value if hasattr(session.status, "value") else session.status,
                        "awaiting_command_id": session.awaiting_command_id,
                        "barcode": session.barcode,
                        "created_at": session.created_at.isoformat() if session.created_at else None,
                        "started_at": session.started_at.isoformat() if session.started_at else None,
                        "ended_at": session.ended_at.isoformat() if session.ended_at else None,
                        "event_type": event_type,
                        "event_payload": event_payload,
                        "failure_domain": session.failure_domain,
                        "failure_code": session.failure_code,
                        "failure_message": session.failure_message,
                    },
                    "outbox_items": [],
                }
            payload = dict(outbox.payload_json) if isinstance(outbox.payload_json, dict) else {}
            sessions[sid]["outbox_items"].append(
                {
                    "id": outbox.id,
                    "session_id": outbox.session_id,
                    "workline_id": outbox.workline_id,
                    "dispatch_key": outbox.dispatch_key,
                    "dispatch_type": _enum_value(outbox.dispatch_type),
                    "target_type": _enum_value(outbox.target_type),
                    "target_code": outbox.target_code,
                    "status": _enum_value(outbox.status),
                    "last_error": outbox.last_error,
                    "is_actionable": False,
                    "runtime_hold_id": outbox.blocked_by_runtime_hold_id,
                    "payload_json": payload,
                    "source_device": None,
                    "failure_summary": {
                        "code": session.failure_code or outbox.last_error,
                        "message": session.failure_message or outbox.last_error,
                        "runtime_hold_id": outbox.blocked_by_runtime_hold_id,
                    },
                    "history_group_key": f"session:{sid}",
                }
            )
        return list(sessions.values())[:limit]

    async def _block_or_fail(
        self,
        db: AsyncSession,
        outbox_id: int,
        *,
        status: SystemOutboxStatus,
        reason: str,
    ) -> SystemOutbox | None:
        outbox = await self._get_active_for_block(db, outbox_id)
        if outbox is None:
            return None
        outbox.status = status
        outbox.last_error = reason
        outbox.next_retry_at = None
        outbox.finished_at = timezone.now_for_db()
        await db.flush()
        return outbox

    async def _get_active_for_block(self, db: AsyncSession, outbox_id: int) -> SystemOutbox | None:
        columns = cast("Any", SystemOutbox).__table__.c
        result = await db.execute(select(SystemOutbox).where(columns.id == outbox_id).with_for_update())
        outbox = result.scalar_one_or_none()
        if outbox is None:
            return None
        if outbox.status not in {SystemOutboxStatus.NEW, SystemOutboxStatus.DISPATCHING}:
            return None
        return outbox

    async def _release_blocked(self, db: AsyncSession, *conditions: Any) -> int:
        columns = cast("Any", SystemOutbox).__table__.c
        result = await db.execute(
            select(SystemOutbox)
            .where(columns.status == SystemOutboxStatus.BLOCKED_RESOURCE, *conditions)
            .with_for_update()
        )
        outboxes = list(result.scalars().all())
        for outbox in outboxes:
            self._release_blocked_outbox(outbox)
        await db.flush()
        return len(outboxes)

    @staticmethod
    def _release_blocked_outbox(outbox: SystemOutbox) -> None:
        outbox.status = SystemOutboxStatus.NEW
        outbox.attempt_count = 0
        SystemOutboxRepository._clear_block(outbox)

    @staticmethod
    def _clear_block(outbox: SystemOutbox) -> None:
        outbox.next_retry_at = None
        outbox.last_error = None
        outbox.finished_at = None
        outbox.blocked_by_runtime_hold_id = None
        outbox.blocked_by_reconciliation_session_id = None
        outbox.blocked_device_id = None
        outbox.blocked_workline_id = None
        outbox.blocked_reason = None


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


system_outbox_repository = SystemOutboxRepository()
outbox_repository = system_outbox_repository

__all__ = ["SystemOutboxRepository", "outbox_repository", "system_outbox_repository"]
