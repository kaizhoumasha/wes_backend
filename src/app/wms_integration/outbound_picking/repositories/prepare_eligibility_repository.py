"""PickingTask prepare 的实时 WorkLine 事实读取。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from wes_plugin_sdk.prepare_policy import PrepareDeviceFact, PrepareRuntimeFacts

from src.app.device.repositories import DeviceStatusObservationRepository, device_status_observation_repository
from src.app.execution.repositories.position_projection_repository import PositionProjectionRepository
from src.app.workline.repositories import WorkLineRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class PickingWorklineFactsRepository:
    """从 WorkLine 冻结绑定与最新权威记录构造不可变事实，不判断业务准入。"""

    def __init__(
        self,
        *,
        workline_repository: WorkLineRepository | None = None,
        observation_repository: DeviceStatusObservationRepository | None = None,
    ) -> None:
        self._worklines = workline_repository or WorkLineRepository()
        self._observations = observation_repository or device_status_observation_repository

    async def read_facts(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
    ) -> PrepareRuntimeFacts:
        bindings = await self._worklines.list_bindings(db, workline_id)
        position_bindings = await self._worklines.list_position_bindings(db, workline_id)
        devices = []
        for binding in bindings:
            observation = await self._observations.get_latest_for_device(db, binding.device_code)
            devices.append(
                PrepareDeviceFact(
                    binding.contract_key,
                    binding.contract_version,
                    binding.status_max_age_ms,
                    observation.contract_key if observation else None,
                    observation.contract_version if observation else None,
                    observation.received_at if observation else None,
                    observation.mode if observation else None,
                    observation.status if observation else None,
                    observation.current_command_code if observation else None,
                )
            )

        projection_summary = await PositionProjectionRepository().get_active_workline_summary(db, workline_id)
        has_positioned_object = projection_summary["count"] > 0
        return PrepareRuntimeFacts(bool(position_bindings), tuple(devices), bool(has_positioned_object))


picking_workline_facts_repository = PickingWorklineFactsRepository()

__all__ = ["PickingWorklineFactsRepository", "picking_workline_facts_repository"]
