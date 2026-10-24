"""交接与首次事实的事务读写；写调用方先持有 WorkLine authority 锁。"""

from sqlalchemy import or_, select
from wes_plugin_sdk import PickingMaterialAccept

from src.app.device.contracts import EcsDeviceEvent
from src.app.device.models.command import DeviceCommand
from src.app.execution.models import InboundEvidence, MaterialExecution
from src.app.wms_adapter.outbound_picking.material_decide_typed import decode_outcome
from src.utils.canonical_json import canonical_json_digest

from .pickup_model import AutomaticPickingPickup
from .return_model import AutomaticPickingReturn
from .work_model import AutomaticPickingWork


class ReturnRepository:
    async def work_for_update(self, db, work_id):
        return await db.scalar(
            select(AutomaticPickingWork)
            .where(AutomaticPickingWork.id == work_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def by_source_work(self, db, work_id):
        return await db.scalar(
            select(AutomaticPickingReturn).where(AutomaticPickingReturn.source_work_id == work_id).with_for_update()
        )

    async def command(self, db, command_code):
        return await db.scalar(select(DeviceCommand).where(DeviceCommand.command_code == command_code))

    async def add_handoff(self, db, *, work, evidence_id):
        row = AutomaticPickingReturn(
            workline_id=work.workline_id,
            bin_code=work.bin_code,
            source_work_id=work.id,
            entry_command_code=work.exit_command_code,
            scan3_evidence_id=evidence_id,
        )
        db.add(row)
        work.phase = "CLOSED"
        await db.flush()
        return row

    async def by_id_for_update(self, db, return_id):
        return await db.scalar(
            select(AutomaticPickingReturn).where(AutomaticPickingReturn.id == return_id).with_for_update()
        )

    async def list_current(self, db, line_id):
        return list(
            (
                await db.scalars(
                    select(AutomaticPickingReturn)
                    .where(AutomaticPickingReturn.workline_id == line_id, AutomaticPickingReturn.archived_at.is_(None))
                    .order_by(AutomaticPickingReturn.id)
                )
            ).all()
        )

    async def list_fifo(self, db, line_id):
        """到位顺序不代表回架 READY；None 为顺序/事实未闭合，[] 也不代表物理 EMPTY。"""
        rows = list(
            (
                await db.scalars(
                    select(AutomaticPickingReturn)
                    .where(
                        AutomaticPickingReturn.workline_id == line_id,
                        AutomaticPickingReturn.archived_at.is_(None),
                        AutomaticPickingReturn.scan4_event_time.is_not(None),
                    )
                    .order_by(AutomaticPickingReturn.scan4_event_time)
                )
            ).all()
        )
        previous = None
        for row in rows:
            evidence = await db.get(InboundEvidence, row.scan4_evidence_id)
            if (
                evidence is None
                or evidence.kind != "DEVICE_EVENT"
                or evidence.workline_id != line_id
                or evidence.apply_status != "APPLIED"
                or evidence.published_at is None
                or canonical_json_digest(evidence.normalized_payload) != evidence.payload_digest
            ):
                return None
            try:
                event = EcsDeviceEvent.model_validate(evidence.normalized_payload)
            except ValueError:
                return None
            if (
                event.event_type != "SCAN_COMPLETED"
                or event.is_debug
                or event.device_code != evidence.device_code
                or event.contract_key != evidence.contract_key
                or event.contract_version != evidence.contract_version
                or event.timestamp != row.scan4_event_time
                or row.scan4_event_time == previous
            ):
                return None
            previous = row.scan4_event_time
        return rows

    async def record_scan4(self, db, current, *, evidence_id, event_time):
        current.scan4_evidence_id, current.scan4_event_time = evidence_id, event_time
        await db.flush()

    async def time_conflict(self, db, current):
        return (
            await db.scalar(
                select(AutomaticPickingReturn.id).where(
                    AutomaticPickingReturn.workline_id == current.workline_id,
                    AutomaticPickingReturn.archived_at.is_(None),
                    AutomaticPickingReturn.scan4_event_time == current.scan4_event_time,
                    AutomaticPickingReturn.id != current.id,
                )
            )
            is not None
        )

    async def scan4_owner(self, db, evidence_id):
        return await db.scalar(
            select(AutomaticPickingReturn.id).where(AutomaticPickingReturn.scan4_evidence_id == evidence_id)
        )

    async def by_direction_command(self, db, command_code):
        from sqlalchemy import or_

        return await db.scalar(
            select(AutomaticPickingReturn)
            .where(
                or_(
                    AutomaticPickingReturn.scan3_command_code == command_code,
                    AutomaticPickingReturn.scan4_command_code == command_code,
                )
            )
            .with_for_update()
        )

    async def evidence(self, db, evidence_id):
        return await db.get(InboundEvidence, evidence_id)

    async def bind_direction(self, db, current, *, role, command_code):
        if role == "SCAN3":
            current.scan3_command_code = command_code
        else:
            current.scan4_command_code = command_code
        await db.flush()

    async def qualify(self, db, current, evidence_id):
        current.ready_evidence_id = evidence_id
        await db.flush()

    async def normal_complete(self, db, work):
        """当前 Work 的明确无后继决定 + 当前逐盘义务闭合；不查询已闭合前盘。"""
        if work.authorization_evidence_id is None or work.consumed_pickup_id is not None:
            return False
        fact = await db.get(InboundEvidence, work.authorization_evidence_id)
        if (
            fact is None
            or fact.kind != "WMS_RESULT"
            or fact.apply_status != "APPLIED"
            or fact.operation != "outbound.material.decide@v1"
            or fact.workline_id != work.workline_id
            or fact.published_at is None
            or canonical_json_digest(fact.normalized_payload) != fact.payload_digest
        ):
            return False
        result = decode_outcome(fact.normalized_payload).result
        if type(result) is not PickingMaterialAccept or result.next_source_action is not None:
            return False
        pending = await db.scalar(
            select(AutomaticPickingPickup.id)
            .outerjoin(MaterialExecution, MaterialExecution.id == AutomaticPickingPickup.material_execution_id)
            .where(
                AutomaticPickingPickup.work_id == work.id,
                or_(MaterialExecution.id.is_(None), MaterialExecution.status != "CLOSED"),
            )
            .limit(1)
        )
        return pending is None
