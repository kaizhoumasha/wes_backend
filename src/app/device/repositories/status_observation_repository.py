"""设备状态观察持久化 owner。"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from src.app.device.models.evidence import DeviceStatusObservation
from src.database.base_repository import BaseRepository


class DeviceStatusObservationRepository(BaseRepository[DeviceStatusObservation]):
    def __init__(self) -> None:
        super().__init__(DeviceStatusObservation)

    async def add_status_observation(
        self,
        db: AsyncSession,
        observation: DeviceStatusObservation,
    ) -> DeviceStatusObservation:
        db.add(observation)
        await db.flush()
        return observation

    async def get_latest_for_device(
        self,
        db: AsyncSession,
        device_code: str,
    ) -> DeviceStatusObservation | None:
        columns = cast("Any", DeviceStatusObservation).__table__.c
        result = await db.execute(
            select(DeviceStatusObservation)
            .where(columns.device_code == device_code)
            .order_by(columns.received_at.desc(), columns.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()


device_status_observation_repository = DeviceStatusObservationRepository()

__all__ = ["DeviceStatusObservationRepository", "device_status_observation_repository"]
