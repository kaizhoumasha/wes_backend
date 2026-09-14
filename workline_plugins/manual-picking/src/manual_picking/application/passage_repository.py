"""本次料箱经过与两段 FIFO 的最小持久查询。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import func, or_
from sqlmodel import select

from .passage_model import ManualPickingPassage

_COLUMNS = cast("Any", ManualPickingPassage).__table__.c

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class PassageRepository:
    async def add(self, db: AsyncSession, passage: ManualPickingPassage) -> ManualPickingPassage:
        db.add(passage)
        await db.flush()
        return passage

    async def scan2_head_for_update(self, db: AsyncSession, workline_id: int) -> ManualPickingPassage | None:
        statement = (
            select(ManualPickingPassage)
            .where(
                _COLUMNS.workline_id == workline_id,
                _COLUMNS.scan2_evidence_id.is_(None),
                _COLUMNS.disposition == "OPEN",
            )
            .order_by(_COLUMNS.scan1_received_at, _COLUMNS.scan1_evidence_id)
            .limit(1)
            .with_for_update()
        )
        return (await db.execute(statement)).scalar_one_or_none()

    async def scan2_in_flight_for_update(self, db: AsyncSession, workline_id: int) -> tuple[ManualPickingPassage, ...]:
        statement = select(ManualPickingPassage).where(
            _COLUMNS.workline_id == workline_id,
            _COLUMNS.scan2_evidence_id.is_not(None),
            _COLUMNS.scan3_evidence_id.is_(None),
            _COLUMNS.disposition != "CLOSED",
        )
        return tuple((await db.execute(statement.with_for_update())).scalars().all())

    async def by_admission_operation_for_update(
        self, db: AsyncSession, operation_id: str
    ) -> ManualPickingPassage | None:
        statement = select(ManualPickingPassage).where(_COLUMNS.admission_operation_id == operation_id)
        return (await db.execute(statement.with_for_update())).scalar_one_or_none()

    async def waiting_for_completion_for_update(
        self, db: AsyncSession, *, workline_id: int, task_id: str, bin_code: str
    ) -> ManualPickingPassage | None:
        statement = select(ManualPickingPassage).where(
            _COLUMNS.workline_id == workline_id,
            _COLUMNS.task_id == task_id,
            _COLUMNS.bin_code == bin_code,
            _COLUMNS.scan2_evidence_id.is_not(None),
            _COLUMNS.admission_operation_id.is_not(None),
            _COLUMNS.wms_result.is_(None),
        )
        return (await db.execute(statement.with_for_update())).scalar_one_or_none()

    async def uniquely_completed_for_update(
        self, db: AsyncSession, *, workline_id: int, task_id: str, bin_code: str
    ) -> ManualPickingPassage | None:
        statement = (
            select(ManualPickingPassage)
            .where(
                _COLUMNS.workline_id == workline_id,
                _COLUMNS.task_id == task_id,
                _COLUMNS.bin_code == bin_code,
                _COLUMNS.wms_completed_evidence_id.is_not(None),
            )
            .limit(2)
            .with_for_update()
        )
        rows = list((await db.execute(statement)).scalars().all())
        return rows[0] if len(rows) == 1 else None

    async def scan1_unclosed_for_update(self, db: AsyncSession, workline_id: int) -> tuple[ManualPickingPassage, ...]:
        statement = select(ManualPickingPassage).where(
            _COLUMNS.workline_id == workline_id,
            _COLUMNS.disposition != "CLOSED",
        )
        return tuple((await db.execute(statement.with_for_update())).scalars().all())

    async def by_command_code_for_update(self, db: AsyncSession, command_code: str) -> ManualPickingPassage | None:
        statement = select(ManualPickingPassage).where(
            or_(
                _COLUMNS.scan1_command_code == command_code,
                _COLUMNS.scan2_command_code == command_code,
                _COLUMNS.scan2_fault_command_code == command_code,
                _COLUMNS.scan3_command_code == command_code,
                _COLUMNS.scan4_command_code == command_code,
            )
        )
        return (await db.execute(statement.with_for_update())).scalar_one_or_none()

    async def unique_open_bin_for_update(
        self, db: AsyncSession, *, workline_id: int, bin_code: str, after_scan2: bool | None = False
    ) -> ManualPickingPassage | None:
        statement = select(ManualPickingPassage).where(
            _COLUMNS.workline_id == workline_id,
            _COLUMNS.bin_code == bin_code,
            _COLUMNS.disposition != "CLOSED",
        )
        if after_scan2 is True:
            statement = statement.where(_COLUMNS.scan3_evidence_id.is_not(None))
        elif after_scan2 is False:
            statement = statement.where(_COLUMNS.scan3_evidence_id.is_(None))
        rows = list((await db.execute(statement.with_for_update())).scalars().all())
        return rows[0] if len(rows) == 1 else None

    async def ready_return_prefix_for_update(
        self, db: AsyncSession, workline_id: int, *, limit: int = 4
    ) -> tuple[ManualPickingPassage, ...]:
        rows = await self.unfinished_return_prefix_for_update(db, workline_id, limit=limit)
        ready: list[ManualPickingPassage] = []
        for row in rows:
            if row.return_state != "READY":
                break
            ready.append(row)
        return tuple(ready)

    async def unfinished_return_prefix_for_update(
        self, db: AsyncSession, workline_id: int, *, limit: int = 4
    ) -> tuple[ManualPickingPassage, ...]:
        statement = (
            select(ManualPickingPassage)
            .where(
                _COLUMNS.workline_id == workline_id,
                _COLUMNS.scan4_evidence_id.is_not(None),
                _COLUMNS.return_state != "RETURNED",
            )
            .order_by(_COLUMNS.scan4_received_at, _COLUMNS.scan4_evidence_id)
            .limit(limit)
            .with_for_update()
        )
        return tuple((await db.execute(statement)).scalars().all())

    async def get_unfinished_workload_summary(self, db: AsyncSession, workline_id: int) -> dict[str, object]:
        statement = select(func.count(_COLUMNS.id)).where(
            _COLUMNS.workline_id == workline_id,
            _COLUMNS.disposition != "CLOSED",
        )
        count = cast("int", (await db.execute(statement)).scalar_one())
        return {
            "count": count,
            "sample": "manual-picking passage" if count else None,
        }


__all__ = ["PassageRepository"]
