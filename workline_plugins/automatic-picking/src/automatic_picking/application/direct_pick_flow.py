"""有效退料单盘成员直接创建 Pickup；不借用 Bin Work 或猜测设备定位协议。"""

from sqlalchemy import select
from wes_plugin_sdk import PickingRackSlot

from src.app.device.contracts import WORKLINE_BUSINESS_REF_TYPE, DeviceCommandRequest
from src.app.wms_integration.outbound_picking.models import DirectPickExecution
from src.app.workline.models import WorkLine
from src.core.uuid7 import new_uuid7

from .pickup_model import AutomaticPickingDirectPick, AutomaticPickingPickup
from .pickup_repository import SourceObservation
from .return_rack_repository import ReturnRackRepository, member_done_clause


class AutomaticPickingDirectPickFlow:
    def __init__(self, repository, commands, request_factory):
        self.repository, self.commands, self.request_factory = repository, commands, request_factory

    async def advance_in_session(self, db, *, line, task=None):
        await db.scalar(select(WorkLine).where(WorkLine.id == line.id).with_for_update())
        if task is not None:
            if task.workline_id != line.id:
                raise ValueError("DirectPick task belongs to another WorkLine")
            members = (
                await db.scalars(
                    select(DirectPickExecution)
                    .where(
                        DirectPickExecution.picking_task_id == task.id,
                        ~member_done_clause(DirectPickExecution),
                    )
                    .order_by(DirectPickExecution.id)
                )
            ).all()
            for member in members:
                current = await db.scalar(
                    select(AutomaticPickingDirectPick).where(AutomaticPickingDirectPick.member_id == member.id)
                )
                if current is None:
                    db.add(
                        AutomaticPickingDirectPick(workline_id=line.id, picking_task_id=task.id, member_id=member.id)
                    )
            await db.flush()
        count = 0
        current = (
            await db.scalars(
                select(AutomaticPickingDirectPick)
                .where(
                    AutomaticPickingDirectPick.workline_id == line.id,
                    AutomaticPickingDirectPick.completed_evidence_id.is_(None),
                )
                .order_by(AutomaticPickingDirectPick.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).all()
        for owner in current:
            member = await self.repository.direct_member(db, owner)
            pickup = await db.scalar(
                select(AutomaticPickingPickup).where(AutomaticPickingPickup.direct_pick_id == owner.id)
            )
            # 原 Command 由共享 owner 恢复；取消只阻止新动作，不丢弃已创建的义务。
            if pickup is not None or await ReturnRackRepository().member_done(db, member):
                continue
            if not await ReturnRackRepository().member_admissible(db, line.id, member):
                continue
            source = PickingRackSlot(member.rack_id, member.rack_face, member.slot_id)
            observed = await self.repository.physical_reader.read_in_session(db, work=owner, source=source, pickup=None)
            if type(observed) is not SourceObservation:
                raise TypeError("DirectPick requires exact typed source observation")
            if not observed.source_ready:
                continue
            pickup_id = new_uuid7()
            request = await self.request_factory(db, owner=owner, member=member, source=source, pickup_id=pickup_id)
            if request is None:
                continue
            if (
                type(request) is not DeviceCommandRequest
                or request.workline_id != line.id
                or request.execution_ref_type != WORKLINE_BUSINESS_REF_TYPE
                or request.execution_ref_id != pickup_id
                or request.material_execution_id is not None
            ):
                raise ValueError("DirectPick Command differs from member/Pickup identity")
            command = await self.commands.create_command_in_session(db, request)
            db.add(
                AutomaticPickingPickup(
                    pickup_id=pickup_id,
                    direct_pick_id=owner.id,
                    authorization_evidence_id=member.source_evidence_id,
                    command_code=command.command_code,
                )
            )
            await db.flush()
            count += 1
        return count
