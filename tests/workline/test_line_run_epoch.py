"""LineRunEpoch 与设备合同绑定的领域不变量。"""

from __future__ import annotations

from datetime import datetime

import pytest

from src.app.workline.models.line_run_epoch import (
    LineRunEpoch,
    LineRunEpochDeviceBinding,
    LineRunEpochPositionBinding,
    LineRunEpochStatus,
)
from src.app.workline.services.line_run_epoch_service import (
    ActiveLineRunEpochExistsError,
    DeviceBindingConflictError,
    LineRunEpochService,
    PositionBindingConflictError,
)


class FakeLineRunEpochRepository:
    """仅模拟 Service 所需的持久化端口，不复制数据库行为。"""

    def __init__(self) -> None:
        self.active_epoch: LineRunEpoch | None = None
        self.bindings: dict[tuple[int, str], LineRunEpochDeviceBinding] = {}
        self.positions: dict[tuple[int, str], LineRunEpochPositionBinding] = {}

    async def get_active_for_workline_for_update(self, _db: object, workline_id: int) -> LineRunEpoch | None:
        if self.active_epoch is not None and self.active_epoch.workline_id == workline_id:
            return self.active_epoch
        return None

    async def add_epoch(self, _db: object, epoch: LineRunEpoch) -> LineRunEpoch:
        epoch.id = 11
        self.active_epoch = epoch
        return epoch

    async def close_epoch(self, _db: object, epoch: LineRunEpoch, *, closed_at: datetime) -> LineRunEpoch:
        epoch.status = LineRunEpochStatus.CLOSED
        epoch.closed_at = closed_at
        self.active_epoch = None
        return epoch

    async def has_sendable_for_epoch_for_update(self, _db: object, line_run_epoch_id: int) -> bool:
        return getattr(self, "has_sendable", False)

    async def get_binding_for_update(
        self,
        _db: object,
        *,
        line_run_epoch_id: int,
        device_code: str,
    ) -> LineRunEpochDeviceBinding | None:
        return self.bindings.get((line_run_epoch_id, device_code))

    async def get_binding_by_role_for_update(
        self,
        _db: object,
        *,
        line_run_epoch_id: int,
        device_role: str,
    ) -> LineRunEpochDeviceBinding | None:
        return next(
            (
                binding
                for (epoch_id, _), binding in self.bindings.items()
                if epoch_id == line_run_epoch_id and binding.device_role == device_role
            ),
            None,
        )

    async def add_binding(
        self,
        _db: object,
        binding: LineRunEpochDeviceBinding,
    ) -> LineRunEpochDeviceBinding:
        binding.id = 21
        self.bindings[(binding.line_run_epoch_id, binding.device_code)] = binding
        return binding

    async def get_position_binding_for_update(self, _db: object, *, line_run_epoch_id: int, position_role: str):
        return self.positions.get((line_run_epoch_id, position_role))

    async def get_position_binding_by_location_for_update(
        self, _db: object, *, line_run_epoch_id: int, location_id: str
    ):
        return next(
            (
                binding
                for (epoch_id, _), binding in self.positions.items()
                if epoch_id == line_run_epoch_id and binding.location_id == location_id
            ),
            None,
        )

    async def add_position_binding(self, _db: object, binding: LineRunEpochPositionBinding):
        binding.id = 31
        self.positions[(binding.line_run_epoch_id, binding.position_role)] = binding
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
        plugin_key="rough_sorter",
        plugin_version="1.0.0",
        flow_mode="ROUGH_SORT_INBOUND",
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
            plugin_key="rough_sorter",
            plugin_version="1.0.0",
            flow_mode="ROUGH_SORT_INBOUND",
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
        plugin_key="rough_sorter",
        plugin_version="1.0.0",
        flow_mode="ROUGH_SORT_INBOUND",
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
        device_role="MEASUREMENT_DEVICE",
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
        device_role="MEASUREMENT_DEVICE",
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
            device_role="MEASUREMENT_DEVICE",
            contract_key="arm.pick",
            contract_version="2.1",
            status_max_age_ms=1_000,
            command_timeout_ms=30_000,
        )


@pytest.mark.asyncio
async def test_binding_role_cannot_be_reassigned_to_another_device() -> None:
    service, _ = _service()
    await service.bind_device(
        object(),
        line_run_epoch_id=11,
        device_id=7,
        device_code="ARM-01",
        device_role="MEASUREMENT_DEVICE",
        contract_key="arm.pick",
        contract_version="2.0",
        status_max_age_ms=1_000,
        command_timeout_ms=30_000,
    )

    with pytest.raises(DeviceBindingConflictError, match="角色"):
        await service.bind_device(
            object(),
            line_run_epoch_id=11,
            device_id=8,
            device_code="ARM-02",
            device_role="MEASUREMENT_DEVICE",
            contract_key="arm.pick",
            contract_version="2.0",
            status_max_age_ms=1_000,
            command_timeout_ms=30_000,
        )


