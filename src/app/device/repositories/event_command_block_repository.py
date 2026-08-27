"""EVENT 命令阻塞因果的持久化 owner。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from src.app.device.models.event_command_block import (
    DeviceEventCommandBlock,
    DeviceEventCommandBlockStatus,
)
from src.database.base_repository import BaseRepository


class DeviceEventCommandBlockRepository(BaseRepository[DeviceEventCommandBlock]):
    """阻塞记录创建、锁定与历史查询。"""

    def __init__(self) -> None:
        super().__init__(DeviceEventCommandBlock)

    async def add_block(
        self,
        db: AsyncSession,
        block: DeviceEventCommandBlock,
    ) -> DeviceEventCommandBlock:
        db.add(block)
        await db.flush()
        return block

    async def get_by_id_for_update(
        self,
        db: AsyncSession,
        *,
        block_id: int,
        evidence_id: int,
    ) -> DeviceEventCommandBlock | None:
        columns = cast("Any", DeviceEventCommandBlock).__table__.c
        result = await db.execute(
            select(DeviceEventCommandBlock)
            .where(columns.id == block_id, columns.evidence_id == evidence_id)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_latest_for_evidence(
        self,
        db: AsyncSession,
        *,
        evidence_id: int,
    ) -> DeviceEventCommandBlock | None:
        columns = cast("Any", DeviceEventCommandBlock).__table__.c
        result = await db.execute(
            select(DeviceEventCommandBlock)
            .where(columns.evidence_id == evidence_id)
            .order_by(columns.blocked_at.desc(), columns.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def mark_requeued(
        self,
        db: AsyncSession,
        block: DeviceEventCommandBlock,
        *,
        requeued_at: datetime,
    ) -> None:
        block.status = DeviceEventCommandBlockStatus.REQUEUED
        block.requeued_at = requeued_at
        await db.flush()


device_event_command_block_repository = DeviceEventCommandBlockRepository()

__all__ = ["DeviceEventCommandBlockRepository", "device_event_command_block_repository"]
