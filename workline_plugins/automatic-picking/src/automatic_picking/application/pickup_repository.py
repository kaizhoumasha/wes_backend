"""来源事务的生产持久端口；设备事实语义由显式注入的 reader 提供。"""

from dataclasses import dataclass

from sqlalchemy import select
from wes_plugin_sdk import BinWorkPlanReady, PickingBinCell, PickingCellSource, PickingMaterialAccept

from automatic_picking.handlers.source_authorization import WorkSourceFact
from src.app.device.models.command import DeviceCommand
from src.app.execution.models import InboundEvidence, MaterialExecution
from src.app.execution.repositories import inbound_evidence_repository
from src.app.wms_adapter.outbound_picking.material_decide_typed import decode_outcome as decode_material
from src.app.wms_adapter.outbound_picking.work_plan_typed import decode_outcome as decode_plan
from src.app.wms_integration.outbound_picking.models import PickingTask, PickingTaskBinSourceRack
from src.app.workline.models import WorkLine
from src.utils.canonical_json import canonical_json_digest

from .feed_model import AutomaticPickingFeed
from .pickup_model import AutomaticPickingPickup
from .work_model import AutomaticPickingWork


@dataclass(frozen=True, slots=True)
class SourceObservation:
    """reader 已核对当前来源及匹配 Command 的可靠取出事实；不把 ACK 当作取出。"""

    source_ready: bool
    removed_evidence_id: int | None


