"""WMS 批次决定在原事务内创建唯一 CTU Transport。"""

from __future__ import annotations

import logging
from typing import Any

from wes_plugin_sdk import (
    BinBatchNoBatch,
    BinInboundBatchIntent,
    BinInboundBatchRackFaceDone,
    BinInboundBatchReady,
    BinReturnBatchReady,
)

from .batch_transport import inbound_moves, return_moves
from .drain_execution_repository import DrainExecutionRepository

INBOUND_STEP = "MANUAL_PICKING_INBOUND_BATCH"
RETURN_STEP = "MANUAL_PICKING_RETURN_BATCH"
logger = logging.getLogger(__name__)


class ManualPickingBatchResultFlow:
    def __init__(
        self, reader: Any, transport: Any, passages: Any, batches: Any = None, *, drain_executions: Any = None
    ) -> None:
        from .batch_repository import BatchRepository

        self._drain_executions = drain_executions or DrainExecutionRepository()
        self._reader = reader
        self._transport = transport
        self._passages = passages
        self._batches = batches or BatchRepository()

    async def apply_inbound_in_session(
        self,
        db: Any,
        evidence: Any,
        *,
        workline_id: int,
        picking_task_id: int,
        confirmed_rack_id: str,
        confirmed_face: str,
        inlet_location: str,
    ) -> str | None:
        intent, outcome = await self._reader.read_inbound(db, evidence, workline_id=workline_id)
        if intent.operation_id != evidence.operation_id or (intent.rack_id, intent.rack_face) != (
            confirmed_rack_id,
            confirmed_face,
        ):
            return None
        result = outcome.result
        if isinstance(result, BinInboundBatchReady):
            await self._batches.record_allocation(
                db, workline_id=workline_id, picking_task_id=picking_task_id, intent=intent, evidence_id=evidence.id
            )
            await self.create_inbound_chunk(
                db,
                workline_id=workline_id,
                picking_task_id=picking_task_id,
                intent=intent,
                ready=result,
                evidence_id=evidence.id,
                offset=0,
                inlet_location=inlet_location,
            )
            return "INBOUND_READY"
        if isinstance(result, BinInboundBatchRackFaceDone):
            await self._batches.record_allocation(
                db, workline_id=workline_id, picking_task_id=picking_task_id, intent=intent, evidence_id=evidence.id
            )
            return "RACK_FACE_DONE"
        return None

    async def create_inbound_chunk(
        self,
        db: Any,
        *,
        workline_id: int,
        picking_task_id: int,
        intent: BinInboundBatchIntent,
        ready: BinInboundBatchReady,
        evidence_id: int,
        offset: int,
        inlet_location: str,
    ) -> None:
        moves = inbound_moves(intent, ready, inlet_location=inlet_location)[offset : offset + 4]
        if not moves:
            raise ValueError("inbound chunk offset exceeds frozen face allocation")
        await self._transport.create(
            db,
            workline_id=workline_id,
            picking_task_id=picking_task_id,
            source_evidence_id=evidence_id,
            correlation_id=f"{intent.operation_id}:{offset}",
            step=INBOUND_STEP,
            resource_fence_id=intent.operation_id,
            moves=moves,
        )

    async def apply_return_in_session(  # noqa: PLR0911
        self,
        db: Any,
        evidence: Any,
        *,
        workline_id: int,
        workline_code: str,
        confirmed_rack_id: str,
        confirmed_face: str,
        return_location: str,
    ) -> str | int | None:
        intent, outcome = await self._reader.read_return(db, evidence, workline_id=workline_id)
        if (
            intent.operation_id != evidence.operation_id
            or intent.workline_code != workline_code
            or (intent.rack_id, intent.rack_face) != (confirmed_rack_id, confirmed_face)
            or any(candidate.source_location_code != return_location for candidate in intent.return_candidates)
        ):
            return None
        if isinstance(outcome.result, BinBatchNoBatch):
            return "RETURN_NO_BATCH"
        try:
            execution = await self._drain_executions.execution_for_return_batch(
                db, workline_id=workline_id, operation_id=intent.operation_id
            )
            if execution is not None and execution.status != "ACTIVE":
                return "DRAIN_RETURN_AFTER_CLOSE"
            members = (
                await self._drain_executions.members_for_return_batch(
                    db, workline_id=workline_id, operation_id=intent.operation_id
                )
                if execution is not None
                else ()
            )
            rows = (
                await self._passages.by_ids_for_update(
                    db, workline_id=workline_id, return_ids=tuple(member.return_id for member in members)
                )
                if members
                else await self._passages.ready_prefix_for_update(db, workline_id)
            )
        except ValueError:
            return None
        candidates = intent.return_candidates
        if tuple(row.bin_code for row in rows[: len(candidates)]) != tuple(
            candidate.bin_code for candidate in candidates
        ):
            return None
        if not members and not await self._batches.return_request_in_round(
            db,
            workline_id=workline_id,
            operation_id=intent.operation_id,
            arrived_at=max(row.created_at for row in rows[: len(candidates)]),
        ):
            return None
        result = outcome.result
        if isinstance(result, BinReturnBatchReady):
            if await self._batches.has_unclosed_return_transport_for_face(
                db,
                workline_id=workline_id,
                rack_id=confirmed_rack_id,
                rack_face=confirmed_face,
            ):
                return 1000
            moves = return_moves(intent, result)
            if await self._batches.has_conflicting_return_target(db, moves):
                logger.error("manual_picking.return_target_conflict operation_id=%s", intent.operation_id)
                return None
            await self._transport.create(
                db,
                workline_id=workline_id,
                source_evidence_id=evidence.id,
                correlation_id=intent.operation_id,
                step=RETURN_STEP,
                resource_fence_id=intent.operation_id,
                moves=moves,
            )
            for row in rows[: len(moves)]:
                row.return_state = "RETURN_REQUESTED"
                row.return_batch_evidence_id = evidence.id
            return "RETURN_READY"
        return None


__all__ = ["INBOUND_STEP", "RETURN_STEP", "ManualPickingBatchResultFlow"]
