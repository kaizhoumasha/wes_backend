"""Transport 聚合的数据库访问。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import or_, select, update

from src.app.transport.contracts import MAX_SUBMIT_ATTEMPTS
from src.app.transport.models import (
    TransportEvidence,
    TransportMember,
    TransportPositionProjection,
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
            statement = statement.with_for_update()
        return await db.scalar(statement)

    async def list_members(self, db: AsyncSession, transport_task_id: str) -> list[TransportMember]:
        result = await db.scalars(
            select(TransportMember)
            .where(TransportMember.transport_task_id == transport_task_id)
            .order_by(TransportMember.ordinal.asc(), TransportMember.id.asc())
        )
        return list(result)

    async def get_projection(
        self,
        db: AsyncSession,
        object_type: str,
        object_id: str,
        *,
        for_update: bool = False,
    ) -> TransportPositionProjection | None:
        statement = select(TransportPositionProjection).where(
            TransportPositionProjection.object_type == object_type,
            TransportPositionProjection.object_id == object_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return await db.scalar(statement)

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

    async def claim_pending_tasks(
        self,
        db: AsyncSession,
        *,
        limit: int,
        token: str,
        now: datetime,
        claim_until: datetime,
    ) -> list[TransportTask]:
        statement = (
            select(TransportTask)
            .where(
                TransportTask.status == "PENDING",
                TransportTask.submit_attempt_count < MAX_SUBMIT_ATTEMPTS,
                TransportTask.send_started_at.is_(None),
                or_(TransportTask.next_submit_at.is_(None), TransportTask.next_submit_at <= now),
                or_(TransportTask.submit_claim_until.is_(None), TransportTask.submit_claim_until < now),
            )
            .order_by(TransportTask.next_submit_at.asc().nullsfirst(), TransportTask.id.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        tasks = list(await db.scalars(statement))
        for task in tasks:
            task.submit_claim_token = token
            task.submit_claim_until = claim_until
        await db.flush()
        return tasks

    async def mark_send_started(
        self,
        db: AsyncSession,
        *,
        transport_task_id: str,
        token: str,
        now: datetime,
    ) -> TransportTask | None:
        task = await self.get_task(db, transport_task_id, for_update=True)
        if (
            task is None
            or task.status != "PENDING"
            or task.submit_claim_token != token
            or task.send_started_at is not None
            or task.submit_attempt_count >= MAX_SUBMIT_ATTEMPTS
        ):
            return None
        task.submit_attempt_count += 1
        task.send_started_at = now
        task.updated_at = now
        await db.flush()
        return task

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

    async def get_evidence_by_event_id(
        self,
        db: AsyncSession,
        event_id: str,
        *,
        for_update: bool = False,
    ) -> TransportEvidence | None:
        statement = select(TransportEvidence).where(TransportEvidence.event_id == event_id)
        if for_update:
            statement = statement.with_for_update()
        return await db.scalar(statement)

    async def get_evidence(
        self,
        db: AsyncSession,
        evidence_id: int,
        *,
        for_update: bool = False,
    ) -> TransportEvidence | None:
        statement = select(TransportEvidence).where(TransportEvidence.id == evidence_id)
        if for_update:
            statement = statement.with_for_update()
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
