"""回程 execution 的当前行、命令指针与物理 FIFO 查询。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import func, or_
from sqlmodel import select

from .return_model import BinLineReturn

_COLUMNS = cast("Any", BinLineReturn).__table__.c

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class ReturnRepository:
    async def add(self, db: AsyncSession, row: BinLineReturn) -> BinLineReturn:
        db.add(row)
        await db.flush()
        return row

    async def current_bin_for_update(self, db: AsyncSession, workline_id: int, bin_code: str) -> BinLineReturn | None:
        return await db.scalar(
            select(BinLineReturn)
            .where(
                _COLUMNS.workline_id == workline_id,
                _COLUMNS.bin_code == bin_code,
                _COLUMNS.return_state.not_in(("EXITED", "VOIDED")),
            )
            .with_for_update()
        )

    async def by_command_code_for_update(self, db: AsyncSession, command_code: str) -> BinLineReturn | None:
        return await db.scalar(
            select(BinLineReturn)
            .where(
                _COLUMNS.return_state.not_in(("EXITED", "VOIDED")),
                or_(_COLUMNS.scan3_command_code == command_code, _COLUMNS.scan4_command_code == command_code),
            )
            .with_for_update()
        )

    async def unfinished_prefix_for_update(
        self, db: AsyncSession, workline_id: int, *, limit: int = 4
    ) -> tuple[BinLineReturn, ...]:
        result = await db.scalars(
            select(BinLineReturn)
            .where(
                _COLUMNS.workline_id == workline_id,
                _COLUMNS.scan4_evidence_id.is_not(None),
                _COLUMNS.return_state.not_in(("EXITED", "VOIDED")),
            )
            .order_by(_COLUMNS.scan4_event_time, _COLUMNS.scan4_evidence_id)
            .limit(limit)
            .with_for_update()
        )
        return tuple(result.all())

    async def ready_prefix_for_update(
        self, db: AsyncSession, workline_id: int, *, limit: int = 4
    ) -> tuple[BinLineReturn, ...]:
        rows = await self.unfinished_prefix_for_update(db, workline_id, limit=limit)
        ready = []
        for row in rows:
            if row.return_state != "READY":
                break
            ready.append(row)
        return tuple(ready)

    async def requested_for_update(self, db: AsyncSession, workline_id: int) -> tuple[BinLineReturn, ...]:
        result = await db.scalars(
            select(BinLineReturn)
            .where(_COLUMNS.workline_id == workline_id, _COLUMNS.return_state == "RETURN_REQUESTED")
            .with_for_update()
        )
        return tuple(result.all())

    async def count_current(self, db: AsyncSession, workline_id: int) -> int:
        return int(
            await db.scalar(
                select(func.count(_COLUMNS.id)).where(
                    _COLUMNS.workline_id == workline_id,
                    _COLUMNS.return_state.not_in(("EXITED", "VOIDED")),
                )
            )
            or 0
        )


__all__ = ["ReturnRepository"]
