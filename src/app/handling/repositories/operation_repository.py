"""Handling operation repository。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import select

from src.app.handling.models import HandlingMove, HandlingOperation, HandlingStep
from src.database.base_repository import BaseRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class HandlingOperationRepository(BaseRepository[HandlingOperation]):
    """Handling operation 数据访问。"""

    def __init__(self) -> None:
        super().__init__(HandlingOperation)

    async def get_by_operation_key(self, db: AsyncSession, operation_key: str) -> HandlingOperation | None:
        columns = cast("Any", HandlingOperation).__table__.c
        result = await db.execute(select(HandlingOperation).where(columns.operation_key == operation_key))
        return cast("HandlingOperation | None", result.scalar_one_or_none())


class HandlingMoveRepository(BaseRepository[HandlingMove]):
    """Handling move 数据访问。"""

    def __init__(self) -> None:
        super().__init__(HandlingMove)

    async def list_by_operation_id(self, db: AsyncSession, operation_id: int) -> list[HandlingMove]:
        columns = cast("Any", HandlingMove).__table__.c
        result = await db.execute(
            select(HandlingMove).where(columns.operation_id == operation_id).order_by(columns.sequence_no)
        )
        return list(result.scalars().all())


class HandlingStepRepository(BaseRepository[HandlingStep]):
    """Handling step 数据访问。"""

    def __init__(self) -> None:
        super().__init__(HandlingStep)

    async def get_by_dispatch_key(self, db: AsyncSession, dispatch_key: str) -> HandlingStep | None:
        columns = cast("Any", HandlingStep).__table__.c
        result = await db.execute(select(HandlingStep).where(columns.dispatch_key == dispatch_key))
        return cast("HandlingStep | None", result.scalar_one_or_none())

    async def list_by_operation_id(self, db: AsyncSession, operation_id: int) -> list[HandlingStep]:
        columns = cast("Any", HandlingStep).__table__.c
        result = await db.execute(
            select(HandlingStep).where(columns.operation_id == operation_id).order_by(columns.sequence_no)
        )
        return list(result.scalars().all())


handling_operation_repository = HandlingOperationRepository()
handling_move_repository = HandlingMoveRepository()
handling_step_repository = HandlingStepRepository()


__all__ = [
    "HandlingMoveRepository",
    "HandlingOperationRepository",
    "HandlingStepRepository",
    "handling_move_repository",
    "handling_operation_repository",
    "handling_step_repository",
]
