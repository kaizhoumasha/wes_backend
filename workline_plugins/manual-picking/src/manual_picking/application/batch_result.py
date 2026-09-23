"""WMS 批次决定在原事务内创建唯一 CTU Transport。"""

from __future__ import annotations

from typing import Any

from wes_plugin_sdk import (
    BinBatchNoBatch,
    BinInboundBatchIntent,
    BinInboundBatchRackFaceDone,
    BinInboundBatchReady,
    BinReturnBatchReady,
)

from .batch_transport import inbound_moves, return_moves

INBOUND_STEP = "MANUAL_PICKING_INBOUND_BATCH"
RETURN_STEP = "MANUAL_PICKING_RETURN_BATCH"


class ManualPickingBatchResultFlow:
    def __init__(self, reader: Any, transport: Any, passages: Any) -> None:
        self._reader = reader
        self._transport = transport
        self._passages = passages

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

    async def apply_return_in_session(
        self,
        db: Any,
        evidence: Any,
        *,
        workline_id: int,
        workline_code: str,
        confirmed_rack_id: str,
        confirmed_face: str,
        return_location: str,
    ) -> str | None:
        intent, outcome = await self._reader.read_return(db, evidence, workline_id=workline_id)
        if (
            intent.operation_id != evidence.operation_id
            or intent.workline_code != workline_code
            or (intent.rack_id, intent.rack_face) != (confirmed_rack_id, confirmed_face)
            or any(candidate.source_location_code != return_location for candidate in intent.return_candidates)
        ):
            return None
        rows = await self._passages.ready_return_prefix_for_update(db, workline_id)
        candidates = intent.return_candidates
        if tuple(row.bin_code for row in rows[: len(candidates)]) != tuple(
            candidate.bin_code for candidate in candidates
        ):
            return None
        result = outcome.result
        if isinstance(result, BinReturnBatchReady):
            moves = return_moves(intent, result)
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
            return "RETURN_READY"
        if isinstance(result, BinBatchNoBatch):
            return "RETURN_NO_BATCH"
        return None


__all__ = ["INBOUND_STEP", "RETURN_STEP", "ManualPickingBatchResultFlow"]
