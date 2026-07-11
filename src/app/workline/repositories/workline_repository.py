"""WorkLine Repository 层"""

from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.contracts.runtime_inbox_query import RuntimeInboxQueryPort
from src.app.device.models.command import CommandStatus, DeviceCommand
from src.app.runtime.orchestration.models import SessionStatus, WorklineSession
from src.app.runtime.orchestration.repositories.runtime_hold_repository import runtime_hold_repository
from src.app.sys.models.outbox import SystemOutbox, SystemOutboxStatus
from src.app.workline.models import WorkLine
from src.database.base_repository import BaseRepository


class WorkLineRepository(BaseRepository[WorkLine]):
    """作业线数据访问层"""

    def __init__(self, *, runtime_inbox_query: RuntimeInboxQueryPort) -> None:
        """初始化作业线仓库"""
        super().__init__(WorkLine)
        self.runtime_inbox_query = runtime_inbox_query

    async def get_by_line_code(
        self,
        db: AsyncSession,
        line_code: str,
    ) -> WorkLine | None:
        """根据作业线编码查询"""
        columns = cast("Any", WorkLine).__table__.c
        result = await db.execute(
            select(WorkLine).where(
                columns.line_code == line_code,
                columns.is_deleted.is_(False),
            )
        )
        return result.scalar_one_or_none()

    async def get_for_update(
        self,
        db: AsyncSession,
        workline_id: int,
        *,
        populate_existing: bool = False,
    ) -> WorkLine | None:
        """根据 ID 查询并锁定 WorkLine，用于安全状态切换。"""

        columns = cast("Any", WorkLine).__table__.c
        statement = (
            select(WorkLine)
            .where(
                columns.id == workline_id,
                columns.is_deleted.is_(False),
            )
            .with_for_update()
        )
        if populate_existing:
            statement = statement.execution_options(populate_existing=True)
        result = await db.execute(statement)
        return result.scalar_one_or_none()

    async def get_unfinished_workload_summary(
        self,
        db: AsyncSession,
        workline_id: int,
    ) -> dict[str, Any]:
        """返回 WorkLine 未完成运行负载摘要，避免停用前全量加载对象。"""

        session_columns = cast("Any", WorklineSession).__table__.c
        session_terminal_statuses = [
            SessionStatus.COMPLETED.value,
            SessionStatus.FAILED.value,
            SessionStatus.CANCELLED.value,
        ]
        session_where = (
            session_columns.workline_id == workline_id,
            session_columns.status.not_in(session_terminal_statuses),
        )
        command_columns = cast("Any", DeviceCommand).__table__.c
        command_where = (
            command_columns.workline_id == workline_id,
            command_columns.status.in_(
                [
                    CommandStatus.PENDING.value,
                    CommandStatus.SENT.value,
                    CommandStatus.ACK_RECEIVED.value,
                ]
            ),
        )
        outbox_columns = cast("Any", SystemOutbox).__table__.c
        outbox_where = (
            outbox_columns.workline_id == workline_id,
            outbox_columns.status.in_(
                [
                    SystemOutboxStatus.NEW.value,
                    SystemOutboxStatus.DISPATCHING.value,
                    SystemOutboxStatus.BLOCKED_RESOURCE.value,
                ]
            ),
        )
        session_count = await self._count(db, WorklineSession, session_where)
        command_count = await self._count(db, DeviceCommand, command_where)
        outbox_count = await self._count(db, SystemOutbox, outbox_where)
        # FAILED 即使尚未到 next_retry_at 仍是未完成负载；仅 PROCESSED/DEAD_LETTER 为终态。
        inbox_count = await self.runtime_inbox_query.count_unfinished_by_workline(db, workline_id)
        runtime_hold_count = await runtime_hold_repository.count_active_by_workline(db, workline_id)

        sample = await self._first_workload_sample(
            db,
            session_columns,
            command_columns,
            outbox_columns,
            session_where,
            command_where,
            outbox_where,
            workline_id,
            runtime_hold_count,
        )
        by_type = {
            "sessions": session_count,
            "commands": command_count,
            "outboxes": outbox_count,
            "inboxes": inbox_count,
            "runtime_holds": runtime_hold_count,
        }
        return {
            "count": sum(by_type.values()),
            "sample": sample,
            "by_type": by_type,
        }

    @staticmethod
    async def _count(db: AsyncSession, model: type[Any], where_conditions: tuple[Any, ...]) -> int:
        result = await db.execute(select(func.count()).select_from(model).where(*where_conditions))
        return int(result.scalar_one() or 0)

    async def _first_workload_sample(
        self,
        db: AsyncSession,
        session_columns: Any,
        command_columns: Any,
        outbox_columns: Any,
        session_where: tuple[Any, ...],
        command_where: tuple[Any, ...],
        outbox_where: tuple[Any, ...],
        workline_id: int,
        runtime_hold_count: int,
    ) -> dict[str, Any] | None:
        session_sample = await db.execute(
            select(session_columns.session_code, session_columns.status)
            .where(*session_where)
            .order_by(session_columns.id.asc())
            .limit(1)
        )
        session_row = session_sample.first()
        if session_row:
            return {"type": "session", "session_code": session_row[0], "status": session_row[1]}

        command_sample = await db.execute(
            select(command_columns.command_code, command_columns.status)
            .where(*command_where)
            .order_by(command_columns.id.asc())
            .limit(1)
        )
        command_row = command_sample.first()
        if command_row:
            return {"type": "command", "command_code": command_row[0], "status": command_row[1]}

        outbox_sample = await db.execute(
            select(outbox_columns.dispatch_key, outbox_columns.status)
            .where(*outbox_where)
            .order_by(outbox_columns.id.asc())
            .limit(1)
        )
        outbox_row = outbox_sample.first()
        if outbox_row:
            return {"type": "outbox", "dispatch_key": outbox_row[0], "status": outbox_row[1]}

        inbox_sample = await self.runtime_inbox_query.first_unfinished_by_workline(db, workline_id)
        if inbox_sample is not None:
            return {"type": "inbox", "inbox_id": inbox_sample.id, "status": inbox_sample.status}
        if runtime_hold_count:
            return {"type": "runtime_hold", "count": runtime_hold_count, "status": "ACTIVE_BLOCKING"}
        return None
