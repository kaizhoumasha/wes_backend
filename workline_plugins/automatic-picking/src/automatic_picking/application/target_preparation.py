"""本盘可靠取出且普通/ROTATE/REPLACE 目标就绪后创建原 PUT。"""

from dataclasses import dataclass

from wes_plugin_sdk import (
    PickingBinCell,
    PickingMaterialAccept,
    PickingRackSlot,
    PickingTargetRackSlot,
    PickingTargetReplace,
    PickingTargetRotate,
)

from src.app.device.contracts import WORKLINE_BUSINESS_REF_TYPE, DeviceCommandRequest

from .pickup_repository import SourceObservation
from .target_replacement_repository import TARGET_DEPARTURE_STEP, TARGET_INBOUND_STEP


@dataclass(frozen=True, slots=True)
class ReadyTarget:
    """reader 已核对本盘目标架、面、工作位及直接准备依赖；未知返回 None。"""

    workline_id: int
    material_execution_id: int
    target: PickingTargetRackSlot

    def __post_init__(self):
        if any(type(value) is not int or value <= 0 for value in (self.workline_id, self.material_execution_id)):
            raise ValueError("ready target requires WorkLine/material identity")
        if type(self.target) is not PickingTargetRackSlot:
            raise TypeError("ready target requires exact RACK_SLOT")


class AutomaticPickingTargetFlow:
    def __init__(
        self,
        repository,
        commands,
        material_reader,
        target_reader,
        request_factory,
        *,
        rotation_flow=None,
        replacement_flow=None,
    ):
        self.rotation_flow = rotation_flow
        self.replacement_flow = replacement_flow
        self.repository, self.commands = repository, commands
        self.material_reader, self.target_reader, self.request_factory = material_reader, target_reader, request_factory

    async def advance_in_session(self, db, *, line_id, now):
        count = 0
        if self.replacement_flow is not None:
            await self.replacement_flow.recover_in_session(db, line_id=line_id)
        for pickup_id in await self.repository.target_pending(db, line_id):
            work, pickup, _ = await self.repository.material_context_for_update(db, pickup_id)
            execution = await self.repository.material_execution(db, pickup.material_execution_id)
            if (
                execution is None
                or execution.execution_code != pickup.pickup_id
                or execution.material_trace_id != pickup.pickup_id
                or execution.workline_id != line_id
                or work.workline_id != line_id
            ):
                raise ValueError("PUT material identity differs from Pickup")
            if execution.status == "CLOSED":
                continue
            if pickup.put_command_code is not None:
                command = await self.repository.put_command(db, pickup.put_command_code)
                if (
                    command is None
                    or command.workline_id != line_id
                    or command.material_execution_id != execution.id
                    or command.execution_ref_type != WORKLINE_BUSINESS_REF_TYPE
                    or command.execution_ref_id != "put:" + pickup.pickup_id
                    or command.command_code == pickup.command_code
                ):
                    raise ValueError("PUT Command identity differs from Pickup")
                # 原义务不因取消、时限或映射变化换身份；本片不执行后续报告/闭合。
                continue
            if pickup.material_confirmation_id is None:
                snapshot = await self.material_reader.by_operation_id(
                    db,
                    material_execution_id=execution.id,
                    operation_id=execution.execution_code,
                )
            else:
                snapshot = await self.material_reader.by_id(
                    db,
                    material_execution_id=execution.id,
                    confirmation_id=pickup.material_confirmation_id,
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
                raise ValueError("PUT requires current published ACCEPT for this BIN_CELL material")
            accepted = snapshot.outcome.result
            if not await self.repository.target_allowed(db, work):
                continue
            reader = self.target_reader
            if type(accepted.target_preparation) is PickingTargetRotate:
                if self.rotation_flow is None:
                    continue
                count += await self.rotation_flow.advance_in_session(
                    db, line_id=line_id, now=now, pickup_id=pickup.pickup_id
                )
                reader = self.rotation_flow
            elif type(accepted.target_preparation) is PickingTargetReplace:
                if self.replacement_flow is None:
                    continue
                count += await self.replacement_flow.prepare_in_session(
                    db, work=work, pickup=pickup, execution=execution, accepted=accepted
                )
                reader = self.replacement_flow
            # Work 当前栈顶可能已属于 M2；PUT 只核对本盘冻结来源和原 Command。
            source_fact = await self.repository.physical_reader.read_in_session(
                db, work=work, source=source, pickup=pickup
            )
            if type(source_fact) is not SourceObservation:
                raise TypeError("PUT requires typed source observation")
            if source_fact.removed_evidence_id is None:
                continue
            removed = await self.repository.material_evidence(db, source_fact.removed_evidence_id)
            if (
                removed is None
                or removed.kind != "DEVICE_RESULT"
                or removed.workline_id != line_id
                or removed.command_code != pickup.command_code
                or removed.apply_status != "APPLIED"
            ):
                raise ValueError("removed fact differs from Pickup source Command")
            observed = await reader.read_in_session(
                db,
                workline_id=line_id,
                material_execution_id=execution.id,
                target=accepted.target_locator,
            )
            if observed is None:
                continue
            if (
                type(observed) is not ReadyTarget
                or observed.workline_id != line_id
                or observed.material_execution_id != execution.id
                or observed.target != accepted.target_locator
            ):
                raise ValueError("ready target identity differs from frozen ACCEPT")
            count += await self._create_put(db, line_id, pickup, execution, accepted, now)
        return count

    async def _create_put(self, db, line_id, pickup, execution, accepted, now):
        request = await self.request_factory(
            db,
            workline_id=line_id,
            pickup_id=pickup.pickup_id,
            material_execution_id=execution.id,
            target=accepted.target_locator,
            now=now,
        )
        if request is None:
            return 0
        if (
            type(request) is not DeviceCommandRequest
            or request.workline_id != line_id
            or request.material_execution_id != execution.id
            or request.execution_ref_type != WORKLINE_BUSINESS_REF_TYPE
            or request.execution_ref_id != "put:" + pickup.pickup_id
        ):
            raise ValueError("PUT request must retain WorkLine/material/step identity")
        command = await self.commands.create_command_in_session(db, request)
        pickup.put_command_code = command.command_code
        await db.flush()
        return 1

    async def apply_transport_result_in_session(self, db, *, line, evidence):
        if evidence.normalized_payload.get("step") in {TARGET_DEPARTURE_STEP, TARGET_INBOUND_STEP}:
            if self.replacement_flow is None:
                return "WAIT_FOR_EXIT"
            return await self.replacement_flow.apply_transport_result_in_session(db, line=line, evidence=evidence)
        if self.rotation_flow is None:
            return "WAIT_FOR_EXIT"
        return await self.rotation_flow.apply_transport_result_in_session(db, line=line, evidence=evidence)
