"""人工出库联调 run 的数据库访问。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import exists, select

from src.app.execution.models import InboundEvidence, WmsConfirmation
from src.app.wms_integration.outbound_picking.models import DirectPickExecution, PickingTask, PickingTaskBinSourceRack
from src.app.workline.models import WorkLine
from src.app.workline_integration_debug.models import IntegrationRun, IntegrationRunStep

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class IntegrationRunRepository:
    async def add_run(self, db: AsyncSession, run: IntegrationRun, steps: list[IntegrationRunStep]) -> None:
        db.add(run)
        await db.flush()
        db.add_all(steps)
        await db.flush()

    async def get_run(
        self,
        db: AsyncSession,
        run_id: str,
        *,
        for_update: bool = False,
    ) -> IntegrationRun | None:
        columns = cast("Any", IntegrationRun).__table__.c
        statement = select(IntegrationRun).where(columns.run_id == run_id)
        if for_update:
            statement = statement.with_for_update()
        return await db.scalar(statement)

    async def get_active_for_workline(
        self,
        db: AsyncSession,
        workline_id: int,
        *,
        for_update: bool = False,
    ) -> IntegrationRun | None:
        columns = cast("Any", IntegrationRun).__table__.c
        statement = select(IntegrationRun).where(columns.active_scope == f"WORKLINE:{workline_id}")
        if for_update:
            statement = statement.with_for_update()
        return await db.scalar(statement)

    async def list_recent(self, db: AsyncSession, *, limit: int) -> list[IntegrationRun]:
        columns = cast("Any", IntegrationRun).__table__.c
        return list(
            await db.scalars(select(IntegrationRun).order_by(columns.updated_at.desc(), columns.id.desc()).limit(limit))
        )

    async def list_steps(self, db: AsyncSession, run_id: str) -> list[IntegrationRunStep]:
        columns = cast("Any", IntegrationRunStep).__table__.c
        return list(
            await db.scalars(
                select(IntegrationRunStep)
                .where(columns.run_id == run_id)
                .order_by(columns.ordinal.asc(), columns.id.asc())
            )
        )

    async def add_step(self, db: AsyncSession, step: IntegrationRunStep) -> None:
        db.add(step)
        await db.flush()

    async def get_step_for_update(
        self,
        db: AsyncSession,
        run_id: str,
        phase: str,
    ) -> IntegrationRunStep | None:
        columns = cast("Any", IntegrationRunStep).__table__.c
        return await db.scalar(
            select(IntegrationRunStep).where(columns.run_id == run_id, columns.phase == phase).with_for_update()
        )

    async def get_step_by_client_request_id(
        self,
        db: AsyncSession,
        client_request_id: str,
        *,
        for_update: bool = False,
    ) -> IntegrationRunStep | None:
        columns = cast("Any", IntegrationRunStep).__table__.c
        statement = select(IntegrationRunStep).where(columns.client_request_id == client_request_id)
        if for_update:
            statement = statement.with_for_update()
        return await db.scalar(statement)

    async def next_ordinal(self, db: AsyncSession, run_id: str) -> int:
        steps = await self.list_steps(db, run_id)
        return 0 if not steps else steps[-1].ordinal + 1

    async def get_workline_by_code(
        self,
        db: AsyncSession,
        workline_code: str,
        *,
        for_update: bool = False,
    ) -> WorkLine | None:
        columns = cast("Any", WorkLine).__table__.c
        statement = select(WorkLine).where(columns.line_code == workline_code, columns.is_deleted.is_(False))
        if for_update:
            statement = statement.with_for_update()
        return await db.scalar(statement)

    async def get_picking_task(self, db: AsyncSession, task_id: str) -> PickingTask | None:
        columns = cast("Any", PickingTask).__table__.c
        return await db.scalar(select(PickingTask).where(columns.task_id == task_id))

    async def list_plan_resources(self, db: AsyncSession, picking_task_id: int) -> dict[str, list[dict[str, Any]]]:
        direct = cast("Any", DirectPickExecution).__table__.c
        bins = cast("Any", PickingTaskBinSourceRack).__table__.c
        direct_rows = (
            await db.execute(
                select(direct.rack_id, direct.rack_face, direct.slot_id, direct.plan_revision)
                .where(direct.picking_task_id == picking_task_id)
                .order_by(direct.id)
            )
        ).mappings()
        bin_rows = (
            await db.execute(
                select(bins.rack_id, bins.rack_face, bins.plan_revision)
                .where(bins.picking_task_id == picking_task_id)
                .order_by(bins.id)
            )
        ).mappings()
        return {
            "direct_picks": [dict(row) for row in direct_rows],
            "bin_source_racks": [dict(row) for row in bin_rows],
        }

    async def get_evidence(self, db: AsyncSession, evidence_id: int) -> InboundEvidence | None:
        columns = cast("Any", InboundEvidence).__table__.c
        return await db.scalar(select(InboundEvidence).where(columns.id == evidence_id))

    async def get_evidence_by_operation(
        self,
        db: AsyncSession,
        operation: str,
        operation_id: str,
        *,
        for_update: bool = False,
    ) -> InboundEvidence | None:
        columns = cast("Any", InboundEvidence).__table__.c
        statement = select(InboundEvidence).where(columns.operation == operation, columns.operation_id == operation_id)
        if for_update:
            statement = statement.with_for_update()
        return await db.scalar(statement)

    async def get_confirmation(self, db: AsyncSession, confirmation_id: int) -> WmsConfirmation | None:
        columns = cast("Any", WmsConfirmation).__table__.c
        return await db.scalar(select(WmsConfirmation).where(columns.id == confirmation_id))

    async def get_prepare_confirmation(
        self,
        db: AsyncSession,
        picking_task_id: int,
    ) -> WmsConfirmation | None:
        columns = cast("Any", WmsConfirmation).__table__.c
        return await db.scalar(
            select(WmsConfirmation).where(
                columns.picking_task_id == picking_task_id,
                columns.operation == "outbound.picking_task.prepare@v1",
            )
        )

    async def owns_operation(self, db: AsyncSession, *, workline_id: int, operation_id: str) -> bool:
        run_columns = cast("Any", IntegrationRun).__table__.c
        step_columns = cast("Any", IntegrationRunStep).__table__.c
        owned = await db.scalar(
            select(
                exists().where(
                    run_columns.run_id == step_columns.run_id,
                    run_columns.workline_id == workline_id,
                    step_columns.operation_id == operation_id,
                )
            )
        )
        return bool(owned)

    async def flush(self, db: AsyncSession) -> None:
        await db.flush()


integration_run_repository = IntegrationRunRepository()

__all__ = ["IntegrationRunRepository", "integration_run_repository"]
