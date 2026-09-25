"""人工拣料 CTU 串行门禁读取现有可靠对象，不复制执行状态。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import and_, or_, select
from wes_plugin_sdk import (
    BinBatchNoBatch,
    BinInboundBatchIntent,
    BinInboundBatchRackFaceDone,
    BinInboundBatchReady,
    wms_operations,
)

from src.app.execution.models import (
    InboundEvidence,
    InboundEvidenceKind,
    TransportDecisionBinding,
    WmsConfirmation,
    WmsConfirmationStatus,
)
from src.app.transport.models import TransportEvidence, TransportMember, TransportTask
from src.app.wms_adapter.outbound_picking.inbound_batch_typed import decode_outcome as decode_inbound
from src.app.wms_adapter.outbound_picking.inbound_batch_wire import BIN_INBOUND_BATCH_OPERATION
from src.app.wms_adapter.outbound_picking.return_batch_wire import BIN_RETURN_BATCH_OPERATION
from src.app.wms_integration.outbound_picking.services.bin_batch import BinBatchResultReader

from .batch_progress_model import ManualPickingInboundBatch, ManualPickingInboundBatchScan
from .batch_result import INBOUND_STEP

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

_BATCH_OPERATIONS = (BIN_INBOUND_BATCH_OPERATION, BIN_RETURN_BATCH_OPERATION)


@dataclass(frozen=True)
class InboundFaceProgress:
    intent: BinInboundBatchIntent
    result: BinInboundBatchReady | BinInboundBatchRackFaceDone
    evidence_id: int
    next_offset: int | None
    complete: bool  # 业务完成仍要求所有投料 BIN 已有 SCAN1。
    feed_complete: bool  # RackCycle 只要求可靠投料终态，不等待线内业务。
    last_chunk_created_at: datetime | None


class BatchRepository:
    def __init__(self, history: BinBatchResultReader | None = None) -> None:
        self._history = history or BinBatchResultReader()

    async def record_allocation(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        picking_task_id: int,
        intent: BinInboundBatchIntent,
        evidence_id: int,
    ) -> None:
        """冻结仍在运行的 Batch 身份；WMS 确认可在完成后清理。"""
        batches = cast("Any", ManualPickingInboundBatch).__table__.c
        row = await db.scalar(select(ManualPickingInboundBatch).where(batches.operation_id == intent.operation_id))
        identity = (
            workline_id,
            picking_task_id,
            intent.task_id,
            intent.plan_revision,
            intent.rack_id,
            intent.rack_face,
            evidence_id,
        )
        if row is not None:
            if (
                row.workline_id,
                row.picking_task_id,
                row.task_id,
                row.plan_revision,
                row.rack_id,
                row.rack_face,
                row.response_evidence_id,
            ) != identity:
                raise ValueError("inbound batch identity changed")
            return
        db.add(
            ManualPickingInboundBatch(
                workline_id=workline_id,
                picking_task_id=picking_task_id,
                operation_id=intent.operation_id,
                task_id=intent.task_id,
                plan_revision=intent.plan_revision,
                rack_id=intent.rack_id,
                rack_face=intent.rack_face,
                response_evidence_id=evidence_id,
            )
        )

    async def record_scan1(
        self, db: AsyncSession, *, workline_id: int, picking_task_id: int, transport_task_id: str, bin_code: str
    ) -> bool:
        """只给来源 Transport 所属的当前 inbound Batch 记录一次进入执行。"""
        bindings = cast("Any", TransportDecisionBinding).__table__.c
        transports = cast("Any", TransportTask).__table__.c
        members = cast("Any", TransportMember).__table__.c
        binding = await db.scalar(
            select(TransportDecisionBinding)
            .join(TransportTask, bindings.client_request_id == transports.client_request_id)
            .join(TransportMember, members.transport_task_id == transports.transport_task_id)
            .where(
                bindings.workline_id == workline_id,
                bindings.picking_task_id == picking_task_id,
                bindings.step == INBOUND_STEP,
                transports.transport_task_id == transport_task_id,
                members.object_type == "BIN",
                members.object_id == bin_code,
            )
        )
        if binding is None or not binding.correlation_id.startswith(f"{binding.resource_fence_id}:"):
            return False
        batches = cast("Any", ManualPickingInboundBatch).__table__.c
        current = await db.scalar(
            select(batches.id).where(
                batches.workline_id == workline_id,
                batches.picking_task_id == picking_task_id,
                batches.operation_id == binding.resource_fence_id,
                batches.response_evidence_id == binding.source_evidence_id,
            )
        )
        if current is None:
            return False
        scans = cast("Any", ManualPickingInboundBatchScan).__table__.c
        existing = await db.scalar(
            select(scans.id).where(
                scans.operation_id == binding.resource_fence_id,
                scans.bin_code == bin_code,
            )
        )
        if existing is not None:
            return False
        db.add(
            ManualPickingInboundBatchScan(
                workline_id=workline_id, operation_id=binding.resource_fence_id, bin_code=bin_code
            )
        )
        return True

    async def has_unclosed_action_for_face(
        self, db: AsyncSession, workline_id: int, task_id: str, plan_revision: int, rack_id: str, rack_face: str
    ) -> bool:
        """只阻塞当前批次/货架面，避免无关异常冻结整条工作线。"""
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
                confirmations.request_payload["data"]["rack_id"].as_string() == rack_id,
                confirmations.request_payload["data"]["rack_face"].as_string() == rack_face,
                or_(
                    confirmations.operation == BIN_RETURN_BATCH_OPERATION,
                    and_(
                        confirmations.request_payload["data"]["task_id"].as_string() == task_id,
                        confirmations.request_payload["data"]["plan_revision"].as_integer() == plan_revision,
                    ),
                ),
            )
            .limit(1)
        )
        if pending is not None:
            return True
        evidences = cast("Any", InboundEvidence).__table__.c
        unpublished = await db.scalar(
            select(evidences.id)
            .join(WmsConfirmation, confirmations.response_evidence_id == evidences.id)
            .where(
                confirmations.workline_id == workline_id,
                confirmations.operation.in_(_BATCH_OPERATIONS),
                confirmations.request_payload["data"]["rack_id"].as_string() == rack_id,
                confirmations.request_payload["data"]["rack_face"].as_string() == rack_face,
                or_(
                    confirmations.operation == BIN_RETURN_BATCH_OPERATION,
                    and_(
                        confirmations.request_payload["data"]["task_id"].as_string() == task_id,
                        confirmations.request_payload["data"]["plan_revision"].as_integer() == plan_revision,
                    ),
                ),
                evidences.published_at.is_(None),
                evidences.kind == InboundEvidenceKind.WMS_RESULT,
                evidences.operation.in_(_BATCH_OPERATIONS),
            )
            .limit(1)
        )
        if unpublished is not None:
            return True
        transports = cast("Any", TransportTask).__table__.c
        members = cast("Any", TransportMember).__table__
        active = await db.scalar(
            select(transports.id)
            .join(members, members.c.transport_task_id == transports.transport_task_id)
            .where(
                transports.authority_workline_id == workline_id,
                transports.kind == "BIN_MOVE",
                members.c.object_type == "BIN",
                members.c.target_json["rack_id"].as_string() == rack_id,
                members.c.target_json["rack_face"].as_string() == rack_face,
                or_(
                    transports.status.in_(("PENDING", "ACCEPTED", "RECONCILING")),
                    transports.outcome_version > transports.published_outcome_version,
                ),
            )
            .limit(1)
        )
        return active is not None

    async def return_retry_due(
        self,
        db: AsyncSession,
        workline_id: int,
        rack_id: str,
        rack_face: str,
        source_evidence_id: int,
        _now: datetime,
        after: datetime,
    ) -> bool:
        latest = await self._history.latest_return(db, workline_id=workline_id, rack_id=rack_id, rack_face=rack_face)
        if latest is None:
            return True
        outcome, completed_at = latest
        bindings = cast("Any", TransportDecisionBinding).__table__.c
        transports = cast("Any", TransportTask).__table__.c
        results = cast("Any", TransportEvidence).__table__.c
        arrived_at = await db.scalar(
            select(results.received_at)
            .select_from(TransportTask)
            .join(TransportDecisionBinding, bindings.client_request_id == transports.client_request_id)
            .join(TransportEvidence, results.transport_task_id == transports.transport_task_id)
            .where(
                bindings.workline_id == workline_id,
                bindings.resource_fence_id == rack_id,
                bindings.source_evidence_id == source_evidence_id,
                bindings.step == "PICKING_TASK_BIN_SOURCE_RACK_IN",
                transports.status == "SUCCEEDED",
                results.operation == "transport.task.resulted@v1",
                results.status == "APPLIED",
            )
            .order_by(results.outcome_revision.desc())
            .limit(1)
        )
        if arrived_at is None:
            return False
        if completed_at < arrived_at:
            return True
        # NO_BATCH 是本次回架决定的确定终态：继续当前货架的 CTU02/CTU03，最终由 drain 补发空载货架。
        if isinstance(outcome.result, BinBatchNoBatch):
            return False
        # 一次投料间隙只冻结一个回架决定；新 SCAN4 或 retry 到期不重开该机会。
        if completed_at >= after:
            return False
        result = outcome.result
        return not isinstance(result, BinBatchNoBatch)

    async def inbound_progress(
        self,
        db: AsyncSession,
        workline_id: int,
        task_id: str,
        plan_revision: int,
        rack_id: str,
        rack_face: str,
        inlet_location: str,
    ) -> InboundFaceProgress | None:
        batches = cast("Any", ManualPickingInboundBatch).__table__.c
        batch = await db.scalar(
            select(ManualPickingInboundBatch).where(
                batches.workline_id == workline_id,
                batches.task_id == task_id,
                batches.plan_revision == plan_revision,
                batches.rack_id == rack_id,
                batches.rack_face == rack_face,
            )
        )
        if batch is None:
            return None
        evidence = await db.get(InboundEvidence, batch.response_evidence_id)
        if (
            evidence is None
            or evidence.workline_id != workline_id
            or evidence.kind != InboundEvidenceKind.WMS_RESULT
            or evidence.operation != BIN_INBOUND_BATCH_OPERATION
            or evidence.operation_id != batch.operation_id
            or evidence.apply_status != "APPLIED"
        ):
            raise ValueError("current inbound batch lacks applied response evidence")
        if evidence.published_at is None:
            return None
        intent = wms_operations.outbound_bin_inbound_batch(
            operation_id=batch.operation_id,
            task_id=batch.task_id,
            plan_revision=batch.plan_revision,
            rack_id=batch.rack_id,
            rack_face=batch.rack_face,
        )
        outcome = decode_inbound(evidence.normalized_payload)
        result = outcome.result
        bindings = cast("Any", TransportDecisionBinding).__table__.c
        if isinstance(result, BinInboundBatchRackFaceDone):
            chunk = await db.scalar(
                select(bindings.id)
                .where(
                    bindings.workline_id == workline_id,
                    bindings.step == INBOUND_STEP,
                    bindings.resource_fence_id == intent.operation_id,
                    bindings.source_evidence_id == evidence.id,
                )
                .limit(1)
            )
            return InboundFaceProgress(intent, result, evidence.id, None, chunk is None, chunk is None, None)
        if not isinstance(result, BinInboundBatchReady):
            raise TypeError("inbound face allocation has no determinate result")
        transports = cast("Any", TransportTask).__table__.c
        scans = cast("Any", ManualPickingInboundBatchScan).__table__.c
        batch_scope = (
            bindings.workline_id == workline_id,
            bindings.step == INBOUND_STEP,
            bindings.resource_fence_id == intent.operation_id,
            bindings.source_evidence_id == evidence.id,
        )
        bound_rows = (
            await db.execute(
                select(bindings.correlation_id, TransportTask)
                .select_from(TransportDecisionBinding)
                .outerjoin(TransportTask, bindings.client_request_id == transports.client_request_id)
                .where(*batch_scope)
            )
        ).all()
        chunk_transports: dict[str, list[TransportTask | None]] = {}
        for correlation_id, transport in bound_rows:
            if correlation_id == intent.operation_id:
                raise ValueError("legacy inbound binding requires reconciliation before chunk dispatch")
            chunk_transports.setdefault(correlation_id, []).append(transport)
        # 以现有 binding 子查询限定整面读取；查询次数和绑定参数数不随分段数增长。
        bound_task_ids = (
            select(transports.transport_task_id)
            .join(TransportDecisionBinding, bindings.client_request_id == transports.client_request_id)
            .where(*batch_scope)
        )
        members_by_transport: dict[str, list[TransportMember]] = {}
        for member in await db.scalars(
            select(TransportMember).where(TransportMember.transport_task_id.in_(bound_task_ids))
        ):
            members_by_transport.setdefault(member.transport_task_id, []).append(member)
        scanned = set(
            (
                await db.scalars(
                    select(scans.bin_code).where(
                        scans.workline_id == workline_id,
                        scans.operation_id == intent.operation_id,
                    )
                )
            ).all()
        )
        all_scanned = True
        last_chunk_created_at = None
        expected_position = {"kind": "HANDOFF_POSITION", "location_code": inlet_location}
        for offset in range(0, len(result.bins), 4):
            rows = chunk_transports.get(f"{intent.operation_id}:{offset}", [])
            if not rows:
                return InboundFaceProgress(
                    intent, result, evidence.id, offset if all_scanned else None, False, False, last_chunk_created_at
                )
            if len(rows) != 1 or rows[0] is None:
                return InboundFaceProgress(intent, result, evidence.id, None, False, False, None)
            row = rows[0]
            if (
                row.status != "SUCCEEDED"
                or row.kind != "BIN_MOVE"
                or row.authority_workline_id != workline_id
                or row.outcome_version == 0
                or row.published_outcome_version < row.outcome_version
            ):
                return InboundFaceProgress(intent, result, evidence.id, None, False, False, None)
            chunk_codes = {member.bin_code for member in result.bins[offset : offset + 4]}
            members = members_by_transport.get(row.transport_task_id, [])
            if (
                len(members) != len(chunk_codes)
                or {member.object_id for member in members} != chunk_codes
                or any(
                    member.object_type != "BIN"
                    or member.status != "SUCCEEDED"
                    or member.position_unknown
                    or member.final_position_json != expected_position
                    for member in members
                )
            ):
                return InboundFaceProgress(intent, result, evidence.id, None, False, False, None)
            all_scanned = all_scanned and chunk_codes <= scanned
            last_chunk_created_at = row.created_at
        return InboundFaceProgress(intent, result, evidence.id, None, all_scanned, True, last_chunk_created_at)


__all__ = ["BatchRepository", "InboundFaceProgress"]
