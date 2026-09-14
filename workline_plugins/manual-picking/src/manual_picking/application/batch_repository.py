"""人工拣料 CTU 串行门禁读取现有可靠对象，不复制执行状态。"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import or_, select
from wes_plugin_sdk import BinBatchNoBatch, BinInboundBatchRackFaceDone

from src.app.execution.models import InboundEvidence, InboundEvidenceKind, WmsConfirmation, WmsConfirmationStatus
from src.app.transport.models import TransportTask
from src.app.wms_adapter.outbound_picking.inbound_batch_wire import BIN_INBOUND_BATCH_OPERATION
from src.app.wms_adapter.outbound_picking.return_batch_wire import BIN_RETURN_BATCH_OPERATION
from src.app.wms_integration.outbound_picking.services.bin_batch import BinBatchResultReader

from .passage_model import ManualPickingPassage

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

_BATCH_OPERATIONS = (BIN_INBOUND_BATCH_OPERATION, BIN_RETURN_BATCH_OPERATION)


class BatchRepository:
    def __init__(self, history: BinBatchResultReader | None = None) -> None:
        self._history = history or BinBatchResultReader()

    async def has_unclosed_action(self, db: AsyncSession, workline_id: int) -> bool:
        confirmations = cast("Any", WmsConfirmation).__table__.c
        pending = await db.scalar(
            select(confirmations.id)
            .where(
                confirmations.workline_id == workline_id,
                confirmations.operation.in_(_BATCH_OPERATIONS),
                confirmations.status.in_(
                    (
                        WmsConfirmationStatus.PENDING,
                        WmsConfirmationStatus.DISPATCHING,
                        WmsConfirmationStatus.RECONCILING,
                    )
                ),
            )
            .limit(1)
        )
        if pending is not None:
            return True
        evidences = cast("Any", InboundEvidence).__table__.c
        unpublished = await db.scalar(
            select(evidences.id)
            .where(
                evidences.workline_id == workline_id,
                evidences.published_at.is_(None),
                evidences.kind == InboundEvidenceKind.WMS_RESULT,
                evidences.operation.in_(_BATCH_OPERATIONS),
            )
            .limit(1)
        )
        if unpublished is not None:
            return True
        transports = cast("Any", TransportTask).__table__.c
        active = await db.scalar(
            select(transports.id)
            .where(
                transports.authority_workline_id == workline_id,
                transports.kind == "BIN_MOVE",
                or_(
                    transports.status.in_(("PENDING", "ACCEPTED", "RECONCILING")),
                    transports.outcome_version > transports.published_outcome_version,
                ),
            )
            .limit(1)
        )
        return active is not None

    async def return_retry_due(
        self, db: AsyncSession, workline_id: int, rack_id: str, rack_face: str, now: datetime
    ) -> bool:
        latest = await self._history.latest_return(db, workline_id=workline_id, rack_id=rack_id, rack_face=rack_face)
        if latest is None:
            return True
        outcome, completed_at = latest
        result = outcome.result
        if not isinstance(result, BinBatchNoBatch) or now >= completed_at + timedelta(
            milliseconds=result.retry_after_ms
        ):
            return True
        passages = cast("Any", ManualPickingPassage).__table__.c
        evidences = cast("Any", InboundEvidence).__table__.c
        new_ready = await db.scalar(
            select(passages.id)
            .join(InboundEvidence, evidences.command_code == passages.scan4_command_code)
            .where(
                passages.workline_id == workline_id,
                passages.return_state == "READY",
                evidences.kind == InboundEvidenceKind.DEVICE_RESULT,
                evidences.published_at > completed_at,
                evidences.published_at <= now,
            )
            .limit(1)
        )
        return new_ready is not None

    async def inbound_retry_due(
        self, db: AsyncSession, workline_id: int, task_id: str, rack_id: str, rack_face: str, now: datetime
    ) -> bool:
        latest = await self._history.latest_inbound(
            db, workline_id=workline_id, task_id=task_id, rack_id=rack_id, rack_face=rack_face
        )
        if latest is None:
            return True
        outcome, completed_at = latest
        if isinstance(outcome.result, BinInboundBatchRackFaceDone):
            return False
        if isinstance(outcome.result, BinBatchNoBatch):
            return now >= completed_at + timedelta(milliseconds=outcome.result.retry_after_ms)
        return True


__all__ = ["BatchRepository"]
