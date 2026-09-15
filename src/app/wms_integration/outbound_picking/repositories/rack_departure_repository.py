"""货架离场决定的原 Confirmation 与结果 Evidence 读取。"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from src.app.execution.models import InboundEvidence, WmsConfirmation
from src.app.wms_adapter.outbound_picking.departure_wire import RACK_DEPARTURE_OPERATION


class RackDepartureRepository:
    async def latest(self, db: AsyncSession, picking_task_id: int, rack_id: str) -> WmsConfirmation | None:
        columns = cast("Any", WmsConfirmation).__table__.c
        return await db.scalar(
            select(WmsConfirmation)
            .where(
                columns.picking_task_id == picking_task_id,
                columns.operation == RACK_DEPARTURE_OPERATION,
                columns.request_payload["data"]["rack_id"].as_string() == rack_id,
            )
            .order_by(columns.id.desc())
            .limit(1)
        )

    async def evidence(self, db: AsyncSession, evidence_id: int | None) -> InboundEvidence | None:
        return await db.get(InboundEvidence, evidence_id) if evidence_id is not None else None


__all__ = ["RackDepartureRepository"]
