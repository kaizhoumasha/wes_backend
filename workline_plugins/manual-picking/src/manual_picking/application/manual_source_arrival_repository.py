"""当前人工来源架到位事实的读取和事务存储。"""

from sqlalchemy import select
from sqlalchemy.orm import aliased

from src.app.execution.models import TransportDecisionBinding
from src.app.transport.models import TransportMember, TransportTask
from src.app.wms_integration.outbound_picking.models import PickingTask
from src.app.wms_integration.outbound_picking.models.plan_members import PickingTaskBinSourceRack
from src.app.workline.models import WorkLine

from .drain_repository import SOURCE_RACK_IN_STEP, current_rack_action_predicate
from .manual_source_arrival_model import ManualPickingSourceArrival


class ManualSourceArrivalRepository:
    async def line_for_update(self, db, workline_id):
        return await db.scalar(select(WorkLine).where(WorkLine.id == workline_id).with_for_update())

    async def context(self, db, *, workline_id, picking_task_id, plan_revision, rack_id, rack_face, transport_task_id):
        return (
            await db.execute(
                select(PickingTask, PickingTaskBinSourceRack, TransportDecisionBinding, TransportTask, TransportMember)
                .join(PickingTaskBinSourceRack, PickingTaskBinSourceRack.picking_task_id == PickingTask.id)
                .join(
                    TransportDecisionBinding,
                    (TransportDecisionBinding.picking_task_id == PickingTask.id)
                    & (TransportDecisionBinding.source_evidence_id == PickingTaskBinSourceRack.source_evidence_id)
                    & (TransportDecisionBinding.resource_fence_id == PickingTaskBinSourceRack.rack_id),
                )
                .join(TransportTask, TransportTask.client_request_id == TransportDecisionBinding.client_request_id)
                .join(TransportMember, TransportMember.transport_task_id == TransportTask.transport_task_id)
                .where(
                    PickingTask.id == picking_task_id,
                    PickingTask.workline_id == workline_id,
                    PickingTaskBinSourceRack.plan_revision == plan_revision,
                    PickingTaskBinSourceRack.rack_id == rack_id,
                    PickingTaskBinSourceRack.rack_face == rack_face,
                    TransportDecisionBinding.workline_id == workline_id,
                    TransportDecisionBinding.step == SOURCE_RACK_IN_STEP,
                    TransportTask.transport_task_id == transport_task_id,
                )
                .with_for_update()
            )
        ).one_or_none()

    async def current(self, db, workline_id):
        return await db.scalar(
            select(ManualPickingSourceArrival).where(
                ManualPickingSourceArrival.workline_id == workline_id, ManualPickingSourceArrival.is_current.is_(True)
            )
        )

    async def by_transport(self, db, workline_id, transport_task_id):
        return await db.scalar(
            select(ManualPickingSourceArrival).where(
                ManualPickingSourceArrival.workline_id == workline_id,
                ManualPickingSourceArrival.source_transport_task_id == transport_task_id,
            )
        )

    async def retire_current(self, db, workline_id):
        current = await self.current(db, workline_id)
        if current is not None:
            current.is_current = False
            await db.flush()

    async def superseded(self, db, fact):
        owner = await db.scalar(
            select(TransportDecisionBinding).where(TransportDecisionBinding.causal_token == fact.source_causal_token)
        )
        if owner is None:
            return True
        newer = aliased(TransportDecisionBinding)
        return (
            await db.scalar(
                select(newer.id)
                .join(TransportTask, TransportTask.client_request_id == newer.client_request_id)
                .where(
                    newer.resource_fence_id == fact.rack_id,
                    newer.causal_token > fact.source_causal_token,
                    TransportTask.kind.in_(("RACK_MOVE", "RACK_ROTATE")),
                    current_rack_action_predicate(newer, TransportTask, owner),
                )
                .limit(1)
            )
            is not None
        )

    async def save(self, db, fact):
        db.add(fact)
        await db.flush()