@pytest.mark.asyncio
async def test_binding_device_cannot_be_reassigned_to_another_role() -> None:
    service, _ = _service()
    await service.bind_device(
        object(),
        line_run_epoch_id=11,
        device_id=7,
        device_code="ARM-01",
        device_role="MEASUREMENT_DEVICE",
        contract_key="arm.pick",
        contract_version="2.0",
        status_max_age_ms=1_000,
        command_timeout_ms=30_000,
    )

    with pytest.raises(DeviceBindingConflictError, match="设备"):
        await service.bind_device(
            object(),
            line_run_epoch_id=11,
            device_id=7,
            device_code="ARM-01",
            device_role="TRANSFER_DEVICE",
            contract_key="arm.pick",
            contract_version="2.0",
            status_max_age_ms=1_000,
            command_timeout_ms=30_000,
        )


@pytest.mark.asyncio
async def test_close_active_epoch_allows_next_generation() -> None:
    service, repository = _service()
    first = await service.create_epoch(
        object(),
        epoch_code="EPOCH-LINE-01-0001",
        workline_id=1,
        plugin_key="rough_sorter",
        plugin_version="1.0.0",
        flow_mode="ROUGH_SORT_INBOUND",
        topology_digest="a" * 64,
        configuration_digest="b" * 64,
        started_at=datetime(2026, 8, 13),
    )

    closed = await service.close_active_epoch(
        object(), workline_id=1, closed_at=datetime(2026, 8, 13, 0, 1), command_repository=repository
    )
    second = await service.create_epoch(
        object(),
        epoch_code="EPOCH-LINE-01-0002",
        workline_id=1,
        plugin_key="rough_sorter",
        plugin_version="1.0.1",
        flow_mode="ROUGH_SORT_INBOUND",
        topology_digest="c" * 64,
        configuration_digest="d" * 64,
        started_at=datetime(2026, 8, 13, 0, 1),
    )

    assert closed is first
    assert closed.status == LineRunEpochStatus.CLOSED
    assert closed.closed_at == datetime(2026, 8, 13, 0, 1)
    assert second.status == LineRunEpochStatus.ACTIVE


@pytest.mark.asyncio
async def test_close_epoch_rejects_commands_still_in_send_window() -> None:
    service, repository = _service()
    await service.create_epoch(
        object(),
        epoch_code="EPOCH-LINE-01-0001",
        workline_id=1,
        plugin_key="rough_sorter",
        plugin_version="1.0.0",
        flow_mode="ROUGH_SORT_INBOUND",
        topology_digest="a" * 64,
        configuration_digest="b" * 64,
        started_at=datetime(2026, 8, 13),
    )
    repository.has_sendable = True

    with pytest.raises(ActiveLineRunEpochExistsError, match="sendable"):
        await service.close_active_epoch(
            object(), workline_id=1, closed_at=datetime(2026, 8, 13, 0, 1), command_repository=repository
        )

    assert repository.active_epoch is not None


@pytest.mark.asyncio
async def test_epoch_freezes_plugin_identity_and_flow_mode_without_generic_state() -> None:
    service, _ = _service()

    epoch = await service.create_epoch(
        object(),
        epoch_code="EPOCH-LINE-01-0001",
        workline_id=1,
        plugin_key="rough_sorter",
        plugin_version="1.0.0",
        flow_mode="ROUGH_SORT_INBOUND",
        topology_digest="a" * 64,
        configuration_digest="b" * 64,
        started_at=datetime(2026, 8, 13),
    )

    assert epoch.plugin_key == "rough_sorter"
    assert epoch.plugin_version == "1.0.0"
    assert epoch.flow_mode == "ROUGH_SORT_INBOUND"
    assert "plugin_state" not in LineRunEpoch.model_fields


def test_epoch_device_binding_freezes_business_role() -> None:
    assert "device_role" in LineRunEpochDeviceBinding.model_fields


@pytest.mark.asyncio
async def test_epoch_position_binding_is_idempotent_but_role_and_location_are_immutable() -> None:
    service, _ = _service()
    first = await service.bind_position(
        object(),
        line_run_epoch_id=11,
        position_role="PIPELINE_OUTLET",
        location_id="ROUGH-LINE-1-OUTLET",
        location_type="HANDOFF_POSITION",
    )
    duplicate = await service.bind_position(
        object(),
        line_run_epoch_id=11,
        position_role="PIPELINE_OUTLET",
        location_id="ROUGH-LINE-1-OUTLET",
        location_type="HANDOFF_POSITION",
    )

    assert duplicate is first
    with pytest.raises(PositionBindingConflictError):
        await service.bind_position(
            object(),
            line_run_epoch_id=11,
            position_role="PIPELINE_OUTLET",
            location_id="OTHER-OUTLET",
            location_type="HANDOFF_POSITION",
        )
    with pytest.raises(PositionBindingConflictError):
        await service.bind_position(
            object(),
            line_run_epoch_id=11,
            position_role="NG_POSITION",
            location_id="ROUGH-LINE-1-OUTLET",
            location_type="NG_POSITION",
        )


def test_epoch_position_binding_identity_contains_complete_static_topology() -> None:
    binding = LineRunEpochPositionBinding(
        line_run_epoch_id=11,
        position_role="NG_POSITION",
        location_id="ROUGH-LINE-1-NG",
        location_type="NG_POSITION",
    )

    assert binding.identity_tuple() == (11, "NG_POSITION", "ROUGH-LINE-1-NG", "NG_POSITION")
