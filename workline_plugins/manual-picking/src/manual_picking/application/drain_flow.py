"""任务完成后的排空决定，复用宿主 prepare 与可靠 WMS 批次。"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from wes_plugin_sdk import (
    BinBatchNoBatch,
    BinReturnCandidate,
    ReturnBufferDrainReady,
    ReturnBufferDrainWait,
    wms_operations,
)

from src.app.wms_integration.outbound_picking.repositories.picking_task_repository import picking_task_repository
from src.core.uuid7 import new_uuid7


class ManualPickingDrainFlow:
    def __init__(
        self,
        repository: Any,
        passages: Any,
        prepare: Any,
        scheduler: Any,
        batch_scheduler: Any,
        history: Any,
        *,
        tasks: Any = None,
        uuid_factory: Any = new_uuid7,
    ) -> None:
        self.repository = repository
        self._passages = passages
        self._prepare = prepare
        self._scheduler = scheduler
        self._batch_scheduler = batch_scheduler
        self._history = history
        self._tasks = tasks or picking_task_repository
        self._uuid_factory = uuid_factory

    async def decide_in_session(self, db: Any, line: Any, now: Any) -> tuple[int, Any]:
        current = await self.repository.current(db, line.id)
        if current is not None:
            if current.intent.workline_code != line.line_code:
                raise ValueError("drain frozen WorkLine owner mismatch")
            if isinstance(current.result, ReturnBufferDrainReady):
                return 0, current
            if not isinstance(current.result, ReturnBufferDrainWait):
                return 0, None
            if now < current.completed_at + timedelta(milliseconds=current.result.retry_after_ms):
                return 0, None
        elif not await self.repository.has_completed_task(db, line.id):
            return 0, None
        rows = await self._passages.ready_return_prefix_for_update(db, line.id)
        if not rows:
            return 0, None
        if current is None:
            if await self._tasks.has_active_for_workline(db, line.id):
                return 0, None
            prepared = await self._prepare.prepare_next_in_session(db, line, now=now)
            if prepared.prepared:
                return 1, None
        intent = wms_operations.workline_return_buffer_drain_rack_decide(
            operation_id=self._uuid_factory(),
            workline_code=line.line_code,
            required_slot_count=len(rows),
        )
        await self._scheduler.create_in_session(db, intent, workline_id=line.id, created_at=now)
        return 1, None

    async def return_in_session(self, db: Any, line: Any, decision: Any, location: str, now: Any) -> bool:
        rack_face = await self.active_rack_face(db, line, decision)
        if rack_face is None:
            return False
        rack_id, face = rack_face
        latest = await self._history.latest_return(db, workline_id=line.id, rack_id=rack_id, rack_face=face)
        if latest is not None:
            outcome, completed_at = latest
            if isinstance(outcome.result, BinBatchNoBatch) and now < completed_at + timedelta(
                milliseconds=outcome.result.retry_after_ms
            ):
                return False
        rows = await self._passages.ready_return_prefix_for_update(db, line.id)
        if not rows:
            return False
        intent = wms_operations.outbound_bin_return_batch(
            operation_id=self._uuid_factory(),
            workline_code=line.line_code,
            rack_id=rack_id,
            rack_face=face,
            return_candidates=tuple(BinReturnCandidate(i, row.bin_code, location) for i, row in enumerate(rows, 1)),
        )
        await self._batch_scheduler.create_in_session(db, intent, workline_id=line.id, created_at=now)
        return True

    async def active_rack_face(self, db: Any, line: Any, decision: Any) -> tuple[str, str] | None:
        if not isinstance(decision.result, ReturnBufferDrainReady):
            return None
        rows = await self._passages.ready_return_prefix_for_update(db, line.id)
        for rack in decision.result.racks:
            for face in rack.rack_faces:
                latest = await self._history.latest_return(
                    db,
                    workline_id=line.id,
                    rack_id=rack.rack_id,
                    rack_face=face,
                )
                if latest is None:
                    return (rack.rack_id, face) if rows else None
                outcome, _ = latest
                if isinstance(outcome.result, BinBatchNoBatch):
                    continue
                return (rack.rack_id, face) if rows else None
        if rows:
            raise ValueError("drain READY 容量未闭合但所有货架面均已耗尽")
        return None
