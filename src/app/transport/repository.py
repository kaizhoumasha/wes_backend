"""Transport 聚合的数据库访问。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import and_, delete, exists, func, or_, select, text, update
from sqlmodel import col

from src.app.execution.locks import position_projection_lock_identity
from src.app.execution.models.inbound_evidence import InboundEvidence
from src.app.execution.models.position_projection import PositionProjection
from src.app.execution.models.transport_decision_binding import TransportDecisionBinding
from src.app.execution.models.wms_confirmation import WmsConfirmation
from src.app.transport.contracts import (
    MAX_SUBMIT_ATTEMPTS,
    TRANSPORT_DEBUG_CALLER_WORKLINE_ID,
    TRANSPORT_POSITION_OPERATION,
)
from src.app.transport.models import (
    TransportCallbackReceipt,
    TransportDebugPositionProjection,
    TransportEvidence,
    TransportMember,
    TransportTask,
)
from src.app.wms_adapter.return_buffer_drain.wire import RETURN_BUFFER_DRAIN_OPERATION
from src.app.wms_integration.outbound_picking.models.picking_task import PickingTask

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession


class TransportRepository:
    """只执行 Transport 聚合 SQL 和 flush，不自行提交事务。"""

    async def lock_task_identity(self, db: AsyncSession, transport_task_id: str) -> None:
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
            {"identity": f"transport-result:{transport_task_id}"},
        )

    async def requeue_unassociated_evidence(self, db: AsyncSession, transport_task_id: str) -> int:
        result = await db.execute(
            update(TransportEvidence)
            .where(
                col(TransportEvidence.transport_task_id) == transport_task_id,
                col(TransportEvidence.status) == "CONFLICT",
                col(TransportEvidence.conflict_code) == "TRANSPORT_TASK_NOT_FOUND",
            )
            .values(status="PENDING", conflict_code=None, processed_at=None, claim_token=None, claim_until=None)
            .returning(col(TransportEvidence.id))
        )
        return len(result.scalars().all())

    async def lock_position_result(self, db: AsyncSession, object_type: str, object_id: str) -> None:
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
            {"identity": position_projection_lock_identity(object_type, object_id)},
        )

    async def has_other_position_facts(
        self,
        db: AsyncSession,
        *,
        object_type: str,
        object_id: str,
        transport_task_id: str,
        current_created_at: datetime,
        ordered_authority_workline_id: int | None,
        current_caller_workline_id: str,
    ) -> bool:
        # A closed fact predating the current ordered task is historical context,
        # not a live conflict. Only an open or newer fact from another authority/
        # caller can make the result indeterminate.
        competing_fact = and_(
            or_(
                col(TransportTask.status).notin_(("SUCCEEDED", "FAILED")),
                col(TransportMember.updated_at) >= current_created_at,
            ),
            or_(
                col(TransportTask.authority_workline_id).is_distinct_from(ordered_authority_workline_id),
                col(TransportTask.caller_json)["workline_id"].as_string().is_distinct_from(current_caller_workline_id),
            ),
            col(TransportTask.caller_json)["workline_id"].as_string() != TRANSPORT_DEBUG_CALLER_WORKLINE_ID,
        )
        return bool(
            await db.scalar(
                select(
                    exists().where(
                        col(TransportMember.object_type) == object_type,
                        col(TransportMember.object_id) == object_id,
                        col(TransportMember.transport_task_id) != transport_task_id,
                        col(TransportMember.last_operation_id).is_not(None),
                        col(TransportTask.transport_task_id) == col(TransportMember.transport_task_id),
                        competing_fact if ordered_authority_workline_id is not None else True,
                    )
                )
            )
        )

    async def previous_position_fact_closed_before(
        self,
        db: AsyncSession,
        *,
        object_type: str,
        object_id: str,
        previous_transport_task_id: str,
        previous_operation_id: str,
        current_created_at: datetime,
        current_authority_workline_id: int,
        current_caller_workline_id: str,
    ) -> bool:
        return bool(
            await db.scalar(
                select(TransportMember.id)
                .join(TransportTask, col(TransportTask.transport_task_id) == col(TransportMember.transport_task_id))
                .where(
                    col(TransportMember.object_type) == object_type,
                    col(TransportMember.object_id) == object_id,
                    col(TransportMember.transport_task_id) == previous_transport_task_id,
                    col(TransportMember.last_operation_id) == previous_operation_id,
                    col(TransportMember.status) == "SUCCEEDED",
                    col(TransportMember.position_unknown).is_(False),
                    col(TransportMember.updated_at) < current_created_at,
                    col(TransportTask.status) == "SUCCEEDED",
                    col(TransportTask.authority_workline_id) == current_authority_workline_id,
                    col(TransportTask.caller_json)["workline_id"].as_string() == current_caller_workline_id,
                )
                .limit(1)
            )
        )

    async def get_task_by_client_request(
        self,
        db: AsyncSession,
        client_request_id: str,
    ) -> TransportTask | None:
        return await db.scalar(select(TransportTask).where(col(TransportTask.client_request_id) == client_request_id))

    async def get_task(
        self,
        db: AsyncSession,
        transport_task_id: str,
        *,
        for_update: bool = False,
    ) -> TransportTask | None:
        statement = select(TransportTask).where(col(TransportTask.transport_task_id) == transport_task_id)
        if for_update:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return await db.scalar(statement)

    async def list_members(self, db: AsyncSession, transport_task_id: str) -> list[TransportMember]:
        result = await db.scalars(
            select(TransportMember)
            .where(col(TransportMember.transport_task_id) == transport_task_id)
            .order_by(col(TransportMember.ordinal).asc(), col(TransportMember.id).asc())
        )
        return list(result)

    async def list_final_result_projection_candidates(
        self, db: AsyncSession, *, limit: int
    ) -> list[tuple[str, str, str, str, datetime]]:
        task_table, member_table = TransportTask.__table__, TransportMember.__table__
        projection_table = PositionProjection.__table__
        binding_table, picking_table = TransportDecisionBinding.__table__, PickingTask.__table__
        evidence_table, confirmation_table = InboundEvidence.__table__, WmsConfirmation.__table__
        newer_confirmation = confirmation_table.alias("newer_drain_confirmation")
        task, member = task_table.c, member_table.c
        projection = projection_table.c
        binding, picking = binding_table.c, picking_table.c
        statement = (
            select(
                task.transport_task_id,
                member.object_type,
                member.object_id,
                member.last_operation_id,
                member.updated_at,
            )
            .select_from(task_table)
            .join(member_table, member.transport_task_id == task.transport_task_id)
            .join(binding_table, binding.client_request_id == task.client_request_id)
            .outerjoin(picking_table, picking.id == binding.picking_task_id)
            .outerjoin(evidence_table, evidence_table.c.id == binding.source_evidence_id)
            .outerjoin(confirmation_table, confirmation_table.c.response_evidence_id == evidence_table.c.id)
            .outerjoin(
                projection_table,
                and_(projection.object_type == member.object_type, projection.object_id == member.object_id),
            )
            .where(
                task.authority_workline_id.is_not(None),
                binding.workline_id == task.authority_workline_id,
                binding.causal_token > 0,
                or_(
                    and_(
                        binding.picking_task_id.is_not(None),
                        picking.workline_id == binding.workline_id,
                        picking.status.in_(("PREPARING", "EXECUTING")),
                    ),
                    and_(
                        binding.picking_task_id.is_(None),
                        evidence_table.c.kind == "WMS_RESULT",
                        evidence_table.c.operation == RETURN_BUFFER_DRAIN_OPERATION,
                        evidence_table.c.workline_id == binding.workline_id,
                        evidence_table.c.operation_id.is_not(None),
                        evidence_table.c.published_at.is_not(None),
                        confirmation_table.c.operation == RETURN_BUFFER_DRAIN_OPERATION,
                        confirmation_table.c.operation_id == evidence_table.c.operation_id,
                        confirmation_table.c.workline_id == binding.workline_id,
                        confirmation_table.c.status == "COMPLETED",
                        ~exists(
                            select(newer_confirmation.c.id).where(
                                newer_confirmation.c.workline_id == binding.workline_id,
                                newer_confirmation.c.operation == RETURN_BUFFER_DRAIN_OPERATION,
                                newer_confirmation.c.operation_id > evidence_table.c.operation_id,
                            )
                        ),
                    ),
                ),
                member.status.in_(("SUCCEEDED", "FAILED")),
                member.last_operation_id.is_not(None),
                or_(
                    projection.id.is_(None),
                    and_(
                        projection.source_causal_token.is_not(None),
                        projection.source_effect_phase.is_not(None),
                        or_(
                            projection.source_causal_token < binding.causal_token,
                            and_(
                                projection.source_causal_token == binding.causal_token,
                                or_(
                                    projection.source_transport_task_id.is_distinct_from(task.transport_task_id),
                                    projection.source_operation_id.is_distinct_from(member.last_operation_id),
                                    projection.source_effect_phase.is_distinct_from("FINAL_RESULT"),
                                ),
                            ),
                        ),
                    ),
                ),
            )
            .order_by(member.updated_at.asc(), member.id.asc())
            .limit(limit)
        )
        return [tuple(row) for row in (await db.execute(statement)).all()]

    async def is_current_drain_binding(self, db: AsyncSession, binding_id: int) -> bool:
        binding = TransportDecisionBinding.__table__.c
        evidence = InboundEvidence.__table__.c
        confirmation = WmsConfirmation.__table__.c
        newer = WmsConfirmation.__table__.alias("newer_drain_confirmation").c
        return bool(
            await db.scalar(
                select(binding.id)
                .join(InboundEvidence.__table__, evidence.id == binding.source_evidence_id)
                .join(WmsConfirmation.__table__, confirmation.response_evidence_id == evidence.id)
                .where(
                    binding.id == binding_id,
                    binding.picking_task_id.is_(None),
                    evidence.kind == "WMS_RESULT",
                    evidence.operation == RETURN_BUFFER_DRAIN_OPERATION,
                    evidence.workline_id == binding.workline_id,
                    evidence.operation_id.is_not(None),
                    evidence.published_at.is_not(None),
                    confirmation.operation == RETURN_BUFFER_DRAIN_OPERATION,
                    confirmation.operation_id == evidence.operation_id,
                    confirmation.workline_id == binding.workline_id,
                    confirmation.status == "COMPLETED",
                    ~exists(
                        select(newer.id).where(
                            newer.workline_id == binding.workline_id,
                            newer.operation == RETURN_BUFFER_DRAIN_OPERATION,
                            newer.operation_id > evidence.operation_id,
                        )
                    ),
                )
                .limit(1)
            )
        )

    async def list_ack_invalidation_projection_candidates(
        self, db: AsyncSession, *, limit: int
    ) -> list[tuple[str, str, str, str, datetime]]:
        """Return accepted-task members whose ACK fact may have left position unknown.

        This scan is deliberately independent from final-result replay: the durable
        ``ACCEPTED`` task state is the only source fact and no provider call is made.
        """
        task, member = TransportTask.__table__.c, TransportMember.__table__.c
        binding, picking = TransportDecisionBinding.__table__.c, PickingTask.__table__.c
        evidence, confirmation = InboundEvidence.__table__.c, WmsConfirmation.__table__.c
        newer_confirmation = WmsConfirmation.__table__.alias("newer_drain_confirmation").c
        projection = PositionProjection.__table__.c
        statement = (
            select(
                task.transport_task_id,
                member.object_type,
                member.object_id,
                task.submit_operation_id,
                task.updated_at,
            )
            .select_from(TransportTask.__table__)
            .join(TransportMember.__table__, member.transport_task_id == task.transport_task_id)
            .join(TransportDecisionBinding.__table__, binding.client_request_id == task.client_request_id)
            .outerjoin(PickingTask.__table__, picking.id == binding.picking_task_id)
            .outerjoin(InboundEvidence.__table__, evidence.id == binding.source_evidence_id)
            .outerjoin(WmsConfirmation.__table__, confirmation.response_evidence_id == evidence.id)
            .outerjoin(
                PositionProjection.__table__,
                and_(projection.object_type == member.object_type, projection.object_id == member.object_id),
            )
            .where(
                task.status == "ACCEPTED",
                task.submit_operation_id.is_not(None),
                task.authority_workline_id.is_not(None),
                binding.workline_id == task.authority_workline_id,
                binding.causal_token > 0,
                or_(
                    and_(
                        binding.picking_task_id.is_not(None),
                        picking.workline_id == binding.workline_id,
                        picking.status.in_(("PREPARING", "EXECUTING")),
                    ),
                    and_(
                        binding.picking_task_id.is_(None),
                        evidence.kind == "WMS_RESULT",
                        evidence.operation == RETURN_BUFFER_DRAIN_OPERATION,
                        evidence.workline_id == binding.workline_id,
                        evidence.operation_id.is_not(None),
                        evidence.published_at.is_not(None),
                        confirmation.operation == RETURN_BUFFER_DRAIN_OPERATION,
                        confirmation.operation_id == evidence.operation_id,
                        confirmation.workline_id == binding.workline_id,
                        confirmation.status == "COMPLETED",
                        ~exists(
                            select(newer_confirmation.id).where(
                                newer_confirmation.workline_id == binding.workline_id,
                                newer_confirmation.operation == RETURN_BUFFER_DRAIN_OPERATION,
                                newer_confirmation.operation_id > evidence.operation_id,
                            )
                        ),
                    ),
                ),
                projection.id.is_not(None),
                projection.source_causal_token.is_not(None),
                projection.source_effect_phase.is_not(None),
                or_(
                    projection.source_causal_token < binding.causal_token,
                    and_(
                        projection.source_causal_token == binding.causal_token,
                        projection.source_effect_phase.is_distinct_from("FINAL_RESULT"),
                        or_(
                            projection.source_transport_task_id.is_distinct_from(task.transport_task_id),
                            projection.source_operation_id.is_distinct_from(task.submit_operation_id),
                            projection.source_effect_phase.is_distinct_from("ACK_INVALIDATION"),
                        ),
                    ),
                ),
            )
            .order_by(task.updated_at.asc(), member.id.asc())
            .limit(limit)
        )
        return [tuple(row) for row in (await db.execute(statement)).all()]

    async def get_debug_position_projection(
        self,
        db: AsyncSession,
        object_type: str,
        object_id: str,
        *,
        for_update: bool = False,
    ) -> TransportDebugPositionProjection | None:
        statement = select(TransportDebugPositionProjection).where(
            col(TransportDebugPositionProjection.object_type) == object_type,
            col(TransportDebugPositionProjection.object_id) == object_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return await db.scalar(statement)

    async def apply_debug_position_projection(
        self,
        db: AsyncSession,
        *,
        object_type: str,
        object_id: str,
        position_json: dict[str, Any] | None,
        position_unknown: bool,
        arrival_face: str | None,
        operation_id: str,
        transport_task_id: str,
        updated_at: datetime,
    ) -> TransportDebugPositionProjection:
        projection = await self.get_debug_position_projection(db, object_type, object_id, for_update=True)
        if projection is not None and projection.source_transport_task_id != transport_task_id:
            projection.position_unknown = True
            await db.flush()
            return projection
        if projection is None:
            projection = TransportDebugPositionProjection(
                object_type=object_type,
                object_id=object_id,
                source_operation_id=operation_id,
                source_transport_task_id=transport_task_id,
                updated_at=updated_at,
            )
            db.add(projection)
        projection.position_json = position_json
        projection.position_unknown = position_unknown
        projection.arrival_face = arrival_face
        projection.source_operation_id = operation_id
        projection.source_transport_task_id = transport_task_id
        projection.updated_at = updated_at
        await db.flush()
        return projection

    async def get_debug_reset_counts(
        self,
        db: AsyncSession,
        transport_task_id: str,
    ) -> tuple[int, int, int, int]:
        """返回回执、Evidence、位置投影和成员数量。"""

        callback_receipt_count = await db.scalar(
            select(func.count())
            .select_from(TransportCallbackReceipt)
            .where(
                col(TransportCallbackReceipt.response_data_json)["transport_task_id"].as_string() == transport_task_id
            )
        )
        evidence_count = await db.scalar(
            select(func.count())
            .select_from(TransportEvidence)
            .where(col(TransportEvidence.transport_task_id) == transport_task_id)
        )
        position_projection_count = await db.scalar(
            select(func.count())
            .select_from(TransportDebugPositionProjection)
            .where(col(TransportDebugPositionProjection.source_transport_task_id) == transport_task_id)
        )
        member_count = await db.scalar(
            select(func.count())
            .select_from(TransportMember)
            .where(col(TransportMember.transport_task_id) == transport_task_id)
        )
        return (
            int(callback_receipt_count or 0),
            int(evidence_count or 0),
            int(position_projection_count or 0),
            int(member_count or 0),
        )

    async def delete_debug_task_aggregate(
        self,
        db: AsyncSession,
        transport_task_id: str,
    ) -> tuple[int, int, int, int, int]:
        """按依赖顺序删除指定 TransportTask 的完整本地 Transport 链路。"""

        position_projections = await db.execute(
            delete(TransportDebugPositionProjection).where(
                col(TransportDebugPositionProjection.source_transport_task_id) == transport_task_id
            )
        )
        receipts = await db.execute(
            delete(TransportCallbackReceipt).where(
                col(TransportCallbackReceipt.response_data_json)["transport_task_id"].as_string() == transport_task_id
            )
        )
        evidence = await db.execute(
            delete(TransportEvidence).where(col(TransportEvidence.transport_task_id) == transport_task_id)
        )
        members = await db.execute(
            delete(TransportMember).where(col(TransportMember.transport_task_id) == transport_task_id)
        )
        tasks = await db.execute(delete(TransportTask).where(col(TransportTask.transport_task_id) == transport_task_id))
        return (
            int(receipts.rowcount or 0),
            int(evidence.rowcount or 0),
            int(position_projections.rowcount or 0),
            int(members.rowcount or 0),
            int(tasks.rowcount or 0),
        )

    async def add_aggregate(
        self,
        db: AsyncSession,
        task: TransportTask,
        members: list[TransportMember],
    ) -> None:
        db.add(task)
        # 未声明 ORM relationship 时 SQLAlchemy 不保证跨表插入顺序；
        # 先落主记录以满足 PostgreSQL 外键。
        await db.flush()
        db.add_all(members)
        await db.flush()

    async def claim_next_pending_task(
        self,
        db: AsyncSession,
        *,
        token: str,
        now: datetime,
        claim_until: datetime,
        excluded_task_ids: set[str] | None = None,
    ) -> TransportTask | None:
        predicates = [
            col(TransportTask.status) == "PENDING",
            col(TransportTask.reason_code).is_(None),
            col(TransportTask.submit_attempt_count) < MAX_SUBMIT_ATTEMPTS,
            col(TransportTask.send_started_at).is_(None),
            ~exists(
                select(col(TransportEvidence.id)).where(
                    col(TransportEvidence.transport_task_id) == col(TransportTask.transport_task_id),
                )
            ),
            or_(col(TransportTask.next_submit_at).is_(None), col(TransportTask.next_submit_at) <= now),
            or_(col(TransportTask.submit_claim_until).is_(None), col(TransportTask.submit_claim_until) < now),
        ]
        if excluded_task_ids:
            predicates.append(col(TransportTask.transport_task_id).not_in(excluded_task_ids))
        statement = (
            select(TransportTask)
            .where(*predicates)
            .order_by(
                col(TransportTask.next_submit_at).is_not(None).asc(),
                col(TransportTask.next_submit_at).asc(),
                col(TransportTask.id).asc(),
            )
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        task = await db.scalar(statement)
        if task is None:
            return None
        task.submit_claim_token = token
        task.submit_claim_until = claim_until
        task.updated_at = now
        await db.flush()
        return task

    async def release_unsent_claim(self, db: AsyncSession, task: TransportTask, *, token: str) -> None:
        if task.submit_claim_token != token:
            raise RuntimeError("transport unsent claim does not match")
        task.submit_claim_token = None
        task.submit_claim_until = None
        if task.send_started_at is not None:
            if task.submit_attempt_count < 1:
                raise RuntimeError("transport submit attempt state does not match")
            task.submit_attempt_count -= 1
            task.send_started_at = None
        await db.flush()

    async def claim_overdue_tasks(
        self,
        db: AsyncSession,
        *,
        limit: int,
        now: datetime,
    ) -> list[TransportTask]:
        return list(
            await db.scalars(
                select(TransportTask)
                .where(
                    col(TransportTask.status) == "ACCEPTED",
                    col(TransportTask.result_deadline_at).is_not(None),
                    col(TransportTask.result_deadline_at) <= now,
                )
                .order_by(col(TransportTask.result_deadline_at).asc(), col(TransportTask.id).asc())
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )

    async def claim_ambiguous_submissions(
        self,
        db: AsyncSession,
        *,
        limit: int,
        now: datetime,
    ) -> list[TransportTask]:
        return list(
            await db.scalars(
                select(TransportTask)
                .where(
                    col(TransportTask.status) == "PENDING",
                    col(TransportTask.reason_code).is_(None),
                    col(TransportTask.send_started_at).is_not(None),
                    col(TransportTask.submit_claim_until).is_not(None),
                    col(TransportTask.submit_claim_until) < now,
                )
                .order_by(col(TransportTask.submit_claim_until).asc(), col(TransportTask.id).asc())
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )

    async def claim_pending_outcomes(
        self,
        db: AsyncSession,
        *,
        limit: int,
        token: str,
        now: datetime,
        claim_until: datetime,
    ) -> list[TransportTask]:
        tasks = list(
            await db.scalars(
                select(TransportTask)
                .where(
                    col(TransportTask.outcome_version) > col(TransportTask.published_outcome_version),
                    col(TransportTask.outcome_json).is_not(None),
                    or_(col(TransportTask.outcome_claim_until).is_(None), col(TransportTask.outcome_claim_until) < now),
                )
                .order_by(col(TransportTask.updated_at).asc(), col(TransportTask.id).asc())
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        for task in tasks:
            task.outcome_claim_token = token
            task.outcome_claim_until = claim_until
        await db.flush()
        return tasks

    async def add_evidence(self, db: AsyncSession, evidence: TransportEvidence) -> None:
        db.add(evidence)
        await db.flush()

    async def add_callback_receipt(self, db: AsyncSession, receipt: TransportCallbackReceipt) -> None:
        db.add(receipt)
        await db.flush()

    async def get_callback_receipt(
        self,
        db: AsyncSession,
        operation: str,
        operation_id: str,
        *,
        for_update: bool = False,
    ) -> TransportCallbackReceipt | None:
        statement = select(TransportCallbackReceipt).where(
            col(TransportCallbackReceipt.operation) == operation,
            col(TransportCallbackReceipt.operation_id) == operation_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return await db.scalar(statement)

    async def get_evidence_by_operation_id(
        self,
        db: AsyncSession,
        operation: str,
        operation_id: str,
        *,
        for_update: bool = False,
    ) -> TransportEvidence | None:
        statement = select(TransportEvidence).where(
            col(TransportEvidence.operation) == operation,
            col(TransportEvidence.operation_id) == operation_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return await db.scalar(statement)

    async def get_evidence_by_outcome_revision(
        self,
        db: AsyncSession,
        transport_task_id: str,
        outcome_revision: int,
        *,
        for_update: bool = False,
    ) -> TransportEvidence | None:
        statement = select(TransportEvidence).where(
            col(TransportEvidence.transport_task_id) == transport_task_id,
            col(TransportEvidence.outcome_revision) == outcome_revision,
        )
        if for_update:
            statement = statement.with_for_update()
        return await db.scalar(statement)

    async def list_applied_position_evidence(
        self,
        db: AsyncSession,
        transport_task_id: str,
    ) -> list[TransportEvidence]:
        result = await db.scalars(
            select(TransportEvidence).where(
                col(TransportEvidence.transport_task_id) == transport_task_id,
                col(TransportEvidence.operation) == TRANSPORT_POSITION_OPERATION,
                col(TransportEvidence.status) == "APPLIED",
            )
        )
        return list(result)

    async def get_task_with_latest_evidence(
        self,
        db: AsyncSession,
        transport_task_id: str,
    ) -> tuple[TransportTask, TransportEvidence | None, int] | None:
        latest_evidence_id = (
            select(col(TransportEvidence.id))
            .where(col(TransportEvidence.transport_task_id) == transport_task_id)
            .order_by(col(TransportEvidence.received_at).desc(), col(TransportEvidence.id).desc())
            .limit(1)
            .scalar_subquery()
        )
        pending_count = (
            select(func.count())
            .select_from(TransportEvidence)
            .where(
                col(TransportEvidence.transport_task_id) == transport_task_id,
                col(TransportEvidence.status) == "PENDING",
            )
            .scalar_subquery()
        )
        result = await db.execute(
            select(TransportTask, TransportEvidence, pending_count)
            .outerjoin(TransportEvidence, col(TransportEvidence.id) == latest_evidence_id)
            .where(col(TransportTask.transport_task_id) == transport_task_id)
        )
        row = result.one_or_none()
        return None if row is None else (row[0], row[1], int(row[2]))

    async def list_tasks_with_latest_evidence(
        self,
        db: AsyncSession,
        *,
        limit: int,
        cursor_created_at: datetime | None,
        cursor_id: int | None,
        kind: str | None,
        status: str | None,
    ) -> list[tuple[TransportTask, TransportEvidence | None]]:
        latest_evidence_id = (
            select(col(TransportEvidence.id))
            .where(col(TransportEvidence.transport_task_id) == col(TransportTask.transport_task_id))
            .order_by(col(TransportEvidence.received_at).desc(), col(TransportEvidence.id).desc())
            .limit(1)
            .correlate(TransportTask)
            .scalar_subquery()
        )
        statement = select(TransportTask, TransportEvidence).outerjoin(
            TransportEvidence,
            col(TransportEvidence.id) == latest_evidence_id,
        )
        if cursor_created_at is not None and cursor_id is not None:
            statement = statement.where(
                or_(
                    col(TransportTask.created_at) < cursor_created_at,
                    and_(col(TransportTask.created_at) == cursor_created_at, col(TransportTask.id) < cursor_id),
                )
            )
        if kind is not None:
            statement = statement.where(col(TransportTask.kind) == kind)
        if status is not None:
            statement = statement.where(col(TransportTask.status) == status)
        result = await db.execute(
            statement.order_by(col(TransportTask.created_at).desc(), col(TransportTask.id).desc()).limit(limit)
        )
        return [(row[0], row[1]) for row in result.all()]

    async def has_evidence(self, db: AsyncSession, transport_task_id: str) -> bool:
        evidence_id = await db.scalar(
            select(col(TransportEvidence.id))
            .where(col(TransportEvidence.transport_task_id) == transport_task_id)
            .limit(1)
        )
        return evidence_id is not None

    async def get_evidence(
        self,
        db: AsyncSession,
        evidence_id: int,
        *,
        for_update: bool = False,
    ) -> TransportEvidence | None:
        statement = select(TransportEvidence).where(col(TransportEvidence.id) == evidence_id)
        if for_update:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return await db.scalar(statement)

    async def claim_pending_evidence(
        self,
        db: AsyncSession,
        *,
        limit: int,
        token: str,
        now: datetime,
        claim_until: datetime,
    ) -> list[TransportEvidence]:
        evidence = list(
            await db.scalars(
                select(TransportEvidence)
                .where(
                    col(TransportEvidence.status) == "PENDING",
                    or_(col(TransportEvidence.claim_until).is_(None), col(TransportEvidence.claim_until) < now),
                )
                .order_by(col(TransportEvidence.received_at).asc(), col(TransportEvidence.id).asc())
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        for item in evidence:
            item.claim_token = token
            item.claim_until = claim_until
        await db.flush()
        return evidence


__all__ = ["TransportRepository"]
