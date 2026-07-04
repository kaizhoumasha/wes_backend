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


ACTIVE_OR_FROZEN_RESERVATION_STATUSES = (
    BinCellReservationStatus.PLANNED.value,
    BinCellReservationStatus.RECONCILING.value,
)


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

    async def get_active_or_frozen_by_bin_cell(
        self,
        db: AsyncSession,
        *,
        bin_code: str,
        bin_cell_index: str,
    ) -> WorklineBinCellReservation | None:
        """查询料箱格位当前 active/frozen 预占。"""

        columns = cast("Any", WorklineBinCellReservation).__table__.c
        result = await db.execute(
            select(WorklineBinCellReservation).where(
                columns.bin_code == bin_code,
                columns.bin_cell_index == bin_cell_index,
                columns.reservation_status.in_(ACTIVE_OR_FROZEN_RESERVATION_STATUSES),
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

    async def list_active_or_frozen_by_bin_codes(
        self,
        db: AsyncSession,
        bin_codes: list[str],
    ) -> list[WorklineBinCellReservation]:
        """批量查询料箱当前 active/frozen 预占。"""

        if not bin_codes:
            return []

        columns = cast("Any", WorklineBinCellReservation).__table__.c
        result = await db.execute(
            select(WorklineBinCellReservation)
            .where(
                columns.bin_code.in_(bin_codes),
                columns.reservation_status.in_(ACTIVE_OR_FROZEN_RESERVATION_STATUSES),
            )
            .order_by(columns.bin_code.asc(), columns.bin_cell_index.asc(), columns.id.asc())
        )
        return list(result.scalars().all())

    async def list_active_or_frozen_by_pkg_codes(
        self,
        db: AsyncSession,
        pkg_codes: list[str],
    ) -> list[WorklineBinCellReservation]:
        """批量查询 PKG 当前 active/frozen 预占。"""

        unique_pkg_codes = list(dict.fromkeys(pkg_codes))
        if not unique_pkg_codes:
            return []

        columns = cast("Any", WorklineBinCellReservation).__table__.c
        result = await db.execute(
            select(WorklineBinCellReservation)
            .where(
                columns.pkg_code.in_(unique_pkg_codes),
                columns.reservation_status.in_(ACTIVE_OR_FROZEN_RESERVATION_STATUSES),
            )
            .order_by(columns.pkg_code.asc(), columns.bin_code.asc(), columns.bin_cell_index.asc(), columns.id.asc())
        )
        return list(result.scalars().all())

    async def list_expired_planned(
        self,
        db: AsyncSession,
        *,
        expired_at: datetime,
        limit: int,
    ) -> list[WorklineBinCellReservation]:
        """查询已过期且尚未发生物理投放的计划预占。"""

        if limit <= 0:
            return []

        columns = cast("Any", WorklineBinCellReservation).__table__.c
        result = await db.execute(
            select(WorklineBinCellReservation)
            .where(
                columns.reservation_status == BinCellReservationStatus.PLANNED.value,
                columns.expires_at.is_not(None),
                columns.expires_at <= expired_at,
            )
            .order_by(columns.expires_at.asc(), columns.id.asc())
            .limit(limit)
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

    async def mark_reconciling(
        self,
        db: AsyncSession,
        reservation: WorklineBinCellReservation,
        *,
        reason_code: str,
        evidence: dict[str, Any] | None = None,
    ) -> WorklineBinCellReservation:
        """将预占标记为需对账，保持 active/frozen 唯一占位。"""

        reservation.reservation_status = BinCellReservationStatus.RECONCILING
        metadata = dict(reservation.metadata_json or {})
        metadata["reconciling_reason_code"] = reason_code
        if evidence is not None:
            metadata["reconciling_evidence"] = evidence
        reservation.metadata_json = metadata
        evidence_json = dict(reservation.evidence_json or {})
        evidence_json["reconciling_reason_code"] = reason_code
        if evidence is not None:
            evidence_json["reconciling_evidence"] = evidence
        reservation.evidence_json = evidence_json
        db.add(reservation)
        return reservation


workline_bin_cell_reservation_repository = WorklineBinCellReservationRepository()


__all__ = ["WorklineBinCellReservationRepository", "workline_bin_cell_reservation_repository"]
