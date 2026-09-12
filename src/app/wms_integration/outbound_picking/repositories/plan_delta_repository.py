"""计划准入所需的精确读取；任务锁后不反向锁 confirmation。"""

from __future__ import annotations

from itertools import batched
from typing import Any

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from src.app.execution.models import InboundEvidence, InboundEvidenceConflict, WmsConfirmation
from src.app.wms_adapter.outbound_picking.wire import PICKING_TASK_PREPARE_OPERATION
from src.app.wms_integration.outbound_picking.models import DirectPickExecution, PickingTaskBinSourceRack
from src.app.workline.models import WorkLine

MEMBER_BATCH_SIZE = 250


class PickingTaskPlanDeltaRepository:
    async def get_evidence(self, db: AsyncSession, evidence_id: int) -> InboundEvidence | None:
        return await db.get(InboundEvidence, evidence_id)

    async def first_rejection(self, db: AsyncSession, evidence_id: int) -> str | None:
        columns = InboundEvidenceConflict.__table__.c
        return await db.scalar(
            select(columns.reason_code)
            .where(
                columns.first_evidence_id == evidence_id,
                columns.reason_code.in_(("REVISION_CONFLICT", "STATE_CONFLICT", "REFERENCE_CONFLICT")),
            )
            .order_by(columns.id)
            .limit(1)
        )

    async def prepare_context(self, db: AsyncSession, task: Any) -> tuple[list[WmsConfirmation], WorkLine | None]:
        columns = WmsConfirmation.__table__.c
        confirmations = list(
            (
                await db.scalars(
                    select(WmsConfirmation).where(
                        columns.picking_task_id == task.id, columns.operation == PICKING_TASK_PREPARE_OPERATION
                    )
                )
            ).all()
        )
        line = await db.get(WorkLine, task.workline_id) if task.workline_id else None
        return confirmations, line

    async def source_identities(
        self,
        db: AsyncSession,
        task_id: int,
        *,
        direct_picks: list[tuple[str, str, str]],
        bin_racks: list[tuple[str, str]],
    ) -> tuple[set[tuple[str, str, str]], set[tuple[str, str]]]:
        direct = DirectPickExecution.__table__.c
        bins = PickingTaskBinSourceRack.__table__.c
        picks: set[tuple[str, str, str]] = set()
        racks: set[tuple[str, str]] = set()
        # 仅查询本次候选身份，保持批次上限与原文精确比较。
        for batch in batched(direct_picks, MEMBER_BATCH_SIZE, strict=False):
            candidates = list(batch)
            rows = (
                await db.execute(
                    select(direct.rack_id, direct.rack_face, direct.slot_id).where(
                        direct.picking_task_id == task_id,
                        tuple_(direct.rack_id, direct.rack_face, direct.slot_id).in_(candidates),
                    )
                )
            ).all()
            picks.update(tuple(row) for row in rows)
        for batch in batched(bin_racks, MEMBER_BATCH_SIZE, strict=False):
            candidates = list(batch)
            rows = (
                await db.execute(
                    select(bins.rack_id, bins.rack_face).where(
                        bins.picking_task_id == task_id,
                        tuple_(bins.rack_id, bins.rack_face).in_(candidates),
                    )
                )
            ).all()
            racks.update(tuple(row) for row in rows)
        return picks, racks

    async def add_members(self, db: AsyncSession, task_id: int, data: Any, evidence_id: int) -> None:
        for batch in batched(data.added_direct_picks or (), MEMBER_BATCH_SIZE, strict=False):
            for pick in batch:
                locator = pick.source_locator
                db.add(
                    DirectPickExecution(
                        picking_task_id=task_id,
                        rack_id=locator.rack_id,
                        rack_face=locator.rack_face,
                        slot_id=locator.slot_id,
                        plan_revision=data.plan_revision,
                        source_evidence_id=evidence_id,
                    )
                )
            await db.flush()
        for batch in batched(data.added_bin_source_racks or (), MEMBER_BATCH_SIZE, strict=False):
            for rack in batch:
                db.add(
                    PickingTaskBinSourceRack(
                        picking_task_id=task_id,
                        rack_id=rack.rack_id,
                        rack_face=rack.rack_face,
                        plan_revision=data.plan_revision,
                        source_evidence_id=evidence_id,
                    )
                )
            await db.flush()
        # 仅初始 target 的 revision 也要持久化任务/Evidence。
        await db.flush()
