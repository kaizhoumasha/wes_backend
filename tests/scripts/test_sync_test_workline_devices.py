import pytest
from sqlalchemy import func, select

from scripts.data.sync_test_workline_devices import (
    TEST_ROUGH_SORTER_DEVICES,
    TEST_ROUGH_SORTER_LINE_CODE,
    sync_test_workline_devices,
)
from src.app.device.models import Device, DeviceStatus
from src.app.workline.models import LineType, WorkLine, WorkLineRunMode
from src.app.workline.models.safety import WorkLineRuntimeStatus
from src.workline_plugins.rough_sorter.contract import (
    ACTION_MOVE_FORWARD,
    ACTION_MOVE_TO_NG,
    ACTION_PICK_AND_PUT,
    ACTION_PUT_TO_BIN,
    EVENT_SCAN_COMPLETED,
    ROLE_CONVEYOR,
    ROLE_INPUT_ARM,
    ROLE_OUTPUT_ARM,
    ROUGH_SORTER_CONTRACT_VERSION,
    ROUGH_SORTER_PLUGIN_KEY,
)


@pytest.mark.asyncio
async def test_sync_test_workline_devices_creates_required_topology(db_session) -> None:
    result = await sync_test_workline_devices(db_session)

    assert result["summary"]["worklines"]["created"] == 1
    assert result["summary"]["devices"]["created"] == len(TEST_ROUGH_SORTER_DEVICES)

    workline = (
        await db_session.execute(select(WorkLine).where(WorkLine.line_code == TEST_ROUGH_SORTER_LINE_CODE))
    ).scalar_one()
    assert workline.is_active is True
    assert workline.plugin_key == ROUGH_SORTER_PLUGIN_KEY
    assert workline.contract_version == ROUGH_SORTER_CONTRACT_VERSION
    assert workline.run_mode == WorkLineRunMode.SIMULATION

    devices = (await db_session.execute(select(Device).order_by(Device.sort_order.asc()))).scalars().all()
    assert [device.device_role for device in devices] == [ROLE_INPUT_ARM, ROLE_CONVEYOR, ROLE_OUTPUT_ARM]
    assert {device.device_status for device in devices} == {DeviceStatus.IDLE}

    input_arm, conveyor, output_arm = devices
    assert input_arm.capabilities_json["supports_event_types"] == [EVENT_SCAN_COMPLETED]
    assert input_arm.capabilities_json["supports_command_types"] == [
        ACTION_PICK_AND_PUT,
        ACTION_MOVE_TO_NG,
    ]
    assert conveyor.capabilities_json["supports_command_types"] == [ACTION_MOVE_FORWARD]
    assert output_arm.capabilities_json["supports_command_types"] == [ACTION_PUT_TO_BIN]
    assert conveyor.upstream_device_id == input_arm.id
    assert output_arm.upstream_device_id == conveyor.id


@pytest.mark.asyncio
async def test_sync_test_workline_devices_does_not_modify_existing_rows(db_session) -> None:
    await sync_test_workline_devices(db_session)

    workline = (
        await db_session.execute(select(WorkLine).where(WorkLine.line_code == TEST_ROUGH_SORTER_LINE_CODE))
    ).scalar_one()
    device = (await db_session.execute(select(Device).where(Device.device_code == "RS-INPUT-ARM-01"))).scalar_one()
    workline.line_name = "被测试修改的名称"
    workline.run_mode = WorkLineRunMode.AUTO
    device.capabilities_json = {"supports_command_types": ["TEST"]}
    device.device_status = DeviceStatus.ERROR
    device.error_code = "TEST_RUNTIME_STATE"
    device.host = "10.150.94.122"
    device.port = 8006
    await db_session.commit()

    result = await sync_test_workline_devices(db_session)

    workline_count = await db_session.scalar(select(func.count()).select_from(WorkLine))
    device_count = await db_session.scalar(select(func.count()).select_from(Device))
    assert workline_count == 1
    assert device_count == len(TEST_ROUGH_SORTER_DEVICES)
    assert result["summary"]["worklines"]["unchanged"] == 1
    assert set(result["devices"].values()) == {"unchanged"}

    preserved_workline = (
        await db_session.execute(select(WorkLine).where(WorkLine.line_code == TEST_ROUGH_SORTER_LINE_CODE))
    ).scalar_one()
    repaired_device = (
        await db_session.execute(select(Device).where(Device.device_code == "RS-INPUT-ARM-01"))
    ).scalar_one()
    assert preserved_workline.line_name == "被测试修改的名称"
    assert preserved_workline.run_mode == WorkLineRunMode.AUTO
    assert repaired_device.capabilities_json == {"supports_command_types": ["TEST"]}
    assert repaired_device.device_status == DeviceStatus.ERROR
    assert repaired_device.error_code == "TEST_RUNTIME_STATE"
    assert repaired_device.host == "10.150.94.122"
    assert repaired_device.port == 8006


