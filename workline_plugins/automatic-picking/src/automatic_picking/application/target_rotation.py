"""已发布 ROTATE ACCEPT 的目标准备；停在可读取的权威目标就绪，不创建 PUT。"""

from dataclasses import dataclass

from wes_plugin_sdk import (
    PickingBinCell,
    PickingMaterialAccept,
    PickingRackSlot,
    PickingTargetRackSlot,
    PickingTargetRotate,
    TransportRackPosition,
)

from .target_preparation import ReadyTarget
from .target_rotation_repository import TARGET_ROTATION_STEP


@dataclass(frozen=True, slots=True)
class MatchedTargetRotation:
    """外部 reader 显式给定本盘 ACCEPT、精确工作位与当前架面；不猜设备映射。"""

    workline_id: int
    material_execution_id: int
    accept_evidence_id: int
    target: PickingTargetRackSlot
    position: TransportRackPosition
    current_face: str

    def __post_init__(self):
        if any(
            type(value) is not int or value <= 0
            for value in (self.workline_id, self.material_execution_id, self.accept_evidence_id)
        ):
            raise ValueError("target rotation requires positive identities")
        if type(self.target) is not PickingTargetRackSlot or type(self.position) is not TransportRackPosition:
            raise TypeError("target rotation requires typed target and position")
        if not isinstance(self.current_face, str) or not self.current_face.strip():
            raise ValueError("target rotation requires current face")


class AutomaticPickingTargetRotationFlow:
    def __init__(self, repository, transports, material_reader, position_reader):
        self.repository, self.transports = repository, transports
        self.material_reader, self.position_reader = material_reader, position_reader

    async def _context(self, db, pickup_id, line_id):
        work, pickup, _ = await self.repository.material_context_for_update(db, pickup_id)
        execution = await self.repository.material_execution(db, pickup.material_execution_id)
        if (
            execution is None
            or execution.workline_id != line_id
            or work.workline_id != line_id
            or execution.execution_code != pickup.pickup_id
            or execution.material_trace_id != pickup.pickup_id
        ):
            raise ValueError("target rotation material identity differs from Pickup")
        snapshot = (
            await self.material_reader.by_id(
                db, material_execution_id=execution.id, confirmation_id=pickup.material_confirmation_id
            )
            if pickup.material_confirmation_id is not None
            else (
                await self.material_reader.by_operation_id(
                    db, material_execution_id=execution.id, operation_id=execution.execution_code
                )
            )
        )
        task_id, source = await self.repository.material_source(db, work, pickup)
        if (
            snapshot is None
            or snapshot.status != "COMPLETED"
            or snapshot.published_at is None
            or snapshot.evidence_id != pickup.accept_evidence_id
            or type(snapshot.outcome.result) is not PickingMaterialAccept
            or type(snapshot.intent.source_locator) not in (PickingBinCell, PickingRackSlot)
            or snapshot.intent.task_id != task_id
            or snapshot.intent.source_locator != source
            or snapshot.intent.plan_revision != await self.repository.source_revision(db, work)
        ):
            raise ValueError("target rotation requires this material's published ACCEPT identity")
        return work, pickup, execution, snapshot.outcome.result

    async def advance_in_session(self, db, *, line_id, now, pickup_id=None):  # noqa: ARG002 - batch owner 的固定入口
        count = 0
        for current_id in await self.repository.target_pending(db, line_id):
            if pickup_id is not None and current_id != pickup_id:
                continue
            work, pickup, execution, accepted = await self._context(db, current_id, line_id)
            if pickup.target_rotation_transport_task_id is not None:
                await self.repository.transport(db, work, pickup, accepted.target_locator)
                continue
            if (
                type(accepted.target_preparation) is not PickingTargetRotate
                or execution.status == "CLOSED"
                or not await self.repository.target_allowed(db, work)
                or self.position_reader is None
            ):
                continue
            observed = await self.position_reader.read_in_session(
                db, workline_id=line_id, material_execution_id=execution.id, target=accepted.target_locator
            )
            if observed is None:
                continue
            if (
                type(observed) is not MatchedTargetRotation
                or observed.workline_id != line_id
                or observed.material_execution_id != execution.id
                or observed.accept_evidence_id != pickup.accept_evidence_id
                or observed.target != accepted.target_locator
            ):
                raise ValueError("target rotation input identity differs from frozen ACCEPT")
            position = await self.repository.position(db, observed.target.rack_id)
            expected = {"kind": "RACK_POSITION", "location_code": observed.position.location_code}
            if (
                position is None
                or position.workline_id != line_id
                or position.position_unknown
                or position.position_json != expected
                or position.arrival_face != observed.current_face
                or position.source_causal_token is None
                or position.source_effect_phase != "FINAL_RESULT"
                or observed.current_face == observed.target.rack_face
            ):
                continue
            handle = await self.transports.create_rotate(
                db,
                workline_id=line_id,
                picking_task_id=work.picking_task_id,
                source_evidence_id=pickup.accept_evidence_id,
                correlation_id="auto-target-rotation:" + pickup.pickup_id,
                step=TARGET_ROTATION_STEP,
                rack_id=observed.target.rack_id,
                position=observed.position,
                target_face=observed.target.rack_face,
            )
            pickup.target_rotation_transport_task_id = handle.transport_task_id
            await db.flush()
            count += 1
        return count

    async def read_in_session(self, db, *, workline_id, material_execution_id, target):
        pickup_id = await self.repository.pickup_by_material(db, material_execution_id)
        if pickup_id is None:
            return None
        work, pickup, execution, accepted = await self._context(db, pickup_id, workline_id)
        if (
            accepted.target_locator != target
            or type(accepted.target_preparation) is not PickingTargetRotate
            or execution.status == "CLOSED"
            or pickup.target_rotation_transport_task_id is None
        ):
            return None
        if await self.repository.ready(db, work, pickup, target):
            return ReadyTarget(workline_id, material_execution_id, target)
        return None

    async def apply_transport_result_in_session(self, db, *, line, evidence):
        pickup_id = await self.repository.pickup_by_transport(db, evidence.transport_task_id)
        if pickup_id is None:
            return "RECONCILING"
        work, pickup, _, accepted = await self._context(db, pickup_id, line.id)
        task, binding = await self.repository.transport(db, work, pickup, accepted.target_locator)
        if not self.repository.matches(evidence, task, binding):
            return "RECONCILING"
        version = evidence.normalized_payload.get("outcome_version")
        if type(version) is not int or not 0 < version <= task.outcome_version:
            return "RECONCILING"
        return "TARGET_ROTATION_OBSERVED"
