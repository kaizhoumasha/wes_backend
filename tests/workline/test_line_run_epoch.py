"""LineRunEpoch 与设备合同绑定的领域不变量。"""

from __future__ import annotations

from datetime import datetime

import pytest

from src.app.workline.models.line_run_epoch import (
    LineRunEpoch,
    LineRunEpochDeviceBinding,
    LineRunEpochStatus,
)
from src.app.workline.services.line_run_epoch_service import (
    ActiveLineRunEpochExistsError,
    DeviceBindingConflictError,
    LineRunEpochService,
)


class FakeLineRunEpochRepository:
    """仅模拟 Service 所需的持久化端口，不复制数据库行为。"""

    def __init__(self) -> None:
        self.active_epoch: LineRunEpoch | None = None
        self.bindings: dict[tuple[int, str], LineRunEpochDeviceBinding] = {}

    async def get_active_for_workline_for_update(self, _db: object, workline_id: int) -> LineRunEpoch | None:
        if self.active_epoch is not None and self.active_epoch.workline_id == workline_id:
            return self.active_epoch
        return None

    async def add_epoch(self, _db: object, epoch: LineRunEpoch) -> LineRunEpoch:
        epoch.id = 11
        self.active_epoch = epoch
        return epoch

    async def get_binding_for_update(
        self,
        _db: object,
        *,
        line_run_epoch_id: int,
        device_code: str,
    ) -> LineRunEpochDeviceBinding | None:
        return self.bindings.get((line_run_epoch_id, device_code))

    async def add_binding(
        self,
        _db: object,
        binding: LineRunEpochDeviceBinding,
    ) -> LineRunEpochDeviceBinding:
        binding.id = 21
        self.bindings[(binding.line_run_epoch_id, binding.device_code)] = binding
        return binding


def _service() -> tuple[LineRunEpochService, FakeLineRunEpochRepository]:
    repository = FakeLineRunEpochRepository()
    return LineRunEpochService(repository=repository), repository


@pytest.mark.asyncio
async def test_same_workline_rejects_second_active_epoch() -> None:
    service, _ = _service()
    started_at = datetime(2026, 8, 13)

    first = await service.create_epoch(
        object(),
        epoch_code="EPOCH-LINE-01-0001",
        workline_id=1,
        topology_digest="a" * 64,
        configuration_digest="b" * 64,
        started_at=started_at,
    )

    assert first.status == LineRunEpochStatus.ACTIVE
    with pytest.raises(ActiveLineRunEpochExistsError):
        await service.create_epoch(
            object(),
            epoch_code="EPOCH-LINE-01-0002",
            workline_id=1,
            topology_digest="c" * 64,
            configuration_digest="d" * 64,
            started_at=started_at,
        )


@pytest.mark.asyncio
async def test_binding_same_identity_is_idempotent_but_cannot_be_rewritten() -> None:
    service, _ = _service()
    epoch = await service.create_epoch(
        object(),
        epoch_code="EPOCH-LINE-01-0001",
        workline_id=1,
        topology_digest="a" * 64,
        configuration_digest="b" * 64,
        started_at=datetime(2026, 8, 13),
    )
    assert epoch.id is not None

    first = await service.bind_device(
        object(),
        line_run_epoch_id=epoch.id,
        device_id=7,
        device_code="ARM-01",
        contract_key="arm.pick",
        contract_version="2.0",
        status_max_age_ms=1_000,
        command_timeout_ms=30_000,
    )
    duplicate = await service.bind_device(
        object(),
        line_run_epoch_id=epoch.id,
        device_id=7,
        device_code="ARM-01",
        contract_key="arm.pick",
        contract_version="2.0",
        status_max_age_ms=1_000,
        command_timeout_ms=30_000,
    )

    assert duplicate is first
    with pytest.raises(DeviceBindingConflictError):
        await service.bind_device(
            object(),
            line_run_epoch_id=epoch.id,
            device_id=7,
            device_code="ARM-01",
            contract_key="arm.pick",
            contract_version="2.1",
            status_max_age_ms=1_000,
            command_timeout_ms=30_000,
        )