class PickupRepository:
    def __init__(self, physical_reader):
        self.physical_reader = physical_reader

    async def material_context_for_update(self, db, pickup_id):
        work_id = await db.scalar(
            select(AutomaticPickingPickup.work_id).where(AutomaticPickingPickup.pickup_id == pickup_id)
        )
        if work_id is None:
            raise ValueError("scan Pickup identity is missing")
        work = await self._work_for_update(db, work_id)
        pickup = await self.pickup_for_update(db, pickup_id)
        command = await db.scalar(select(DeviceCommand).where(DeviceCommand.command_code == pickup.command_code))
        if command is None or command.workline_id != work.workline_id or command.execution_ref_id != pickup_id:
            raise ValueError("scan source Command identity differs from Pickup")
        return work, pickup, command

    async def scan_evidence_for_update(self, db, evidence_id):
        return await inbound_evidence_repository.get_by_id_for_update(db, evidence_id)

    async def material_evidence(self, db, evidence_id):
        return await db.get(InboundEvidence, evidence_id, populate_existing=True)

    async def material_execution(self, db, material_id):
        return await db.get(MaterialExecution, material_id, populate_existing=True) if material_id is not None else None

    async def material_pending(self, db, line_id):
        return list(
            (
                await db.scalars(
                    select(AutomaticPickingPickup.pickup_id)
                    .join(AutomaticPickingWork, AutomaticPickingWork.id == AutomaticPickingPickup.work_id)
                    .join(MaterialExecution, MaterialExecution.id == AutomaticPickingPickup.material_execution_id)
                    .where(
                        AutomaticPickingWork.workline_id == line_id,
                        MaterialExecution.status != "CLOSED",
                        AutomaticPickingPickup.accept_evidence_id.is_(None),
                    )
                    .order_by(AutomaticPickingPickup.id)
                )
            ).all()
        )

    async def material_retry_allowed(self, db, work, pickup):
        feed = await db.get(AutomaticPickingFeed, work.feed_id)
        member = await db.get(PickingTaskBinSourceRack, feed.source_member_id, populate_existing=True) if feed else None
        return (
            work.phase == "READY"
            and work.archived_at is None
            and member is not None
            and member.picking_task_id == work.picking_task_id
            and member.cancelled_evidence_id is None
            and work.consumed_pickup_id == pickup.pickup_id
            and work.authorization_evidence_id == pickup.authorization_evidence_id
        )

    async def material_source(self, db, work, pickup):
        """本盘仍未闭合时沿原授权引用读取，不使用 Work 的后继授权解释前盘。"""
        evidence = await self._evidence(db, pickup.authorization_evidence_id, work)
        if evidence.normalized_payload.get("operation_id") != evidence.operation_id:
            raise ValueError("source authorization operation identity differs")
        if evidence.id == work.response_evidence_id:
            if (
                evidence.operation != "outbound.bin.work_plan@v1"
                or evidence.operation_id != work.operation_id
                or evidence.picking_task_id != work.picking_task_id
                or evidence.material_execution_id is not None
            ):
                raise ValueError("initial material source identity differs")
            result = decode_plan(evidence.normalized_payload).result
            cell_id = result.cell_id if type(result) is BinWorkPlanReady else None
        else:
            if evidence.operation != "outbound.material.decide@v1":
                raise ValueError("successor material source identity differs")
            result = decode_material(evidence.normalized_payload).result
            cell_id = (
                result.next_source_action.cell_id
                if type(result) is PickingMaterialAccept and result.next_source_action
                else None
            )
        feed = await db.get(AutomaticPickingFeed, work.feed_id)
        member = await db.get(PickingTaskBinSourceRack, feed.source_member_id) if feed is not None else None
        task = await db.get(PickingTask, work.picking_task_id)
        if (
            cell_id is None
            or feed is None
            or member is None
            or task is None
            or feed.workline_id != work.workline_id
            or feed.picking_task_id != task.id
            or member.picking_task_id != task.id
        ):
            raise ValueError("material source lost its frozen Work/Requirement identity")
        return task.task_id, PickingBinCell(member.rack_id, member.rack_face, work.bin_code, cell_id)

    async def _work_for_update(self, db, work_id):
        line_id = await db.scalar(select(AutomaticPickingWork.workline_id).where(AutomaticPickingWork.id == work_id))
        if line_id is None:
            raise ValueError("source Work is missing")
        line = await db.scalar(select(WorkLine).where(WorkLine.id == line_id).with_for_update())
        work = await db.scalar(
            select(AutomaticPickingWork)
            .where(AutomaticPickingWork.id == work_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if line is None or work is None or work.workline_id != line.id:
            raise ValueError("source Work lost its WorkLine")
        return work

    async def _evidence(self, db, evidence_id, work):
        row = await db.get(InboundEvidence, evidence_id) if evidence_id is not None else None
        if (
            row is None
            or row.kind != "WMS_RESULT"
            or row.apply_status != "APPLIED"
            or row.workline_id != work.workline_id
            or canonical_json_digest(row.normalized_payload) != row.payload_digest
        ):
            raise ValueError("source authorization Evidence identity mismatch")
        return row

    async def source_for_update(self, db, work_id):
        work = await self._work_for_update(db, work_id)
        evidence = await self._evidence(db, work.authorization_evidence_id, work)
        initial = work.authorization_evidence_id == work.response_evidence_id
        if initial:
            if (
                evidence.operation != "outbound.bin.work_plan@v1"
                or evidence.operation_id != work.operation_id
                or evidence.picking_task_id != work.picking_task_id
                or evidence.material_execution_id is not None
            ):
                raise ValueError("initial authorization differs from Work")
            result = decode_plan(evidence.normalized_payload).result
            if type(result) is not BinWorkPlanReady:
                raise ValueError("initial authorization requires READY")
            source = PickingCellSource(
                result.cell_id,
                result.cell_index,
                result.bin_type,
                result.reel_layer,
                result.reel_thickness,
                result.reel_diameter,
            )
        else:
            if evidence.operation != "outbound.material.decide@v1" or evidence.material_execution_id is None:
                raise ValueError("successor authorization differs from material Evidence")
            result = decode_material(evidence.normalized_payload).result
            if type(result) is not PickingMaterialAccept:
                raise ValueError("successor authorization requires ACCEPT")
            source = result.next_source_action
        feed = await db.get(AutomaticPickingFeed, work.feed_id)
        # WorkLine 锁只串行事务，不刷新调用方此前缓存的取消事实。
        member = (
            await db.get(PickingTaskBinSourceRack, feed.source_member_id, populate_existing=True)
            if feed is not None
            else None
        )
        if (
            feed is None
            or member is None
            or feed.workline_id != work.workline_id
            or feed.picking_task_id != work.picking_task_id
            or member.picking_task_id != work.picking_task_id
        ):
            raise ValueError("source Work lost its direct Requirement")
        pickup = None
        if work.consumed_pickup_id is not None:
            pickup = await self.pickup_for_update(db, work.consumed_pickup_id)
            if pickup.work_id != work.id or pickup.authorization_evidence_id != evidence.id:
                raise ValueError("consumed Pickup differs from current Work authorization")
        elif not initial and work.top_removed_evidence_id is None:
            # 取出尚未确定时前盘仍是未闭合依赖；事实转入 Work 后不再查询该历史。
            pickup = await db.scalar(
                select(AutomaticPickingPickup)
                .where(
                    AutomaticPickingPickup.work_id == work.id, AutomaticPickingPickup.accept_evidence_id == evidence.id
                )
                .with_for_update()
            )
        observed = await self.physical_reader.read_in_session(db, work=work, source=source, pickup=pickup)
        if type(observed) is not SourceObservation:
            raise TypeError("source observation requires typed current facts")
        if observed.removed_evidence_id is not None and observed.removed_evidence_id != work.top_removed_evidence_id:
            removed = await db.get(InboundEvidence, observed.removed_evidence_id)
            if (
                pickup is None
                or removed is None
                or removed.kind != "DEVICE_RESULT"
                or removed.workline_id != work.workline_id
                or removed.command_code != pickup.command_code
                or removed.apply_status != "APPLIED"
            ):
                raise ValueError("removed fact differs from current top Command")
            work.top_removed_evidence_id = removed.id
        return WorkSourceFact(
            work.id,
            work.workline_id,
            evidence.id,
            source,
            work.consumed_pickup_id,
            initial,
            True if work.top_removed_evidence_id is not None else None,
            work.phase == "READY" and work.archived_at is None,
            member.cancelled_evidence_id is None,
            observed.source_ready,
        )

    async def add_pickup(self, db, *, pickup_id, work_id, authorization_evidence_id, source, command_code):  # noqa: ARG002 - 来源只存原授权 Evidence。
        db.add(
            AutomaticPickingPickup(
                pickup_id=pickup_id,
                work_id=work_id,
                authorization_evidence_id=authorization_evidence_id,
                command_code=command_code,
            )
        )
        await db.flush()

    async def consume_authorization(self, db, *, work_id, authorization_evidence_id, pickup_id):
        work = await db.get(AutomaticPickingWork, work_id)
        pickup = await self.pickup_for_update(db, pickup_id)
        if (
            work.authorization_evidence_id != authorization_evidence_id
            or work.consumed_pickup_id is not None
            or pickup.work_id != work_id
            or pickup.authorization_evidence_id != authorization_evidence_id
        ):
            raise ValueError("authorization has changed or was consumed")
        work.consumed_pickup_id, work.top_removed_evidence_id = pickup_id, None
        await db.flush()

    async def pickup_for_update(self, db, pickup_id):
        row = await db.scalar(
            select(AutomaticPickingPickup)
            .where(AutomaticPickingPickup.pickup_id == pickup_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise ValueError("current Pickup is missing")
        return row

    async def record_accept(self, db, *, pickup_id, evidence_id, outcome):
        pickup = await self.pickup_for_update(db, pickup_id)
        work = await db.get(AutomaticPickingWork, pickup.work_id)
        evidence = await self._evidence(db, evidence_id, work)
        if (
            evidence.operation != "outbound.material.decide@v1"
            or evidence.material_execution_id != pickup.material_execution_id
            or decode_material(evidence.normalized_payload).result != outcome
        ):
            raise ValueError("ACCEPT differs from frozen material Evidence")
        pickup.accept_evidence_id = evidence.id
        await db.flush()

    async def handoff_authorization(self, db, *, work_id, evidence_id, source):  # noqa: ARG002 - 后继内容只存原 ACCEPT Evidence。
        work = await db.get(AutomaticPickingWork, work_id)
        work.authorization_evidence_id, work.consumed_pickup_id = evidence_id, None
        await db.flush()
