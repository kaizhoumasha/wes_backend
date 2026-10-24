"""排空本地执行记录；事务及 WorkLine 锁由调用方拥有。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from sqlalchemy import and_, or_, select
from wes_plugin_sdk import (
    BinBatchNoBatch,
    BinReturnBatchIntent,
    BinReturnBatchOutcome,
    BinReturnBatchReady,
    ReturnBufferDrainReady,
)

from src.app.execution.models import (
    InboundEvidence,
    InboundEvidenceApplyStatus,
    InboundEvidenceKind,
    TransportDecisionBinding,
    WmsConfirmation,
    WmsConfirmationStatus,
)
from src.app.transport.models import TransportTask
from src.app.wms_adapter.outbound_picking.return_batch_wire import BIN_RETURN_BATCH_OPERATION
from src.app.wms_integration.outbound_picking.models import PickingTask
from src.app.wms_integration.outbound_picking.services.bin_batch import BinBatchResultReader

from .bin_line.return_model import BinLineReturn
from .bin_line.return_repository import ReturnRepository
from .drain_execution_model import ManualPickingDrainExecution, ManualPickingDrainReturnMember

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

    from src.app.transport.service import TransportService
    from src.app.wms_integration.return_buffer_drain import ReturnBufferDrainRecord


class DrainExecutionRepository:
    def __init__(
        self,
        reader: BinBatchResultReader | None = None,
        *,
        transports: TransportService | None = None,
    ) -> None:
        self._reader = reader or BinBatchResultReader()
        self._transports = transports

    async def retirement_tasks(self, db: AsyncSession, *, workline_id: int) -> tuple[PickingTask, ...]:
        return tuple(
            (
                await db.scalars(
                    select(PickingTask)
                    .join(InboundEvidence, InboundEvidence.id == PickingTask.issued_evidence_id)
                    .where(
                        PickingTask.workline_id == workline_id,
                        PickingTask.status.in_(("QUEUED", "PREPARING", "EXECUTING")),
                        InboundEvidence.apply_status == InboundEvidenceApplyStatus.APPLIED,
                        InboundEvidence.kind == InboundEvidenceKind.WMS_EVENT,
                        InboundEvidence.operation == "outbound.picking_task.issued@v1",
                    )
                    .order_by(PickingTask.dispatch_sequence, PickingTask.id)
                    .execution_options(populate_existing=True)
                )
            ).all()
        )

    async def retirement_actions(
        self,
        db: AsyncSession,
        *,
        execution: ManualPickingDrainExecution,
        decision: ReturnBufferDrainRecord,
    ) -> tuple[tuple[TransportDecisionBinding, TransportTask, bool], ...]:
        return_evidence_ids = (
            select(WmsConfirmation.response_evidence_id)
            .join(
                ManualPickingDrainReturnMember,
                ManualPickingDrainReturnMember.return_batch_confirmation_id == WmsConfirmation.id,
            )
            .where(ManualPickingDrainReturnMember.execution_id == execution.id)
        )
        bindings = tuple(
            (
                await db.execute(
                    select(TransportDecisionBinding, TransportTask)
                    .join(TransportTask, TransportTask.client_request_id == TransportDecisionBinding.client_request_id)
                    .where(
                        TransportDecisionBinding.workline_id == execution.workline_id,
                        TransportDecisionBinding.picking_task_id.is_(None),
                        TransportTask.authority_workline_id == execution.workline_id,
                        or_(
                            and_(
                                TransportDecisionBinding.source_evidence_id == execution.decision_evidence_id,
                                TransportDecisionBinding.resource_fence_id.in_(
                                    tuple(rack.rack_id for rack in decision.result.racks)
                                ),
                                TransportDecisionBinding.step.in_(
                                    (
                                        "MANUAL_PICKING_RETURN_BUFFER_DRAIN_RACK_IN",
                                        "MANUAL_PICKING_RETURN_BUFFER_DRAIN_RACK_ROTATE",
                                        "MANUAL_PICKING_RETURN_BUFFER_DRAIN_RACK_OUT",
                                    )
                                ),
                            ),
                            and_(
                                TransportDecisionBinding.source_evidence_id.in_(return_evidence_ids),
                                TransportDecisionBinding.step == "MANUAL_PICKING_RETURN_BATCH",
                            ),
                        ),
                    )
                    .order_by(TransportDecisionBinding.id.desc())
                    .execution_options(populate_existing=True)
                )
            ).all()
        )
        current = {}
        for binding, task in bindings:
            key = (binding.step, binding.resource_fence_id, task.request_json.get("target_face"))
            current.setdefault(key, (binding, task))
        if not current:
            return ()
        if self._transports is None:
            raise RuntimeError("drain retirement requires the host Transport result reader")
        actions = []
        for binding, task in current.values():
            has_final = await self._transports.has_pending_definite_result_in_session(db, task)
            actions.append((binding, task, has_final))
        return tuple(actions)

    async def release_windows_in_session(
        self,
        db: AsyncSession,
        *,
        execution: ManualPickingDrainExecution,
        closed_at: datetime,
    ) -> None:
        rows = (
            await db.scalars(
                select(TransportDecisionBinding).where(
                    TransportDecisionBinding.workline_id == execution.workline_id,
                    TransportDecisionBinding.picking_task_id.is_(None),
                    TransportDecisionBinding.source_evidence_id == execution.decision_evidence_id,
                    TransportDecisionBinding.step == "MANUAL_PICKING_RETURN_BUFFER_DRAIN_RACK_IN",
                    TransportDecisionBinding.window_target_location_code.is_not(None),
                    TransportDecisionBinding.window_released_at.is_(None),
                )
            )
        ).all()
        for row in rows:
            row.window_released_at = closed_at
        await db.flush()

    async def ensure_ready_in_session(
        self, db: AsyncSession, *, decision: ReturnBufferDrainRecord
    ) -> ManualPickingDrainExecution:
        if not isinstance(decision.result, ReturnBufferDrainReady) or decision.evidence_id is None:
            raise ValueError("drain execution requires applied READY Evidence")
        execution = await db.scalar(
            select(ManualPickingDrainExecution).where(
                ManualPickingDrainExecution.drain_operation_id == decision.intent.operation_id
            )
        )
        if execution is not None:
            if (execution.workline_id, execution.decision_evidence_id) != (decision.workline_id, decision.evidence_id):
                raise ValueError("drain execution frozen owner/evidence mismatch")
            return execution
        execution = ManualPickingDrainExecution(
            workline_id=decision.workline_id,
            drain_operation_id=decision.intent.operation_id,
            decision_evidence_id=decision.evidence_id,
        )
        db.add(execution)
        await db.flush()
        return execution

    async def active_for_workline(self, db: AsyncSession, workline_id: int) -> ManualPickingDrainExecution | None:
        rows = (
            await db.scalars(
                select(ManualPickingDrainExecution).where(
                    ManualPickingDrainExecution.workline_id == workline_id,
                    ManualPickingDrainExecution.status == "ACTIVE",
                )
            )
        ).all()
        if len(rows) > 1:
            raise ValueError("multiple ACTIVE drain executions have ambiguous WorkLine owner")
        return rows[0] if rows else None

    async def for_evidence(
        self, db: AsyncSession, *, workline_id: int, evidence_id: int
    ) -> ManualPickingDrainExecution | None:
        return (
            await db.execute(
                select(ManualPickingDrainExecution).where(
                    ManualPickingDrainExecution.workline_id == workline_id,
                    ManualPickingDrainExecution.decision_evidence_id == evidence_id,
                )
            )
        ).scalar_one_or_none()

    async def close_in_session(
        self,
        db: AsyncSession,
        execution: ManualPickingDrainExecution,
        *,
        status: Literal["COMPLETED", "EXCEPTION_RETIRED", "MANUAL_WITHDRAWN"],
        closed_at: datetime,
        reason_code: str | None = None,
        blocker_client_request_id: str | None = None,
        trigger_picking_task_id: int | None = None,
    ) -> bool:
        if execution.status != "ACTIVE":
            return False
        if status == "COMPLETED":
            if any(value is not None for value in (reason_code, blocker_client_request_id, trigger_picking_task_id)):
                raise ValueError("normal drain completion cannot carry retirement references")
        elif status == "MANUAL_WITHDRAWN":
            if reason_code != "MANUAL_SCOPE_EXIT" or not blocker_client_request_id or trigger_picking_task_id is None:
                raise ValueError("manual withdrawal requires reason and frozen blocker/trigger")
        elif (
            status != "EXCEPTION_RETIRED"
            or reason_code not in {"DRAIN_ACTION_TERMINAL_EXCEPTION", "DRAIN_RESULT_OVERDUE", "DRAIN_RESULT_UNKNOWN"}
            or not blocker_client_request_id
            or trigger_picking_task_id is None
        ):
            raise ValueError("drain retirement requires reason and frozen blocker/trigger")
        execution.status, execution.closed_at = status, closed_at
        execution.reason_code = reason_code
        execution.blocker_client_request_id = blocker_client_request_id
        execution.trigger_picking_task_id = trigger_picking_task_id
        await db.flush()
        return True

    async def freeze_return_members_in_session(
        self, db: AsyncSession, *, execution_id: int, return_batch_operation_id: str, return_ids: tuple[int, ...]
    ) -> None:
        execution = await db.scalar(
            select(ManualPickingDrainExecution).where(ManualPickingDrainExecution.id == execution_id)
        )
        confirmation = await db.scalar(
            select(WmsConfirmation).where(
                WmsConfirmation.operation == BIN_RETURN_BATCH_OPERATION,
                WmsConfirmation.operation_id == return_batch_operation_id,
            )
        )
        if execution is None or confirmation is None or confirmation.workline_id != execution.workline_id:
            raise ValueError("frozen drain return members owner mismatch")
        members = tuple(
            (
                await db.scalars(
                    select(ManualPickingDrainReturnMember)
                    .where(ManualPickingDrainReturnMember.return_batch_confirmation_id == confirmation.id)
                    .order_by(ManualPickingDrainReturnMember.sequence_no)
                )
            ).all()
        )
        expected = tuple((execution_id, value, number) for number, value in enumerate(return_ids, 1))
        if members:
            if tuple((row.execution_id, row.return_id, row.sequence_no) for row in members) != expected:
                raise ValueError("frozen drain return members identity/sequence mismatch")
            return
        if execution.status != "ACTIVE" or not return_ids or len(set(return_ids)) != len(return_ids):
            raise ValueError("frozen drain return members require ACTIVE owner and unique candidates")
        intent = self._reader.read_return_intent(
            confirmation, workline_id=execution.workline_id, operation_id=return_batch_operation_id
        )
        rows = await ReturnRepository().by_ids_for_update(db, workline_id=execution.workline_id, return_ids=return_ids)
        if tuple(row.bin_code for row in rows) != tuple(candidate.bin_code for candidate in intent.return_candidates):
            raise ValueError("frozen drain return members candidate mismatch")
        for sequence, return_id in enumerate(return_ids, 1):
            db.add(
                ManualPickingDrainReturnMember(
                    execution_id=execution_id,
                    return_batch_confirmation_id=confirmation.id,
                    return_id=return_id,
                    sequence_no=sequence,
                )
            )
        await db.flush()

    async def members_for_return_batch(
        self, db: AsyncSession, *, workline_id: int, operation_id: str
    ) -> tuple[ManualPickingDrainReturnMember, ...]:
        confirmation = await db.scalar(
            select(WmsConfirmation).where(
                WmsConfirmation.operation == BIN_RETURN_BATCH_OPERATION,
                WmsConfirmation.operation_id == operation_id,
            )
        )
        if confirmation is None:
            raise ValueError("return batch Confirmation missing")
        intent = self._reader.read_return_intent(confirmation, workline_id=workline_id, operation_id=operation_id)
        return await self._members_for_confirmation(
            db, workline_id=workline_id, confirmation=confirmation, intent=intent
        )

    async def _members_for_confirmation(
        self, db: AsyncSession, *, workline_id: int, confirmation: WmsConfirmation, intent: BinReturnBatchIntent
    ) -> tuple[ManualPickingDrainReturnMember, ...]:
        records = (
            await db.execute(
                select(
                    ManualPickingDrainReturnMember,
                    ManualPickingDrainExecution.workline_id,
                    BinLineReturn.workline_id,
                    BinLineReturn.bin_code,
                )
                .select_from(ManualPickingDrainReturnMember)
                .join(
                    ManualPickingDrainExecution,
                    ManualPickingDrainExecution.id == ManualPickingDrainReturnMember.execution_id,
                )
                .join(BinLineReturn, BinLineReturn.id == ManualPickingDrainReturnMember.return_id)
                .where(ManualPickingDrainReturnMember.return_batch_confirmation_id == confirmation.id)
                .order_by(ManualPickingDrainReturnMember.sequence_no)
            )
        ).all()
        if not records:
            return ()
        members = tuple(record[0] for record in records)
        if (
            confirmation.workline_id != workline_id
            or any(record[1] != workline_id or record[2] != workline_id for record in records)
            or len({member.execution_id for member in members}) != 1
            or tuple(member.sequence_no for member in members) != tuple(range(1, len(intent.return_candidates) + 1))
            or tuple(record[3] for record in records)
            != tuple(candidate.bin_code for candidate in intent.return_candidates)
        ):
            raise ValueError("frozen drain return members owner/candidate/sequence mismatch")
        return members

    async def execution_for_return_batch(
        self, db: AsyncSession, *, workline_id: int, operation_id: str
    ) -> ManualPickingDrainExecution | None:
        members = await self.members_for_return_batch(db, workline_id=workline_id, operation_id=operation_id)
        if not members:
            return None
        execution = await db.scalar(
            select(ManualPickingDrainExecution).where(ManualPickingDrainExecution.id == members[0].execution_id)
        )
        if execution is None or execution.workline_id != workline_id:
            raise ValueError("frozen drain return execution owner missing")
        return execution

    async def eligible_return_prefix(
        self, db: AsyncSession, *, workline_id: int, rows: tuple[BinLineReturn, ...]
    ) -> tuple[BinLineReturn, ...]:
        if not rows:
            return ()
        records = (
            await db.execute(
                select(
                    ManualPickingDrainReturnMember,
                    ManualPickingDrainExecution.status,
                    WmsConfirmation,
                    InboundEvidence,
                )
                .select_from(ManualPickingDrainReturnMember)
                .join(
                    ManualPickingDrainExecution,
                    ManualPickingDrainExecution.id == ManualPickingDrainReturnMember.execution_id,
                )
                .join(
                    WmsConfirmation, WmsConfirmation.id == ManualPickingDrainReturnMember.return_batch_confirmation_id
                )
                .outerjoin(InboundEvidence, InboundEvidence.id == WmsConfirmation.response_evidence_id)
                .where(
                    ManualPickingDrainExecution.workline_id == workline_id,
                    ManualPickingDrainReturnMember.return_id.in_(tuple(row.id for row in rows)),
                )
            )
        ).all()
        blocked = set()
        results = {}
        for member, _status, confirmation, evidence in records:
            if (
                confirmation.status != WmsConfirmationStatus.COMPLETED
                or evidence is None
                or evidence.apply_status != InboundEvidenceApplyStatus.APPLIED
                or evidence.published_at is None
            ):
                blocked.add(member.return_id)
                continue
            if confirmation.id not in results:
                intent, outcome = await self._reader.read_return(db, evidence, workline_id=workline_id)
                members = await self._members_for_confirmation(
                    db, workline_id=workline_id, confirmation=confirmation, intent=intent
                )
                if not members:
                    raise ValueError("drain return batch frozen members missing")
                results[confirmation.id] = outcome.result
            result = results[confirmation.id]
            # 候选快照不是储位分配；确定未分配的成员不由 drain 生命周期继续占有。
            if isinstance(result, BinBatchNoBatch) or (
                isinstance(result, BinReturnBatchReady) and member.sequence_no > len(result.moves)
            ):
                continue
            blocked.add(member.return_id)
        for index, row in enumerate(rows):
            if row.id in blocked:
                rows = rows[:index]
                break
        if not rows:
            return ()
        confirmations = (
            await db.scalars(
                select(WmsConfirmation)
                .outerjoin(InboundEvidence, InboundEvidence.id == WmsConfirmation.response_evidence_id)
                .where(
                    WmsConfirmation.workline_id == workline_id,
                    WmsConfirmation.operation == BIN_RETURN_BATCH_OPERATION,
                    or_(
                        WmsConfirmation.status != WmsConfirmationStatus.COMPLETED,
                        InboundEvidence.published_at.is_(None),
                    ),
                )
            )
        ).all()
        for confirmation in confirmations:
            intent = self._reader.read_return_intent(
                confirmation, workline_id=workline_id, operation_id=confirmation.operation_id
            )
            candidates = {candidate.bin_code for candidate in intent.return_candidates}
            blocked.update(
                row.id for row in rows if row.bin_code in candidates and row.created_at <= confirmation.created_at
            )
        for index, row in enumerate(rows):
            if row.id in blocked:
                return rows[:index]
        return rows

    async def pending_return_for_rack(
        self, db: AsyncSession, *, execution_id: int, workline_id: int, rack_id: str
    ) -> tuple[BinReturnBatchIntent, BinReturnBatchOutcome | None, InboundEvidence | None] | None:
        confirmation_ids = select(ManualPickingDrainReturnMember.return_batch_confirmation_id).where(
            ManualPickingDrainReturnMember.execution_id == execution_id
        )
        record = (
            await db.execute(
                select(WmsConfirmation, InboundEvidence)
                .outerjoin(InboundEvidence, InboundEvidence.id == WmsConfirmation.response_evidence_id)
                .where(
                    WmsConfirmation.id.in_(confirmation_ids),
                    WmsConfirmation.workline_id == workline_id,
                    WmsConfirmation.operation == BIN_RETURN_BATCH_OPERATION,
                    WmsConfirmation.request_payload["data"]["rack_id"].as_string() == rack_id,
                    InboundEvidence.published_at.is_(None),
                )
                .order_by(WmsConfirmation.created_at.desc())
                .limit(1)
            )
        ).first()
        if record is None:
            return None
        confirmation, evidence = record
        intent = self._reader.read_return_intent(
            confirmation, workline_id=workline_id, operation_id=confirmation.operation_id
        )
        outcome = None
        if confirmation.status == WmsConfirmationStatus.COMPLETED and evidence is not None:
            _, outcome = await self._reader.read_return(db, evidence, workline_id=workline_id)
        return intent, outcome, evidence

    async def latest_return_evidence_for_execution(
        self, db: AsyncSession, *, execution_id: int, workline_id: int, rack_id: str, rack_face: str
    ) -> tuple[InboundEvidence, datetime] | None:
        confirmation_ids = select(ManualPickingDrainReturnMember.return_batch_confirmation_id).where(
            ManualPickingDrainReturnMember.execution_id == execution_id
        )
        records = (
            await db.execute(
                select(InboundEvidence, WmsConfirmation)
                .join(WmsConfirmation, WmsConfirmation.response_evidence_id == InboundEvidence.id)
                .where(
                    WmsConfirmation.id.in_(confirmation_ids),
                    WmsConfirmation.workline_id == workline_id,
                    WmsConfirmation.operation == BIN_RETURN_BATCH_OPERATION,
                    WmsConfirmation.status == WmsConfirmationStatus.COMPLETED,
                    InboundEvidence.published_at.is_not(None),
                )
                .order_by(WmsConfirmation.completed_at.desc(), WmsConfirmation.id.desc())
            )
        ).all()
        for evidence, confirmation in records:
            intent = self._reader.read_return_intent(
                confirmation, workline_id=workline_id, operation_id=confirmation.operation_id
            )
            if (intent.rack_id, intent.rack_face) != (rack_id, rack_face):
                continue
            if confirmation.completed_at is None or evidence.operation_id != intent.operation_id:
                raise ValueError("drain return batch lacks completion identity/time")
            members = await self._members_for_confirmation(
                db, workline_id=workline_id, confirmation=confirmation, intent=intent
            )
            if not members or members[0].execution_id != execution_id:
                raise ValueError("drain return batch frozen execution mismatch")
            return evidence, confirmation.completed_at
        return None
