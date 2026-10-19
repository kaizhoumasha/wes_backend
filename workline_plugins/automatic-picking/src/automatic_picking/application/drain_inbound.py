"""已发布排空 READY 的进场交接；完整参数只接受显式权威输入。"""

from dataclasses import dataclass

from wes_plugin_sdk import TransportRackPosition, TransportRackReference, TransportRcsTemplateId

from .drain_inbound_repository import DRAIN_INBOUND_STEP, DrainInboundRepository
from .drain_model import AutomaticPickingDrainRack


@dataclass(frozen=True, slots=True)
class MatchedDrainInbound:
    workline_id: int
    operation_id: str
    ready_evidence_id: int
    rack_id: str
    target: TransportRackPosition
    target_face: str
    rcs_template_id: TransportRcsTemplateId

    def __post_init__(self):
        if (
            type(self.workline_id) is not int
            or self.workline_id <= 0
            or type(self.ready_evidence_id) is not int
            or self.ready_evidence_id <= 0
            or any(type(v) is not str or not v.strip() for v in (self.operation_id, self.rack_id, self.target_face))
            or type(self.target) is not TransportRackPosition
            or self.rcs_template_id is not TransportRcsTemplateId.CTU01
        ):
            raise ValueError("drain inbound requires exact owner, READY, rack, position, face and CTU01")

    @property
    def source(self):
        return TransportRackReference(self.rack_id)


class DrainInboundFlow:
    def __init__(self, creator, reader):
        self.creator, self.reader = creator, reader
        self.repository = DrainInboundRepository()

    async def advance_in_session(self, db, line, current, ready):
        rows = {row.rack_id: row for row in await self.repository.racks(db, current.id)}
        count = 0
        # 恢复原义务不依赖新输入；racks 是集合，不能因一架等待而阻塞其它架。
        for row in rows.values():
            count += await self.repository.recover_arrival(db, line, row)
        if not line.is_active:
            return count
        for rack in ready.racks:
            if rack.rack_id in rows:
                continue
            intent = await self.reader.read_in_session(
                db,
                workline_id=line.id,
                operation_id=current.operation_id,
                ready_evidence_id=current.response_evidence_id,
                rack_id=rack.rack_id,
            )
            if intent is None:
                continue
            if type(intent) is not MatchedDrainInbound or (
                intent.workline_id,
                intent.operation_id,
                intent.ready_evidence_id,
                intent.rack_id,
                intent.target_face,
            ) != (line.id, current.operation_id, current.response_evidence_id, rack.rack_id, rack.rack_faces[0]):
                raise ValueError("drain inbound input differs from exact current READY")
            admitted = await self.creator.create_windowed_inbound(
                db,
                workline_id=line.id,
                workline_code=line.line_code,
                picking_task_id=None,
                source_evidence_id=current.response_evidence_id,
                correlation_id=f"drain:{current.operation_id}:{rack.rack_id}",
                step=DRAIN_INBOUND_STEP,
                resource_fence_id=rack.rack_id,
                intent=intent,
            )
            if admitted == "PENDING":
                continue
            binding = await self.repository.binding(db, line.id, current.operation_id, rack.rack_id)
            if binding is None:
                # 旧 drain 的 WorkLine-owned 同架窗口不属于当前轮，不能冒领 REUSED。
                if admitted == "REUSED":
                    continue
                raise ValueError("drain inbound lacks exact created binding")
            if binding.source_evidence_id != current.response_evidence_id or binding.resource_fence_id != rack.rack_id:
                raise ValueError("drain inbound binding identity drift")
            db.add(AutomaticPickingDrainRack(drain_id=current.id, rack_id=rack.rack_id, binding_id=binding.id))
            await db.flush()
            count += 1
        return count

    async def apply_transport_result_in_session(self, db, *, line, evidence):
        return await self.repository.observe(db, line, evidence)
