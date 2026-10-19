"""排空原成员与普通回架的当前关联、到位读取和成功事实交接。"""

from sqlalchemy import select
from wes_plugin_sdk import BinReturnBatchReady, ReturnBufferDrainReady

from src.app.execution.models import InboundEvidence, TransportDecisionBinding
from src.app.transport.models import TransportTask
from src.app.wms_integration.return_buffer_drain import ReturnBufferDrainResultReader
from src.utils.canonical_json import canonical_json_digest

from .drain_inbound_repository import DrainInboundRepository
from .drain_model import AutomaticPickingDrain
from .drain_repository import DrainRepository


class DrainReturnRepository:
    @staticmethod
    def validate_members(root):
        if (
            type(root.return_ids) is not list
            or type(root.returned_ids) is not list
            or not root.return_ids
            or any(type(i) is not int or i <= 0 for i in root.return_ids + root.returned_ids)
            or len(set(root.return_ids)) != len(root.return_ids)
            or len(set(root.returned_ids)) != len(root.returned_ids)
            or not set(root.returned_ids).issubset(root.return_ids)
        ):
            raise ValueError("drain returned facts differ from original members")

    async def current(self, db, line):
        root = await DrainRepository().current(db, line.id)
        if root is None:
            return None
        self.validate_members(root)
        snapshot = await ReturnBufferDrainResultReader().for_operation(
            db, workline_id=line.id, operation_id=root.operation_id
        )
        if snapshot is None or snapshot.intent.required_slot_count != len(root.return_ids):
            raise ValueError("drain return lost original request scope")
        result = snapshot.result
        if type(result) is ReturnBufferDrainReady and root.response_evidence_id != snapshot.evidence_id:
            result = None  # 仍由原 batch owner 完成 READY 引用交接。
        return root, result

    async def faces(self, db, line, current, faces):
        if current is None or type(current[1]) is not ReturnBufferDrainReady:
            return faces, ()
        from .return_allocation_flow import MatchedReturnFace

        root, _ready = current
        inbound, known, actions = DrainInboundRepository(), [], set()
        for row in await inbound.racks(db, root.id):
            binding = await db.get(TransportDecisionBinding, row.binding_id)
            task = await db.scalar(
                select(TransportTask).where(TransportTask.client_request_id == binding.client_request_id)
            )
            if task is None:
                raise ValueError("drain return lost original ingress")
            actions.add(task.transport_task_id)
            arrival = await inbound.current_arrival(db, line, row)
            if arrival is None or row.arrival_evidence_id != arrival[1].id:
                continue
            task, fact = arrival
            known.append(MatchedReturnFace(fact.id, line.id, row.rack_id, task.request_json["target_face"]))
        # 同一旧进场即使由另一入口上报，也必须通过当前原因果及9B持久交接。
        ordinary = []
        for face in faces:
            fact = await db.get(InboundEvidence, face.arrival_evidence_id)
            if fact.transport_task_id not in actions:
                ordinary.append(face)
        return tuple(dict.fromkeys((*ordinary, *known))), tuple(known)

    @staticmethod
    def allowed(current, row, *, drain_face):
        if current is None:
            return True
        root, result = current
        if row.id in root.returned_ids:
            return False  # 已确认成功不等于同批未知成员的当前领取已交接。
        if row.id in root.return_ids and result is None:
            return False
        return not drain_face or row.id in root.return_ids

    @staticmethod
    def owner(current, rows):
        if current is None or type(current[1]) is not ReturnBufferDrainReady:
            return None
        root, _ready = current
        return root.id if any(row.id in root.return_ids for row in rows) else None

    async def record_returned(self, db, allocation, task, members, evidence, reader):
        if allocation.drain_id is None:
            return 0
        root = await db.get(AutomaticPickingDrain, allocation.drain_id, with_for_update=True)
        if root is None or root.workline_id != allocation.workline_id:
            raise ValueError("returned allocation lost original drain owner")
        self.validate_members(root)
        intent, outcome = await reader.read_return(
            db, await db.get(InboundEvidence, allocation.response_evidence_id), workline_id=root.workline_id
        )
        frozen = {k: v for k, v in evidence.normalized_payload.items() if k not in {"step", "source_evidence_id"}}
        if (
            evidence.kind != "TRANSPORT_RESULT"
            or evidence.apply_status != "APPLIED"
            or evidence.workline_id != root.workline_id
            or evidence.picking_task_id is not None
            or evidence.transport_task_id != task.transport_task_id
            or evidence.published_at is None
            or evidence.normalized_payload.get("step") != "AUTOMATIC_PICKING_RETURN"
            or evidence.normalized_payload.get("source_evidence_id") != allocation.response_evidence_id
            or canonical_json_digest(evidence.normalized_payload) != evidence.payload_digest
            or canonical_json_digest(frozen) != canonical_json_digest(task.outcome_json)
            or type(outcome.result) is not BinReturnBatchReady
            or intent.operation_id != allocation.operation_id
            or intent.rack_id != allocation.rack_id
            or intent.rack_face != allocation.rack_face
            or len(intent.return_candidates) != len(allocation.return_ids)
            or len(outcome.result.moves) != allocation.selected_count
            or len(members) != allocation.selected_count
        ):
            raise ValueError("drain return success differs from exact published allocation")
        completed = set()
        for index, (candidate, move, member) in enumerate(
            zip(intent.return_candidates[: allocation.selected_count], outcome.result.moves, members, strict=True)
        ):
            expected = {
                "kind": "RACK_BIN_SLOT",
                "rack_id": move.target.rack_id,
                "rack_face": move.target.rack_face,
                "slot_id": move.target.slot_id,
            }
            if (
                member.object_type != "BIN"
                or member.object_id != candidate.bin_code
                or member.object_id != move.bin_code
                or member.target_json != expected
            ):
                raise ValueError("drain member differs from frozen backstock identity")
            if member.status == "SUCCEEDED" and not member.position_unknown and member.final_position_json == expected:
                completed.add(allocation.return_ids[index])
        completed &= set(root.return_ids)
        added = completed - set(root.returned_ids)
        if not added:
            return 0
        root.returned_ids = [i for i in root.return_ids if i in set(root.returned_ids) | completed]
        await db.flush()
        return len(added)
