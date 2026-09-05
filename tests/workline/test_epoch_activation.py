"""LineRunEpoch 完整激活的基础层行为。"""

from datetime import datetime

import pytest

from src.app.device.models.device import Device
from src.app.wms_integration.outbound_picking.models import PickingTask as _PickingTask
from src.app.workline.epoch_activation import (
    LineRunEpochDeviceBindingInput,
    LineRunEpochPositionBindingInput,
)
from src.app.workline.epoch_digest import configuration_digest, topology_digest
from src.app.workline.models.line_run_epoch import LineRunEpoch, LineRunEpochDeviceBinding
from src.app.workline.models.workline import LineType, WorkLine
from src.app.workline.repositories.line_run_epoch_repository import LineRunEpochRepository
from src.app.workline.services.line_run_epoch_service import ActiveLineRunEpochExistsError, LineRunEpochService


class CompleteEpochRepository:
    def __init__(self) -> None:
        self.active: LineRunEpoch | None = None
        self.complete_write: (
            tuple[
                LineRunEpoch,
                tuple[LineRunEpochDeviceBindingInput, ...],
                tuple[LineRunEpochPositionBindingInput, ...],
            ]
            | None
        ) = None

    async def get_active_for_workline_for_update(self, _db: object, workline_id: int) -> LineRunEpoch | None:
        if self.active is not None and self.active.workline_id == workline_id:
            return self.active
        return None

    async def add_complete_epoch(
        self,
        _db: object,
        epoch: LineRunEpoch,
        device_bindings: tuple[LineRunEpochDeviceBindingInput, ...],
        position_bindings: tuple[LineRunEpochPositionBindingInput, ...],
    ) -> LineRunEpoch:
        epoch.id = 11
        self.active = epoch
        self.complete_write = epoch, device_bindings, position_bindings
        return epoch


def _device() -> LineRunEpochDeviceBindingInput:
    return LineRunEpochDeviceBindingInput(
        device_id=7,
        device_code="DEVICE-7",
        device_role="DEVICE_ROLE",
        endpoint_base_url="http://ecs-epoch:8080",
        contract_key="generic.contract",
        contract_version="1.0",
        status_max_age_ms=1_000,
        command_timeout_ms=5_000,
    )


def _position() -> LineRunEpochPositionBindingInput:
    return LineRunEpochPositionBindingInput(
        position_role="INPUT_POSITION",
        location_id="LOCATION-1",
        location_type="RACK_CELL",
    )


@pytest.mark.asyncio
async def test_activate_epoch_freezes_snapshot_and_writes_complete_aggregate_once() -> None:
    repository = CompleteEpochRepository()
    service = LineRunEpochService(repository=repository)  # type: ignore[arg-type]
    configuration = {"limits": {"maximum": 10}, "mode": "GENERIC"}
    device = _device()
    position = _position()

    epoch = await service.activate_epoch(
        object(),
        epoch_code="REQUEST-1",
        workline_id=3,
        plugin_key="example_plugin",
        plugin_version="1.0",
        flow_mode="GENERIC_FLOW",
        configuration_snapshot=configuration,
        device_bindings=(device,),
        position_bindings=(position,),
        started_at=datetime(2026, 8, 19),
    )
    configuration["limits"]["maximum"] = 99  # type: ignore[index]

    assert epoch.configuration_snapshot_json == {"limits": {"maximum": 10}, "mode": "GENERIC"}
    assert epoch.configuration_digest == configuration_digest(
        "example_plugin",
        "1.0",
        "GENERIC_FLOW",
        {"limits": {"maximum": 10}, "mode": "GENERIC"},
    )
    assert epoch.topology_digest == topology_digest((device,), (position,))
    assert repository.complete_write == (epoch, (device,), (position,))


