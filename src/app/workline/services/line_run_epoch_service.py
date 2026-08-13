"""LineRunEpoch 创建、关闭与设备合同冻结。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from src.app.workline.models.line_run_epoch import LineRunEpoch, LineRunEpochDeviceBinding
from src.app.workline.repositories.line_run_epoch_repository import (
    LineRunEpochRepository,
    line_run_epoch_repository,
)


class ActiveLineRunEpochExistsError(ValueError):
    """同一 WorkLine 已有活动 Epoch。"""


class DeviceBindingConflictError(ValueError):
    """同一 Epoch 的设备绑定被请求改写。"""


class LineRunEpochRepositoryPort(Protocol):
    """Service 所需的最小持久化端口。"""

    async def get_active_for_workline_for_update(self, db: object, workline_id: int) -> LineRunEpoch | None: ...

    async def add_epoch(self, db: object, epoch: LineRunEpoch) -> LineRunEpoch: ...

    async def close_epoch(self, db: object, epoch: LineRunEpoch, *, closed_at: datetime) -> LineRunEpoch: ...

    async def get_binding_for_update(
        self,
        db: object,
        *,
        line_run_epoch_id: int,
        device_code: str,
    ) -> LineRunEpochDeviceBinding | None: ...

    async def add_binding(
        self,
        db: object,
        binding: LineRunEpochDeviceBinding,
    ) -> LineRunEpochDeviceBinding: ...


class SendableCommandRepositoryPort(Protocol):
    async def has_sendable_for_epoch_for_update(self, db: object, line_run_epoch_id: int) -> bool: ...


class LineRunEpochService:
    """维护 Epoch 单活动和 binding 不可改写不变量。"""

    def __init__(self, repository: LineRunEpochRepositoryPort | None = None) -> None:
        self._repository = repository or line_run_epoch_repository

    async def create_epoch(
        self,
        db: AsyncSession | object,
        *,
        epoch_code: str,
        workline_id: int,
        topology_digest: str,
        configuration_digest: str,
        started_at: datetime,
    ) -> LineRunEpoch:
        active = await self._repository.get_active_for_workline_for_update(db, workline_id)
        if active is not None:
            raise ActiveLineRunEpochExistsError(f"workline {workline_id} 已存在活动 Epoch {active.epoch_code}")
        epoch = LineRunEpoch(
            epoch_code=epoch_code,
            workline_id=workline_id,
            topology_digest=topology_digest,
            configuration_digest=configuration_digest,
            started_at=started_at,
        )
        return await self._repository.add_epoch(db, epoch)

    async def bind_device(
        self,
        db: AsyncSession | object,
        *,
        line_run_epoch_id: int,
        device_id: int,
        device_code: str,
        contract_key: str,
        contract_version: str,
        status_max_age_ms: int,
        command_timeout_ms: int,
    ) -> LineRunEpochDeviceBinding:
        candidate = LineRunEpochDeviceBinding(
            line_run_epoch_id=line_run_epoch_id,
            device_id=device_id,
            device_code=device_code,
            contract_key=contract_key,
            contract_version=contract_version,
            status_max_age_ms=status_max_age_ms,
            command_timeout_ms=command_timeout_ms,
        )
        existing = await self._repository.get_binding_for_update(
            db,
            line_run_epoch_id=line_run_epoch_id,
            device_code=device_code,
        )
        if existing is None:
            return await self._repository.add_binding(db, candidate)
        if existing.identity_tuple() == candidate.identity_tuple():
            return existing
        raise DeviceBindingConflictError(f"Epoch {line_run_epoch_id} 的设备 {device_code} 已冻结为其他合同绑定")

    async def close_active_epoch(
        self,
        db: AsyncSession | object,
        *,
        workline_id: int,
        closed_at: datetime,
        command_repository: SendableCommandRepositoryPort,
    ) -> LineRunEpoch | None:
        active = await self._repository.get_active_for_workline_for_update(db, workline_id)
        if active is None:
            return None
        if active.id is None:
            raise RuntimeError("活动 Epoch 缺少持久化主键")
        if await command_repository.has_sendable_for_epoch_for_update(db, active.id):
            raise ActiveLineRunEpochExistsError(f"Epoch {active.epoch_code} 仍存在 sendable DeviceCommand")
        return await self._repository.close_epoch(db, active, closed_at=closed_at)


line_run_epoch_service = LineRunEpochService(repository=LineRunEpochRepository())

__all__ = [
    "ActiveLineRunEpochExistsError",
    "DeviceBindingConflictError",
    "LineRunEpochService",
    "line_run_epoch_service",
]
