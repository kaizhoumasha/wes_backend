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

from manual_picking.definition import FIVE_RACK
from src.app.execution.repositories.transport_decision_binding_repository import transport_decision_binding_repository
from src.app.wms_integration.outbound_picking.repositories.picking_task_repository import picking_task_repository
from src.core.uuid7 import new_uuid7

from .drain_execution_repository import DrainExecutionRepository
from .drain_repository import DRAIN_RACK_IN_STEP, DRAIN_RACK_OUT_STEP, DRAIN_RACK_ROTATE_STEP

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
        drain_executions: Any = None,
        tasks: Any = None,
        bindings: Any = None,
        uuid_factory: Any = new_uuid7,
    ) -> None:
        self._drain_executions = drain_executions or DrainExecutionRepository()
        self.repository = repository
        self._passages = passages
        self._prepare = prepare
        self._scheduler = scheduler
        self._batch_scheduler = batch_scheduler
        self._history = history
        self._tasks = tasks or picking_task_repository
        self._bindings = bindings or transport_decision_binding_repository
        self._uuid_factory = uuid_factory

    async def retire_exception_in_session(self, db: Any, line: Any, now: Any) -> bool:
        """调用方持有 WorkLine 锁；只退出原业务编排，物理事实仍由宿主重放。"""
        decision = await self.repository.current(db, line.id)
        if decision is None or not isinstance(decision.result, ReturnBufferDrainReady):
            return False
        execution = await self._drain_executions.for_evidence(db, workline_id=line.id, evidence_id=decision.evidence_id)
        if execution is None or execution.status != "ACTIVE":
            return False
        tasks = await self._drain_executions.retirement_tasks(db, workline_id=line.id)
        trigger = next((task for task in tasks if task.status in {"QUEUED", "PREPARING", "EXECUTING"}), None)
        if trigger is None:
            return False
        actions = await self._drain_executions.retirement_actions(db, execution=execution, decision=decision)
        # 已留存的确定终态由 Transport Evidence owner 先应用，下次持久扫描再重验。
        if any(has_final and task.status in {"ACCEPTED", "RECONCILING"} for _, task, has_final in actions):
            return False
        blockers = []
        for binding, task, _ in actions:
            if task.status in {"FAILED", "REJECTED"}:
                if (
                    binding.step == DRAIN_RACK_IN_STEP
                    or (binding.step == DRAIN_RACK_OUT_STEP and task.status == "REJECTED")
                    or (
                        binding.step in {DRAIN_RACK_ROTATE_STEP, DRAIN_RACK_OUT_STEP}
                        and task.status == "FAILED"
                        and task.reason_code == "RCS_TASK_CANCELLED"
                    )
                ):
                    continue
                blockers.append((0, task.client_request_id, "DRAIN_ACTION_TERMINAL_EXCEPTION"))
            elif task.status == "ACCEPTED" and task.result_deadline_at is not None and now > task.result_deadline_at:
                blockers.append((1, task.client_request_id, "DRAIN_RESULT_OVERDUE"))
            elif task.status == "RECONCILING":
                blockers.append((2, task.client_request_id, "DRAIN_RESULT_UNKNOWN"))
        if not blockers:
            return False
        _, blocker, reason = min(blockers)
        closed = await self._drain_executions.close_in_session(
            db,
            execution,
            status="EXCEPTION_RETIRED",
            closed_at=now,
            reason_code=reason,
            blocker_client_request_id=blocker,
            trigger_picking_task_id=trigger.id,
        )
        if closed:
            await self._drain_executions.release_windows_in_session(db, execution=execution, closed_at=now)
        return closed

    async def decide_in_session(
        self,
        db: Any,
        line: Any,
        now: Any,
        *,
        limit: int = 4,
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
        rows = await self._passages.ready_prefix_for_update(db, line.id, limit=limit)
        rows = await self._drain_executions.eligible_return_prefix(db, workline_id=line.id, rows=tuple(rows))
        if not rows:
            return 0, None
        if current is None:
            if await self._tasks.has_active_for_workline(db, line.id) and await self._five_rack_window_occupied(
                db, line
            ):
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

    async def _five_rack_window_occupied(self, db: Any, line: Any) -> bool:
        """FIVE_RACK 容量窗口当前是否被占用——业务事实（R6），不是全局位置投影数量（红队复核）。

        活动 PickingTask 存在但 FIVE_RACK 目标点当前没有未释放的窗口时，drain 仍可插缝；
        窗口占用与否最终仍由 RackInboundWindowService.admit 的目标点行锁裁决，不是新的竞态。
        """
        location = (line.position_bindings.get(FIVE_RACK.slot_key) or {}).get("location_id")
        if not location:
            return True  # 拿不到目标点信息时保守拦截，不静默放行
        active = await self._bindings.list_active_window_for_target(
            db, workline_id=line.id, target_location_code=location
        )
        return bool(active)

    async def trigger_full_drain_in_session(self, db: Any, line: Any, now: Any) -> None:
        """停线前主动排空：不等待下一次 tick，一次性纳入当前全部待回库料箱。"""
        await self.decide_in_session(db, line, now, limit=FULL_DRAIN_LIMIT)

    async def return_in_session(self, db: Any, line: Any, decision: Any, rack_id: str, location: str, now: Any) -> bool:
        execution = await self._drain_executions.for_evidence(db, workline_id=line.id, evidence_id=decision.evidence_id)
        if execution is None:
            raise ValueError("drain READY execution missing")
        if execution.status != "ACTIVE":
            return False
        rack_face = await self.active_rack_face(db, line, decision, rack_id)
        if rack_face is None:
            return False
        rack_id, face, exhausted = rack_face
        if exhausted:
            return False
        latest = await self._latest_return_for_drain(db, line, decision, rack_id, face)
        if latest is not None:
            outcome, _ = latest
            if isinstance(outcome.result, BinBatchNoBatch):
                return False
        rows = await self._passages.ready_prefix_for_update(db, line.id)
        rows = await self._drain_executions.eligible_return_prefix(db, workline_id=line.id, rows=tuple(rows))
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
        await self._drain_executions.freeze_return_members_in_session(
            db,
            execution_id=execution.id,
            return_batch_operation_id=intent.operation_id,
            return_ids=tuple(row.id for row in rows),
        )
        return True

    async def active_rack_face(self, db: Any, line: Any, decision: Any, rack_id: str) -> tuple[str, str, bool] | None:
        if not isinstance(decision.result, ReturnBufferDrainReady):
            return None
        rows = await self._passages.ready_prefix_for_update(db, line.id)
        rows = await self._drain_executions.eligible_return_prefix(db, workline_id=line.id, rows=tuple(rows))
        last_exhausted: tuple[str, str] | None = None
        for rack in decision.result.racks:
            if rack.rack_id != rack_id:
                continue
            for face in rack.rack_faces:
                latest = await self._latest_return_for_drain(db, line, decision, rack.rack_id, face)
                if latest is None:
                    if rows:
                        return rack.rack_id, face, False
                    return (*last_exhausted, False) if last_exhausted is not None else (rack.rack_id, face, False)
                outcome, _ = latest
                if isinstance(outcome.result, BinBatchNoBatch):
                    last_exhausted = rack.rack_id, face
                    continue
                return rack.rack_id, face, False
            return (*last_exhausted, True) if last_exhausted is not None else None
        raise ValueError("rack is not reserved by drain decision")

    async def _latest_return_for_drain(self, db: Any, line: Any, decision: Any, rack_id: str, face: str) -> Any:
        execution = await self._drain_executions.for_evidence(db, workline_id=line.id, evidence_id=decision.evidence_id)
        if execution is None:
            raise ValueError("drain READY execution missing")
        latest = await self._drain_executions.latest_return_evidence_for_execution(
            db, execution_id=execution.id, workline_id=line.id, rack_id=rack_id, rack_face=face
        )
        if latest is None:
            return None
        evidence, completed_at = latest
        _, outcome = await self._history.read_return(db, evidence, workline_id=line.id)
        return outcome, completed_at
