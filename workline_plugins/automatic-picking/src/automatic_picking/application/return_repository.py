"""点2交接的事务读写；调用方先持有现有 WorkLine authority 锁。"""

from sqlalchemy import select

from src.app.device.models.command import DeviceCommand

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
