"""原 reservation 的同架冻结面序；原动作恢复先于下一面创建。"""

from sqlalchemy import select
from wes_plugin_sdk import ReturnBufferDrainReady, TransportRackPosition

from src.app.execution.models import TransportDecisionBinding
from src.app.transport.models import TransportMember, TransportTask
from src.utils.timezone import timezone

from .drain_inbound_repository import DRAIN_ROTATION_STEP, DrainInboundRepository
from .return_motion_model import AutomaticPickingReturnMotion


class DrainRotationFlow:
    def __init__(self, returns, creator, *, clock=timezone.now_for_db):
        self.returns, self.creator, self.clock = returns, creator, clock
        self.repository = DrainInboundRepository()

    async def prepare_in_session(self, db, line):
        if not line.is_active:
            return 0
        current = await self.returns.drains.current(db, line)
        if current is None or type(current[1]) is not ReturnBufferDrainReady:
            return 0
        root, ready = current
        phases, count = await self.returns.motions.current(db, line.id), 0
        for row in await self.repository.racks(db, root.id):
            arrival = await self.repository.current_arrival(db, line, row)
            if arrival is None or row.arrival_evidence_id != arrival[1].id:
                continue
            task, fact = arrival
            faces = next(r.rack_faces for r in ready.racks if r.rack_id == row.rack_id)
            if task.request_json["target_face"] == faces[-1]:
                continue
            stage = next(
                (
                    p
                    for p in phases
                    if p.arrival_evidence_id == fact.id
                    and p.rack_id == row.rack_id
                    and p.rack_face == task.request_json["target_face"]
                ),
                None,
            )
            if stage is None:
                stage = AutomaticPickingReturnMotion(
                    workline_id=line.id,
                    arrival_evidence_id=fact.id,
                    source_evidence_id=root.response_evidence_id,
                    rack_id=row.rack_id,
                    rack_face=task.request_json["target_face"],
                )
                db.add(stage)
                await db.flush()
                phases.append(stage)
                count += 1
            if stage.source_evidence_id != root.response_evidence_id or stage.inbound_client_request_id is not None:
                raise ValueError("drain motion stage differs from original reservation")
        return count

    async def advance_in_session(self, db, line, root, ready):
        phases, count = await self.returns.motions.current(db, line.id), 0
        for row in await self.repository.racks(db, root.id):
            arrival = await self.repository.current_arrival(db, line, row)
            if arrival is None or row.arrival_evidence_id != arrival[1].id:
                continue
            task, fact = arrival
            faces = next(r.rack_faces for r in ready.racks if r.rack_id == row.rack_id)
            index = faces.index(task.request_json["target_face"])
            if index + 1 == len(faces):
                continue  # 离场由后续切片承接。
            stage = next(
                (
                    p
                    for p in phases
                    if p.arrival_evidence_id == fact.id and p.rack_id == row.rack_id and p.rack_face == faces[index]
                ),
                None,
            )
            if stage is None or stage.closed_at is None:
                continue
            if stage.source_evidence_id != root.response_evidence_id or stage.inbound_client_request_id is not None:
                raise ValueError("drain motion stage differs from original reservation")
            binding = await db.get(TransportDecisionBinding, row.binding_id)
            if await self._newer_action(db, row, binding):
                continue
            allocations = await self.returns.repository.current(db, line.id)
            direct = [a for a in allocations if self.returns._motion_allocation_matches(a, stage, (root, ready))]
            if any([not await self.returns._motion_obligation_closed(db, a) for a in direct]):
                continue
            position = task.request_json["position" if task.kind == "RACK_ROTATE" else "target"]
            handle = await self.creator.create_rotate(
                db,
                workline_id=line.id,
                picking_task_id=None,
                source_evidence_id=root.response_evidence_id,
                correlation_id=f"drain-rotation:{root.operation_id}:{row.rack_id}:{index + 1}",
                step=DRAIN_ROTATION_STEP,
                rack_id=row.rack_id,
                position=TransportRackPosition(position["location_code"]),
                target_face=faces[index + 1],
            )
            next_binding = await db.scalar(
                select(TransportDecisionBinding).where(
                    TransportDecisionBinding.client_request_id == handle.client_request_id
                )
            )
            if next_binding is None:
                raise ValueError("drain rotation lost created binding")
            row.binding_id, row.arrival_evidence_id = next_binding.id, None
            stage.archived_at = self.clock()
            await db.flush()
            count += 1
        return count

    async def _newer_action(self, db, row, binding):
        actions = (
            await db.execute(
                select(TransportTask, TransportDecisionBinding)
                .join(TransportMember, TransportMember.transport_task_id == TransportTask.transport_task_id)
                .outerjoin(
                    TransportDecisionBinding,
                    TransportDecisionBinding.client_request_id == TransportTask.client_request_id,
                )
                .where(
                    TransportMember.object_type == "RACK",
                    TransportMember.object_id == row.rack_id,
                    TransportTask.created_at >= binding.created_at,
                )
            )
        ).all()
        return any(
            (owner is None or owner.causal_token > binding.causal_token)
            and not (task.status == "REJECTED" and task.outcome_version == task.published_outcome_version)
            for task, owner in actions
        )
