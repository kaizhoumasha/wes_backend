"""Transport 聚合的数据库访问。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import and_, delete, func, or_, select, update

from src.app.transport.contracts import MAX_SUBMIT_ATTEMPTS, TRANSPORT_POSITION_OPERATION
from src.app.transport.models import (
    TransportCallbackReceipt,
    TransportDebugPositionProjection,
    TransportEvidence,
    TransportMember,
    TransportResourceBinding,
    TransportTask,
)

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession


class TransportRepository:
    """只执行 Transport 聚合 SQL 和 flush，不自行提交事务。"""

    async def get_task_by_client_request(
        self,
        db: AsyncSession,
        client_request_id: str,
    ) -> TransportTask | None:
        return await db.scalar(select(TransportTask).where(TransportTask.client_request_id == client_request_id))

    async def get_task(
        self,
        db: AsyncSession,
        transport_task_id: str,
        *,
        for_update: bool = False,
    ) -> TransportTask | None:
        statement = select(TransportTask).where(TransportTask.transport_task_id == transport_task_id)
        if for_update:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return await db.scalar(statement)

    async def list_members(self, db: AsyncSession, transport_task_id: str) -> list[TransportMember]:
        result = await db.scalars(
            select(TransportMember)
            .where(TransportMember.transport_task_id == transport_task_id)
            .order_by(TransportMember.ordinal.asc(), TransportMember.id.asc())
        )
        return list(result)

    async def get_debug_position_projection(
        self,
        db: AsyncSession,
        object_type: str,
        object_id: str,
        *,
        for_update: bool = False,
    ) -> TransportDebugPositionProjection | None:
        statement = select(TransportDebugPositionProjection).where(
            TransportDebugPositionProjection.object_type == object_type,
            TransportDebugPositionProjection.object_id == object_id,
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
    ) -> tuple[int, int, int, int, int, int]:
        """返回回执、Evidence、位置投影、成员、绑定和活跃绑定数量。"""

        callback_receipt_count = await db.scalar(
            select(func.count())
            .select_from(TransportCallbackReceipt)
            .where(TransportCallbackReceipt.response_data_json["transport_task_id"].as_string() == transport_task_id)
        )
        evidence_count = await db.scalar(
            select(func.count())
            .select_from(TransportEvidence)
            .where(TransportEvidence.transport_task_id == transport_task_id)
        )
        position_projection_count = await db.scalar(
            select(func.count())
            .select_from(TransportDebugPositionProjection)
            .where(TransportDebugPositionProjection.source_transport_task_id == transport_task_id)
        )
        member_count = await db.scalar(
            select(func.count())
            .select_from(TransportMember)
            .where(TransportMember.transport_task_id == transport_task_id)
        )
        binding_count = await db.scalar(
            select(func.count())
            .select_from(TransportResourceBinding)
            .where(TransportResourceBinding.transport_task_id == transport_task_id)
        )
        active_binding_count = await db.scalar(
            select(func.count())
            .select_from(TransportResourceBinding)
            .where(
                TransportResourceBinding.transport_task_id == transport_task_id,
                TransportResourceBinding.released_at.is_(None),
            )
        )
        return (
            int(callback_receipt_count or 0),
            int(evidence_count or 0),
            int(position_projection_count or 0),
            int(member_count or 0),
            int(binding_count or 0),
            int(active_binding_count or 0),
        )

    async def delete_debug_task_aggregate(
        self,
        db: AsyncSession,
        transport_task_id: str,
    ) -> tuple[int, int, int, int, int, int]:
        """按依赖顺序删除指定 TransportTask 的完整本地 Transport 链路。"""

        position_projections = await db.execute(
            delete(TransportDebugPositionProjection).where(
                TransportDebugPositionProjection.source_transport_task_id == transport_task_id
            )
        )
        receipts = await db.execute(
            delete(TransportCallbackReceipt).where(
                TransportCallbackReceipt.response_data_json["transport_task_id"].as_string() == transport_task_id
            )
        )
        evidence = await db.execute(
            delete(TransportEvidence).where(TransportEvidence.transport_task_id == transport_task_id)
        )
        bindings = await db.execute(
            delete(TransportResourceBinding).where(TransportResourceBinding.transport_task_id == transport_task_id)
        )
        members = await db.execute(
            delete(TransportMember).where(TransportMember.transport_task_id == transport_task_id)
        )
        tasks = await db.execute(delete(TransportTask).where(TransportTask.transport_task_id == transport_task_id))
        return (
            int(receipts.rowcount or 0),
            int(evidence.rowcount or 0),
            int(position_projections.rowcount or 0),
            int(members.rowcount or 0),
            int(bindings.rowcount or 0),
            int(tasks.rowcount or 0),
        )

    async def add_aggregate(
        self,
        db: AsyncSession,
        task: TransportTask,
        members: list[TransportMember],
        bindings: list[TransportResourceBinding],
    ) -> None:
        db.add(task)
        # 未声明 ORM relationship 时 SQLAlchemy 不保证跨表插入顺序；
        # 先落主记录以满足 PostgreSQL 外键。
        await db.flush()
        db.add_all(members)
        db.add_all(bindings)
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
            TransportTask.status == "PENDING",
            TransportTask.submit_attempt_count < MAX_SUBMIT_ATTEMPTS,
            TransportTask.send_started_at.is_(None),
            or_(TransportTask.next_submit_at.is_(None), TransportTask.next_submit_at <= now),
            or_(TransportTask.submit_claim_until.is_(None), TransportTask.submit_claim_until < now),
        ]
        if excluded_task_ids:
            predicates.append(TransportTask.transport_task_id.not_in(excluded_task_ids))
        statement = (
            select(TransportTask)
            .where(*predicates)
            .order_by(
                TransportTask.next_submit_at.is_not(None).asc(),
                TransportTask.next_submit_at.asc(),
                TransportTask.id.asc(),
            )
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        task = await db.scalar(statement)
        if task is None:
            return None
        task.submit_claim_token = token
        task.submit_claim_until = claim_until
        task.submit_attempt_count += 1
        task.send_started_at = now
        task.updated_at = now
        await db.flush()
        return task

    async def release_unsent_claim(self, db: AsyncSession, task: TransportTask, *, token: str) -> None:
        if task.submit_claim_token != token or task.send_started_at is None or task.submit_attempt_count < 1:
            raise RuntimeError("transport unsent claim does not match")
        task.submit_claim_token = None
        task.submit_claim_until = None
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
                    TransportTask.status == "ACCEPTED",
                    TransportTask.result_deadline_at.is_not(None),
                    TransportTask.result_deadline_at <= now,
                )
                .order_by(TransportTask.result_deadline_at.asc(), TransportTask.id.asc())
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
                    TransportTask.status == "PENDING",
                    TransportTask.send_started_at.is_not(None),
                    TransportTask.submit_claim_until.is_not(None),
                    TransportTask.submit_claim_until < now,
                )
                .order_by(TransportTask.submit_claim_until.asc(), TransportTask.id.asc())
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
                    TransportTask.outcome_version > TransportTask.published_outcome_version,
                    TransportTask.outcome_json.is_not(None),
                    or_(TransportTask.outcome_claim_until.is_(None), TransportTask.outcome_claim_until < now),
                )
                .order_by(TransportTask.updated_at.asc(), TransportTask.id.asc())
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        for task in tasks:
            task.outcome_claim_token = token
            task.outcome_claim_until = claim_until
        await db.flush()
        return tasks

    async def release_bindings(self, db: AsyncSession, transport_task_id: str, *, now: datetime) -> None:
        await db.execute(
            update(TransportResourceBinding)
            .where(
                TransportResourceBinding.transport_task_id == transport_task_id,
                TransportResourceBinding.released_at.is_(None),
            )
            .values(released_at=now)
        )

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
            TransportCallbackReceipt.operation == operation,
            TransportCallbackReceipt.operation_id == operation_id,
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
            TransportEvidence.operation == operation,
            TransportEvidence.operation_id == operation_id,
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
            TransportEvidence.transport_task_id == transport_task_id,
            TransportEvidence.outcome_revision == outcome_revision,
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
                TransportEvidence.transport_task_id == transport_task_id,
                TransportEvidence.operation == TRANSPORT_POSITION_OPERATION,
                TransportEvidence.status == "APPLIED",
            )
        )
        return list(result)

    async def get_task_with_latest_evidence(
        self,
        db: AsyncSession,
        transport_task_id: str,
    ) -> tuple[TransportTask, TransportEvidence | None] | None:
        latest_evidence_id = (
            select(TransportEvidence.id)
            .where(TransportEvidence.transport_task_id == transport_task_id)
            .order_by(TransportEvidence.received_at.desc(), TransportEvidence.id.desc())
            .limit(1)
            .scalar_subquery()
        )
        result = await db.execute(
            select(TransportTask, TransportEvidence)
            .outerjoin(TransportEvidence, TransportEvidence.id == latest_evidence_id)
            .where(TransportTask.transport_task_id == transport_task_id)
        )
        row = result.one_or_none()
        return None if row is None else (row[0], row[1])

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
            select(TransportEvidence.id)
            .where(TransportEvidence.transport_task_id == TransportTask.transport_task_id)
            .order_by(TransportEvidence.received_at.desc(), TransportEvidence.id.desc())
            .limit(1)
            .correlate(TransportTask)
            .scalar_subquery()
        )
        statement = select(TransportTask, TransportEvidence).outerjoin(
            TransportEvidence,
            TransportEvidence.id == latest_evidence_id,
        )
        if cursor_created_at is not None and cursor_id is not None:
            statement = statement.where(
                or_(
                    TransportTask.created_at < cursor_created_at,
                    and_(TransportTask.created_at == cursor_created_at, TransportTask.id < cursor_id),
                )
            )
        if kind is not None:
            statement = statement.where(TransportTask.kind == kind)
        if status is not None:
            statement = statement.where(TransportTask.status == status)
        result = await db.execute(
            statement.order_by(TransportTask.created_at.desc(), TransportTask.id.desc()).limit(limit)
        )
        return [(row[0], row[1]) for row in result.all()]

    async def has_evidence(self, db: AsyncSession, transport_task_id: str) -> bool:
        evidence_id = await db.scalar(
            select(TransportEvidence.id).where(TransportEvidence.transport_task_id == transport_task_id).limit(1)
        )
        return evidence_id is not None

    async def get_evidence(
        self,
        db: AsyncSession,
        evidence_id: int,
        *,
        for_update: bool = False,
    ) -> TransportEvidence | None:
        statement = select(TransportEvidence).where(TransportEvidence.id == evidence_id)
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
                    TransportEvidence.status == "PENDING",
                    or_(TransportEvidence.claim_until.is_(None), TransportEvidence.claim_until < now),
                )
                .order_by(TransportEvidence.received_at.asc(), TransportEvidence.id.asc())
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
