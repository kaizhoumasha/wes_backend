"""Transport 自动联调轮次的数据库访问。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import and_, func, or_, select

from src.app.execution.models import InboundEvidence, InboundEvidenceConflict, InboundEvidenceKind
from src.app.resource.models.resource import RackBinMount, RackBinMountStatus
from src.app.transport.models import (
    TransportCallbackReceipt,
    TransportDebugRun,
    TransportDebugRunStep,
    TransportEvidence,
    TransportMember,
    TransportResourceBinding,
    TransportTask,
)

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession


class TransportDebugRunRepository:
    """只执行自动联调聚合 SQL 和 flush，不自行提交事务。"""

    async def add_run(
        self,
        db: AsyncSession,
        run: TransportDebugRun,
        first_step: TransportDebugRunStep,
    ) -> None:
        db.add(run)
        await db.flush()
        db.add(first_step)
        await db.flush()

    async def get_run(
        self,
        db: AsyncSession,
        run_id: str,
        *,
        for_update: bool = False,
    ) -> TransportDebugRun | None:
        columns = cast("Any", TransportDebugRun).__table__.c
        statement = select(TransportDebugRun).where(columns.run_id == run_id)
        if for_update:
            statement = statement.with_for_update()
        return await db.scalar(statement)

    async def get_active_run(
        self,
        db: AsyncSession,
        *,
        for_update: bool = False,
    ) -> TransportDebugRun | None:
        columns = cast("Any", TransportDebugRun).__table__.c
        statement = select(TransportDebugRun).where(columns.active_scope == "GLOBAL")
        if for_update:
            statement = statement.with_for_update()
        return await db.scalar(statement)

    async def claim_active_runs(
        self,
        db: AsyncSession,
        *,
        token: str,
        now: datetime,
        claim_until: datetime,
        limit: int,
    ) -> list[tuple[str, str]]:
        columns = cast("Any", TransportDebugRun).__table__.c
        runs = list(
            await db.scalars(
                select(TransportDebugRun)
                .where(
                    columns.active_scope == "GLOBAL",
                    or_(columns.claim_until.is_(None), columns.claim_until < now),
                )
                .order_by(columns.updated_at.asc(), columns.id.asc())
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        for run in runs:
            run.claim_token = token
            run.claim_until = claim_until
        await db.flush()
        return [(run.run_id, token) for run in runs]

    async def claim_run(
        self,
        db: AsyncSession,
        *,
        run_id: str,
        token: str,
        now: datetime,
        claim_until: datetime,
    ) -> bool:
        columns = cast("Any", TransportDebugRun).__table__.c
        run = await db.scalar(
            select(TransportDebugRun)
            .where(
                columns.run_id == run_id,
                columns.active_scope == "GLOBAL",
                or_(columns.claim_until.is_(None), columns.claim_until < now),
            )
            .with_for_update(skip_locked=True)
        )
        if run is None:
            return False
        run.claim_token = token
        run.claim_until = claim_until
        await db.flush()
        return True

    async def get_claimed_run(
        self,
        db: AsyncSession,
        *,
        run_id: str,
        token: str,
        now: datetime,
    ) -> TransportDebugRun | None:
        columns = cast("Any", TransportDebugRun).__table__.c
        return await db.scalar(
            select(TransportDebugRun)
            .where(
                columns.run_id == run_id,
                columns.claim_token == token,
                columns.claim_until > now,
            )
            .with_for_update()
        )

    async def get_current_step(
        self,
        db: AsyncSession,
        run: TransportDebugRun,
        *,
        for_update: bool = False,
    ) -> TransportDebugRunStep | None:
        columns = cast("Any", TransportDebugRunStep).__table__.c
        statement = select(TransportDebugRunStep).where(
            columns.run_id == run.run_id,
            columns.ordinal == run.current_step_ordinal,
        )
        if for_update:
            statement = statement.with_for_update()
        return await db.scalar(statement)

    async def add_step(self, db: AsyncSession, step: TransportDebugRunStep) -> None:
        db.add(step)
        await db.flush()

    async def list_steps(self, db: AsyncSession, run_id: str) -> list[TransportDebugRunStep]:
        columns = cast("Any", TransportDebugRunStep).__table__.c
        return list(
            await db.scalars(
                select(TransportDebugRunStep)
                .where(columns.run_id == run_id)
                .order_by(columns.ordinal.asc(), columns.id.asc())
            )
        )

    async def list_current_steps(
        self,
        db: AsyncSession,
        runs: list[TransportDebugRun],
    ) -> dict[str, TransportDebugRunStep]:
        if not runs:
            return {}
        step_columns = cast("Any", TransportDebugRunStep).__table__.c
        steps = await db.scalars(
            select(TransportDebugRunStep).where(
                or_(
                    *(
                        and_(
                            step_columns.run_id == run.run_id,
                            step_columns.ordinal == run.current_step_ordinal,
                        )
                        for run in runs
                    )
                )
            )
        )
        return {step.run_id: step for step in steps}

    async def list_recent_runs(
        self,
        db: AsyncSession,
        *,
        limit: int,
        before_created_at: datetime | None = None,
        before_id: int | None = None,
    ) -> list[TransportDebugRun]:
        columns = cast("Any", TransportDebugRun).__table__.c
        statement = select(TransportDebugRun)
        if before_created_at is not None or before_id is not None:
            if before_created_at is None or before_id is None:
                raise ValueError("history cursor requires both created_at and id")
            statement = statement.where(
                or_(
                    columns.created_at < before_created_at,
                    (columns.created_at == before_created_at) & (columns.id < before_id),
                )
            )
        return list(await db.scalars(statement.order_by(columns.created_at.desc(), columns.id.desc()).limit(limit)))

    async def get_transport_task(self, db: AsyncSession, transport_task_id: str) -> TransportTask | None:
        columns = cast("Any", TransportTask).__table__.c
        return await db.scalar(select(TransportTask).where(columns.transport_task_id == transport_task_id))

    async def list_transport_tasks(
        self,
        db: AsyncSession,
        transport_task_ids: list[str],
    ) -> dict[str, TransportTask]:
        if not transport_task_ids:
            return {}
        columns = cast("Any", TransportTask).__table__.c
        tasks = await db.scalars(select(TransportTask).where(columns.transport_task_id.in_(transport_task_ids)))
        return {task.transport_task_id: task for task in tasks}

    async def list_transport_members(self, db: AsyncSession, transport_task_id: str) -> list[TransportMember]:
        columns = cast("Any", TransportMember).__table__.c
        return list(
            await db.scalars(
                select(TransportMember)
                .where(columns.transport_task_id == transport_task_id)
                .order_by(columns.ordinal.asc(), columns.id.asc())
            )
        )

    async def list_active_mounts(
        self,
        db: AsyncSession,
        rack_id: str,
        *,
        for_update: bool = False,
    ) -> list[RackBinMount]:
        columns = cast("Any", RackBinMount).__table__.c
        statement = (
            select(RackBinMount)
            .where(
                columns.rack_code == rack_id,
                columns.mount_status == RackBinMountStatus.MOUNTED,
                columns.ended_at.is_(None),
            )
            .order_by(columns.rack_slot_code.asc(), columns.id.asc())
        )
        if for_update:
            statement = statement.with_for_update()
        return list(await db.scalars(statement))

    async def max_device_evidence_id(self, db: AsyncSession) -> int:
        columns = cast("Any", InboundEvidence).__table__.c
        maximum = await db.scalar(select(func.max(columns.id)).where(columns.kind == InboundEvidenceKind.DEVICE_EVENT))
        return int(maximum or 0)

    async def list_device_evidences_since(
        self,
        db: AsyncSession,
        *,
        received_at: datetime,
        evidence_high_watermark: int,
        after_received_at: datetime | None,
        after_id: int | None,
        limit: int,
    ) -> list[InboundEvidence]:
        columns = cast("Any", InboundEvidence).__table__.c
        statement = select(InboundEvidence).where(
            columns.kind == InboundEvidenceKind.DEVICE_EVENT,
            columns.id > evidence_high_watermark,
            columns.received_at >= received_at,
        )
        if after_received_at is not None or after_id is not None:
            if after_received_at is None or after_id is None:
                raise ValueError("evidence page cursor requires both received_at and id")
            statement = statement.where(
                or_(
                    columns.received_at > after_received_at,
                    and_(columns.received_at == after_received_at, columns.id > after_id),
                )
            )
        return list(await db.scalars(statement.order_by(columns.received_at.asc(), columns.id.asc()).limit(limit)))

    async def has_evidence_conflicts(self, db: AsyncSession, evidence_ids: list[int]) -> bool:
        if not evidence_ids:
            return False
        columns = cast("Any", InboundEvidenceConflict).__table__.c
        count = await db.scalar(
            select(func.count()).select_from(InboundEvidenceConflict).where(columns.first_evidence_id.in_(evidence_ids))
        )
        return bool(count)

    async def has_transport_evidence_conflict(self, db: AsyncSession, run_id: str) -> bool:
        evidence_columns = cast("Any", TransportEvidence).__table__.c
        step_columns = cast("Any", TransportDebugRunStep).__table__.c
        count = await db.scalar(
            select(func.count())
            .select_from(TransportEvidence)
            .join(
                TransportDebugRunStep,
                step_columns.transport_task_id == evidence_columns.transport_task_id,
            )
            .where(
                step_columns.run_id == run_id,
                evidence_columns.status == "CONFLICT",
            )
        )
        if count:
            return True
        receipt_columns = cast("Any", TransportCallbackReceipt).__table__.c
        task_ids = select(step_columns.transport_task_id).where(
            step_columns.run_id == run_id,
            step_columns.transport_task_id.is_not(None),
        )
        conflict_count = await db.scalar(
            select(func.count())
            .select_from(TransportCallbackReceipt)
            .where(
                or_(
                    receipt_columns.response_code == "CONFLICT",
                    receipt_columns.conflict_code.is_not(None),
                ),
                receipt_columns.response_data_json["transport_task_id"].as_string().in_(task_ids),
            )
        )
        return bool(conflict_count)

    async def has_pending_transport_evidence(self, db: AsyncSession, run_id: str) -> bool:
        evidence_columns = cast("Any", TransportEvidence).__table__.c
        step_columns = cast("Any", TransportDebugRunStep).__table__.c
        count = await db.scalar(
            select(func.count())
            .select_from(TransportEvidence)
            .join(
                TransportDebugRunStep,
                step_columns.transport_task_id == evidence_columns.transport_task_id,
            )
            .where(
                step_columns.run_id == run_id,
                evidence_columns.status == "PENDING",
            )
        )
        return bool(count)

    async def has_run_observed_evidence_conflict(self, db: AsyncSession, run_id: str) -> bool:
        steps = await self.list_steps(db, run_id)
        evidence_ids = [
            evidence_id
            for step in steps
            for item in step.observed_bins_json
            if isinstance(item, dict)
            for evidence_id in [item.get("evidence_id")]
            if isinstance(evidence_id, int)
        ]
        return await self.has_evidence_conflicts(db, evidence_ids)

    async def has_active_transport_binding(self, db: AsyncSession, run_id: str) -> bool:
        step_columns = cast("Any", TransportDebugRunStep).__table__.c
        binding_columns = cast("Any", TransportResourceBinding).__table__.c
        count = await db.scalar(
            select(func.count())
            .select_from(TransportResourceBinding)
            .join(
                TransportDebugRunStep,
                step_columns.transport_task_id == binding_columns.transport_task_id,
            )
            .where(
                step_columns.run_id == run_id,
                binding_columns.released_at.is_(None),
            )
        )
        return bool(count)

    async def list_active_transport_binding_task_ids(self, db: AsyncSession, run_id: str) -> set[str]:
        step_columns = cast("Any", TransportDebugRunStep).__table__.c
        binding_columns = cast("Any", TransportResourceBinding).__table__.c
        task_ids = await db.scalars(
            select(binding_columns.transport_task_id)
            .select_from(TransportResourceBinding)
            .join(
                TransportDebugRunStep,
                step_columns.transport_task_id == binding_columns.transport_task_id,
            )
            .where(
                step_columns.run_id == run_id,
                binding_columns.released_at.is_(None),
            )
        )
        return set(task_ids)

    async def is_task_linked_to_active_run(self, db: AsyncSession, transport_task_id: str) -> bool:
        run_columns = cast("Any", TransportDebugRun).__table__.c
        step_columns = cast("Any", TransportDebugRunStep).__table__.c
        count = await db.scalar(
            select(func.count())
            .select_from(TransportDebugRunStep)
            .join(TransportDebugRun, run_columns.run_id == step_columns.run_id)
            .where(
                step_columns.transport_task_id == transport_task_id,
                run_columns.status.in_(("RUNNING", "NEEDS_ATTENTION")),
            )
        )
        return bool(count)

    async def is_task_dispatch_allowed(self, db: AsyncSession, transport_task_id: str) -> bool:
        run_columns = cast("Any", TransportDebugRun).__table__.c
        step_columns = cast("Any", TransportDebugRunStep).__table__.c
        run = await db.scalar(
            select(TransportDebugRun)
            .select_from(TransportDebugRunStep)
            .join(TransportDebugRun, run_columns.run_id == step_columns.run_id)
            .where(step_columns.transport_task_id == transport_task_id)
        )
        if run is None:
            return True
        if run.status != "RUNNING":
            return False
        return (
            not await self.has_pending_transport_evidence(db, run.run_id)
            and not await self.has_transport_evidence_conflict(db, run.run_id)
            and not await self.has_run_observed_evidence_conflict(db, run.run_id)
        )


__all__ = ["TransportDebugRunRepository"]
