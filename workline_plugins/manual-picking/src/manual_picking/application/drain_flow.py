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

# ponytail: 停线场景下"排空全部"用一个足够大的上限代替真正的无界查询，
# 超过该规模的滚筒线缓冲区需要改为按计数分批触发。
FULL_DRAIN_LIMIT = 1000


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

    async def decide_in_session(
        self,
        db: Any,
        line: Any,
        now: Any,
        *,
        limit: int = 4,
        allow_active_task: bool = False,
    ) -> tuple[int, Any]:
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
        elif not allow_active_task and not await self.repository.has_completed_task(db, line.id):
            return 0, None
        rows = await self._passages.ready_return_prefix_for_update(db, line.id, limit=limit)
        if not rows:
            return 0, None
        if current is None:
            if not allow_active_task and await self._tasks.has_active_for_workline(db, line.id):
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

    async def trigger_full_drain_in_session(self, db: Any, line: Any, now: Any) -> None:
        """停线前主动排空：不等待下一次 tick，一次性纳入当前全部待回库料箱。"""
        await self.decide_in_session(db, line, now, limit=FULL_DRAIN_LIMIT)

    async def return_in_session(self, db: Any, line: Any, decision: Any, rack_id: str, location: str, now: Any) -> bool:
        rack_face = await self.active_rack_face(db, line, decision, rack_id)
        if rack_face is None:
            return False
        rack_id, face, exhausted = rack_face
        if exhausted:
            return False
        if await self._history.has_unclosed_return(
            db,
            workline_id=line.id,
            rack_id=rack_id,
            rack_face=face,
        ):
            return False
        latest = await self._latest_return_for_drain(db, line, decision, rack_id, face)
        if latest is not None:
            outcome, _ = latest
            if isinstance(outcome.result, BinBatchNoBatch):
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

    async def active_rack_face(self, db: Any, line: Any, decision: Any, rack_id: str) -> tuple[str, str, bool] | None:
        if not isinstance(decision.result, ReturnBufferDrainReady):
            return None
        rows = await self._passages.ready_return_prefix_for_update(db, line.id)
        last_exhausted: tuple[str, str] | None = None
        for rack in decision.result.racks:
            if rack.rack_id != rack_id:
                continue
            for face in rack.rack_faces:
                latest = await self._latest_return_for_drain(db, line, decision, rack.rack_id, face)
                if latest is None:
                    if rows:
                        return rack.rack_id, face, False
                    return (*last_exhausted, True) if last_exhausted is not None else None
                outcome, _ = latest
                if isinstance(outcome.result, BinBatchNoBatch):
                    last_exhausted = rack.rack_id, face
                    continue
                return rack.rack_id, face, False
            return (*last_exhausted, True) if last_exhausted is not None else None
        raise ValueError("rack is not reserved by drain decision")

    async def _latest_return_for_drain(self, db: Any, line: Any, decision: Any, rack_id: str, face: str) -> Any:
        latest = await self._history.latest_return(
            db,
            workline_id=line.id,
            rack_id=rack_id,
            rack_face=face,
        )
        if latest is None:
            return None
        _, completed_at = latest
        if decision.completed_at is None:
            raise ValueError("drain completed time missing")
        return latest if completed_at > decision.completed_at else None
