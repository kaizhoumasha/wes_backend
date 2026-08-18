"""换架业务身份与 Transport client identity 的持久化 owner。"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from src.app.execution.models import RackReplacementTransportBinding
from src.database.base_repository import BaseRepository


class RackReplacementTransportBindingRepository(BaseRepository[RackReplacementTransportBinding]):
    def __init__(self) -> None:
        super().__init__(RackReplacementTransportBinding)

    async def lock_business_identity(self, db: AsyncSession, rack_replacement_id: str, leg: str) -> None:
        _ = await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
            {"identity": f"{rack_replacement_id}:{leg}"},
        )

    async def lock_rack_fence(self, db: AsyncSession, *, line_run_epoch_id: int, current_rack_id: str) -> None:
        _ = await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
            {"identity": f"rack-fence:{line_run_epoch_id}:{current_rack_id}"},
        )

    async def get_old_out_fence_for_update(
        self,
        db: AsyncSession,
        *,
        line_run_epoch_id: int,
        current_rack_id: str,
    ) -> RackReplacementTransportBinding | None:
        columns = cast("Any", RackReplacementTransportBinding).__table__.c
        result = await db.execute(
            select(RackReplacementTransportBinding)
            .where(
                columns.line_run_epoch_id == line_run_epoch_id,
                columns.current_rack_id == current_rack_id,
                columns.leg == "OLD_OUT",
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_by_business_identity_for_update(
        self,
        db: AsyncSession,
        *,
        rack_replacement_id: str,
        leg: str,
    ) -> RackReplacementTransportBinding | None:
        columns = cast("Any", RackReplacementTransportBinding).__table__.c
        result = await db.execute(
            select(RackReplacementTransportBinding)
            .where(
                columns.rack_replacement_id == rack_replacement_id,
                columns.leg == leg,
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_by_client_request_id_for_update(
        self,
        db: AsyncSession,
        client_request_id: str,
    ) -> RackReplacementTransportBinding | None:
        columns = cast("Any", RackReplacementTransportBinding).__table__.c
        result = await db.execute(
            select(RackReplacementTransportBinding)
            .where(columns.client_request_id == client_request_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def get_by_client_request_id(
        self,
        db: AsyncSession,
        client_request_id: str,
    ) -> RackReplacementTransportBinding | None:
        columns = cast("Any", RackReplacementTransportBinding).__table__.c
        result = await db.execute(
            select(RackReplacementTransportBinding).where(columns.client_request_id == client_request_id)
        )
        return result.scalar_one_or_none()

    async def add(
        self,
        db: AsyncSession,
        binding: RackReplacementTransportBinding,
    ) -> RackReplacementTransportBinding:
        db.add(binding)
        await db.flush()
        return binding


rack_replacement_transport_binding_repository = RackReplacementTransportBindingRepository()

__all__ = [
    "RackReplacementTransportBindingRepository",
    "rack_replacement_transport_binding_repository",
]
