"""插件 Transport Decision 与 client identity 的持久化 owner。"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from src.app.execution.locks import position_projection_lock_identity
from src.app.execution.models import TransportDecisionBinding
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

    async def lock_object_authority(
        self,
        db: AsyncSession,
        *,
        object_type: str,
        object_id: str,
    ) -> None:
        """Serialize ownership-changing binding creation with projection mutation."""

        _ = await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
            {"identity": position_projection_lock_identity(object_type, object_id)},
        )

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

    async def list_task_resource_fence_ids(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        picking_task_id: int,
        steps: tuple[str, ...],
    ) -> set[str]:
        columns = cast("Any", TransportDecisionBinding).__table__.c
        result = await db.scalars(
            select(columns.resource_fence_id).where(
                columns.workline_id == workline_id,
                columns.picking_task_id == picking_task_id,
                columns.step.in_(steps),
            )
        )
        return {str(resource_id) for resource_id in result.all()}

    async def list_task_member_bindings(
        self, db: AsyncSession, *, workline_id: int, picking_task_id: int, steps: tuple[str, ...]
    ) -> set[tuple[int, str]]:
        columns = cast("Any", TransportDecisionBinding).__table__.c
        result = await db.execute(
            select(columns.source_evidence_id, columns.resource_fence_id).where(
                columns.workline_id == workline_id,
                columns.picking_task_id == picking_task_id,
                columns.step.in_(steps),
            )
        )
        return {(int(evidence_id), str(rack_id)) for evidence_id, rack_id in result.all()}

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
