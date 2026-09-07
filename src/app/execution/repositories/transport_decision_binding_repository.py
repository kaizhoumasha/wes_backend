"""插件 Transport Decision 与 client identity 的持久化 owner。"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from src.app.execution.models import TransportDecisionBinding
from src.app.transport.models import TransportTask
from src.database.base_repository import BaseRepository


class TransportDecisionBindingRepository(BaseRepository[TransportDecisionBinding]):
    def __init__(self) -> None:
        super().__init__(TransportDecisionBinding)

    async def lock_decision_identity(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        correlation_id: str,
        step: str,
    ) -> None:
        _ = await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
            {"identity": f"transport-decision:{workline_id}:{correlation_id}:{step}"},
        )

    async def lock_resource_fence(self, db: AsyncSession, *, workline_id: int, resource_fence_id: str) -> None:
        _ = await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
            {"identity": f"transport-resource-fence:{workline_id}:{resource_fence_id}"},
        )

    async def get_by_resource_step_for_update(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        resource_fence_id: str,
        step: str,
        exclude_task_statuses: tuple[str, ...] = (),
        retain_transport_task_id: str | None = None,
    ) -> TransportDecisionBinding | None:
        columns = cast("Any", TransportDecisionBinding).__table__.c
        tasks = cast("Any", TransportTask).__table__.c
        statement = (
            select(TransportDecisionBinding)
            .outerjoin(TransportTask, tasks.client_request_id == columns.client_request_id)
            .where(
                columns.workline_id == workline_id, columns.resource_fence_id == resource_fence_id, columns.step == step
            )
        )
        if exclude_task_statuses:
            statement = statement.where(
                or_(
                    tasks.id.is_(None),
                    tasks.status.not_in(exclude_task_statuses),
                    tasks.transport_task_id == retain_transport_task_id,
                )
            )
        result = await db.execute(
            statement.order_by(columns.id.desc()).limit(1).with_for_update(of=TransportDecisionBinding)
        )
        return result.scalar_one_or_none()

    async def get_by_decision_identity_for_update(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        correlation_id: str,
        step: str,
    ) -> TransportDecisionBinding | None:
        columns = cast("Any", TransportDecisionBinding).__table__.c
        result = await db.execute(
            select(TransportDecisionBinding)
            .where(
                columns.workline_id == workline_id,
                columns.correlation_id == correlation_id,
                columns.step == step,
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_by_client_request_id_for_update(
        self,
        db: AsyncSession,
        client_request_id: str,
    ) -> TransportDecisionBinding | None:
        columns = cast("Any", TransportDecisionBinding).__table__.c
        result = await db.execute(
            select(TransportDecisionBinding)
            .where(columns.client_request_id == client_request_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def get_by_client_request_id(
        self,
        db: AsyncSession,
        client_request_id: str,
    ) -> TransportDecisionBinding | None:
        columns = cast("Any", TransportDecisionBinding).__table__.c
        result = await db.execute(
            select(TransportDecisionBinding).where(columns.client_request_id == client_request_id)
        )
        return result.scalar_one_or_none()

    async def add(
        self,
        db: AsyncSession,
        binding: TransportDecisionBinding,
    ) -> TransportDecisionBinding:
        db.add(binding)
        await db.flush()
        return binding


transport_decision_binding_repository = TransportDecisionBindingRepository()

__all__ = [
    "TransportDecisionBindingRepository",
    "transport_decision_binding_repository",
]
