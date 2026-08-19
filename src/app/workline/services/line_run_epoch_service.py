"""LineRunEpoch 创建、关闭与设备合同冻结。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import TYPE_CHECKING, Any, Protocol, cast

from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from src.app.workline.epoch_digest import canonical_configuration_snapshot, configuration_digest, topology_digest
from src.app.workline.models.line_run_epoch import LineRunEpoch
from src.app.workline.repositories.line_run_epoch_repository import (
    LineRunEpochRepository,
    line_run_epoch_repository,
)

if TYPE_CHECKING:
    from src.app.workline.epoch_activation import (
        LineRunEpochDeviceBindingInput,
        LineRunEpochPositionBindingInput,
    )


class ActiveLineRunEpochExistsError(ValueError):
    """同一 WorkLine 已有活动 Epoch。"""


class LineRunEpochRepositoryPort(Protocol):
    """Service 所需的最小持久化端口。"""

    async def get_active_for_workline_for_update(self, db: Any, workline_id: int) -> LineRunEpoch | None: ...

    async def has_active_epoch(self, db: Any) -> bool: ...

    async def add_complete_epoch(
        self,
        db: Any,
        epoch: LineRunEpoch,
        device_bindings: tuple[LineRunEpochDeviceBindingInput, ...],
        position_bindings: tuple[LineRunEpochPositionBindingInput, ...],
    ) -> LineRunEpoch: ...

    async def close_epoch(self, db: Any, epoch: LineRunEpoch, *, closed_at: datetime) -> LineRunEpoch: ...


class UnclosedCommandRepositoryPort(Protocol):
    async def has_unclosed_for_epoch_for_update(self, db: Any, line_run_epoch_id: int) -> bool: ...


class LineRunEpochService:
    """维护 Epoch 单活动和 binding 不可改写不变量。"""

    def __init__(self, repository: LineRunEpochRepositoryPort | None = None) -> None:
        self._repository: LineRunEpochRepositoryPort = repository or cast(
            "LineRunEpochRepositoryPort", line_run_epoch_repository
        )

    async def assert_execution_worker_startable(self, db: AsyncSession | object) -> None:
        repository = cast("LineRunEpochRepositoryPort", self._repository)
        if await repository.has_active_epoch(db):
            raise ActiveLineRunEpochExistsError("execution worker cannot start while an ACTIVE LineRunEpoch exists")

    async def activate_epoch(
        self,
        db: AsyncSession | object,
        *,
        epoch_code: str,
        workline_id: int,
        plugin_key: str,
        plugin_version: str,
        flow_mode: str,
        configuration_snapshot: dict[str, object],
        device_bindings: tuple[LineRunEpochDeviceBindingInput, ...],
        position_bindings: tuple[LineRunEpochPositionBindingInput, ...],
        started_at: datetime,
    ) -> LineRunEpoch:
        active = await self._repository.get_active_for_workline_for_update(db, workline_id)
        if active is not None:
            raise ActiveLineRunEpochExistsError(f"workline {workline_id} 已存在活动 Epoch {active.epoch_code}")
        frozen_snapshot = canonical_configuration_snapshot(configuration_snapshot)
        epoch = LineRunEpoch(
            epoch_code=epoch_code,
            workline_id=workline_id,
            plugin_key=plugin_key,
            plugin_version=plugin_version,
            flow_mode=flow_mode,
            topology_digest=topology_digest(device_bindings, position_bindings),
            configuration_digest=configuration_digest(plugin_key, plugin_version, flow_mode, frozen_snapshot),
            configuration_snapshot_json=frozen_snapshot,
            started_at=started_at,
        )
        return await self._repository.add_complete_epoch(db, epoch, device_bindings, position_bindings)

    async def close_active_epoch(
        self,
        db: AsyncSession | object,
        *,
        workline_id: int,
        closed_at: datetime,
        command_repository: UnclosedCommandRepositoryPort,
    ) -> LineRunEpoch | None:
        active = await self._repository.get_active_for_workline_for_update(db, workline_id)
        if active is None:
            return None
        if active.id is None:
            raise RuntimeError("活动 Epoch 缺少持久化主键")
        if await command_repository.has_unclosed_for_epoch_for_update(db, active.id):
            raise ActiveLineRunEpochExistsError(f"Epoch {active.epoch_code} 仍存在 unclosed DeviceCommand")
        return await self._repository.close_epoch(db, active, closed_at=closed_at)


line_run_epoch_service = LineRunEpochService(repository=LineRunEpochRepository())

__all__ = [
    "ActiveLineRunEpochExistsError",
    "LineRunEpochService",
    "line_run_epoch_service",
]
