"""人工拣料 CTU 串行门禁读取现有可靠对象，不复制执行状态。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import and_, or_, select
from wes_plugin_sdk import BinBatchNoBatch, BinInboundBatchIntent, BinInboundBatchRackFaceDone, BinInboundBatchReady

from src.app.execution.models import (
    InboundEvidence,
    InboundEvidenceKind,
    TransportDecisionBinding,
    WmsConfirmation,
    WmsConfirmationStatus,
)
from src.app.transport.models import TransportMember, TransportTask
from src.app.wms_adapter.outbound_picking.inbound_batch_wire import BIN_INBOUND_BATCH_OPERATION
from src.app.wms_adapter.outbound_picking.return_batch_wire import BIN_RETURN_BATCH_OPERATION
from src.app.wms_integration.outbound_picking.services.bin_batch import BinBatchResultReader

from .batch_result import INBOUND_STEP
from .passage_model import ManualPickingPassage

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
        self, db: AsyncSession, workline_id: int, rack_id: str, rack_face: str, _now: datetime, after: datetime
    ) -> bool:
        latest = await self._history.latest_return(db, workline_id=workline_id, rack_id=rack_id, rack_face=rack_face)
        if latest is None:
            return True
        outcome, completed_at = latest
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
        detail = await self._history.latest_inbound_detail(
            db,
            workline_id=workline_id,
            task_id=task_id,
            plan_revision=plan_revision,
            rack_id=rack_id,
            rack_face=rack_face,
        )
        if detail is None:
            return None
        intent, outcome, evidence, _ = detail
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
        passages = cast("Any", ManualPickingPassage).__table__.c
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
                    select(passages.bin_code).where(
                        passages.workline_id == workline_id,
                        passages.task_id == task_id,
                        passages.scan1_evidence_id.is_not(None),
                        passages.bin_code.in_(
                            select(TransportMember.object_id).where(
                                TransportMember.transport_task_id.in_(bound_task_ids),
                                TransportMember.object_type == "BIN",
                            )
                        ),
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
