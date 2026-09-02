"""插件 Transport Decision 与 client identity 的持久化 owner。"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from src.app.execution.models import TransportDecisionBinding
from src.database.base_repository import BaseRepository


class TransportDecisionBindingRepository(BaseRepository[TransportDecisionBinding]):
    def __init__(self) -> None:
        super().__init__(TransportDecisionBinding)

    async def lock_decision_identity(
        self,
        db: AsyncSession,
        *,
        line_run_epoch_id: int,
        correlation_id: str,
        step: str,
    ) -> None:
        _ = await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
            {"identity": f"transport-decision:{line_run_epoch_id}:{correlation_id}:{step}"},
        )

    async def lock_resource_fence(self, db: AsyncSession, *, line_run_epoch_id: int, resource_fence_id: str) -> None:
        _ = await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
            {"identity": f"transport-resource-fence:{line_run_epoch_id}:{resource_fence_id}"},
        )

    async def get_by_resource_step_for_update(
        self,
        db: AsyncSession,
        *,
        line_run_epoch_id: int,
        resource_fence_id: str,
        step: str,
    ) -> TransportDecisionBinding | None:
        columns = cast("Any", TransportDecisionBinding).__table__.c
        result = await db.execute(
            select(TransportDecisionBinding)
            .where(
                columns.line_run_epoch_id == line_run_epoch_id,
                columns.resource_fence_id == resource_fence_id,
                columns.step == step,
            )
            .with_for_update()
        )
        return result.scalars().first()

    async def get_by_decision_identity_for_update(
        self,
        db: AsyncSession,
        *,
        line_run_epoch_id: int,
        correlation_id: str,
        step: str,
    ) -> TransportDecisionBinding | None:
        columns = cast("Any", TransportDecisionBinding).__table__.c
        result = await db.execute(
            select(TransportDecisionBinding)
            .where(
                columns.line_run_epoch_id == line_run_epoch_id,
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
