"""排空当前引用与本线准入事实；调用方持有 WorkLine 事务锁。"""

from sqlalchemy import select

from src.app.execution.models import InboundEvidence, TransportDecisionBinding
from src.app.wms_integration.outbound_picking.repositories.picking_task_repository import PickingTaskRepository

from .drain_model import AutomaticPickingDrain
from .feed_repository import FeedRepository
from .return_allocation_repository import ReturnAllocationRepository
from .work_model import AutomaticPickingWork


class DrainRepository:
    def __init__(self):
        self.allocations = ReturnAllocationRepository()
        self.returns = self.allocations.returns
        self.tasks = PickingTaskRepository()

    @staticmethod
    def validate_departure_handoffs(root, ready):
        handoffs = root.departure_handoffs
        if (
            type(handoffs) is not dict
            or not set(handoffs).issubset(r.rack_id for r in ready.racks)
            or any(type(value) is not str or not value.strip() for value in handoffs.values())
            or len(set(handoffs.values())) != len(handoffs)
        ):
            raise ValueError("drain departure handoffs differ from original reservation")

    async def current(self, db, line_id):
        return await db.scalar(
            select(AutomaticPickingDrain)
            .where(AutomaticPickingDrain.workline_id == line_id, AutomaticPickingDrain.archived_at.is_(None))
            .with_for_update()
        )

    async def evidence(self, db, evidence_id):
        return await db.get(InboundEvidence, evidence_id)

    async def normal_service_pending(self, db, line_id):
        work = await db.scalar(
            select(AutomaticPickingWork.id).where(
                AutomaticPickingWork.workline_id == line_id,
                AutomaticPickingWork.archived_at.is_(None),
                AutomaticPickingWork.phase != "CLOSED",
            )
        )
        window = await db.scalar(
            select(TransportDecisionBinding.id).where(
                TransportDecisionBinding.workline_id == line_id,
                TransportDecisionBinding.step == "PICKING_TASK_BIN_SOURCE_RACK_IN",
                TransportDecisionBinding.window_target_location_code.is_not(None),
                TransportDecisionBinding.window_released_at.is_(None),
            )
        )
        return (
            work is not None or window is not None or bool(await FeedRepository().list_admission_pending(db, line_id))
        )

    async def replace(self, db, *, current, line_id, operation_id, return_ids):
        if current is None:
            current = AutomaticPickingDrain(
                workline_id=line_id, operation_id=operation_id, return_ids=return_ids, request_return_ids=return_ids
            )
            db.add(current)
        else:
            current.operation_id, current.return_ids = operation_id, return_ids
            current.request_return_ids = return_ids
            current.response_evidence_id = current.retry_at = None
            current.returned_ids = []
            current.departure_handoffs = {}
        await db.flush()
        return current

    async def continue_current(self, db, *, current, operation_id, remaining, requested, now):
        # 先释放唯一 current 索引；整次交接仍由调用方的 WorkLine 事务原子提交。
        current.archived_at = now
        await db.flush()
        successor = AutomaticPickingDrain(
            workline_id=current.workline_id,
            operation_id=operation_id,
            started_operation_id=current.started_operation_id,
            return_ids=list(remaining),
            request_return_ids=list(requested),
        )
        db.add(successor)
        await db.flush()
        # 只移交未闭合业务归属，原分配请求、Transport 和因果 Evidence 不变。
        for allocation in await self.allocations.current(db, current.workline_id):
            if allocation.drain_id == current.id and allocation.settled_at is None:
                allocation.drain_id = successor.id
        await db.flush()
        return successor
