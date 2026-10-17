"""调用方持有 WorkLine authority 锁，阶段与原回架请求在同一事务关联。"""

from sqlalchemy import select

from .return_allocation_model import AutomaticPickingReturnAllocation
from .return_motion_model import AutomaticPickingReturnMotion


class ReturnMotionRepository:
    async def current(self, db, line_id):
        return list(
            (
                await db.scalars(
                    select(AutomaticPickingReturnMotion)
                    .where(
                        AutomaticPickingReturnMotion.workline_id == line_id,
                        AutomaticPickingReturnMotion.archived_at.is_(None),
                    )
                    .order_by(AutomaticPickingReturnMotion.id)
                    .with_for_update()
                )
            ).all()
        )

    async def add(self, db, intent):
        face = intent.face
        row = AutomaticPickingReturnMotion(
            workline_id=face.workline_id,
            arrival_evidence_id=face.arrival_evidence_id,
            source_evidence_id=intent.source_evidence_id,
            rack_id=face.rack_id,
            rack_face=face.rack_face,
        )
        db.add(row)
        await db.flush()
        return row

    async def allocation(self, db, current):
        row = await db.get(AutomaticPickingReturnAllocation, current.allocation_id)
        if row is None or (row.workline_id, row.arrival_evidence_id, row.rack_id, row.rack_face) != (
            current.workline_id,
            current.arrival_evidence_id,
            current.rack_id,
            current.rack_face,
        ):
            raise ValueError("motion phase lost original allocation identity")
        return row

    async def bind(self, db, current, allocation):
        current.allocation_id = allocation.id
        await db.flush()

    async def close(self, db, current, *, now):
        current.closed_at = now
        await db.flush()

    async def retire(self, db, phases, faces, *, now):
        known = {(f.arrival_evidence_id, f.rack_id, f.rack_face) for f in faces}
        for current in phases:
            if (
                current.closed_at is not None
                and (current.arrival_evidence_id, current.rack_id, current.rack_face) not in known
                and any(
                    f.rack_id == current.rack_id and f.arrival_evidence_id != current.arrival_evidence_id for f in faces
                )
            ):
                current.archived_at = now
        await db.flush()
