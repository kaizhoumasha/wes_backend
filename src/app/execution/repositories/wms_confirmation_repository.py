"""WmsConfirmation 持久化 owner。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Any, cast

from sqlalchemy import and_, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from src.app.execution.models.wms_confirmation import WmsConfirmation, WmsConfirmationStatus
from src.database.base_repository import BaseRepository


class WmsConfirmationRepository(BaseRepository[WmsConfirmation]):
    def __init__(self) -> None:
        super().__init__(WmsConfirmation)

    async def lock_identity(self, db: AsyncSession, operation: str, operation_id: str) -> None:
        identity = f"{operation}\x1f{operation_id}"
        _ = await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
            {"identity": identity},
        )

    async def get_by_identity_for_update(
        self,
        db: AsyncSession,
        operation: str,
        operation_id: str,
    ) -> WmsConfirmation | None:
        columns = cast("Any", WmsConfirmation).__table__.c
        result = await db.execute(
            select(WmsConfirmation)
            .where(columns.operation == operation, columns.operation_id == operation_id)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def add(self, db: AsyncSession, confirmation: WmsConfirmation) -> WmsConfirmation:
        db.add(confirmation)
        await db.flush()
        return confirmation

    async def list_for_execution(
        self,
        db: AsyncSession,
        material_execution_id: int,
    ) -> list[WmsConfirmation]:
        columns = cast("Any", WmsConfirmation).__table__.c
        result = await db.execute(
            select(WmsConfirmation)
            .where(columns.material_execution_id == material_execution_id)
            .order_by(columns.created_at, columns.id)
        )
        return list(result.scalars())

    async def list_for_executions_for_update(
        self,
        db: AsyncSession,
        *,
        material_execution_ids: tuple[int, ...],
        operation: str,
    ) -> list[WmsConfirmation]:
        if not material_execution_ids:
            return []
        columns = cast("Any", WmsConfirmation).__table__.c
        result = await db.execute(
            select(WmsConfirmation)
            .where(
                columns.material_execution_id.in_(material_execution_ids),
                columns.operation == operation,
            )
            .order_by(columns.created_at, columns.id)
            .with_for_update()
        )
        return list(result.scalars())

    async def claim_eligible(
        self,
        db: AsyncSession,
        *,
        now: datetime,
        claim_token: str,
        claim_expires_at: datetime,
        limit: int,
    ) -> list[WmsConfirmation]:
        columns = cast("Any", WmsConfirmation).__table__.c
        result = await db.execute(
            select(WmsConfirmation)
            .where(
                or_(
                    and_(
                        columns.status == WmsConfirmationStatus.PENDING,
                        or_(columns.next_attempt_at.is_(None), columns.next_attempt_at <= now),
                    ),
                    and_(
                        columns.status == WmsConfirmationStatus.DISPATCHING,
                        columns.claim_expires_at.is_not(None),
                        columns.claim_expires_at <= now,
                    ),
                ),
            )
            .order_by(
                columns.created_at,
                columns.id,
            )
            .limit(limit)
            .with_for_update(of=WmsConfirmation, skip_locked=True)
        )
        confirmations = list(result.scalars())
        for confirmation in confirmations:
            confirmation.status = WmsConfirmationStatus.DISPATCHING
            confirmation.claim_token = claim_token
            confirmation.claimed_at = now
            confirmation.claim_expires_at = claim_expires_at
            confirmation.last_dispatch_at = now
            confirmation.attempt_count += 1
        await db.flush()
        return confirmations

    async def get_claimed_for_update(
        self,
        db: AsyncSession,
        confirmation_id: int,
        claim_token: str,
    ) -> WmsConfirmation | None:
        columns = cast("Any", WmsConfirmation).__table__.c
        result = await db.execute(
            select(WmsConfirmation)
            .where(
                columns.id == confirmation_id,
                columns.status == WmsConfirmationStatus.DISPATCHING,
                columns.claim_token == claim_token,
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def flush(self, db: AsyncSession) -> None:
        await db.flush()


wms_confirmation_repository = WmsConfirmationRepository()

__all__ = ["WmsConfirmationRepository", "wms_confirmation_repository"]
