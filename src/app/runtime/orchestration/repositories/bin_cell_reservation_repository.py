"""工作线料箱格位预占 Repository。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import select

from src.app.runtime.orchestration.models.bin_cell_reservation import (
    BinCellReservationStatus,
    WorklineBinCellReservation,
)
from src.database.base_repository import BaseRepository

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession


class WorklineBinCellReservationRepository(BaseRepository[WorklineBinCellReservation]):
    """工作线料箱格位预占 Repository。"""

    def __init__(self) -> None:
        super().__init__(WorklineBinCellReservation)

    async def get_active_by_bin_cell(
        self,
        db: AsyncSession,
        *,
        bin_code: str,
        bin_cell_index: str,
    ) -> WorklineBinCellReservation | None:
        """查询料箱格位当前 active 预占。"""

        columns = cast("Any", WorklineBinCellReservation).__table__.c
        result = await db.execute(
            select(WorklineBinCellReservation).where(
                columns.bin_code == bin_code,
                columns.bin_cell_index == bin_cell_index,
                columns.reservation_status == BinCellReservationStatus.PLANNED.value,
            )
        )
        return result.scalar_one_or_none()

    async def list_active_by_bin_codes(
        self,
        db: AsyncSession,
        bin_codes: list[str],
    ) -> list[WorklineBinCellReservation]:
        """批量查询料箱当前 active 预占。"""

        if not bin_codes:
            return []

        columns = cast("Any", WorklineBinCellReservation).__table__.c
        result = await db.execute(
            select(WorklineBinCellReservation)
            .where(
                columns.bin_code.in_(bin_codes),
                columns.reservation_status == BinCellReservationStatus.PLANNED.value,
            )
            .order_by(columns.bin_code.asc(), columns.bin_cell_index.asc(), columns.id.asc())
        )
        return list(result.scalars().all())

    async def mark_consumed(
        self,
        db: AsyncSession,
        reservation: WorklineBinCellReservation,
        *,
        consumed_at: datetime,
    ) -> WorklineBinCellReservation:
        """将预占标记为已被物理占用消耗。"""

        reservation.reservation_status = BinCellReservationStatus.CONSUMED
        reservation.consumed_at = consumed_at
        db.add(reservation)
        return reservation

    async def mark_released(
        self,
        db: AsyncSession,
        reservation: WorklineBinCellReservation,
        *,
        released_at: datetime,
    ) -> WorklineBinCellReservation:
        """释放未消耗的预占。"""

        reservation.reservation_status = BinCellReservationStatus.RELEASED
        reservation.released_at = released_at
        db.add(reservation)
        return reservation


workline_bin_cell_reservation_repository = WorklineBinCellReservationRepository()


__all__ = ["WorklineBinCellReservationRepository", "workline_bin_cell_reservation_repository"]
