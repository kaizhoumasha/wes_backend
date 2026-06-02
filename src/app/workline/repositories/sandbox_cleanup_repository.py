"""沙箱工作线清理候选数据访问层。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import delete, or_, select, update

from src.app.device.models import Device, DeviceCommand, DeviceStatus
from src.app.rack.models import RackTask
from src.app.sys.models import SystemOutbox
from src.app.workline.models import WorkLine, WorkLineRunMode
from src.app.workline.models.bin_cell_reservation import WorklineBinCellReservation
from src.app.workline.models.diagnostic import WorklineDiagnostic
from src.app.workline.models.dispatch_attempt import WorklineDispatchAttempt
from src.app.workline.models.inbox import WorklineInbox
from src.app.workline.models.runtime_hold import NgReturnItem, RuntimeHold
from src.app.workline.models.safety import WorkLineRuntimeStatus, WorklineSafetyIncident
from src.app.workline.models.session import RunMode, WorklineSession
from src.app.workline.models.timeline import WorklineTimeline

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


COUNT_KEYS = (
    "sessions",
    "inboxes",
    "outboxes",
    "commands",
    "runtime_holds",
    "ng_return_items",
    "rack_tasks",
    "bin_cell_reservations",
    "timelines",
    "diagnostics",
    "dispatch_attempts",
    "safety_incidents",
)
ID_CHUNK_SIZE = 500


@dataclass(frozen=True, slots=True)
class SandboxCleanupSelection:
    """沙箱清理候选 ID 集合。

    preview 与 execute 共用同一选择集，避免预览计数和实际删除范围漂移。
    """

    sessions: list[int]
    inboxes: list[int]
    outboxes: list[int]
    commands: list[int]
    runtime_holds: list[int]
    ng_return_items: list[int]
    rack_tasks: list[int]
    bin_cell_reservations: list[int]
    timelines: list[int]
    diagnostics: list[int]
    dispatch_attempts: list[int]
    safety_incidents: list[int]

    def counts(self) -> dict[str, int]:
        """返回前端契约使用的稳定计数 key。"""

        return {key: len(getattr(self, key)) for key in COUNT_KEYS}


class SandboxCleanupRepository:
    """沙箱清理候选数据访问层。"""

    async def get_workline(self, db: AsyncSession, workline_id: int) -> WorkLine | None:
        """按 ID 查询工作线。"""

        columns = cast("Any", WorkLine).__table__.c
        result = await db.execute(select(WorkLine).where(columns.id == workline_id))
        return result.scalar_one_or_none()

    async def get_workline_for_update(self, db: AsyncSession, workline_id: int) -> WorkLine | None:
        """按 ID 查询并锁定工作线，用于串行化执行清理。"""

        columns = cast("Any", WorkLine).__table__.c
        result = await db.execute(select(WorkLine).where(columns.id == workline_id).with_for_update())
        return result.scalar_one_or_none()

    async def collect_selection(self, db: AsyncSession, workline_id: int) -> SandboxCleanupSelection:
        """收集单条 SIMULATION 工作线的沙箱运行时候选 ID。"""

        session_ids = await self._collect_ids(db, self._sandbox_session_ids_stmt(workline_id))
        inbox_ids = await self._collect_ids(db, self._sandbox_inbox_ids_stmt(workline_id))
        outbox_ids = await self._collect_ids(db, self._sandbox_outbox_ids_stmt(workline_id))
        command_ids = await self._collect_ids(db, self._sandbox_command_ids_stmt(workline_id))
        runtime_hold_ids = await self._collect_ids(db, self._sandbox_runtime_hold_ids_stmt(workline_id))
        ng_return_item_ids = await self._collect_ids(db, self._sandbox_ng_return_item_ids_stmt(workline_id))
        rack_task_ids = await self._collect_ids(db, self._sandbox_rack_task_ids_stmt(workline_id))
        bin_cell_reservation_ids = await self._collect_ids(db, self._sandbox_bin_cell_reservation_ids_stmt(workline_id))
        timeline_ids = await self._collect_ids(db, self._sandbox_timeline_ids_stmt(workline_id))
        diagnostic_ids = await self._collect_ids(db, self._sandbox_diagnostic_ids_stmt(workline_id))
        dispatch_attempt_ids = await self._collect_ids(db, self._sandbox_dispatch_attempt_ids_stmt(workline_id))
        safety_incident_ids = await self._collect_ids(db, self._sandbox_safety_incident_ids_stmt(workline_id))

        return SandboxCleanupSelection(
            sessions=session_ids,
            inboxes=inbox_ids,
            outboxes=outbox_ids,
            commands=command_ids,
            runtime_holds=runtime_hold_ids,
            ng_return_items=ng_return_item_ids,
            rack_tasks=rack_task_ids,
            bin_cell_reservations=bin_cell_reservation_ids,
            timelines=timeline_ids,
            diagnostics=diagnostic_ids,
            dispatch_attempts=dispatch_attempt_ids,
            safety_incidents=safety_incident_ids,
        )

    async def execute_cleanup(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        selection: SandboxCleanupSelection,
    ) -> None:
        """清理单条 SIMULATION 工作线的沙箱运行时数据并重置运行状态。"""

        await self._clear_cyclic_refs(db, workline_id=workline_id, selection=selection)
        await db.flush()

        await self._delete_by_ids(db, WorklineTimeline, selection.timelines)
        await self._delete_by_ids(db, WorklineDiagnostic, selection.diagnostics)
        await self._delete_by_ids(db, WorklineDispatchAttempt, selection.dispatch_attempts)
        await self._delete_by_ids(db, WorklineBinCellReservation, selection.bin_cell_reservations)
        await self._delete_by_ids(db, RackTask, selection.rack_tasks)
        await self._delete_by_ids(db, NgReturnItem, selection.ng_return_items)
        await self._delete_by_ids(db, WorklineSafetyIncident, selection.safety_incidents)
        await self._delete_by_ids(db, RuntimeHold, selection.runtime_holds)
        await self._delete_by_ids(db, SystemOutbox, selection.outboxes)
        await self._delete_by_ids(db, WorklineInbox, selection.inboxes)
        await self._delete_by_ids(db, DeviceCommand, selection.commands)
        await self._delete_by_ids(db, WorklineSession, selection.sessions)
        await self._reset_workline_runtime_state(db, workline_id)
        await db.flush()

    async def _collect_ids(self, db: AsyncSession, stmt: Any) -> list[int]:
        result = await db.execute(stmt)
        return [item_id for item_id in result.scalars().all() if item_id is not None]

    async def _clear_cyclic_refs(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        selection: SandboxCleanupSelection,
    ) -> None:
        outbox_columns = cast("Any", SystemOutbox).__table__.c
        for hold_ids in self._chunks(selection.runtime_holds):
            # 解除所有指向待删 RuntimeHold 的 FK；这些行不一定属于本次删除集合，但必须先断开指针。
            await db.execute(
                update(SystemOutbox)
                .where(outbox_columns.blocked_by_runtime_hold_id.in_(hold_ids))
                .values(blocked_by_runtime_hold_id=None)
            )
        for outbox_ids in self._chunks(selection.outboxes):
            await db.execute(
                update(SystemOutbox).where(outbox_columns.id.in_(outbox_ids)).values(blocked_by_runtime_hold_id=None)
            )

        hold_columns = cast("Any", RuntimeHold).__table__.c
        for hold_ids in self._chunks(selection.runtime_holds):
            await db.execute(
                update(RuntimeHold)
                .where(hold_columns.reopened_from_hold_id.in_(hold_ids))
                .values(reopened_from_hold_id=None)
            )

        session_columns = cast("Any", WorklineSession).__table__.c
        for session_ids in self._chunks(selection.sessions):
            await db.execute(
                update(WorklineSession)
                .where(
                    session_columns.id.in_(session_ids),
                    session_columns.workline_id == workline_id,
                )
                .values(awaiting_command_id=None)
            )

        device_columns = cast("Any", Device).__table__.c
        for command_ids in self._chunks(selection.commands):
            await db.execute(
                update(Device)
                .where(
                    device_columns.work_line_id == workline_id,
                    device_columns.current_command_id.in_(command_ids),
                )
                .values(
                    current_command_id=None,
                    device_status=DeviceStatus.IDLE,
                    error_code=None,
                )
            )

    async def _delete_by_ids(self, db: AsyncSession, model: type[Any], ids: list[int]) -> None:
        columns = cast("Any", model).__table__.c
        for id_batch in self._chunks(ids):
            await db.execute(delete(model).where(columns.id.in_(id_batch)))

    async def _reset_workline_runtime_state(self, db: AsyncSession, workline_id: int) -> None:
        columns = cast("Any", WorkLine).__table__.c
        await db.execute(
            update(WorkLine)
            .where(columns.id == workline_id)
            .values(
                runtime_status=WorkLineRuntimeStatus.STOPPED,
                active_safety_incident_id=None,
                stopped_at=None,
                stopped_reason=None,
                resumed_at=None,
                start_admission_status=None,
                start_admission_message=None,
                start_admission_failed_device_code=None,
                start_admission_checked_at=None,
                last_start_request_id=None,
                last_start_trace_id=None,
            )
        )

    def _chunks(self, ids: list[int]) -> list[list[int]]:
        return [ids[index : index + ID_CHUNK_SIZE] for index in range(0, len(ids), ID_CHUNK_SIZE)]

    def _sandbox_session_ids_stmt(self, workline_id: int) -> Any:
        columns = cast("Any", WorklineSession).__table__.c
        return (
            select(columns.id)
            .where(
                columns.workline_id == workline_id,
                columns.run_mode == RunMode.SIMULATION,
            )
            .order_by(columns.id)
        )

    def _sandbox_inbox_ids_stmt(self, workline_id: int) -> Any:
        columns = cast("Any", WorklineInbox).__table__.c
        session_ids = self._sandbox_session_ids_stmt(workline_id)
        command_ids = self._sandbox_command_ids_stmt(workline_id)
        return (
            select(columns.id)
            .where(
                columns.workline_id == workline_id,
                or_(
                    columns.session_id.in_(session_ids),
                    columns.command_id.in_(command_ids),
                    columns.payload_json["sandbox_mode"].as_boolean().is_(True),
                    columns.source_message_id.startswith("sandbox:"),
                    columns.event_id.startswith("sandbox:"),
                    columns.trace_id.startswith("sandbox:"),
                ),
            )
            .order_by(columns.id)
        )

    def _sandbox_outbox_ids_stmt(self, workline_id: int) -> Any:
        columns = cast("Any", SystemOutbox).__table__.c
        session_ids = self._sandbox_session_ids_stmt(workline_id)
        return (
            select(columns.id)
            .where(
                columns.workline_id == workline_id,
                columns.session_id.in_(session_ids),
            )
            .order_by(columns.id)
        )

    def _sandbox_command_ids_stmt(self, workline_id: int) -> Any:
        columns = cast("Any", DeviceCommand).__table__.c
        session_ids = self._sandbox_session_ids_stmt(workline_id)
        return (
            select(columns.id)
            .where(
                columns.workline_id == workline_id,
                columns.session_id_int.in_(session_ids),
            )
            .order_by(columns.id)
        )

    def _sandbox_runtime_hold_ids_stmt(self, workline_id: int) -> Any:
        columns = cast("Any", RuntimeHold).__table__.c
        session_ids = self._sandbox_session_ids_stmt(workline_id)
        inbox_ids = self._sandbox_inbox_ids_stmt(workline_id)
        outbox_ids = self._sandbox_outbox_ids_stmt(workline_id)
        command_ids = self._sandbox_command_ids_stmt(workline_id)
        return (
            select(columns.id)
            .where(
                columns.workline_id == workline_id,
                or_(
                    columns.session_id.in_(session_ids),
                    columns.source_inbox_id.in_(inbox_ids),
                    columns.source_outbox_id.in_(outbox_ids),
                    columns.source_command_id.in_(command_ids),
                ),
            )
            .order_by(columns.id)
        )

    def _sandbox_ng_return_item_ids_stmt(self, workline_id: int) -> Any:
        columns = cast("Any", NgReturnItem).__table__.c
        session_ids = self._sandbox_session_ids_stmt(workline_id)
        command_ids = self._sandbox_command_ids_stmt(workline_id)
        runtime_hold_ids = self._sandbox_runtime_hold_ids_stmt(workline_id)
        return (
            select(columns.id)
            .where(
                columns.source_workline_id == workline_id,
                or_(
                    columns.source_session_id.in_(session_ids),
                    columns.source_command_id.in_(command_ids),
                    columns.created_from_runtime_hold_id.in_(runtime_hold_ids),
                ),
            )
            .order_by(columns.id)
        )

    def _sandbox_rack_task_ids_stmt(self, workline_id: int) -> Any:
        columns = cast("Any", RackTask).__table__.c
        session_ids = self._sandbox_session_ids_stmt(workline_id)
        outbox_ids = self._sandbox_outbox_ids_stmt(workline_id)
        return (
            select(columns.id)
            .where(
                columns.workline_id == workline_id,
                or_(
                    columns.material_session_id.in_(session_ids),
                    columns.outbox_id.in_(outbox_ids),
                ),
            )
            .order_by(columns.id)
        )

    def _sandbox_bin_cell_reservation_ids_stmt(self, workline_id: int) -> Any:
        columns = cast("Any", WorklineBinCellReservation).__table__.c
        session_ids = self._sandbox_session_ids_stmt(workline_id)
        return (
            select(columns.id)
            .where(
                columns.workline_id == workline_id,
                columns.session_id.in_(session_ids),
            )
            .order_by(columns.id)
        )

    def _sandbox_timeline_ids_stmt(self, workline_id: int) -> Any:
        columns = cast("Any", WorklineTimeline).__table__.c
        session_ids = self._sandbox_session_ids_stmt(workline_id)
        inbox_ids = self._sandbox_inbox_ids_stmt(workline_id)
        command_ids = self._sandbox_command_ids_stmt(workline_id)
        return (
            select(columns.id)
            .where(
                columns.workline_id == workline_id,
                or_(
                    columns.session_id.in_(session_ids),
                    columns.related_inbox_id.in_(inbox_ids),
                    columns.related_command_id.in_(command_ids),
                ),
            )
            .order_by(columns.id)
        )

    def _sandbox_diagnostic_ids_stmt(self, workline_id: int) -> Any:
        columns = cast("Any", WorklineDiagnostic).__table__.c
        session_ids = self._sandbox_session_ids_stmt(workline_id)
        inbox_ids = self._sandbox_inbox_ids_stmt(workline_id)
        outbox_ids = self._sandbox_outbox_ids_stmt(workline_id)
        return (
            select(columns.id)
            .where(
                columns.workline_id == workline_id,
                or_(
                    columns.session_id.in_(session_ids),
                    columns.inbox_id.in_(inbox_ids),
                    columns.outbox_id.in_(outbox_ids),
                ),
            )
            .order_by(columns.id)
        )

    def _sandbox_dispatch_attempt_ids_stmt(self, workline_id: int) -> Any:
        columns = cast("Any", WorklineDispatchAttempt).__table__.c
        outbox_ids = self._sandbox_outbox_ids_stmt(workline_id)
        return select(columns.id).where(columns.outbox_id.in_(outbox_ids)).order_by(columns.id)

    def _sandbox_safety_incident_ids_stmt(self, workline_id: int) -> Any:
        columns = cast("Any", WorklineSafetyIncident).__table__.c
        inbox_ids = self._sandbox_inbox_ids_stmt(workline_id)
        command_ids = self._sandbox_command_ids_stmt(workline_id)
        return (
            select(columns.id)
            .where(
                columns.workline_id == workline_id,
                or_(
                    columns.trigger_payload_json["source"].as_string() == "sandbox",
                    columns.source_inbox_id.in_(inbox_ids),
                    columns.source_command_id.in_(command_ids),
                ),
            )
            .order_by(columns.id)
        )

    def is_simulation_workline(self, workline: WorkLine) -> bool:
        """判断工作线是否为沙箱模式。"""

        return workline.run_mode == WorkLineRunMode.SIMULATION


sandbox_cleanup_repository = SandboxCleanupRepository()


__all__ = [
    "COUNT_KEYS",
    "SandboxCleanupRepository",
    "SandboxCleanupSelection",
    "sandbox_cleanup_repository",
]