@pytest.mark.asyncio
async def test_sync_test_workline_devices_preserves_existing_device_communication_config(db_session) -> None:
    await sync_test_workline_devices(db_session)

    device = (await db_session.execute(select(Device).where(Device.device_code == "RS-INPUT-ARM-01"))).scalar_one()
    device.host = "10.150.94.122"
    device.port = 8006
    device.callback_path = "/api/v1/device/command"
    await db_session.commit()

    result = await sync_test_workline_devices(db_session)

    repaired_device = (
        await db_session.execute(select(Device).where(Device.device_code == "RS-INPUT-ARM-01"))
    ).scalar_one()
    assert result["devices"]["RS-INPUT-ARM-01"] == "unchanged"
    assert repaired_device.host == "10.150.94.122"
    assert repaired_device.port == 8006
    assert repaired_device.callback_path == "/api/v1/device/command"


@pytest.mark.asyncio
async def test_sync_test_workline_devices_does_not_add_missing_devices_when_seed_data_exists(db_session) -> None:
    await sync_test_workline_devices(db_session)

    missing_device = (
        await db_session.execute(select(Device).where(Device.device_code == "RS-OUTPUT-ARM-01"))
    ).scalar_one()
    await db_session.delete(missing_device)
    await db_session.commit()

    result = await sync_test_workline_devices(db_session)

    device_count = await db_session.scalar(select(func.count()).select_from(Device))
    missing_after_sync = (
        await db_session.execute(select(Device).where(Device.device_code == "RS-OUTPUT-ARM-01"))
    ).scalar_one_or_none()
    assert device_count == len(TEST_ROUGH_SORTER_DEVICES) - 1
    assert missing_after_sync is None
    assert result["devices"]["RS-OUTPUT-ARM-01"] == "unchanged"


@pytest.mark.asyncio
async def test_sync_test_workline_devices_does_not_seed_when_any_workline_data_exists(db_session) -> None:
    db_session.add(
        WorkLine(
            line_code="WL-EXISTING-MANUAL",
            line_name="已有人工测试线",
            line_type=LineType.AUTO,
            zone_name="测试库",
            plugin_key="manual-test",
            contract_version="manual",
            config={},
            runtime_config_json={},
            run_mode=WorkLineRunMode.AUTO,
            diagnostic_profile={},
            description="人工创建的测试数据",
            is_active=True,
            runtime_status=WorkLineRuntimeStatus.READY,
        )
    )
    await db_session.commit()

    result = await sync_test_workline_devices(db_session)

    seeded_workline = (
        await db_session.execute(select(WorkLine).where(WorkLine.line_code == TEST_ROUGH_SORTER_LINE_CODE))
    ).scalar_one_or_none()
    device_count = await db_session.scalar(select(func.count()).select_from(Device))
    assert seeded_workline is None
    assert device_count == 0
    assert result["summary"]["worklines"]["unchanged"] == 1
    assert set(result["devices"].values()) == {"unchanged"}
