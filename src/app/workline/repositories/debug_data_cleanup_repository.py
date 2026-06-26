"""非生产调试过程数据清理数据访问层。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import delete, or_, select, update

from src.app.device.models import Device, DeviceCommand
from src.app.handling.models import BinTransitMembership, HandlingMove, HandlingOperation, HandlingStep
from src.app.rack.models import RackOperation, RackTask
from src.app.resource.models import (
    BinCellOccupancy,
    BinContentSnapshot,
    BinContentSnapshotItem,
    BinMaterialMount,
    BinPlacement,
    RackBinMount,
    RackPlacement,
    ResourceSourceSystem,
    ResourceStateEvent,
)
from src.app.sys.models import SystemOutbox
from src.app.workline.models import WorkLine
from src.app.workline.models.bin_cell_reservation import WorklineBinCellReservation
from src.app.workline.models.diagnostic import WorklineDiagnostic
from src.app.workline.models.dispatch_attempt import WorklineDispatchAttempt
from src.app.workline.models.inbox import WorklineInbox
from src.app.workline.models.object_transition_event import ObjectTransitionEvent
from src.app.workline.models.runtime_hold import NgReturnItem, RuntimeHold
from src.app.workline.models.safety import WorklineSafetyIncident
from src.app.workline.models.session import WorklineSession
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
    "rack_operations",
    "rack_tasks",
    "handling_operations",
    "handling_moves",
    "handling_steps",
    "bin_transit_memberships",
    "bin_cell_reservations",
    "timelines",
    "diagnostics",
    "dispatch_attempts",
    "safety_incidents",
    "resource_state_events",
    "object_transition_events",
    "rack_placements",
    "rack_bin_mounts",
    "bin_placements",
    "bin_material_mounts",
    "bin_cell_occupancies",
    "bin_content_snapshots",
    "bin_content_snapshot_items",
    "callback_logs",
    "wms_call_evidence",
)
ID_CHUNK_SIZE = 500


@dataclass(slots=True)
class DebugDataCleanupSelection:
    """调试过程清理候选 ID 集合。"""

    worklines: list[int]
    sessions: list[int]
    inboxes: list[int]
    outboxes: list[int]
    commands: list[int]
    runtime_holds: list[int]
    ng_return_items: list[int]
    rack_operations: list[int]
    rack_tasks: list[int]
    handling_operations: list[int]
    handling_moves: list[int]
    handling_steps: list[int]
    bin_transit_memberships: list[int]
    bin_cell_reservations: list[int]
    timelines: list[int]
    diagnostics: list[int]
    dispatch_attempts: list[int]
    safety_incidents: list[int]
    resource_state_events: list[int]
    object_transition_events: list[int]
    rack_placements: list[int]
    rack_bin_mounts: list[int]
    bin_placements: list[int]
    bin_material_mounts: list[int]
    bin_cell_occupancies: list[int]
    bin_content_snapshots: list[int]
    bin_content_snapshot_items: list[int]
    callback_logs: list[int]
    wms_call_evidence: list[int]
    trace_ids: list[str] = field(default_factory=list)
    session_keys: list[str] = field(default_factory=list)
    dispatch_keys: list[str] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        """返回前端契约使用的稳定计数 key。"""

        return {key: len(getattr(self, key)) for key in COUNT_KEYS}


class DebugDataCleanupRepository:
    """非生产调试过程数据清理数据访问层。"""

    async def get_workline(self, db: AsyncSession, workline_id: int) -> WorkLine | None:
        columns = cast("Any", WorkLine).__table__.c
        result = await db.execute(select(WorkLine).where(columns.id == workline_id))
        return result.scalar_one_or_none()

    async def get_workline_for_update(self, db: AsyncSession, workline_id: int) -> WorkLine | None:
        columns = cast("Any", WorkLine).__table__.c
        result = await db.execute(select(WorkLine).where(columns.id == workline_id).with_for_update())
        return result.scalar_one_or_none()

    async def get_all_workline_ids_for_update(self, db: AsyncSession) -> list[int]:
        columns = cast("Any", WorkLine).__table__.c
        result = await db.execute(select(columns.id).order_by(columns.id).with_for_update())
        return [item_id for item_id in result.scalars().all() if item_id is not None]

    async def collect_for_workline(self, db: AsyncSession, workline_id: int) -> DebugDataCleanupSelection:
        return await self.collect_for_worklines(db, [workline_id])

    async def collect_for_all_worklines(self, db: AsyncSession) -> DebugDataCleanupSelection:
        return await self.collect_for_worklines(
            db,
            await self.get_all_workline_ids_for_update(db),
            include_orphans=True,
        )

    async def collect_for_worklines(
        self,
        db: AsyncSession,
        workline_ids: list[int],
        *,
        include_orphans: bool = False,
    ) -> DebugDataCleanupSelection:
        """按工作线集合收集所有可关联过程数据。"""

        workline_ids = sorted(set(workline_ids))
        if not workline_ids and not include_orphans:
            return self._empty_selection()

        workline_codes = await self._collect_strings(db, self._workline_code_stmt(workline_ids))
        session_ids = await self._collect_ids(db, self._session_ids_stmt(workline_ids))
        trace_ids = await self._collect_strings(db, self._session_trace_ids_stmt(session_ids))
        session_codes = await self._collect_strings(db, self._session_code_stmt(session_ids))
        session_keys = sorted({*(str(item_id) for item_id in session_ids), *session_codes})

        inbox_ids = await self._collect_ids(db, self._inbox_ids_stmt(workline_ids, session_ids, trace_ids))
        trace_ids = sorted({*trace_ids, *await self._collect_strings(db, self._inbox_trace_ids_stmt(inbox_ids))})
        outbox_ids = await self._collect_ids(db, self._outbox_ids_stmt(workline_ids, session_ids, trace_ids))
        command_ids = await self._collect_ids(db, self._command_ids_stmt(workline_ids, trace_ids))

        trace_ids = sorted({*trace_ids, *await self._collect_strings(db, self._outbox_trace_ids_stmt(outbox_ids))})
        dispatch_keys = await self._collect_strings(db, self._outbox_dispatch_keys_stmt(outbox_ids))
        inbox_event_ids = await self._collect_strings(db, self._inbox_event_ids_stmt(inbox_ids))

        runtime_hold_ids = await self._collect_ids(
            db,
            self._runtime_hold_ids_stmt(workline_ids, session_ids, trace_ids, inbox_ids, outbox_ids, command_ids),
        )
        ng_return_item_ids = await self._collect_ids(
            db, self._ng_return_item_ids_stmt(workline_ids, session_ids, command_ids, runtime_hold_ids)
        )
        rack_operation_ids = await self._collect_ids(
            db, self._rack_operation_ids_stmt(workline_ids, workline_codes, session_ids, trace_ids)
        )
        rack_task_ids = await self._collect_ids(
            db,
            self._rack_task_ids_stmt(
                workline_ids,
                workline_codes,
                session_ids,
                outbox_ids,
                rack_operation_ids,
                trace_ids,
                dispatch_keys,
            ),
        )
        handling_operation_ids = await self._collect_ids(
            db, self._handling_operation_ids_stmt(workline_ids, workline_codes, session_ids, trace_ids)
        )
        handling_move_ids = await self._collect_ids(db, self._handling_move_ids_stmt(handling_operation_ids))
        handling_step_ids = await self._collect_ids(
            db,
            self._handling_step_ids_stmt(handling_operation_ids, outbox_ids, command_ids, trace_ids, dispatch_keys),
        )
        bin_transit_membership_ids = await self._collect_ids(
            db,
            self._bin_transit_membership_ids_stmt(
                workline_ids,
                workline_codes,
                session_ids,
                trace_ids,
                handling_operation_ids,
                handling_move_ids,
            ),
        )
        bin_cell_reservation_ids = await self._collect_ids(
            db, self._bin_cell_reservation_ids_stmt(workline_ids, workline_codes, session_ids)
        )
        timeline_ids = await self._collect_ids(
            db, self._timeline_ids_stmt(workline_ids, session_ids, trace_ids, inbox_ids, command_ids)
        )
        diagnostic_ids = await self._collect_ids(
            db, self._diagnostic_ids_stmt(workline_ids, session_ids, inbox_ids, outbox_ids)
        )
        dispatch_attempt_ids = await self._collect_ids(db, self._dispatch_attempt_ids_stmt(outbox_ids, dispatch_keys))
        safety_incident_ids = await self._collect_ids(
            db, self._safety_incident_ids_stmt(workline_ids, inbox_ids, command_ids)
        )

        resource_state_event_ids = await self._collect_ids(
            db, self._resource_state_event_ids_stmt(workline_ids, workline_codes, session_ids, trace_ids)
        )
        resource_state_event_source_ids = await self._collect_strings(
            db, self._resource_state_event_source_ids_stmt(resource_state_event_ids)
        )
        object_transition_event_ids = await self._collect_ids(
            db,
            self._object_transition_event_ids_stmt(
                session_ids,
                trace_ids,
                handling_operation_ids,
                handling_move_ids,
                resource_state_event_ids,
                resource_state_event_source_ids,
            ),
        )
        rack_placement_ids = await self._collect_ids(
            db, self._rack_placement_ids_stmt(workline_ids, workline_codes, session_ids, trace_ids)
        )
        rack_bin_mount_ids = await self._collect_ids(db, self._rack_bin_mount_ids_stmt(session_ids, trace_ids))
        bin_placement_ids = await self._collect_ids(
            db, self._bin_placement_ids_stmt(workline_ids, workline_codes, session_ids, trace_ids)
        )
        bin_material_mount_ids = await self._collect_ids(db, self._bin_material_mount_ids_stmt(session_ids, trace_ids))
        bin_cell_occupancy_ids = await self._collect_ids(db, self._bin_cell_occupancy_ids_stmt(session_ids, trace_ids))
        resource_trace_ids, resource_session_ids = await self._collect_resource_link_keys(
            db,
            resource_state_event_ids=resource_state_event_ids,
            rack_placement_ids=rack_placement_ids,
            rack_bin_mount_ids=rack_bin_mount_ids,
            bin_placement_ids=bin_placement_ids,
            bin_material_mount_ids=bin_material_mount_ids,
            bin_cell_occupancy_ids=bin_cell_occupancy_ids,
        )
        trace_ids = sorted({*trace_ids, *resource_trace_ids})
        session_ids = sorted({*session_ids, *resource_session_ids})
        session_keys = sorted({*(str(item_id) for item_id in session_ids), *session_codes})
        resource_state_event_ids = await self._collect_ids(
            db, self._resource_state_event_ids_stmt(workline_ids, workline_codes, session_ids, trace_ids)
        )
        resource_state_event_source_ids = await self._collect_strings(
            db, self._resource_state_event_source_ids_stmt(resource_state_event_ids)
        )
        object_transition_event_ids = await self._collect_ids(
            db,
            self._object_transition_event_ids_stmt(
                session_ids,
                trace_ids,
                handling_operation_ids,
                handling_move_ids,
                resource_state_event_ids,
                resource_state_event_source_ids,
            ),
        )
        rack_placement_ids = await self._collect_ids(
            db, self._rack_placement_ids_stmt(workline_ids, workline_codes, session_ids, trace_ids)
        )
        rack_bin_mount_ids = await self._collect_ids(db, self._rack_bin_mount_ids_stmt(session_ids, trace_ids))
        bin_placement_ids = await self._collect_ids(
            db, self._bin_placement_ids_stmt(workline_ids, workline_codes, session_ids, trace_ids)
        )
        bin_material_mount_ids = await self._collect_ids(db, self._bin_material_mount_ids_stmt(session_ids, trace_ids))
        bin_cell_occupancy_ids = await self._collect_ids(db, self._bin_cell_occupancy_ids_stmt(session_ids, trace_ids))
        bin_content_snapshot_ids = await self._collect_ids(db, self._bin_content_snapshot_ids_stmt(session_ids))
        snapshot_ids = await self._collect_strings(
            db, self._bin_content_snapshot_business_ids_stmt(bin_content_snapshot_ids)
        )
        bin_content_snapshot_item_ids = await self._collect_ids(
            db, self._bin_content_snapshot_item_ids_stmt(snapshot_ids)
        )
        callback_log_ids = await self._collect_ids(db, self._callback_log_ids_stmt(trace_ids, inbox_event_ids))
        wms_call_evidence_ids = await self._collect_ids(db, self._wms_call_evidence_ids_stmt(trace_ids, dispatch_keys))
        if include_orphans:
            resource_state_event_ids = sorted(
                {
                    *resource_state_event_ids,
                    *await self._collect_ids(
                        db, self._orphan_process_resource_ids_stmt(ResourceStateEvent, session_ids, trace_ids)
                    ),
                }
            )
            resource_state_event_source_ids = sorted(
                {
                    *resource_state_event_source_ids,
                    *await self._collect_strings(
                        db, self._resource_state_event_source_ids_stmt(resource_state_event_ids)
                    ),
                }
            )
            object_transition_event_ids = sorted(
                {
                    *object_transition_event_ids,
                    *await self._collect_ids(
                        db,
                        self._object_transition_event_ids_stmt(
                            session_ids,
                            trace_ids,
                            handling_operation_ids,
                            handling_move_ids,
                            resource_state_event_ids,
                            resource_state_event_source_ids,
                        ),
                    ),
                }
            )
            rack_bin_mount_ids = sorted(
                {
                    *rack_bin_mount_ids,
                    *await self._collect_ids(
                        db, self._orphan_process_resource_ids_stmt(RackBinMount, session_ids, trace_ids)
                    ),
                }
            )
            bin_material_mount_ids = sorted(
                {
                    *bin_material_mount_ids,
                    *await self._collect_ids(
                        db, self._orphan_process_resource_ids_stmt(BinMaterialMount, session_ids, trace_ids)
                    ),
                }
            )
            bin_cell_occupancy_ids = sorted(
                {
                    *bin_cell_occupancy_ids,
                    *await self._collect_ids(
                        db, self._orphan_process_resource_ids_stmt(BinCellOccupancy, session_ids, trace_ids)
                    ),
                }
            )
            callback_log_ids = sorted(
                {
                    *callback_log_ids,
                    *await self._collect_ids(
                        db, self._orphan_process_callback_log_ids_stmt(trace_ids, inbox_event_ids)
                    ),
                }
            )

        return DebugDataCleanupSelection(
            worklines=workline_ids,
            sessions=session_ids,
            inboxes=inbox_ids,
            outboxes=outbox_ids,
            commands=command_ids,
            runtime_holds=runtime_hold_ids,
            ng_return_items=ng_return_item_ids,
            rack_operations=rack_operation_ids,
            rack_tasks=rack_task_ids,
            handling_operations=handling_operation_ids,
            handling_moves=handling_move_ids,
            handling_steps=handling_step_ids,
            bin_transit_memberships=bin_transit_membership_ids,
            bin_cell_reservations=bin_cell_reservation_ids,
            timelines=timeline_ids,
            diagnostics=diagnostic_ids,
            dispatch_attempts=dispatch_attempt_ids,
            safety_incidents=safety_incident_ids,
            resource_state_events=resource_state_event_ids,
            object_transition_events=object_transition_event_ids,
            rack_placements=rack_placement_ids,
            rack_bin_mounts=rack_bin_mount_ids,
            bin_placements=bin_placement_ids,
            bin_material_mounts=bin_material_mount_ids,
            bin_cell_occupancies=bin_cell_occupancy_ids,
            bin_content_snapshots=bin_content_snapshot_ids,
            bin_content_snapshot_items=bin_content_snapshot_item_ids,
            callback_logs=callback_log_ids,
            wms_call_evidence=wms_call_evidence_ids,
            trace_ids=trace_ids,
            session_keys=session_keys,
            dispatch_keys=dispatch_keys,
        )

    async def execute_cleanup(self, db: AsyncSession, *, selection: DebugDataCleanupSelection) -> None:
        """删除过程数据；仅断开必要引用，不重置 WorkLine/Device 运行态。"""

        await self._clear_cyclic_refs(db, selection)
        await db.flush()

        await self._delete_by_ids(db, BinContentSnapshotItem, selection.bin_content_snapshot_items)
        await self._delete_by_ids(db, ObjectTransitionEvent, selection.object_transition_events)
        await self._delete_by_ids(db, BinTransitMembership, selection.bin_transit_memberships)
        await self._delete_by_ids(db, HandlingStep, selection.handling_steps)
        await self._delete_by_ids(db, HandlingMove, selection.handling_moves)
        await self._delete_by_ids(db, WorklineTimeline, selection.timelines)
        await self._delete_by_ids(db, WorklineDiagnostic, selection.diagnostics)
        await self._delete_by_ids(db, WorklineDispatchAttempt, selection.dispatch_attempts)
        await self._delete_by_ids(db, WorklineBinCellReservation, selection.bin_cell_reservations)
        await self._delete_by_ids(db, RackTask, selection.rack_tasks)
        await self._delete_by_ids(db, RackOperation, selection.rack_operations)
        await self._delete_by_ids(db, HandlingOperation, selection.handling_operations)
        await self._delete_by_ids(db, NgReturnItem, selection.ng_return_items)
        await self._delete_by_ids(db, WorklineSafetyIncident, selection.safety_incidents)
        await self._delete_by_ids(db, RuntimeHold, selection.runtime_holds)
        await self._delete_by_ids(db, ResourceStateEvent, selection.resource_state_events)
        await self._delete_by_ids(db, RackPlacement, selection.rack_placements)
        await self._delete_by_ids(db, RackBinMount, selection.rack_bin_mounts)
        await self._delete_by_ids(db, BinPlacement, selection.bin_placements)
        await self._delete_by_ids(db, BinMaterialMount, selection.bin_material_mounts)
        await self._delete_by_ids(db, BinCellOccupancy, selection.bin_cell_occupancies)
        await self._delete_by_ids(db, BinContentSnapshot, selection.bin_content_snapshots)
        await self._delete_by_ids(db, self._wms_call_evidence_model(), selection.wms_call_evidence)
        await self._delete_by_ids(db, self._callback_log_model(), selection.callback_logs)
        await self._delete_by_ids(db, SystemOutbox, selection.outboxes)
        await self._delete_by_ids(db, WorklineInbox, selection.inboxes)
        await self._delete_by_ids(db, DeviceCommand, selection.commands)
        await self._delete_by_ids(db, WorklineSession, selection.sessions)
        await db.flush()

    async def _collect_ids(self, db: AsyncSession, stmt: Any | None) -> list[int]:
        if stmt is None:
            return []
        result = await db.execute(stmt)
        return sorted({item_id for item_id in result.scalars().all() if item_id is not None})

    async def _collect_strings(self, db: AsyncSession, stmt: Any | None) -> list[str]:
        if stmt is None:
            return []
        result = await db.execute(stmt)
        return sorted({str(item) for item in result.scalars().all() if item})

    async def _clear_cyclic_refs(self, db: AsyncSession, selection: DebugDataCleanupSelection) -> None:
        outbox_columns = cast("Any", SystemOutbox).__table__.c
        for hold_ids in self._chunks(selection.runtime_holds):
            _ = await db.execute(
                update(SystemOutbox)
                .where(outbox_columns.blocked_by_runtime_hold_id.in_(hold_ids))
                .values(blocked_by_runtime_hold_id=None)
            )
        for outbox_ids in self._chunks(selection.outboxes):
            _ = await db.execute(
                update(SystemOutbox).where(outbox_columns.id.in_(outbox_ids)).values(blocked_by_runtime_hold_id=None)
            )

        hold_columns = cast("Any", RuntimeHold).__table__.c
        for hold_ids in self._chunks(selection.runtime_holds):
            _ = await db.execute(
                update(RuntimeHold)
                .where(hold_columns.reopened_from_hold_id.in_(hold_ids))
                .values(reopened_from_hold_id=None)
            )

        session_columns = cast("Any", WorklineSession).__table__.c
        for session_ids in self._chunks(selection.sessions):
            _ = await db.execute(
                update(WorklineSession)
                .where(session_columns.id.in_(session_ids))
                .values(awaiting_device_command_code=None)
            )

        workline_columns = cast("Any", WorkLine).__table__.c
        for incident_ids in self._chunks(selection.safety_incidents):
            _ = await db.execute(
                update(WorkLine)
                .where(workline_columns.active_safety_incident_id.in_(incident_ids))
                .values(active_safety_incident_id=None)
            )

        device_columns = cast("Any", Device).__table__.c
        for command_ids in self._chunks(selection.commands):
            _ = await db.execute(
                update(Device).where(device_columns.current_command_id.in_(command_ids)).values(current_command_id=None)
            )

    async def _delete_by_ids(self, db: AsyncSession, model: type[Any], ids: list[int]) -> None:
        columns = cast("Any", model).__table__.c
        for id_batch in self._chunks(ids):
            _ = await db.execute(delete(model).where(columns.id.in_(id_batch)))

    def _empty_selection(self) -> DebugDataCleanupSelection:
        return DebugDataCleanupSelection(
            worklines=[],
            sessions=[],
            inboxes=[],
            outboxes=[],
            commands=[],
            runtime_holds=[],
            ng_return_items=[],
            rack_operations=[],
            rack_tasks=[],
            handling_operations=[],
            handling_moves=[],
            handling_steps=[],
            bin_transit_memberships=[],
            bin_cell_reservations=[],
            timelines=[],
            diagnostics=[],
            dispatch_attempts=[],
            safety_incidents=[],
            resource_state_events=[],
            object_transition_events=[],
            rack_placements=[],
            rack_bin_mounts=[],
            bin_placements=[],
            bin_material_mounts=[],
            bin_cell_occupancies=[],
            bin_content_snapshots=[],
            bin_content_snapshot_items=[],
            callback_logs=[],
            wms_call_evidence=[],
        )

    def _chunks(self, ids: list[int]) -> list[list[int]]:
        return [ids[index : index + ID_CHUNK_SIZE] for index in range(0, len(ids), ID_CHUNK_SIZE)]

    def _where_any(self, *conditions: Any | None) -> Any | None:
        valid_conditions = [condition for condition in conditions if condition is not None]
        if not valid_conditions:
            return None
        return or_(*valid_conditions)

    def _in_condition(self, column: Any, values: list[Any]) -> Any | None:
        return column.in_(values) if values else None

    def _orphan_process_resource_ids_stmt(
        self,
        model: type[Any],
        session_ids: list[int],
        trace_ids: list[str],
    ) -> Any:
        columns = cast("Any", model).__table__.c
        known_process_condition = cast(
            "Any",
            self._where_any(
                columns.trace_id.is_not(None),
                columns.workline_session_id.is_not(None),
            ),
        )
        exclusion_conditions = [
            self._outside_known_values(columns.trace_id, trace_ids),
            self._outside_known_values(columns.workline_session_id, session_ids),
        ]
        return (
            select(columns.id)
            .where(
                columns.source_system == ResourceSourceSystem.WES_RUNTIME,
                known_process_condition,
                *[condition for condition in exclusion_conditions if condition is not None],
            )
            .order_by(columns.id)
        )

    def _orphan_process_callback_log_ids_stmt(self, trace_ids: list[str], event_ids: list[str]) -> Any:
        columns = cast("Any", self._callback_log_model()).__table__.c
        known_process_condition = cast(
            "Any",
            self._where_any(
                columns.trace_id.is_not(None),
                columns.event_id.is_not(None),
            ),
        )
        exclusion_conditions = [
            self._outside_known_values(columns.trace_id, trace_ids),
            self._outside_known_values(columns.event_id, event_ids),
        ]
        return (
            select(columns.id)
            .where(
                known_process_condition,
                *[condition for condition in exclusion_conditions if condition is not None],
            )
            .order_by(columns.id)
        )

    def _outside_known_values(self, column: Any, values: list[Any]) -> Any | None:
        return or_(column.is_(None), column.not_in(values)) if values else None

    async def _collect_resource_link_keys(
        self,
        db: AsyncSession,
        *,
        resource_state_event_ids: list[int],
        rack_placement_ids: list[int],
        rack_bin_mount_ids: list[int],
        bin_placement_ids: list[int],
        bin_material_mount_ids: list[int],
        bin_cell_occupancy_ids: list[int],
    ) -> tuple[list[str], list[int]]:
        trace_ids: set[str] = set()
        session_ids: set[int] = set()
        for model, ids in (
            (ResourceStateEvent, resource_state_event_ids),
            (RackPlacement, rack_placement_ids),
            (RackBinMount, rack_bin_mount_ids),
            (BinPlacement, bin_placement_ids),
            (BinMaterialMount, bin_material_mount_ids),
            (BinCellOccupancy, bin_cell_occupancy_ids),
        ):
            trace_ids.update(await self._collect_strings(db, self._trace_ids_by_ids_stmt(model, ids)))
            session_ids.update(await self._collect_ids(db, self._session_ids_by_ids_stmt(model, ids)))
        return sorted(trace_ids), sorted(session_ids)

    def _trace_ids_by_ids_stmt(self, model: type[Any], ids: list[int]) -> Any | None:
        columns = cast("Any", model).__table__.c
        return select(columns.trace_id).where(columns.id.in_(ids)) if ids else None

    def _session_ids_by_ids_stmt(self, model: type[Any], ids: list[int]) -> Any | None:
        columns = cast("Any", model).__table__.c
        return select(columns.workline_session_id).where(columns.id.in_(ids)) if ids else None

    def _workline_code_stmt(self, workline_ids: list[int]) -> Any:
        columns = cast("Any", WorkLine).__table__.c
        return select(columns.line_code).where(columns.id.in_(workline_ids))

    def _session_ids_stmt(self, workline_ids: list[int]) -> Any:
        columns = cast("Any", WorklineSession).__table__.c
        return select(columns.id).where(columns.workline_id.in_(workline_ids)).order_by(columns.id)

    def _session_trace_ids_stmt(self, session_ids: list[int]) -> Any | None:
        columns = cast("Any", WorklineSession).__table__.c
        return select(columns.trace_id).where(columns.id.in_(session_ids)) if session_ids else None

    def _session_code_stmt(self, session_ids: list[int]) -> Any | None:
        columns = cast("Any", WorklineSession).__table__.c
        return select(columns.session_code).where(columns.id.in_(session_ids)) if session_ids else None

    def _inbox_ids_stmt(self, workline_ids: list[int], session_ids: list[int], trace_ids: list[str]) -> Any | None:
        columns = cast("Any", WorklineInbox).__table__.c
        blocked_error_conditions = [
            columns.error_message.ilike(f"%workline_id={workline_id},%") for workline_id in workline_ids
        ]
        condition = self._where_any(
            self._in_condition(columns.workline_id, workline_ids),
            self._in_condition(columns.session_id, session_ids),
            self._in_condition(columns.trace_id, trace_ids),
            *blocked_error_conditions,
        )
        return select(columns.id).where(condition).order_by(columns.id) if condition is not None else None

    def _outbox_ids_stmt(self, workline_ids: list[int], session_ids: list[int], trace_ids: list[str]) -> Any | None:
        columns = cast("Any", SystemOutbox).__table__.c
        condition = self._where_any(
            self._in_condition(columns.workline_id, workline_ids),
            self._in_condition(columns.session_id, session_ids),
            self._in_condition(columns.trace_id, trace_ids),
        )
        return select(columns.id).where(condition).order_by(columns.id) if condition is not None else None

    def _command_ids_stmt(self, workline_ids: list[int], trace_ids: list[str]) -> Any | None:
        columns = cast("Any", DeviceCommand).__table__.c
        condition = self._where_any(
            self._in_condition(columns.workline_id, workline_ids),
            self._in_condition(columns.trace_id, trace_ids),
        )
        return select(columns.id).where(condition).order_by(columns.id) if condition is not None else None

    def _inbox_trace_ids_stmt(self, inbox_ids: list[int]) -> Any | None:
        columns = cast("Any", WorklineInbox).__table__.c
        return select(columns.trace_id).where(columns.id.in_(inbox_ids)) if inbox_ids else None

    def _outbox_trace_ids_stmt(self, outbox_ids: list[int]) -> Any | None:
        columns = cast("Any", SystemOutbox).__table__.c
        return select(columns.trace_id).where(columns.id.in_(outbox_ids)) if outbox_ids else None

    def _outbox_dispatch_keys_stmt(self, outbox_ids: list[int]) -> Any | None:
        columns = cast("Any", SystemOutbox).__table__.c
        return select(columns.dispatch_key).where(columns.id.in_(outbox_ids)) if outbox_ids else None

    def _inbox_event_ids_stmt(self, inbox_ids: list[int]) -> Any | None:
        columns = cast("Any", WorklineInbox).__table__.c
        return select(columns.event_id).where(columns.id.in_(inbox_ids)) if inbox_ids else None

    def _runtime_hold_ids_stmt(
        self,
        workline_ids: list[int],
        session_ids: list[int],
        trace_ids: list[str],
        inbox_ids: list[int],
        outbox_ids: list[int],
        command_ids: list[int],
    ) -> Any | None:
        columns = cast("Any", RuntimeHold).__table__.c
        condition = self._where_any(
            self._in_condition(columns.workline_id, workline_ids),
            self._in_condition(columns.session_id, session_ids),
            self._in_condition(columns.trace_id, trace_ids),
            self._in_condition(columns.source_inbox_id, inbox_ids),
            self._in_condition(columns.source_outbox_id, outbox_ids),
            self._in_condition(columns.source_command_id, command_ids),
        )
        return select(columns.id).where(condition).order_by(columns.id) if condition is not None else None

    def _ng_return_item_ids_stmt(
        self,
        workline_ids: list[int],
        session_ids: list[int],
        command_ids: list[int],
        runtime_hold_ids: list[int],
    ) -> Any | None:
        columns = cast("Any", NgReturnItem).__table__.c
        condition = self._where_any(
            self._in_condition(columns.source_workline_id, workline_ids),
            self._in_condition(columns.source_session_id, session_ids),
            self._in_condition(columns.source_command_id, command_ids),
            self._in_condition(columns.created_from_runtime_hold_id, runtime_hold_ids),
        )
        return select(columns.id).where(condition).order_by(columns.id) if condition is not None else None

    def _rack_operation_ids_stmt(
        self,
        workline_ids: list[int],
        workline_codes: list[str],
        session_ids: list[int],
        trace_ids: list[str],
    ) -> Any | None:
        columns = cast("Any", RackOperation).__table__.c
        condition = self._where_any(
            self._in_condition(columns.workline_id, workline_ids),
            self._in_condition(columns.workline_code, workline_codes),
            self._in_condition(columns.material_session_id, session_ids),
            self._in_condition(columns.trace_id, trace_ids),
        )
        return select(columns.id).where(condition).order_by(columns.id) if condition is not None else None

    def _rack_task_ids_stmt(
        self,
        workline_ids: list[int],
        workline_codes: list[str],
        session_ids: list[int],
        outbox_ids: list[int],
        operation_ids: list[int],
        trace_ids: list[str],
        dispatch_keys: list[str],
    ) -> Any | None:
        columns = cast("Any", RackTask).__table__.c
        condition = self._where_any(
            self._in_condition(columns.workline_id, workline_ids),
            self._in_condition(columns.workline_code, workline_codes),
            self._in_condition(columns.material_session_id, session_ids),
            self._in_condition(columns.outbox_id, outbox_ids),
            self._in_condition(columns.operation_id, operation_ids),
            self._in_condition(columns.trace_id, trace_ids),
            self._in_condition(columns.dispatch_key, dispatch_keys),
        )
        return select(columns.id).where(condition).order_by(columns.id) if condition is not None else None

    def _handling_operation_ids_stmt(
        self,
        workline_ids: list[int],
        workline_codes: list[str],
        session_ids: list[int],
        trace_ids: list[str],
    ) -> Any | None:
        columns = cast("Any", HandlingOperation).__table__.c
        condition = self._where_any(
            self._in_condition(columns.workline_id, workline_ids),
            self._in_condition(columns.workline_code, workline_codes),
            self._in_condition(columns.material_session_id, session_ids),
            self._in_condition(columns.trace_id, trace_ids),
        )
        return select(columns.id).where(condition).order_by(columns.id) if condition is not None else None

    def _handling_move_ids_stmt(self, operation_ids: list[int]) -> Any | None:
        columns = cast("Any", HandlingMove).__table__.c
        return (
            select(columns.id).where(columns.operation_id.in_(operation_ids)).order_by(columns.id)
            if operation_ids
            else None
        )

    def _handling_step_ids_stmt(
        self,
        operation_ids: list[int],
        outbox_ids: list[int],
        command_ids: list[int],
        trace_ids: list[str],
        dispatch_keys: list[str],
    ) -> Any | None:
        columns = cast("Any", HandlingStep).__table__.c
        condition = self._where_any(
            self._in_condition(columns.operation_id, operation_ids),
            self._in_condition(columns.outbox_id, outbox_ids),
            self._in_condition(columns.command_id, command_ids),
            self._in_condition(columns.trace_id, trace_ids),
            self._in_condition(columns.dispatch_key, dispatch_keys),
        )
        return select(columns.id).where(condition).order_by(columns.id) if condition is not None else None

    def _bin_transit_membership_ids_stmt(
        self,
        workline_ids: list[int],
        workline_codes: list[str],
        session_ids: list[int],
        trace_ids: list[str],
        handling_operation_ids: list[int],
        handling_move_ids: list[int],
    ) -> Any | None:
        columns = cast("Any", BinTransitMembership).__table__.c
        condition = self._where_any(
            self._in_condition(columns.workline_id, workline_ids),
            self._in_condition(columns.workline_code, workline_codes),
            self._in_condition(columns.workline_session_id, session_ids),
            self._in_condition(columns.trace_id, trace_ids),
            self._in_condition(columns.handling_operation_id, handling_operation_ids),
            self._in_condition(columns.handling_move_id, handling_move_ids),
        )
        return select(columns.id).where(condition).order_by(columns.id) if condition is not None else None

    def _bin_cell_reservation_ids_stmt(
        self,
        workline_ids: list[int],
        workline_codes: list[str],
        session_ids: list[int],
    ) -> Any | None:
        columns = cast("Any", WorklineBinCellReservation).__table__.c
        condition = self._where_any(
            self._in_condition(columns.workline_id, workline_ids),
            self._in_condition(columns.workline_code, workline_codes),
            self._in_condition(columns.session_id, session_ids),
        )
        return select(columns.id).where(condition).order_by(columns.id) if condition is not None else None

    def _timeline_ids_stmt(
        self,
        workline_ids: list[int],
        session_ids: list[int],
        trace_ids: list[str],
        inbox_ids: list[int],
        command_ids: list[int],
    ) -> Any | None:
        columns = cast("Any", WorklineTimeline).__table__.c
        condition = self._where_any(
            self._in_condition(columns.workline_id, workline_ids),
            self._in_condition(columns.session_id, session_ids),
            self._in_condition(columns.trace_id, trace_ids),
            self._in_condition(columns.related_inbox_id, inbox_ids),
            self._in_condition(columns.related_command_id, command_ids),
        )
        return select(columns.id).where(condition).order_by(columns.id) if condition is not None else None

    def _diagnostic_ids_stmt(
        self,
        workline_ids: list[int],
        session_ids: list[int],
        inbox_ids: list[int],
        outbox_ids: list[int],
    ) -> Any | None:
        columns = cast("Any", WorklineDiagnostic).__table__.c
        condition = self._where_any(
            self._in_condition(columns.workline_id, workline_ids),
            self._in_condition(columns.session_id, session_ids),
            self._in_condition(columns.inbox_id, inbox_ids),
            self._in_condition(columns.outbox_id, outbox_ids),
        )
        return select(columns.id).where(condition).order_by(columns.id) if condition is not None else None

    def _dispatch_attempt_ids_stmt(self, outbox_ids: list[int], dispatch_keys: list[str]) -> Any | None:
        columns = cast("Any", WorklineDispatchAttempt).__table__.c
        condition = self._where_any(
            self._in_condition(columns.outbox_id, outbox_ids),
            self._in_condition(columns.dispatch_key, dispatch_keys),
        )
        return select(columns.id).where(condition).order_by(columns.id) if condition is not None else None

    def _safety_incident_ids_stmt(
        self,
        workline_ids: list[int],
        inbox_ids: list[int],
        command_ids: list[int],
    ) -> Any | None:
        columns = cast("Any", WorklineSafetyIncident).__table__.c
        condition = self._where_any(
            self._in_condition(columns.workline_id, workline_ids),
            self._in_condition(columns.source_inbox_id, inbox_ids),
            self._in_condition(columns.source_command_id, command_ids),
        )
        return select(columns.id).where(condition).order_by(columns.id) if condition is not None else None

    def _resource_state_event_ids_stmt(
        self,
        workline_ids: list[int],
        workline_codes: list[str],
        session_ids: list[int],
        trace_ids: list[str],
    ) -> Any | None:
        columns = cast("Any", ResourceStateEvent).__table__.c
        condition = self._where_any(
            self._in_condition(columns.workline_id, workline_ids),
            self._in_condition(columns.workline_code, workline_codes),
            self._in_condition(columns.workline_session_id, session_ids),
            self._in_condition(columns.trace_id, trace_ids),
        )
        return select(columns.id).where(condition).order_by(columns.id) if condition is not None else None

    def _resource_state_event_source_ids_stmt(self, resource_state_event_ids: list[int]) -> Any | None:
        columns = cast("Any", ResourceStateEvent).__table__.c
        condition = self._in_condition(columns.id, resource_state_event_ids)
        return (
            select(columns.source_event_id).where(condition).order_by(columns.source_event_id)
            if condition is not None
            else None
        )

    def _object_transition_event_ids_stmt(
        self,
        session_ids: list[int],
        trace_ids: list[str],
        handling_operation_ids: list[int],
        handling_move_ids: list[int],
        resource_state_event_ids: list[int],
        resource_state_event_source_ids: list[str],
    ) -> Any | None:
        columns = cast("Any", ObjectTransitionEvent).__table__.c
        source_ref = columns.source_ref_json
        json_conditions = [
            source_ref["handling_operation_id"].as_integer().in_(handling_operation_ids)
            if handling_operation_ids
            else None,
            source_ref["handling_move_id"].as_integer().in_(handling_move_ids) if handling_move_ids else None,
            source_ref["resource_state_event_id"].as_integer().in_(resource_state_event_ids)
            if resource_state_event_ids
            else None,
        ]
        condition = self._where_any(
            self._in_condition(columns.workline_session_id, session_ids),
            self._in_condition(columns.trace_id, trace_ids),
            self._in_condition(columns.source_event_id, resource_state_event_source_ids),
            *json_conditions,
        )
        return select(columns.id).where(condition).order_by(columns.id) if condition is not None else None

    def _rack_placement_ids_stmt(
        self,
        workline_ids: list[int],
        workline_codes: list[str],
        session_ids: list[int],
        trace_ids: list[str],
    ) -> Any | None:
        columns = cast("Any", RackPlacement).__table__.c
        condition = self._where_any(
            self._in_condition(columns.workline_id, workline_ids),
            self._in_condition(columns.workline_code, workline_codes),
            self._in_condition(columns.workline_session_id, session_ids),
            self._in_condition(columns.trace_id, trace_ids),
        )
        return select(columns.id).where(condition).order_by(columns.id) if condition is not None else None

    def _rack_bin_mount_ids_stmt(self, session_ids: list[int], trace_ids: list[str]) -> Any | None:
        columns = cast("Any", RackBinMount).__table__.c
        condition = self._where_any(
            self._in_condition(columns.workline_session_id, session_ids),
            self._in_condition(columns.trace_id, trace_ids),
        )
        return select(columns.id).where(condition).order_by(columns.id) if condition is not None else None

    def _bin_placement_ids_stmt(
        self,
        workline_ids: list[int],
        workline_codes: list[str],
        session_ids: list[int],
        trace_ids: list[str],
    ) -> Any | None:
        columns = cast("Any", BinPlacement).__table__.c
        condition = self._where_any(
            self._in_condition(columns.workline_id, workline_ids),
            self._in_condition(columns.workline_code, workline_codes),
            self._in_condition(columns.workline_session_id, session_ids),
            self._in_condition(columns.trace_id, trace_ids),
        )
        return select(columns.id).where(condition).order_by(columns.id) if condition is not None else None

    def _bin_material_mount_ids_stmt(self, session_ids: list[int], trace_ids: list[str]) -> Any | None:
        columns = cast("Any", BinMaterialMount).__table__.c
        condition = self._where_any(
            self._in_condition(columns.workline_session_id, session_ids),
            self._in_condition(columns.trace_id, trace_ids),
        )
        return select(columns.id).where(condition).order_by(columns.id) if condition is not None else None

    def _bin_cell_occupancy_ids_stmt(self, session_ids: list[int], trace_ids: list[str]) -> Any | None:
        columns = cast("Any", BinCellOccupancy).__table__.c
        condition = self._where_any(
            self._in_condition(columns.workline_session_id, session_ids),
            self._in_condition(columns.trace_id, trace_ids),
        )
        return select(columns.id).where(condition).order_by(columns.id) if condition is not None else None

    def _bin_content_snapshot_ids_stmt(self, session_ids: list[int]) -> Any | None:
        columns = cast("Any", BinContentSnapshot).__table__.c
        return (
            select(columns.id).where(columns.source_session_id.in_(session_ids)).order_by(columns.id)
            if session_ids
            else None
        )

    def _bin_content_snapshot_business_ids_stmt(self, snapshot_ids: list[int]) -> Any | None:
        columns = cast("Any", BinContentSnapshot).__table__.c
        return select(columns.snapshot_id).where(columns.id.in_(snapshot_ids)) if snapshot_ids else None

    def _bin_content_snapshot_item_ids_stmt(self, snapshot_ids: list[str]) -> Any | None:
        columns = cast("Any", BinContentSnapshotItem).__table__.c
        return (
            select(columns.id).where(columns.snapshot_id.in_(snapshot_ids)).order_by(columns.id)
            if snapshot_ids
            else None
        )

    def _callback_log_ids_stmt(self, trace_ids: list[str], event_ids: list[str]) -> Any | None:
        columns = cast("Any", self._callback_log_model()).__table__.c
        condition = self._where_any(
            self._in_condition(columns.trace_id, trace_ids),
            self._in_condition(columns.event_id, event_ids),
        )
        return select(columns.id).where(condition).order_by(columns.id) if condition is not None else None

    def _wms_call_evidence_ids_stmt(self, trace_ids: list[str], dispatch_keys: list[str]) -> Any | None:
        columns = cast("Any", self._wms_call_evidence_model()).__table__.c
        condition = self._where_any(
            self._in_condition(columns.trace_id, trace_ids),
            self._in_condition(columns.dispatch_key, dispatch_keys),
        )
        return select(columns.id).where(condition).order_by(columns.id) if condition is not None else None

    def _callback_log_model(self) -> type[Any]:
        from src.app.callback.models.callback_log import CallbackLog

        return CallbackLog

    def _wms_call_evidence_model(self) -> type[Any]:
        from src.app.wms_integration.models.evidence import WmsCallEvidence

        return WmsCallEvidence


debug_data_cleanup_repository = DebugDataCleanupRepository()


__all__ = ["DebugDataCleanupRepository", "DebugDataCleanupSelection", "debug_data_cleanup_repository"]
