"""LineRunEpoch 持久化访问。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from src.app.execution.locks import epoch_lifecycle_lock_identity
from src.app.workline.models.line_run_epoch import (
    LineRunEpoch,
    LineRunEpochDeviceBinding,
    LineRunEpochPositionBinding,
    LineRunEpochStatus,
)
from src.database.base_repository import BaseRepository

if TYPE_CHECKING:
    from src.app.workline.epoch_activation import (
        LineRunEpochDeviceBindingInput,
        LineRunEpochPositionBindingInput,
    )


class LineRunEpochRepository(BaseRepository[LineRunEpoch]):
    """活动 Epoch 与绑定的唯一数据库 owner。"""

    def __init__(self) -> None:
        super().__init__(LineRunEpoch)

    async def lock_start_request(self, db: AsyncSession, request_id: str) -> None:
        """按稳定 request identity 获取 PostgreSQL 事务级串行锁。"""

        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:request_id, 0))"),
            {"request_id": request_id},
        )

    async def lock_epoch_lifecycle(self, db: AsyncSession, line_run_epoch_id: int) -> None:
        """与 projection/owner 写入方共享 Epoch 关闭围栏。"""

        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_identity, 0))"),
            {"lock_identity": epoch_lifecycle_lock_identity(line_run_epoch_id)},
        )

    async def get_by_epoch_code_for_update(self, db: AsyncSession, epoch_code: str) -> LineRunEpoch | None:
        columns = cast("Any", LineRunEpoch).__table__.c
        result = await db.execute(select(LineRunEpoch).where(columns.epoch_code == epoch_code).with_for_update())
        return result.scalar_one_or_none()

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

    async def get_active_for_workline(self, db: AsyncSession, workline_id: int) -> LineRunEpoch | None:
        columns = cast("Any", LineRunEpoch).__table__.c
        result = await db.execute(
            select(LineRunEpoch).where(
                columns.workline_id == workline_id,
                columns.status == LineRunEpochStatus.ACTIVE,
            )
        )
        return result.scalar_one_or_none()

    async def has_active_epoch(self, db: AsyncSession) -> bool:
        columns = cast("Any", LineRunEpoch).__table__.c
        result = await db.execute(select(columns.id).where(columns.status == LineRunEpochStatus.ACTIVE).limit(1))
        return result.scalar_one_or_none() is not None

    async def add_complete_epoch(
        self,
        db: AsyncSession,
        epoch: LineRunEpoch,
        device_bindings: tuple[LineRunEpochDeviceBindingInput, ...],
        position_bindings: tuple[LineRunEpochPositionBindingInput, ...],
    ) -> LineRunEpoch:
        """在调用方事务内完整写入 Epoch 及其全部冻结 binding。"""

        db.add(epoch)
        await db.flush()
        if epoch.id is None:
            raise RuntimeError("LineRunEpoch flush 后缺少主键")
        db.add_all(
            [
                LineRunEpochDeviceBinding(
                    line_run_epoch_id=epoch.id,
                    device_id=binding.device_id,
                    device_code=binding.device_code,
                    device_role=binding.device_role,
                    endpoint_base_url=binding.endpoint_base_url,
                    contract_key=binding.contract_key,
                    contract_version=binding.contract_version,
                    status_max_age_ms=binding.status_max_age_ms,
                    command_timeout_ms=binding.command_timeout_ms,
                )
                for binding in device_bindings
            ]
            + [
                LineRunEpochPositionBinding(
                    line_run_epoch_id=epoch.id,
                    position_role=binding.position_role,
                    location_id=binding.location_id,
                    location_type=binding.location_type,
                )
                for binding in position_bindings
            ]
        )
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

    async def list_bindings(self, db: AsyncSession, line_run_epoch_id: int) -> list[LineRunEpochDeviceBinding]:
        columns = cast("Any", LineRunEpochDeviceBinding).__table__.c
        result = await db.execute(
            select(LineRunEpochDeviceBinding)
            .where(columns.line_run_epoch_id == line_run_epoch_id)
            .order_by(columns.device_role)
        )
        return list(result.scalars())

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
