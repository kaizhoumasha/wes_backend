"""人工 PickingTask prepare 的实时 WorkLine 准入读取。"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import exists, select

from src.app.device.repositories import DeviceStatusObservationRepository, device_status_observation_repository
from src.app.execution.models import PositionProjection
from src.app.workline.models import WorklineSafetyIncident, WorklineSafetyIncidentStatus
from src.app.workline.repositories import LineRunEpochRepository, line_run_epoch_repository

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession


class PickingWorklineEligibilityRepository:
    """从 Epoch 冻结绑定与最新权威事实实时判断准入，不保存可用性投影。"""

    def __init__(
        self,
        *,
        epoch_repository: LineRunEpochRepository | None = None,
        observation_repository: DeviceStatusObservationRepository | None = None,
    ) -> None:
        self._epochs = epoch_repository or line_run_epoch_repository
        self._observations = observation_repository or device_status_observation_repository

    async def is_ready(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        line_run_epoch_id: int,
        now: datetime,
    ) -> bool:
        incident = cast("Any", WorklineSafetyIncident).__table__.c
        has_incident = await db.scalar(
            select(
                exists().where(
                    incident.workline_id == workline_id,
                    incident.status == WorklineSafetyIncidentStatus.ACTIVE,
                )
            )
        )
        if has_incident:
            return False

        bindings = await self._epochs.list_bindings(db, line_run_epoch_id)
        position_bindings = await self._epochs.list_position_bindings(db, line_run_epoch_id)
        if not bindings or not position_bindings:
            return False
        for binding in bindings:
            observation = await self._observations.get_latest_for_device(db, binding.device_code)
            if (
                observation is None
                or observation.contract_key != binding.contract_key
                or observation.contract_version != binding.contract_version
                or observation.received_at < now - timedelta(milliseconds=binding.status_max_age_ms)
                or observation.mode != "AUTO"
                or observation.status != "IDLE"
                or observation.current_command_code is not None
            ):
                return False

        projection = cast("Any", PositionProjection).__table__.c
        has_positioned_object = await db.scalar(
            select(exists().where(projection.line_run_epoch_id == line_run_epoch_id))
        )
        return not bool(has_positioned_object)


picking_workline_eligibility_repository = PickingWorklineEligibilityRepository()

__all__ = ["PickingWorklineEligibilityRepository", "picking_workline_eligibility_repository"]
