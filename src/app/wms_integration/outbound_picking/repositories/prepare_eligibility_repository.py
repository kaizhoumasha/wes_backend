"""PickingTask prepare 的实时 WorkLine 事实读取。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import exists, select
from wes_plugin_sdk.prepare_policy import PrepareDeviceFact, PrepareRuntimeFacts

from src.app.device.repositories import DeviceStatusObservationRepository, device_status_observation_repository
from src.app.execution.models import PositionProjection
from src.app.workline.models import WorklineSafetyIncident, WorklineSafetyIncidentStatus
from src.app.workline.repositories import LineRunEpochRepository, line_run_epoch_repository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class PickingWorklineFactsRepository:
    """从 Epoch 冻结绑定与最新权威记录构造不可变事实，不判断业务准入。"""

    def __init__(
        self,
        *,
        epoch_repository: LineRunEpochRepository | None = None,
        observation_repository: DeviceStatusObservationRepository | None = None,
    ) -> None:
        self._epochs = epoch_repository or line_run_epoch_repository
        self._observations = observation_repository or device_status_observation_repository

    async def read_facts(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        line_run_epoch_id: int,
    ) -> PrepareRuntimeFacts:
        incident = cast("Any", WorklineSafetyIncident).__table__.c
        has_incident = await db.scalar(
            select(
                exists().where(
                    incident.workline_id == workline_id,
                    incident.status == WorklineSafetyIncidentStatus.ACTIVE,
                )
            )
        )
        bindings = await self._epochs.list_bindings(db, line_run_epoch_id)
        position_bindings = await self._epochs.list_position_bindings(db, line_run_epoch_id)
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

        projection = cast("Any", PositionProjection).__table__.c
        has_positioned_object = await db.scalar(
            select(exists().where(projection.line_run_epoch_id == line_run_epoch_id))
        )
        return PrepareRuntimeFacts(
            bool(has_incident), bool(position_bindings), tuple(devices), bool(has_positioned_object)
        )


picking_workline_facts_repository = PickingWorklineFactsRepository()

__all__ = ["PickingWorklineFactsRepository", "picking_workline_facts_repository"]
