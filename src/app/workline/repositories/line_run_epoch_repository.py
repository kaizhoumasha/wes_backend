"""LineRunEpoch 持久化访问。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from src.app.workline.models.line_run_epoch import (
    LineRunEpoch,
    LineRunEpochDeviceBinding,
    LineRunEpochPositionBinding,
    LineRunEpochStatus,
)
from src.database.base_repository import BaseRepository


class LineRunEpochRepository(BaseRepository[LineRunEpoch]):
    """活动 Epoch 与绑定的唯一数据库 owner。"""

    def __init__(self) -> None:
        super().__init__(LineRunEpoch)

    async def get_active_for_workline_for_update(
        self,
        db: AsyncSession,
        workline_id: int,
    ) -> LineRunEpoch | None:
        columns = cast("Any", LineRunEpoch).__table__.c
        result = await db.execute(
            select(LineRunEpoch)
            .where(columns.workline_id == workline_id, columns.status == LineRunEpochStatus.ACTIVE)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def has_active_epoch(self, db: AsyncSession) -> bool:
        columns = cast("Any", LineRunEpoch).__table__.c
        result = await db.execute(select(columns.id).where(columns.status == LineRunEpochStatus.ACTIVE).limit(1))
        return result.scalar_one_or_none() is not None

    async def add_epoch(self, db: AsyncSession, epoch: LineRunEpoch) -> LineRunEpoch:
        db.add(epoch)
        await db.flush()
        return epoch

    async def get_by_id_for_update(self, db: AsyncSession, line_run_epoch_id: int) -> LineRunEpoch | None:
        columns = cast("Any", LineRunEpoch).__table__.c
        result = await db.execute(select(LineRunEpoch).where(columns.id == line_run_epoch_id).with_for_update())
        return result.scalar_one_or_none()

    async def close_epoch(
        self,
        db: AsyncSession,
        epoch: LineRunEpoch,
        *,
        closed_at: datetime,
    ) -> LineRunEpoch:
        epoch.status = LineRunEpochStatus.CLOSED
        epoch.closed_at = closed_at
        await db.flush()
        return epoch

    async def get_binding_for_update(
        self,
        db: AsyncSession,
        *,
        line_run_epoch_id: int,
        device_code: str,
    ) -> LineRunEpochDeviceBinding | None:
        columns = cast("Any", LineRunEpochDeviceBinding).__table__.c
        result = await db.execute(
            select(LineRunEpochDeviceBinding)
            .where(
                columns.line_run_epoch_id == line_run_epoch_id,
                columns.device_code == device_code,
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_binding_for_command_creation(
        self,
        db: AsyncSession,
        *,
        line_run_epoch_id: int,
        device_code: str,
    ) -> LineRunEpochDeviceBinding | None:
        epoch_columns = cast("Any", LineRunEpoch).__table__.c
        binding_columns = cast("Any", LineRunEpochDeviceBinding).__table__.c
        epoch = await db.execute(
            select(LineRunEpoch)
            .where(
                epoch_columns.id == line_run_epoch_id,
                epoch_columns.status == LineRunEpochStatus.ACTIVE,
            )
            .with_for_update()
        )
        if epoch.scalar_one_or_none() is None:
            return None
        result = await db.execute(
            select(LineRunEpochDeviceBinding).where(
                binding_columns.line_run_epoch_id == line_run_epoch_id,
                binding_columns.device_code == device_code,
            )
        )
        return result.scalar_one_or_none()

    async def get_binding_by_role_for_update(
        self,
        db: AsyncSession,
        *,
        line_run_epoch_id: int,
        device_role: str,
    ) -> LineRunEpochDeviceBinding | None:
        columns = cast("Any", LineRunEpochDeviceBinding).__table__.c
        result = await db.execute(
            select(LineRunEpochDeviceBinding)
            .where(
                columns.line_run_epoch_id == line_run_epoch_id,
                columns.device_role == device_role,
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_binding_for_dispatch(
        self,
        db: AsyncSession,
        *,
        line_run_epoch_id: int,
        device_code: str,
    ) -> LineRunEpochDeviceBinding | None:
        columns = cast("Any", LineRunEpochDeviceBinding).__table__.c
        result = await db.execute(
            select(LineRunEpochDeviceBinding).where(
                columns.line_run_epoch_id == line_run_epoch_id,
                columns.device_code == device_code,
            )
        )
        return result.scalar_one_or_none()

    async def get_active_binding_for_device(
        self,
        db: AsyncSession,
        device_code: str,
    ) -> LineRunEpochDeviceBinding | None:
        epoch_columns = cast("Any", LineRunEpoch).__table__.c
        binding_columns = cast("Any", LineRunEpochDeviceBinding).__table__.c
        result = await db.execute(
            select(LineRunEpochDeviceBinding)
            .join(LineRunEpoch, epoch_columns.id == binding_columns.line_run_epoch_id)
            .where(
                binding_columns.device_code == device_code,
                epoch_columns.status == LineRunEpochStatus.ACTIVE,
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def add_binding(
        self,
        db: AsyncSession,
        binding: LineRunEpochDeviceBinding,
    ) -> LineRunEpochDeviceBinding:
        db.add(binding)
        await db.flush()
        return binding

    async def list_bindings(self, db: AsyncSession, line_run_epoch_id: int) -> list[LineRunEpochDeviceBinding]:
        columns = cast("Any", LineRunEpochDeviceBinding).__table__.c
        result = await db.execute(
            select(LineRunEpochDeviceBinding)
            .where(columns.line_run_epoch_id == line_run_epoch_id)
            .order_by(columns.device_role)
        )
        return list(result.scalars())

    async def get_position_binding_for_update(
        self,
        db: AsyncSession,
        *,
        line_run_epoch_id: int,
        position_role: str,
    ) -> LineRunEpochPositionBinding | None:
        columns = cast("Any", LineRunEpochPositionBinding).__table__.c
        result = await db.execute(
            select(LineRunEpochPositionBinding)
            .where(columns.line_run_epoch_id == line_run_epoch_id, columns.position_role == position_role)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_position_binding_by_location_for_update(
        self,
        db: AsyncSession,
        *,
        line_run_epoch_id: int,
        location_id: str,
    ) -> LineRunEpochPositionBinding | None:
        columns = cast("Any", LineRunEpochPositionBinding).__table__.c
        result = await db.execute(
            select(LineRunEpochPositionBinding)
            .where(columns.line_run_epoch_id == line_run_epoch_id, columns.location_id == location_id)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def add_position_binding(
        self,
        db: AsyncSession,
        binding: LineRunEpochPositionBinding,
    ) -> LineRunEpochPositionBinding:
        db.add(binding)
        await db.flush()
        return binding

    async def list_position_bindings(
        self, db: AsyncSession, line_run_epoch_id: int
    ) -> list[LineRunEpochPositionBinding]:
        columns = cast("Any", LineRunEpochPositionBinding).__table__.c
        result = await db.execute(
            select(LineRunEpochPositionBinding)
            .where(columns.line_run_epoch_id == line_run_epoch_id)
            .order_by(columns.position_role)
        )
        return list(result.scalars())


line_run_epoch_repository = LineRunEpochRepository()

__all__ = ["LineRunEpochRepository", "line_run_epoch_repository"]
