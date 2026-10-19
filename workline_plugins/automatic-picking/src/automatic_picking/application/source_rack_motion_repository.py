"""普通来源换面的当前执行引用，调用方先持有 WorkLine 锁。"""

from sqlalchemy import select

from src.app.execution.models import InboundEvidence, PositionProjection, TransportDecisionBinding
from src.app.transport.models import TransportTask
from src.app.wms_integration.outbound_picking.models import PickingTask, PickingTaskBinSourceRack
from src.utils.canonical_json import canonical_json_digest

from .feed_model import AutomaticPickingFeed
from .return_motion_model import AutomaticPickingReturnMotion
from .return_motion_repository import ReturnMotionRepository

ROTATION_STEP = "AUTOMATIC_PICKING_SOURCE_RACK_ROTATE"


class SourceRackMotionRepository:
    def __init__(self, motions=None):
        self.motions = motions or ReturnMotionRepository()

    async def by_id(self, db, motion_id):
        return await db.get(AutomaticPickingReturnMotion, motion_id, with_for_update=True)

    async def pending(self, db, line_id):
        return list(
            (
                await db.scalars(
                    select(AutomaticPickingReturnMotion)
                    .where(
                        AutomaticPickingReturnMotion.workline_id == line_id,
                        AutomaticPickingReturnMotion.archived_at.is_(None),
                        AutomaticPickingReturnMotion.rotation_transport_task_id.is_not(None),
                        AutomaticPickingReturnMotion.departure_operation_id.is_(None),
                    )
                    .order_by(AutomaticPickingReturnMotion.id)
                    .with_for_update()
                )
            ).all()
        )

    async def by_transport(self, db, transport_id):
        return await db.scalar(
            select(AutomaticPickingReturnMotion)
            .where(AutomaticPickingReturnMotion.rotation_transport_task_id == transport_id)
            .with_for_update()
        )

    async def next_member(self, db, line, current, intent):
        member = await db.get(PickingTaskBinSourceRack, intent.next_member_id)
        task = await db.get(PickingTask, member.picking_task_id) if member else None
        end = await db.get(InboundEvidence, current.service_end_evidence_id)
        if (
            member is None
            or task is None
            or end is None
            or task.workline_id != line.id
            or task.id != end.picking_task_id
            or (member.rack_id, member.rack_face) != (current.rack_id, intent.target_face)
        ):
            raise ValueError("rotation next member differs from original source owner")
        if (
            task.status != "EXECUTING"
            or task.plan_blocked_evidence_id is not None
            or member.cancelled_evidence_id is not None
            or member.id in current.completed_source_member_ids
        ):
            return None
        feed = await db.scalar(
            select(AutomaticPickingFeed.id).where(AutomaticPickingFeed.source_member_id == member.id)
        )
        return member if feed is None else None

    async def transport(self, db, current):
        task = await db.scalar(
            select(TransportTask).where(TransportTask.transport_task_id == current.rotation_transport_task_id)
        )
        binding = (
            await db.scalar(
                select(TransportDecisionBinding).where(
                    TransportDecisionBinding.client_request_id == task.client_request_id
                )
            )
            if task
            else None
        )
        end = await db.get(InboundEvidence, current.service_end_evidence_id)
        if (
            end is None
            or task is None
            or binding is None
            or task.kind != "RACK_ROTATE"
            or task.authority_workline_id != current.workline_id
            or binding.workline_id != current.workline_id
            or binding.picking_task_id != end.picking_task_id
            or binding.step != ROTATION_STEP
            or binding.resource_fence_id != current.rack_id
            or binding.source_evidence_id != current.service_end_evidence_id
            or binding.correlation_id != f"auto-source-rotation:{current.id}"
            or task.request_json.get("rack_id") != current.rack_id
            or task.request_json.get("rcs_template_id") != "CTU02"
        ):
            raise ValueError("source rotation lost original Transport identity")
        return task, binding

    async def bind(self, db, current, transport_id):
        current.rotation_transport_task_id = transport_id
        await db.flush()

    async def observe(self, db, line, evidence):
        current = await self.by_transport(db, evidence.transport_task_id)
        if current is None:
            return "IGNORED"
        task, binding = await self.transport(db, current)
        payload = evidence.normalized_payload
        if (
            evidence.workline_id != line.id
            or current.workline_id != line.id
            or evidence.picking_task_id != binding.picking_task_id
            or payload.get("transport_task_id") != task.transport_task_id
            or payload.get("client_request_id") != task.client_request_id
            or payload.get("step") != ROTATION_STEP
            or payload.get("source_evidence_id") != binding.source_evidence_id
            or canonical_json_digest(payload) != evidence.payload_digest
        ):
            return "RECONCILING"
        version = payload.get("outcome_version")
        if type(version) is not int or not 0 < version <= task.outcome_version:
            return "RECONCILING"
        if version < task.outcome_version or current.archived_at is not None:
            return "SOURCE_ROTATION_OLD_OUTCOME"
        current.rotation_result_evidence_id = evidence.id
        await db.flush()
        return "SOURCE_ROTATION_OBSERVED"

    async def service_members(self, db, line, current, face):
        end = await db.get(InboundEvidence, current.service_end_evidence_id)
        task = await db.get(PickingTask, end.picking_task_id) if end else None
        if (
            task is None
            or task.workline_id != line.id
            or task.status != "EXECUTING"
            or task.plan_blocked_evidence_id is not None
        ):
            return task, []
        return task, list(
            (
                await db.scalars(
                    select(PickingTaskBinSourceRack)
                    .where(
                        PickingTaskBinSourceRack.picking_task_id == task.id,
                        PickingTaskBinSourceRack.rack_id == current.rack_id,
                        PickingTaskBinSourceRack.rack_face == face,
                        PickingTaskBinSourceRack.cancelled_evidence_id.is_(None),
                    )
                    .order_by(PickingTaskBinSourceRack.plan_revision, PickingTaskBinSourceRack.id)
                )
            ).all()
        )

    async def superseded(self, db, current, binding=None):
        position = await db.scalar(
            select(PositionProjection).where(
                PositionProjection.object_type == "RACK", PositionProjection.object_id == current.rack_id
            )
        )
        if (
            position is None
            or position.position_unknown
            or position.source_causal_token is None
            or position.source_causal_token
            <= (binding.causal_token if binding is not None else current.round_causal_token)
        ):
            return False
        facts = await db.scalars(
            select(InboundEvidence).where(
                InboundEvidence.transport_task_id == position.source_transport_task_id,
                InboundEvidence.kind == "TRANSPORT_RESULT",
                InboundEvidence.apply_status == "APPLIED",
                InboundEvidence.published_at.is_not(None),
            )
        )
        for fact in facts.all():
            if (
                await self.motions.current_arrival(
                    db, position.workline_id, fact.id, current.rack_id, position.arrival_face, position.position_json
                )
                is not None
            ):
                return True
        return False
