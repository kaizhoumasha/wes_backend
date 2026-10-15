"""人工拣料 CTU 串行门禁读取现有可靠对象，不复制执行状态。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, cast

from sqlalchemy import and_, not_, or_, select, text
from sqlalchemy.orm import aliased
from wes_plugin_sdk import (
    BinBatchNoBatch,
    BinInboundBatchIntent,
    BinInboundBatchRackFaceDone,
    BinInboundBatchReady,
    wms_operations,
)

from src.app.execution.models import (
    InboundEvidence,
    InboundEvidenceApplyStatus,
    InboundEvidenceKind,
    PositionProjection,
    TransportDecisionBinding,
    WmsConfirmation,
    WmsConfirmationStatus,
)
from src.app.execution.repositories.transport_decision_binding_repository import transport_decision_binding_repository
from src.app.transport.models import TransportEvidence, TransportMember, TransportTask
from src.app.wms_adapter.outbound_picking.inbound_batch_typed import decode_outcome as decode_inbound
from src.app.wms_adapter.outbound_picking.inbound_batch_wire import BIN_INBOUND_BATCH_OPERATION
from src.app.wms_adapter.outbound_picking.return_batch_wire import BIN_RETURN_BATCH_OPERATION
from src.app.wms_integration.outbound_picking.models import PickingTask
from src.app.wms_integration.outbound_picking.models.plan_members import DirectPickExecution, PickingTaskBinSourceRack
from src.app.wms_integration.outbound_picking.repositories.plan_delta_repository import PickingTaskPlanDeltaRepository
from src.app.wms_integration.outbound_picking.services.bin_batch import BinBatchResultReader

from .batch_progress_model import ManualPickingInboundBatch, ManualPickingInboundBatchScan
from .batch_result import INBOUND_STEP, RETURN_STEP
from .drain_repository import (
    RETURN_RACK_OUT_STEP,
    RETURN_RACK_ROTATE_STEP,
    SOURCE_RACK_OUT_STEP,
    SOURCE_RACK_ROTATE_STEP,
    current_rack_action_predicate,
)

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

    from src.app.transport.contracts import BinMove, RackBinSlot

_BATCH_OPERATIONS = (BIN_INBOUND_BATCH_OPERATION, BIN_RETURN_BATCH_OPERATION)


@dataclass(frozen=True)
class InboundFaceProgress:
    intent: BinInboundBatchIntent
    result: BinInboundBatchReady | BinInboundBatchRackFaceDone
    evidence_id: int
    next_offset: int | None
    complete: bool  # 业务完成要求所有投料 BIN 已在 SCAN1 或 SCAN2 确认入线。
    feed_complete: bool  # RackCycle 只要求可靠投料终态，不等待线内业务。
    last_chunk_created_at: datetime | None


class BatchRepository:
    def __init__(self, history: BinBatchResultReader | None = None) -> None:
        self._history = history or BinBatchResultReader()

    async def current_rack_arrival_at(
        self, db: AsyncSession, *, workline_id: int, rack_id: str, transport_task_id: str | None = None
    ) -> datetime | None:
        return await db.scalar(
            select(TransportEvidence.received_at)
            .join(
                PositionProjection,
                and_(
                    PositionProjection.source_operation_id == TransportEvidence.operation_id,
                    PositionProjection.source_transport_task_id == TransportEvidence.transport_task_id,
                ),
            )
            .where(
                PositionProjection.workline_id == workline_id,
                PositionProjection.object_type == "RACK",
                PositionProjection.object_id == rack_id,
                PositionProjection.position_unknown.is_(False),
                PositionProjection.source_effect_phase == "FINAL_RESULT",
                TransportEvidence.transport_task_id == transport_task_id if transport_task_id is not None else True,
                TransportEvidence.operation == "transport.task.resulted@v1",
                TransportEvidence.status == "APPLIED",
            )
            .limit(1)
        )

    async def return_request_in_round(
        self, db: AsyncSession, *, workline_id: int, operation_id: str, arrived_at: datetime
    ) -> bool:
        return (
            await db.scalar(
                select(WmsConfirmation.id).where(
                    WmsConfirmation.workline_id == workline_id,
                    WmsConfirmation.operation == BIN_RETURN_BATCH_OPERATION,
                    WmsConfirmation.operation_id == operation_id,
                    WmsConfirmation.created_at >= arrived_at,
                )
            )
            is not None
        )

    async def return_before_motion_state(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        rack_id: str,
        rack_face: str,
        arrived_at: datetime,
    ) -> str | None:
        confirmation = await db.scalar(
            select(WmsConfirmation)
            .where(
                WmsConfirmation.workline_id == workline_id,
                WmsConfirmation.operation == BIN_RETURN_BATCH_OPERATION,
                WmsConfirmation.request_payload["data"]["rack_id"].as_string() == rack_id,
                WmsConfirmation.request_payload["data"]["rack_face"].as_string() == rack_face,
                WmsConfirmation.created_at >= arrived_at,
            )
            .order_by(WmsConfirmation.created_at.desc())
            .limit(1)
        )
        if confirmation is None:
            return None
        evidence = (
            await db.get(InboundEvidence, confirmation.response_evidence_id)
            if confirmation.response_evidence_id
            else None
        )
        if (
            confirmation.status != WmsConfirmationStatus.COMPLETED
            or evidence is None
            or evidence.apply_status != InboundEvidenceApplyStatus.APPLIED
            or evidence.published_at is None
        ):
            return "WAIT"
        await self._history.read_return(db, evidence, workline_id=workline_id)
        return "DONE"

    async def cancelled_source_windows(
        self, db: AsyncSession, *, workline_id: int, target_location_code: str
    ) -> list[tuple[TransportDecisionBinding, TransportTask, PositionProjection]]:
        return await self._cancelled_rack_windows(
            db,
            workline_id=workline_id,
            target_location_code=target_location_code,
            step="PICKING_TASK_BIN_SOURCE_RACK_IN",
            members=PickingTaskBinSourceRack,
        )

    async def cancelled_return_windows(
        self, db: AsyncSession, *, workline_id: int, target_location_code: str
    ) -> list[tuple[TransportDecisionBinding, TransportTask, PositionProjection]]:
        return await self._cancelled_rack_windows(
            db,
            workline_id=workline_id,
            target_location_code=target_location_code,
            step="PICKING_TASK_RETURN_RACK_IN",
            members=DirectPickExecution,
        )

    async def _cancelled_rack_windows(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        target_location_code: str,
        step: str,
        members: Any,
    ) -> list[tuple[TransportDecisionBinding, TransportTask, PositionProjection]]:
        """未释放窗口是清退扫描的 owner；只读本轮事实，不依赖父任务仍在执行。"""
        windows = await transport_decision_binding_repository.list_active_window_for_target(
            db, workline_id=workline_id, target_location_code=target_location_code
        )
        contexts = []
        binding = cast("Any", TransportDecisionBinding).__table__.c
        task = cast("Any", TransportTask).__table__.c
        source = members.__table__.c
        projection = cast("Any", PositionProjection).__table__.c
        for window in windows:
            if window.step != step:
                continue
            await transport_decision_binding_repository.lock_object_authority(
                db, object_type="RACK", object_id=window.resource_fence_id
            )
            rack_members = list(
                await db.scalars(
                    select(members).where(
                        source.picking_task_id == window.picking_task_id,
                        source.rack_id == window.resource_fence_id,
                    )
                )
            )
            if not rack_members or not any(row.cancelled_evidence_id is not None for row in rack_members):
                continue
            unclosed = False
            for row in rack_members:
                if row.cancelled_evidence_id is not None:
                    continue
                if (
                    members is not DirectPickExecution
                    or not await PickingTaskPlanDeltaRepository().is_direct_pick_face_business_closed(
                        db,
                        picking_task_id=window.picking_task_id,
                        plan_revision=row.plan_revision,
                        rack_id=row.rack_id,
                        rack_face=row.rack_face,
                    )
                ):
                    unclosed = True
                    break
            if unclosed:
                continue
            latest = (
                await db.execute(
                    select(TransportDecisionBinding, TransportTask)
                    .join(TransportTask, task.client_request_id == binding.client_request_id)
                    .where(
                        binding.resource_fence_id == window.resource_fence_id,
                        binding.causal_token >= window.causal_token,
                        task.kind.in_(("RACK_MOVE", "RACK_ROTATE")),
                        or_(
                            and_(
                                binding.workline_id == window.workline_id,
                                binding.picking_task_id == window.picking_task_id,
                            ),
                            current_rack_action_predicate(TransportDecisionBinding, TransportTask, window),
                        ),
                    )
                    .order_by(binding.causal_token.desc())
                    .limit(1)
                )
            ).one_or_none()
            if (
                latest is None
                or latest[0].workline_id != workline_id
                or latest[0].picking_task_id != window.picking_task_id
                or latest[0].step
                not in (
                    step,
                    RETURN_RACK_ROTATE_STEP if members is DirectPickExecution else SOURCE_RACK_ROTATE_STEP,
                    RETURN_RACK_OUT_STEP if members is DirectPickExecution else SOURCE_RACK_OUT_STEP,
                )
            ):
                continue
            if window.window_departure_client_request_id is not None and latest[1].status != "REJECTED":
                continue
            in_flight = await db.scalar(
                select(TransportTask.id)
                .where(
                    TransportTask.kind.in_(("RACK_MOVE", "RACK_ROTATE")),
                    TransportTask.request_json["rack_id"].as_string() == window.resource_fence_id,
                    TransportTask.created_at >= window.created_at,
                    TransportTask.status.in_(("PENDING", "ACCEPTED", "RECONCILING")),
                )
                .limit(1)
            )
            if in_flight is not None:
                continue
            position = await db.scalar(
                select(PositionProjection).where(
                    projection.workline_id == workline_id,
                    projection.object_type == "RACK",
                    projection.object_id == window.resource_fence_id,
                    projection.position_unknown.is_(False),
                    projection.position_json["kind"].as_string() == "RACK_POSITION",
                    projection.position_json["location_code"].as_string() == target_location_code,
                    projection.source_causal_token >= window.causal_token,
                    projection.source_causal_token <= latest[0].causal_token,
                )
            )
            if position is not None:
                contexts.append((window, latest[1], position))
        return contexts

    async def current_round_started_at(self, db: AsyncSession, *, workline_id: int, rack_id: str) -> datetime:
        await transport_decision_binding_repository.lock_object_authority(db, object_type="RACK", object_id=rack_id)
        windows = await transport_decision_binding_repository.list_active_window_for_rack(
            db, workline_id=workline_id, rack_id=rack_id
        )
        if not windows:
            # 当前失败离场或退役迟到事实仍有原轮次；只解析当前因果绑定，不恢复历史业务。
            fact_binding = TransportDecisionBinding.__table__.alias("current_rack_fact")
            fact = fact_binding.c
            departure_binding = TransportDecisionBinding.__table__.alias("original_rack_departure")
            departure = departure_binding.c
            windows = list(
                await db.scalars(
                    select(TransportDecisionBinding)
                    .join(
                        PositionProjection, PositionProjection.object_id == TransportDecisionBinding.resource_fence_id
                    )
                    .join(fact_binding, fact.causal_token == PositionProjection.source_causal_token)
                    .outerjoin(
                        departure_binding,
                        departure.client_request_id == TransportDecisionBinding.window_departure_client_request_id,
                    )
                    .where(
                        TransportDecisionBinding.workline_id == workline_id,
                        TransportDecisionBinding.resource_fence_id == rack_id,
                        TransportDecisionBinding.window_target_location_code.is_not(None),
                        PositionProjection.object_type == "RACK",
                        PositionProjection.workline_id == workline_id,
                        PositionProjection.position_unknown.is_(False),
                        PositionProjection.source_effect_phase == "FINAL_RESULT",
                        PositionProjection.position_json["kind"].as_string() == "RACK_POSITION",
                        PositionProjection.position_json["location_code"].as_string()
                        == TransportDecisionBinding.window_target_location_code,
                        fact.workline_id == workline_id,
                        fact.resource_fence_id == rack_id,
                        fact.causal_token >= TransportDecisionBinding.causal_token,
                        or_(
                            fact.source_evidence_id == TransportDecisionBinding.source_evidence_id,
                            and_(
                                fact.picking_task_id.is_not(None),
                                fact.picking_task_id == TransportDecisionBinding.picking_task_id,
                                departure.picking_task_id == fact.picking_task_id,
                                departure.workline_id == workline_id,
                                departure.resource_fence_id == rack_id,
                                departure.step == fact.step,
                                departure.source_evidence_id == fact.source_evidence_id,
                                departure.step.in_(
                                    (SOURCE_RACK_OUT_STEP, RETURN_RACK_OUT_STEP, "MANUAL_PICKING_TRANSFER_RACK_OUT")
                                ),
                            ),
                        ),
                    )
                    .order_by(TransportDecisionBinding.causal_token.desc())
                    .limit(1)
                )
            )
        if len(windows) != 1:
            raise ValueError("rack motion requires exactly one active inbound window or current physical round")
        return windows[0].created_at

    async def record_scan2(self, db: AsyncSession, *, workline_id: int, picking_task_id: int, bin_code: str) -> bool:
        """来源取消停止新投箱；原批次已入线成员继续交由 WMS 判断需求。"""
        batches = ManualPickingInboundBatch.__table__.c
        sources = PickingTaskBinSourceRack.__table__.c
        scans = ManualPickingInboundBatchScan.__table__.c
        entered = (
            select(scans.id)
            .where(
                scans.workline_id == workline_id,
                scans.operation_id == batches.operation_id,
                scans.bin_code == bin_code,
            )
            .exists()
        )
        transports = cast("Any", TransportTask).__table__.c
        members = cast("Any", TransportMember).__table__.c
        bindings = cast("Any", TransportDecisionBinding).__table__.c
        delivered = (
            select(members.id)
            .join(TransportTask, transports.transport_task_id == members.transport_task_id)
            .join(TransportDecisionBinding, bindings.client_request_id == transports.client_request_id)
            .where(
                bindings.workline_id == workline_id,
                bindings.picking_task_id == picking_task_id,
                bindings.step == INBOUND_STEP,
                bindings.resource_fence_id == batches.operation_id,
                bindings.source_evidence_id == batches.response_evidence_id,
                transports.authority_workline_id == workline_id,
                transports.kind == "BIN_MOVE",
                transports.status == "SUCCEEDED",
                transports.outcome_version > 0,
                transports.published_outcome_version == transports.outcome_version,
                members.object_type == "BIN",
                members.object_id == bin_code,
                members.status == "SUCCEEDED",
                members.position_unknown.is_(False),
                members.target_json["kind"].as_string() == "HANDOFF_POSITION",
                members.final_position_json["kind"].as_string() == "HANDOFF_POSITION",
                members.final_position_json["location_code"].as_string()
                == members.target_json["location_code"].as_string(),
            )
            .exists()
        )
        rows = (
            await db.scalars(
                select(ManualPickingInboundBatch)
                .join(
                    PickingTaskBinSourceRack,
                    and_(
                        sources.picking_task_id == batches.picking_task_id,
                        sources.plan_revision == batches.plan_revision,
                        sources.rack_id == batches.rack_id,
                        sources.rack_face == batches.rack_face,
                    ),
                )
                .where(
                    batches.workline_id == workline_id,
                    batches.picking_task_id == picking_task_id,
                    or_(sources.cancelled_evidence_id.is_(None), entered, delivered),
                )
            )
        ).all()
        for batch in rows:
            evidence = await db.get(InboundEvidence, batch.response_evidence_id)
            if (
                evidence is None
                or evidence.workline_id != workline_id
                or evidence.operation != BIN_INBOUND_BATCH_OPERATION
                or evidence.operation_id != batch.operation_id
                or evidence.kind != InboundEvidenceKind.WMS_RESULT
                or evidence.apply_status != InboundEvidenceApplyStatus.APPLIED
                or evidence.published_at is None
            ):
                continue
            result = decode_inbound(evidence.normalized_payload).result
            if isinstance(result, BinInboundBatchReady) and any(member.bin_code == bin_code for member in result.bins):
                existing = await db.scalar(
                    select(scans.id).where(
                        scans.operation_id == batch.operation_id,
                        scans.bin_code == bin_code,
                    )
                )
                if existing is None:
                    db.add(
                        ManualPickingInboundBatchScan(
                            workline_id=workline_id,
                            operation_id=batch.operation_id,
                            bin_code=bin_code,
                        )
                    )
                return True
        return False

    async def has_active_bin_transport(self, db: AsyncSession, bin_code: str) -> bool:
        """同箱当前在途搬运不能被 SCAN3 重新投入重复执行；终态历史不参与。"""
        tasks = cast("Any", TransportTask).__table__.c
        members = cast("Any", TransportMember).__table__.c
        return (
            await db.scalar(
                select(tasks.id)
                .join(TransportMember, members.transport_task_id == tasks.transport_task_id)
                .where(
                    tasks.kind == "BIN_MOVE",
                    tasks.status.in_(("PENDING", "ACCEPTED", "RECONCILING")),
                    members.object_type == "BIN",
                    members.object_id == bin_code,
                )
                .limit(1)
            )
            is not None
        )

    async def has_earlier_due_source_retry(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        picking_task_id: int,
        source_evidence_id: int,
        rack_id: str,
        last_applied_plan_revision: int,
        now: datetime,
    ) -> bool:
        """按有效计划成员顺序处理已到期的来源架重试，不依赖 Evidence 调度顺序。"""
        members = cast("Any", PickingTaskBinSourceRack).__table__.c
        current = await db.execute(
            select(members.plan_revision, members.id)
            .where(
                members.picking_task_id == picking_task_id,
                members.plan_revision <= last_applied_plan_revision,
                members.cancelled_evidence_id.is_(None),
                members.source_evidence_id == source_evidence_id,
                members.rack_id == rack_id,
            )
            .order_by(members.plan_revision, members.id)
            .limit(1)
        )
        current_rank = current.one_or_none()
        if current_rank is None:
            return False

        active = cast("Any", TransportDecisionBinding).__table__.alias("active_source_retry_window").c
        active_window = (
            select(active.id)
            .where(
                active.workline_id == workline_id,
                active.resource_fence_id == members.rack_id,
                active.window_target_location_code.is_not(None),
                active.window_released_at.is_(None),
            )
            .exists()
        )
        earlier_member = or_(
            members.plan_revision < current_rank.plan_revision,
            and_(members.plan_revision == current_rank.plan_revision, members.id < current_rank.id),
        )
        evidence = cast("Any", InboundEvidence).__table__.c
        bindings = cast("Any", TransportDecisionBinding).__table__.c
        transports = cast("Any", TransportTask).__table__.c
        due_earlier = await db.scalar(
            select(members.id)
            .select_from(InboundEvidence)
            .join(TransportTask, transports.transport_task_id == evidence.transport_task_id)
            .join(TransportDecisionBinding, bindings.client_request_id == transports.client_request_id)
            .join(
                PickingTaskBinSourceRack,
                and_(
                    members.picking_task_id == bindings.picking_task_id,
                    members.source_evidence_id == bindings.source_evidence_id,
                    members.rack_id == bindings.resource_fence_id,
                ),
            )
            .where(
                evidence.kind == InboundEvidenceKind.TRANSPORT_RESULT,
                evidence.workline_id == workline_id,
                evidence.apply_status == InboundEvidenceApplyStatus.APPLIED,
                evidence.published_at.is_(None),
                evidence.decision_next_attempt_at <= now,
                transports.status.in_(("FAILED", "REJECTED")),
                bindings.workline_id == workline_id,
                bindings.picking_task_id == picking_task_id,
                bindings.step == "PICKING_TASK_BIN_SOURCE_RACK_IN",
                members.plan_revision <= last_applied_plan_revision,
                members.cancelled_evidence_id.is_(None),
                earlier_member,
                ~active_window,
            )
            .limit(1)
        )
        return due_earlier is not None

    async def has_current_rack_dependency(
        self, db: AsyncSession, workline_id: int, rack_id: str, arrived_at: datetime
    ) -> bool:
        """本轮直接动作；新 RACK 动作提交后、ACK 更新投影前也不能被越过。"""
        tasks = cast("Any", TransportTask).__table__.c
        members = cast("Any", TransportMember).__table__.c
        return (
            await db.scalar(
                select(tasks.id)
                .outerjoin(TransportMember, members.transport_task_id == tasks.transport_task_id)
                .where(
                    tasks.created_at >= arrived_at,
                    or_(
                        and_(
                            tasks.kind.in_(("RACK_MOVE", "RACK_ROTATE")),
                            tasks.request_json["rack_id"].as_string() == rack_id,
                            or_(
                                tasks.status.in_(("PENDING", "ACCEPTED", "RECONCILING")),
                                tasks.outcome_version > tasks.published_outcome_version,
                            ),
                        ),
                        and_(
                            tasks.authority_workline_id == workline_id,
                            tasks.kind == "BIN_MOVE",
                            members.object_type == "BIN",
                            or_(
                                members.source_json["rack_id"].as_string() == rack_id,
                                members.target_json["rack_id"].as_string() == rack_id,
                            ),
                            or_(
                                tasks.outcome_version > tasks.published_outcome_version,
                                and_(
                                    tasks.status.in_(("PENDING", "ACCEPTED", "RECONCILING", "FAILED")),
                                    or_(
                                        members.position_unknown.is_(True),
                                        members.final_position_json["kind"].as_string().is_(None),
                                    ),
                                ),
                            ),
                        ),
                    ),
                )
                .limit(1)
            )
            is not None
        )

    async def has_conflicting_return_target(self, db: AsyncSession, moves: tuple[BinMove, ...]) -> bool:
        """只拒绝活跃搬运的明确目标冲突；不以历史位置维护储位主账。"""
        targets = sorted(
            (target.rack_id, target.rack_face, target.slot_id, move.bin_code)
            for move in moves
            for target in (cast("RackBinSlot", move.target),)
        )
        for rack_id, rack_face, slot_id, _ in targets:
            # 同一目标的校验与 Transport 创建在一个事务中；锁不跨 HTTP。
            await db.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
                {"identity": "return-target:" + json.dumps((rack_id, rack_face, slot_id))},
            )
        members = cast("Any", TransportMember).__table__.c
        tasks = cast("Any", TransportTask).__table__.c
        for rack_id, rack_face, slot_id, bin_code in targets:
            conflict = await db.scalar(
                select(members.id)
                .join(TransportTask, tasks.transport_task_id == members.transport_task_id)
                .where(
                    tasks.status.in_(("PENDING", "ACCEPTED", "RECONCILING")),
                    members.object_type == "BIN",
                    members.object_id != bin_code,
                    members.target_json["kind"].as_string() == "RACK_BIN_SLOT",
                    members.target_json["rack_id"].as_string() == rack_id,
                    members.target_json["rack_face"].as_string() == rack_face,
                    members.target_json["slot_id"].as_string() == slot_id,
                )
                .limit(1)
            )
            if conflict is not None:
                return True
        return False

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

    async def scan1_source(
        self, db: AsyncSession, *, workline_id: int, bin_code: str, inlet_location: str
    ) -> tuple[int, str] | Literal["WAIT_FOR_RESULT"] | None:
        """按未入线的冻结成员关联扫码；本点到位不依赖原搬运终态或历史位置。"""
        newer_task = aliased(TransportTask)
        newer_binding = aliased(TransportDecisionBinding)
        newer_member = aliased(TransportMember)
        newer_action = (
            select(newer_task.id)
            .join(newer_binding, newer_binding.client_request_id == newer_task.client_request_id)
            .join(newer_member, newer_member.transport_task_id == newer_task.transport_task_id)
            .where(
                newer_member.object_type == "BIN",
                newer_member.object_id == bin_code,
                newer_binding.causal_token > TransportDecisionBinding.causal_token,
                or_(
                    newer_task.status.in_(("PENDING", "ACCEPTED", "RECONCILING")),
                    newer_task.outcome_version > newer_task.published_outcome_version,
                ),
            )
            .exists()
        )
        rows = (
            await db.execute(
                select(ManualPickingInboundBatch, TransportMember, InboundEvidence, TransportTask, newer_action)
                .join(
                    TransportDecisionBinding,
                    and_(
                        TransportDecisionBinding.resource_fence_id == ManualPickingInboundBatch.operation_id,
                        TransportDecisionBinding.source_evidence_id == ManualPickingInboundBatch.response_evidence_id,
                        TransportDecisionBinding.picking_task_id == ManualPickingInboundBatch.picking_task_id,
                        TransportDecisionBinding.workline_id == ManualPickingInboundBatch.workline_id,
                    ),
                )
                .join(TransportTask, TransportTask.client_request_id == TransportDecisionBinding.client_request_id)
                .join(TransportMember, TransportMember.transport_task_id == TransportTask.transport_task_id)
                .join(InboundEvidence, InboundEvidence.id == ManualPickingInboundBatch.response_evidence_id)
                .join(
                    PickingTask,
                    and_(
                        PickingTask.id == ManualPickingInboundBatch.picking_task_id,
                        PickingTask.task_id == ManualPickingInboundBatch.task_id,
                        PickingTask.workline_id == workline_id,
                        PickingTask.archived_at.is_(None),
                    ),
                )
                .where(
                    ManualPickingInboundBatch.workline_id == workline_id,
                    TransportDecisionBinding.step == INBOUND_STEP,
                    TransportTask.authority_workline_id == workline_id,
                    TransportTask.kind == "BIN_MOVE",
                    TransportMember.object_type == "BIN",
                    TransportMember.object_id == bin_code,
                    ~select(ManualPickingInboundBatchScan.id)
                    .where(
                        ManualPickingInboundBatchScan.operation_id == ManualPickingInboundBatch.operation_id,
                        ManualPickingInboundBatchScan.bin_code == bin_code,
                    )
                    .exists(),
                )
            )
        ).all()
        if len(rows) != 1:
            return None
        batch, member, evidence, transport, newer_pending = rows[0]
        target = {"kind": "HANDOFF_POSITION", "location_code": inlet_location}
        if (
            member.target_json != target
            or evidence.kind != InboundEvidenceKind.WMS_RESULT
            or evidence.workline_id != workline_id
            or evidence.operation != BIN_INBOUND_BATCH_OPERATION
            or evidence.operation_id != batch.operation_id
            or evidence.apply_status != InboundEvidenceApplyStatus.APPLIED
            or evidence.published_at is None
        ):
            return None
        result = decode_inbound(evidence.normalized_payload).result
        if not isinstance(result, BinInboundBatchReady) or not any(
            item.bin_code == bin_code
            and (item.source_locator.rack_id, item.source_locator.rack_face) == (batch.rack_id, batch.rack_face)
            and member.source_json
            == {
                "kind": "RACK_BIN_SLOT",
                "rack_id": item.source_locator.rack_id,
                "rack_face": item.source_locator.rack_face,
                "slot_id": item.source_locator.slot_id,
            }
            for item in result.bins
        ):
            return None
        if newer_pending:
            return "WAIT_FOR_RESULT"
        return batch.picking_task_id, transport.transport_task_id

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
        self,
        db: AsyncSession,
        workline_id: int,
        task_id: str | None,
        plan_revision: int | None,
        rack_id: str,
        rack_face: str,
    ) -> bool:
        """只阻塞当前批次/货架面，避免无关异常冻结整条工作线。"""
        if (task_id is None) != (plan_revision is None):
            raise ValueError("task_id and plan_revision must be provided together")
        round_started_at = await self.current_round_started_at(db, workline_id=workline_id, rack_id=rack_id)
        if await self.has_current_rack_dependency(db, workline_id, rack_id, round_started_at):
            return True
        confirmations = cast("Any", WmsConfirmation).__table__.c
        inbound_scope = None
        if task_id is not None and plan_revision is not None:
            inbound_scope = and_(
                confirmations.operation == BIN_INBOUND_BATCH_OPERATION,
                confirmations.request_payload["data"]["task_id"].as_string() == task_id,
                confirmations.request_payload["data"]["plan_revision"].as_integer() == plan_revision,
            )
        pending_scope = confirmations.operation == BIN_RETURN_BATCH_OPERATION
        if inbound_scope is not None:
            pending_scope = or_(pending_scope, inbound_scope)
        pending = await db.scalar(
            select(confirmations.id)
            .where(
                confirmations.workline_id == workline_id,
                pending_scope,
                confirmations.created_at >= round_started_at,
                confirmations.status.in_(
                    (
                        WmsConfirmationStatus.PENDING,
                        WmsConfirmationStatus.DISPATCHING,
                        WmsConfirmationStatus.RECONCILING,
                    )
                ),
                confirmations.request_payload["data"]["rack_id"].as_string() == rack_id,
                confirmations.request_payload["data"]["rack_face"].as_string() == rack_face,
            )
            .limit(1)
        )
        if pending is not None:
            return True

        evidences = cast("Any", InboundEvidence).__table__.c
        operation_scope = confirmations.operation == BIN_RETURN_BATCH_OPERATION
        if inbound_scope is not None:
            operation_scope = or_(operation_scope, inbound_scope)
        unpublished = await db.scalar(
            select(evidences.id)
            .join(WmsConfirmation, confirmations.response_evidence_id == evidences.id)
            .where(
                confirmations.workline_id == workline_id,
                operation_scope,
                confirmations.created_at >= round_started_at,
                confirmations.request_payload["data"]["rack_id"].as_string() == rack_id,
                confirmations.request_payload["data"]["rack_face"].as_string() == rack_face,
                evidences.published_at.is_(None),
                # 本轮尚未应用、发布或完成对账的响应仍须完成原批次交接。
                evidences.apply_status.in_(("PENDING", "APPLIED", "RECONCILING")),
                evidences.kind == InboundEvidenceKind.WMS_RESULT,
                evidences.operation.in_(_BATCH_OPERATIONS),
            )
            .limit(1)
        )
        return unpublished is not None

    async def has_unclosed_return_dependency_for_rack(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        rack_id: str,
        round_started_at: datetime | None = None,
        exclude_operation_id: str | None = None,
    ) -> bool:
        """只查当前轮次；退役清退使用已校验的原进场绑定，不重开已释放窗口。"""
        if round_started_at is None:
            windows = await transport_decision_binding_repository.list_active_window_for_rack(
                db, workline_id=workline_id, rack_id=rack_id
            )
            if len(windows) != 1:
                raise ValueError("rack motion requires exactly one active inbound window")
            round_started_at = windows[0].created_at
        confirmations = cast("Any", WmsConfirmation).__table__.c
        evidences = cast("Any", InboundEvidence).__table__.c
        pending = await db.scalar(
            select(confirmations.id)
            .outerjoin(InboundEvidence, evidences.id == confirmations.response_evidence_id)
            .where(
                confirmations.workline_id == workline_id,
                confirmations.operation.in_(_BATCH_OPERATIONS),
                confirmations.request_payload["data"]["rack_id"].as_string() == rack_id,
                confirmations.created_at >= round_started_at,
                or_(
                    confirmations.operation != BIN_RETURN_BATCH_OPERATION,
                    confirmations.operation_id != exclude_operation_id,
                )
                if exclude_operation_id is not None
                else True,
                or_(
                    confirmations.status.in_(
                        (
                            WmsConfirmationStatus.PENDING,
                            WmsConfirmationStatus.DISPATCHING,
                            WmsConfirmationStatus.RECONCILING,
                        )
                    ),
                    and_(
                        evidences.kind == InboundEvidenceKind.WMS_RESULT,
                        evidences.apply_status.in_(("PENDING", "APPLIED", "RECONCILING")),
                        evidences.published_at.is_(None),
                    ),
                ),
            )
            .limit(1)
        )
        if pending is not None:
            return True
        return await self.has_unclosed_return_transport_for_face(
            db, workline_id=workline_id, rack_id=rack_id, rack_face=None, round_started_at=round_started_at
        )

    async def has_newer_rack_action(self, db: AsyncSession, *, rack_id: str, causal_token: int | None) -> bool:
        if causal_token is None:
            return True
        owner = await db.scalar(
            select(TransportDecisionBinding).where(TransportDecisionBinding.causal_token == causal_token)
        )
        if owner is None:
            return True
        return (
            await db.scalar(
                select(TransportDecisionBinding.id)
                .join(TransportTask, TransportTask.client_request_id == TransportDecisionBinding.client_request_id)
                .where(
                    TransportDecisionBinding.causal_token > causal_token,
                    TransportDecisionBinding.resource_fence_id == rack_id,
                    TransportTask.kind.in_(("RACK_MOVE", "RACK_ROTATE")),
                    current_rack_action_predicate(TransportDecisionBinding, TransportTask, owner),
                )
                .limit(1)
            )
            is not None
        )

    async def has_return_batch_transport(self, db: AsyncSession, *, workline_id: int, operation_id: str) -> bool:
        return (
            await db.scalar(
                select(TransportDecisionBinding.id)
                .where(
                    TransportDecisionBinding.workline_id == workline_id,
                    TransportDecisionBinding.step == RETURN_STEP,
                    TransportDecisionBinding.resource_fence_id == operation_id,
                )
                .limit(1)
            )
            is not None
        )

    async def has_unclosed_return_transport_for_face(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        rack_id: str,
        rack_face: str | None,
        round_started_at: datetime | None = None,
    ) -> bool:
        """本面回架 BIN_MOVE 的终态、最终位置和结果未闭合时，禁止下一轮分配。"""
        if round_started_at is None:
            round_started_at = await self.current_round_started_at(db, workline_id=workline_id, rack_id=rack_id)
        transports = cast("Any", TransportTask).__table__.c
        members = cast("Any", TransportMember).__table__
        final_matches_target = and_(
            members.c.final_position_json["kind"]
            .as_string()
            .is_not_distinct_from(members.c.target_json["kind"].as_string()),
            members.c.final_position_json["rack_id"]
            .as_string()
            .is_not_distinct_from(members.c.target_json["rack_id"].as_string()),
            members.c.final_position_json["rack_face"]
            .as_string()
            .is_not_distinct_from(members.c.target_json["rack_face"].as_string()),
            members.c.final_position_json["slot_id"]
            .as_string()
            .is_not_distinct_from(members.c.target_json["slot_id"].as_string()),
        )
        active = await db.scalar(
            select(transports.id)
            .join(members, members.c.transport_task_id == transports.transport_task_id)
            .where(
                transports.authority_workline_id == workline_id,
                transports.kind == "BIN_MOVE",
                members.c.object_type == "BIN",
                members.c.target_json["rack_id"].as_string() == rack_id,
                (members.c.target_json["rack_face"].as_string() == rack_face) if rack_face is not None else True,
                (transports.created_at >= round_started_at) if round_started_at is not None else True,
                or_(
                    transports.status.in_(("PENDING", "ACCEPTED", "RECONCILING")),
                    and_(
                        transports.status == "SUCCEEDED",
                        or_(
                            members.c.status != "SUCCEEDED",
                            not_(final_matches_target),
                        ),
                    ),
                    and_(
                        transports.status == "FAILED",
                        members.c.final_position_json["kind"].as_string().is_(None),
                    ),
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