@pytest.mark.asyncio
async def test_activate_epoch_rejects_existing_active_epoch_before_complete_write() -> None:
    repository = CompleteEpochRepository()
    repository.active = LineRunEpoch(
        epoch_code="EXISTING",
        workline_id=3,
        plugin_key="example_plugin",
        plugin_version="1.0",
        flow_mode="GENERIC_FLOW",
        topology_digest="a" * 64,
        configuration_digest="b" * 64,
        configuration_snapshot_json={},
        started_at=datetime(2026, 8, 19),
    )
    service = LineRunEpochService(repository=repository)  # type: ignore[arg-type]

    with pytest.raises(ActiveLineRunEpochExistsError):
        await service.activate_epoch(
            object(),
            epoch_code="REQUEST-2",
            workline_id=3,
            plugin_key="example_plugin",
            plugin_version="1.0",
            flow_mode="GENERIC_FLOW",
            configuration_snapshot={},
            device_bindings=(_device(),),
            position_bindings=(_position(),),
            started_at=datetime(2026, 8, 19),
        )

    assert repository.complete_write is None


@pytest.mark.asyncio
async def test_real_repository_persists_complete_epoch_aggregate(db_session) -> None:  # type: ignore[no-untyped-def]
    line = WorkLine(line_code="ATOMIC-EPOCH-LINE", line_name="Atomic Epoch", line_type=LineType.AUTO)
    db_session.add(line)
    await db_session.flush()
    device = Device(
        device_code="ATOMIC-EPOCH-DEVICE",
        device_name="Atomic Epoch Device",
        work_line_id=line.id,
        device_role="DEVICE_ROLE",
    )
    db_session.add(device)
    second_device = Device(
        device_code="ATOMIC-EPOCH-DEVICE-2",
        device_name="Atomic Epoch Device 2",
        work_line_id=line.id,
        device_role="DEVICE_ROLE",
    )
    db_session.add(second_device)
    await db_session.flush()
    repository = LineRunEpochRepository()
    service = LineRunEpochService(repository=repository)
    device_input = _device()
    device_input = LineRunEpochDeviceBindingInput(
        device_id=device.id,
        device_code=device.device_code,
        device_role=device_input.device_role,
        endpoint_base_url=device_input.endpoint_base_url,
        contract_key=device_input.contract_key,
        contract_version=device_input.contract_version,
        status_max_age_ms=device_input.status_max_age_ms,
        command_timeout_ms=device_input.command_timeout_ms,
    )
    second_device_input = LineRunEpochDeviceBindingInput(
        device_id=second_device.id,
        device_code=second_device.device_code,
        device_role=device_input.device_role,
        endpoint_base_url=device_input.endpoint_base_url,
        contract_key=device_input.contract_key,
        contract_version=device_input.contract_version,
        status_max_age_ms=device_input.status_max_age_ms,
        command_timeout_ms=device_input.command_timeout_ms,
    )

    epoch = await service.activate_epoch(
        db_session,
        epoch_code="ATOMIC-EPOCH-REQUEST",
        workline_id=line.id,
        plugin_key="example_plugin",
        plugin_version="1.0",
        flow_mode="GENERIC_FLOW",
        configuration_snapshot={"mode": "GENERIC"},
        device_bindings=(device_input, second_device_input),
        position_bindings=(_position(),),
        started_at=datetime(2026, 8, 19),
    )

    assert epoch.id is not None
    assert [binding.device_code for binding in await repository.list_bindings(db_session, epoch.id)] == [
        "ATOMIC-EPOCH-DEVICE",
        "ATOMIC-EPOCH-DEVICE-2",
    ]
    assert [binding.location_id for binding in await repository.list_position_bindings(db_session, epoch.id)] == [
        "LOCATION-1"
    ]
    bindings = await repository.list_bindings_by_role_for_update(
        db_session,
        line_run_epoch_id=epoch.id,
        device_role="DEVICE_ROLE",
    )
    assert [binding.device_code for binding in bindings] == ["ATOMIC-EPOCH-DEVICE", "ATOMIC-EPOCH-DEVICE-2"]


def test_epoch_allows_multiple_devices_with_the_same_role() -> None:
    constraint_names = {constraint.name for constraint in LineRunEpochDeviceBinding.__table__.constraints}

    assert "ux_line_run_epoch_device_bindings_epoch_device_role" not in constraint_names
