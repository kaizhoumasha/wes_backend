"""PickingTask 取消所需的计划成员精确锁定与一次性标记。"""

from __future__ import annotations

from itertools import batched
from typing import TYPE_CHECKING

from sqlalchemy import select, tuple_

from src.app.execution.models import InboundEvidenceConflict, TransportDecisionBinding
from src.app.transport.models import TransportTask
from src.app.wms_integration.outbound_picking.models import DirectPickExecution, PickingTaskBinSourceRack
from src.app.wms_integration.outbound_picking.repositories.plan_delta_repository import MEMBER_BATCH_SIZE

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.app.wms_adapter.outbound_picking.cancel_wire import PickingTaskCancelMembersData


class PickingTaskCancelRepository:
    async def first_rejection(self, db: AsyncSession, evidence_id: int) -> str | None:
        columns = InboundEvidenceConflict.__table__.c
        return await db.scalar(
            select(columns.reason_code)
            .where(
                columns.first_evidence_id == evidence_id,
                columns.reason_code.in_(("STATE_CONFLICT", "REFERENCE_CONFLICT")),
            )
            .order_by(columns.id)
            .limit(1)
        )

    async def cancel_members(
        self,
        db: AsyncSession,
        *,
        task_id: int,
        data: PickingTaskCancelMembersData,
        evidence_id: int,
    ) -> tuple[bool, tuple[str, ...]]:
        requested_picks = {
            (source.rack_id, source.rack_face, slot_id)
            for source in data.direct_pick_sources or ()
            for slot_id in source.slot_ids
        }
        requested_racks = {
            (source.rack_id, rack_face) for source in data.bin_source_racks or () for rack_face in source.rack_faces
        }
        direct_rows: list[DirectPickExecution] = []
        rack_rows: list[PickingTaskBinSourceRack] = []
        direct = DirectPickExecution.__table__.c
        racks = PickingTaskBinSourceRack.__table__.c
        for batch in batched(requested_picks, MEMBER_BATCH_SIZE, strict=False):
            direct_rows.extend(
                (
                    await db.scalars(
                        select(DirectPickExecution)
                        .where(
                            direct.picking_task_id == task_id,
                            direct.cancelled_evidence_id.is_(None),
                            tuple_(direct.rack_id, direct.rack_face, direct.slot_id).in_(list(batch)),
                        )
                        .with_for_update()
                    )
                ).all()
            )
        for batch in batched(requested_racks, MEMBER_BATCH_SIZE, strict=False):
            rack_rows.extend(
                (
                    await db.scalars(
                        select(PickingTaskBinSourceRack)
                        .where(
                            racks.picking_task_id == task_id,
                            racks.cancelled_evidence_id.is_(None),
                            tuple_(racks.rack_id, racks.rack_face).in_(list(batch)),
                        )
                        .with_for_update()
                    )
                ).all()
            )
        found_picks = {(row.rack_id, row.rack_face, row.slot_id) for row in direct_rows}
        found_racks = {(row.rack_id, row.rack_face) for row in rack_rows}
        if found_picks != requested_picks or found_racks != requested_racks:
            return False, ()
        source_evidence_ids = {row.source_evidence_id for row in (*direct_rows, *rack_rows)}
        rack_ids = {row.rack_id for row in (*direct_rows, *rack_rows)}
        bindings = TransportDecisionBinding.__table__.c
        transports = TransportTask.__table__.c
        transport_task_ids = tuple(
            str(value)
            for value in (
                await db.scalars(
                    select(transports.transport_task_id)
                    .join(TransportDecisionBinding, bindings.client_request_id == transports.client_request_id)
                    .where(
                        bindings.picking_task_id == task_id,
                        bindings.source_evidence_id.in_(source_evidence_ids),
                        bindings.resource_fence_id.in_(rack_ids),
                    )
                    .order_by(transports.id)
                )
            ).all()
        )
        for row in (*direct_rows, *rack_rows):
            row.cancelled_evidence_id = evidence_id
        await db.flush()
        return True, transport_task_ids


__all__ = ["PickingTaskCancelRepository"]
