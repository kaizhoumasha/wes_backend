"""在工作线事务内确认货架就位后推进一个 CTU 业务批次。"""

from __future__ import annotations

from typing import Any

from manual_picking.definition import FIVE_RACK, OUTLET, TRANSFER_RACK
from src.app.wms_integration.outbound_picking.repositories.picking_task_repository import picking_task_repository
from src.utils.timezone import timezone

from .passage_repository import PassageRepository
from .rack_readiness import rack_ready


class ManualPickingBatchDriver:
    def __init__(
        self, flow: Any, *, plans: Any, positions: Any, transports: Any, passages: Any = None, tasks: Any = None
    ) -> None:
        self._flow = flow
        self._plans = plans
        self._positions = positions
        self._transports = transports
        self._passages = passages or PassageRepository()
        self._tasks = tasks or picking_task_repository

    async def advance_completed_in_session(self, db: Any, line: Any) -> int:
        rows = await self._passages.unfinished_return_prefix_for_update(db, line.id)
        if not rows:
            return 0
        task = await self._tasks.get_by_task_id_for_update(db, rows[0].task_id)
        if task is None or task.workline_id != line.id or task.status != "EXECUTION_COMPLETED":
            return 0
        return await self.advance_in_session(db, line, task, require_target=False)

    async def advance_in_session(self, db: Any, line: Any, task: Any, *, require_target: bool = True) -> int:
        if not task.target_rack_id or not task.target_rack_face:
            return 0
        bindings = line.position_bindings
        if require_target and not await rack_ready(
            db,
            line,
            task.target_rack_id,
            task.target_rack_face,
            bindings[TRANSFER_RACK.slot_key]["location_id"],
            positions=self._positions,
            transports=self._transports,
        ):
            return 0
        ready = None
        for source in await self._plans.list_bin_source_racks(db, task.id):
            if await rack_ready(
                db,
                line,
                source.rack_id,
                source.rack_face,
                bindings[FIVE_RACK.slot_key]["location_id"],
                positions=self._positions,
                transports=self._transports,
            ):
                if ready is not None:
                    return 0
                ready = source
        if ready is None:
            return 0
        return int(
            await self._flow.advance_in_session(
                db,
                workline_id=line.id,
                workline_code=line.line_code,
                task_id=task.task_id,
                rack_id=ready.rack_id,
                rack_face=ready.rack_face,
                return_location=bindings[OUTLET.slot_key]["location_id"],
                now=timezone.now_for_db(),
            )
        )


__all__ = ["ManualPickingBatchDriver"]
