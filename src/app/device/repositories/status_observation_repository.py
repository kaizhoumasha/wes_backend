"""设备状态观察持久化 owner。"""

from __future__ import annotations

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


device_status_observation_repository = DeviceStatusObservationRepository()

__all__ = ["DeviceStatusObservationRepository", "device_status_observation_repository"]
