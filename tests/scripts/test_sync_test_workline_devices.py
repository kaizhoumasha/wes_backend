import pytest
from sqlalchemy import func, select

from scripts.data.sync_test_workline_devices import (
    TEST_ROUGH_SORTER_DEVICES,
    TEST_ROUGH_SORTER_LINE_CODE,
    TEST_ROUGH_SORTER_RACK_POSITIONS,
    TEST_SMT_SORTING_INBOUND_DEVICES,
    TEST_SMT_SORTING_INBOUND_LINE_CODE,
    TEST_SMT_SORTING_INBOUND_RACK_POSITIONS,
    sync_test_workline_devices,
)
from src.app.device.models import Device, DeviceProtocol, DeviceStatus
from src.app.resource.models import RackKind
from src.app.runtime.orchestration.models.rack_position import WorklineRackPosition, WorklineRackPositionRole
from src.app.workline.models import LineType, WorkLine, WorkLineRunMode
from src.app.workline.models.safety import WorkLineRuntimeStatus
from src.app.workline.services.workline_service import WorkLineService
from src.workline_plugins.rough_sorter.contract import (
    ACTION_MOVE_FORWARD,
    ACTION_MOVE_TO_NG,
    ACTION_PICK_AND_PUT,
    ACTION_PUT_TO_BIN,
    EVENT_ROUGH_SORTER_STORAGE_RETRY,
    EVENT_SCAN_COMPLETED,
    ROLE_CONVEYOR,
    ROLE_INPUT_ARM,
    ROLE_OUTPUT_ARM,
    ROUGH_SORTER_CONTRACT_VERSION,
    ROUGH_SORTER_PLUGIN_KEY,
)
from src.workline_plugins.smt_sorting_inbound.constants import (
    COMMAND_NG_PLACE,
    COMMAND_SOURCE_PICK,
    COMMAND_TARGET_PLACE,
    EVENT_WORKING_BIN_SCAN,
    ROLE_SORTING_SCAN_PLATFORM,
    ROLE_SORTING_SOURCE_ARM,
    ROLE_SORTING_TARGET_ARM,
    ROLE_SORTING_WORKSTATION,
    SMT_SORTING_INBOUND_CONTRACT_VERSION,
    SMT_SORTING_INBOUND_PLUGIN_KEY,
)


@pytest.mark.asyncio
async def test_sync_test_workline_devices_rejects_prod_before_creating_debug_master_data(
    db_session,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "prod")

    with pytest.raises(RuntimeError, match="APP_ENV=prod"):
        await sync_test_workline_devices(db_session)

    workline_count = await db_session.scalar(
        select(func.count())
        .select_from(WorkLine)
        .where(WorkLine.line_code.in_([TEST_ROUGH_SORTER_LINE_CODE, TEST_SMT_SORTING_INBOUND_LINE_CODE]))
    )
    device_count = await db_session.scalar(
        select(func.count())
        .select_from(Device)
        .where(
            Device.device_code.in_(
                [seed.device_code for seed in TEST_ROUGH_SORTER_DEVICES + TEST_SMT_SORTING_INBOUND_DEVICES]
            )
        )
    )
    assert workline_count == 0
    assert device_count == 0


@pytest.mark.asyncio
async def test_sync_test_workline_devices_rejects_prod_from_settings_even_without_process_env(
    db_session,
    monkeypatch,
) -> None:
    from scripts.data import sync_test_workline_devices as seed_module

    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setattr(seed_module, "settings", type("SettingsStub", (), {"APP_ENV": "prod"})())

    with pytest.raises(RuntimeError, match="APP_ENV=prod"):
        await sync_test_workline_devices(db_session)

    workline_count = await db_session.scalar(
        select(func.count())
        .select_from(WorkLine)
        .where(WorkLine.line_code.in_([TEST_ROUGH_SORTER_LINE_CODE, TEST_SMT_SORTING_INBOUND_LINE_CODE]))
    )
    assert workline_count == 0


@pytest.mark.asyncio
async def test_sync_test_workline_devices_creates_required_topology(db_session, monkeypatch) -> None:
    monkeypatch.delenv("MOCK_ECS_URL", raising=False)
    monkeypatch.delenv("MOCK_ECS_HOST", raising=False)
    monkeypatch.delenv("MOCK_ECS_PORT", raising=False)

    result = await sync_test_workline_devices(db_session)

    assert result["summary"]["worklines"]["created"] == 1
    assert result["summary"]["devices"]["created"] == len(TEST_ROUGH_SORTER_DEVICES)
    assert result["summary"]["rack_positions"]["created"] == len(TEST_ROUGH_SORTER_RACK_POSITIONS)
    assert result["summary"]["total_worklines"] == 2
    assert result["summary"]["total_devices"] == len(TEST_ROUGH_SORTER_DEVICES) + len(TEST_SMT_SORTING_INBOUND_DEVICES)

    workline = (
        await db_session.execute(select(WorkLine).where(WorkLine.line_code == TEST_ROUGH_SORTER_LINE_CODE))
    ).scalar_one()
    assert workline.is_active is True
    assert workline.plugin_key == ROUGH_SORTER_PLUGIN_KEY
    assert workline.contract_version == ROUGH_SORTER_CONTRACT_VERSION
    assert workline.run_mode == WorkLineRunMode.AUTO
    assert workline.runtime_status == WorkLineRuntimeStatus.STOPPED

    devices = (
        (
            await db_session.execute(
                select(Device).where(Device.work_line_id == workline.id).order_by(Device.sort_order.asc())
            )
        )
        .scalars()
        .all()
    )
    assert [device.device_role for device in devices] == [ROLE_INPUT_ARM, ROLE_CONVEYOR, ROLE_OUTPUT_ARM]
    assert {device.device_status for device in devices} == {DeviceStatus.IDLE}

    input_arm, conveyor, output_arm = devices
    assert input_arm.capabilities_json["supports_event_types"] == [
        EVENT_SCAN_COMPLETED,
        EVENT_ROUGH_SORTER_STORAGE_RETRY,
    ]
    assert input_arm.capabilities_json["supports_command_types"] == [
        ACTION_PICK_AND_PUT,
        ACTION_MOVE_TO_NG,
    ]
    assert conveyor.capabilities_json["supports_command_types"] == [ACTION_MOVE_FORWARD]
    assert output_arm.capabilities_json["supports_command_types"] == [ACTION_PUT_TO_BIN]
    assert {device.host for device in devices} == {"mock_ecs"}
    assert {device.port for device in devices} == {8010}
    assert {device.callback_path for device in devices} == {"/api/v1/device/command"}
    assert {device.capabilities_json["status_path"] for device in devices} == {"/api/v1/device/status"}
    assert conveyor.upstream_device_id == input_arm.id
    assert output_arm.upstream_device_id == conveyor.id

    rough_rack_positions = (
        (
            await db_session.execute(
                select(WorklineRackPosition).where(WorklineRackPosition.workline_code == TEST_ROUGH_SORTER_LINE_CODE)
            )
        )
        .scalars()
        .all()
    )
    assert len(rough_rack_positions) == 1
    rack_position = rough_rack_positions[0]
    assert rack_position.workline_id == workline.id
    assert rack_position.workline_code == TEST_ROUGH_SORTER_LINE_CODE
    assert rack_position.position_code == "SINGLE_LAYER_A"
    assert rack_position.position_role == WorklineRackPositionRole.SMT_CLASSIFIER_SINGLE_RACK_WORK
    assert rack_position.allowed_rack_kind == RackKind.SINGLE_LAYER
    assert rack_position.capacity == 1
    assert rack_position.enabled is True


@pytest.mark.asyncio
async def test_sync_test_workline_devices_defaults_device_connection_to_mock_ecs(db_session, monkeypatch) -> None:
    monkeypatch.delenv("MOCK_ECS_URL", raising=False)
    monkeypatch.delenv("MOCK_ECS_HOST", raising=False)
    monkeypatch.delenv("MOCK_ECS_PORT", raising=False)

    await sync_test_workline_devices(db_session)

    devices = (await db_session.execute(select(Device))).scalars().all()
    assert {device.host for device in devices} == {"mock_ecs"}
    assert {device.port for device in devices} == {8010}


@pytest.mark.asyncio
async def test_sync_test_workline_devices_uses_mock_ecs_env_connection(db_session, monkeypatch) -> None:
    monkeypatch.delenv("MOCK_ECS_URL", raising=False)
    monkeypatch.setenv("MOCK_ECS_HOST", "mock_ecs")
    monkeypatch.setenv("MOCK_ECS_PORT", "8010")

    await sync_test_workline_devices(db_session)

    devices = (await db_session.execute(select(Device))).scalars().all()
    assert {device.host for device in devices} == {"mock_ecs"}
    assert {device.port for device in devices} == {8010}


@pytest.mark.asyncio
async def test_sync_test_workline_devices_prefers_mock_ecs_url(db_session, monkeypatch) -> None:
    monkeypatch.setenv("MOCK_ECS_URL", "http://mock_ecs:8010")
    monkeypatch.setenv("MOCK_ECS_HOST", "localhost")
    monkeypatch.setenv("MOCK_ECS_PORT", "9999")

    await sync_test_workline_devices(db_session)

    devices = (await db_session.execute(select(Device))).scalars().all()
    assert {device.host for device in devices} == {"mock_ecs"}
    assert {device.port for device in devices} == {8010}


@pytest.mark.asyncio
async def test_sync_test_workline_devices_refreshes_existing_seed_rows_without_touching_runtime_state(
    db_session,
) -> None:
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
    rack_position_count = await db_session.scalar(select(func.count()).select_from(WorklineRackPosition))
    assert workline_count == 2
    assert device_count == len(TEST_ROUGH_SORTER_DEVICES) + len(TEST_SMT_SORTING_INBOUND_DEVICES)
    assert rack_position_count == len(TEST_ROUGH_SORTER_RACK_POSITIONS) + len(TEST_SMT_SORTING_INBOUND_RACK_POSITIONS)
    assert result["summary"]["worklines"]["updated"] == 1
    assert result["devices"]["RS-INPUT-ARM-01"] == "updated"
    assert result["rack_positions"]["SINGLE_LAYER_A"] == "unchanged"

    refreshed_workline = (
        await db_session.execute(select(WorkLine).where(WorkLine.line_code == TEST_ROUGH_SORTER_LINE_CODE))
    ).scalar_one()
    refreshed_device = (
        await db_session.execute(select(Device).where(Device.device_code == "RS-INPUT-ARM-01"))
    ).scalar_one()
    assert refreshed_workline.line_name == "测试粗分机作业线"
    assert refreshed_workline.run_mode == WorkLineRunMode.AUTO
    assert refreshed_device.capabilities_json["supports_event_types"] == [
        EVENT_SCAN_COMPLETED,
        EVENT_ROUGH_SORTER_STORAGE_RETRY,
    ]
    assert refreshed_device.capabilities_json["status_path"] == "/api/v1/device/status"
    assert refreshed_device.device_status == DeviceStatus.ERROR
    assert refreshed_device.error_code == "TEST_RUNTIME_STATE"
    assert refreshed_device.host == "10.150.94.122"
    assert refreshed_device.port == 8006


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
async def test_sync_test_workline_devices_adds_missing_devices_when_seed_data_exists(db_session) -> None:
    await sync_test_workline_devices(db_session)

    missing_device = (
        await db_session.execute(select(Device).where(Device.device_code == "RS-OUTPUT-ARM-01"))
    ).scalar_one()
    await db_session.delete(missing_device)
    await db_session.commit()

    result = await sync_test_workline_devices(db_session)

    device_count = await db_session.scalar(select(func.count()).select_from(Device))
    added_after_sync = (
        await db_session.execute(select(Device).where(Device.device_code == "RS-OUTPUT-ARM-01"))
    ).scalar_one_or_none()
    assert device_count == len(TEST_ROUGH_SORTER_DEVICES) + len(TEST_SMT_SORTING_INBOUND_DEVICES)
    assert added_after_sync is not None
    assert result["devices"]["RS-OUTPUT-ARM-01"] == "created"


@pytest.mark.asyncio
async def test_sync_test_workline_devices_seeds_rough_sorter_when_unrelated_workline_exists(db_session) -> None:
    db_session.add(
        WorkLine(
            line_code="WL-EXISTING-MANUAL",
            line_name="已有人工测试线",
            line_type=LineType.AUTO,
            zone_name="开发库",
            plugin_key="manual-test",
            contract_version="manual",
            config={},
            runtime_config_json={},
            run_mode=WorkLineRunMode.AUTO,
            diagnostic_profile={},
            description="人工创建的开发数据",
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
    rack_position_count = await db_session.scalar(select(func.count()).select_from(WorklineRackPosition))
    assert seeded_workline is not None
    assert device_count == len(TEST_ROUGH_SORTER_DEVICES) + len(TEST_SMT_SORTING_INBOUND_DEVICES)
    assert rack_position_count == len(TEST_ROUGH_SORTER_RACK_POSITIONS) + len(TEST_SMT_SORTING_INBOUND_RACK_POSITIONS)
    assert result["summary"]["worklines"]["created"] == 1
    assert set(result["devices"].values()) == {"created"}
    assert result["summary"]["total_worklines"] == 3


@pytest.mark.asyncio
async def test_sync_test_workline_devices_creates_smt_sorting_inbound_topology(db_session, monkeypatch) -> None:
    monkeypatch.delenv("MOCK_ECS_URL", raising=False)
    monkeypatch.delenv("MOCK_ECS_HOST", raising=False)
    monkeypatch.delenv("MOCK_ECS_PORT", raising=False)

    result = await sync_test_workline_devices(db_session)

    workline = (
        await db_session.execute(select(WorkLine).where(WorkLine.line_code == TEST_SMT_SORTING_INBOUND_LINE_CODE))
    ).scalar_one()
    assert workline.line_name == "测试 SMT 分拣入库作业线"
    assert workline.line_type == LineType.AUTO
    assert workline.plugin_key == SMT_SORTING_INBOUND_PLUGIN_KEY
    assert workline.contract_version == SMT_SORTING_INBOUND_CONTRACT_VERSION
    assert workline.run_mode == WorkLineRunMode.SIMULATION
    assert workline.runtime_status == WorkLineRuntimeStatus.STOPPED
    assert workline.is_active is True

    devices = (
        (
            await db_session.execute(
                select(Device).where(Device.work_line_id == workline.id).order_by(Device.sort_order.asc())
            )
        )
        .scalars()
        .all()
    )
    assert [device.device_code for device in devices] == [
        "SORT-SOURCE-ARM-01",
        "SORT-TARGET-ARM-01",
        "SORT-SCAN-PLATFORM-01",
        "SORT-WORKSTATION-01",
    ]
    assert [device.device_role for device in devices] == [
        ROLE_SORTING_SOURCE_ARM,
        ROLE_SORTING_TARGET_ARM,
        ROLE_SORTING_SCAN_PLATFORM,
        ROLE_SORTING_WORKSTATION,
    ]
    capabilities_by_code = {device.device_code: device.capabilities_json for device in devices}
    assert capabilities_by_code["SORT-SOURCE-ARM-01"]["supports_command_types"] == [COMMAND_SOURCE_PICK]
    assert capabilities_by_code["SORT-TARGET-ARM-01"]["supports_command_types"] == [
        COMMAND_TARGET_PLACE,
        COMMAND_NG_PLACE,
    ]
    assert capabilities_by_code["SORT-SCAN-PLATFORM-01"]["supports_event_types"] == [EVENT_WORKING_BIN_SCAN]
    assert capabilities_by_code["SORT-WORKSTATION-01"]["supports_event_types"] == []
    assert {device.host for device in devices} == {"mock_ecs"}
    assert {device.port for device in devices} == {8010}
    assert {device.protocol for device in devices} == {DeviceProtocol.HTTP}
    assert {device.callback_path for device in devices} == {"/api/v1/device/command"}
    assert {device.timeout for device in devices} == {300000}
    assert {device.vendor_type for device in devices} == {"SANDBOX"}
    assert {device.capabilities_json["status_path"] for device in devices} == {"/api/v1/device/status"}

    smt_rack_positions = (
        (
            await db_session.execute(
                select(WorklineRackPosition).where(
                    WorklineRackPosition.workline_code == TEST_SMT_SORTING_INBOUND_LINE_CODE
                )
            )
        )
        .scalars()
        .all()
    )
    positions_by_code = {position.position_code: position for position in smt_rack_positions}
    assert set(positions_by_code) == {seed.position_code for seed in TEST_SMT_SORTING_INBOUND_RACK_POSITIONS}
    assert "NG_STATION" not in positions_by_code
    assert "WORKSTATION" not in positions_by_code
    expected_rack_kinds = {
        "SOURCE_STATION_A": RackKind.SINGLE_LAYER,
        "SOURCE_STATION_B": RackKind.SINGLE_LAYER,
        "TARGET_STATION": RackKind.FIVE_LAYER,
    }
    for position_code, position in positions_by_code.items():
        assert position.workline_id == workline.id
        assert position.position_code == position_code
        assert position.allowed_rack_kind == expected_rack_kinds[position_code]
        assert position.capacity == 1
        assert position.enabled is True
        assert position.logic_location_code == f"{TEST_SMT_SORTING_INBOUND_LINE_CODE}:{position_code}"
        assert position.external_location_code == position_code
        assert position.metadata_json["seed_source"] == "local-dev"
    assert positions_by_code["SOURCE_STATION_A"].metadata_json["single_layer_boundary"] is True
    assert positions_by_code["SOURCE_STATION_B"].metadata_json["single_layer_boundary"] is True
    assert positions_by_code["TARGET_STATION"].metadata_json["rack_boundary"] == "FIVE_LAYER"
    assert positions_by_code["SOURCE_STATION_A"].position_role == WorklineRackPositionRole.SMT_SORTER_STATION
    assert positions_by_code["SOURCE_STATION_B"].position_role == WorklineRackPositionRole.SMT_SORTER_STATION
    assert positions_by_code["TARGET_STATION"].position_role == WorklineRackPositionRole.SMT_SORTER_STATION
    assert positions_by_code["SOURCE_STATION_A"].device_role == ROLE_SORTING_SOURCE_ARM
    assert positions_by_code["SOURCE_STATION_B"].device_role == ROLE_SORTING_SOURCE_ARM
    assert positions_by_code["TARGET_STATION"].device_role == ROLE_SORTING_TARGET_ARM
    assert result["summary_by_workline"][TEST_SMT_SORTING_INBOUND_LINE_CODE]["devices"]["created"] == len(
        TEST_SMT_SORTING_INBOUND_DEVICES
    )
    assert result["summary_by_workline"][TEST_SMT_SORTING_INBOUND_LINE_CODE]["rack_positions"]["created"] == len(
        TEST_SMT_SORTING_INBOUND_RACK_POSITIONS
    )


@pytest.mark.asyncio
async def test_sync_test_workline_devices_smt_configuration_status_passes(db_session, monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")

    await sync_test_workline_devices(db_session)

    workline = (
        await db_session.execute(select(WorkLine).where(WorkLine.line_code == TEST_SMT_SORTING_INBOUND_LINE_CODE))
    ).scalar_one()
    status = await WorkLineService().configuration_status(db_session, workline.id)  # type: ignore[arg-type]

    failed_checks = [check for check in status.checks if check.status == "FAIL"]
    assert status.can_activate is True
    assert failed_checks == []


@pytest.mark.asyncio
async def test_sync_test_workline_devices_is_idempotent_for_rough_and_smt(db_session) -> None:
    await sync_test_workline_devices(db_session)

    result = await sync_test_workline_devices(db_session)

    workline_count = await db_session.scalar(select(func.count()).select_from(WorkLine))
    device_count = await db_session.scalar(select(func.count()).select_from(Device))
    rack_position_count = await db_session.scalar(select(func.count()).select_from(WorklineRackPosition))
    assert workline_count == 2
    assert device_count == len(TEST_ROUGH_SORTER_DEVICES) + len(TEST_SMT_SORTING_INBOUND_DEVICES)
    assert rack_position_count == len(TEST_ROUGH_SORTER_RACK_POSITIONS) + len(TEST_SMT_SORTING_INBOUND_RACK_POSITIONS)
    assert result["summary_by_workline"][TEST_ROUGH_SORTER_LINE_CODE]["worklines"]["unchanged"] == 1
    assert result["summary_by_workline"][TEST_SMT_SORTING_INBOUND_LINE_CODE]["worklines"]["unchanged"] == 1
    assert result["summary_by_workline"][TEST_SMT_SORTING_INBOUND_LINE_CODE]["devices"]["unchanged"] == len(
        TEST_SMT_SORTING_INBOUND_DEVICES
    )


@pytest.mark.asyncio
async def test_sync_test_workline_devices_preserves_existing_smt_device_runtime_and_connection_state(
    db_session,
) -> None:
    await sync_test_workline_devices(db_session)

    device = (await db_session.execute(select(Device).where(Device.device_code == "SORT-SOURCE-ARM-01"))).scalar_one()
    device.host = "10.150.94.130"
    device.port = 18010
    device.protocol = DeviceProtocol.HTTPS
    device.callback_path = "/field/device/command"
    device.device_status = DeviceStatus.ERROR
    device.current_command_id = 123
    device.error_code = "FIELD_DEBUG"
    device.maintenance_mode = True
    device.capabilities_json = {"supports_command_types": ["OLD"], "status_path": "/old/status"}
    await db_session.commit()

    result = await sync_test_workline_devices(db_session)

    refreshed = (
        await db_session.execute(select(Device).where(Device.device_code == "SORT-SOURCE-ARM-01"))
    ).scalar_one()
    assert result["devices_by_workline"][TEST_SMT_SORTING_INBOUND_LINE_CODE]["SORT-SOURCE-ARM-01"] == "updated"
    assert refreshed.capabilities_json["supports_command_types"] == [COMMAND_SOURCE_PICK]
    assert refreshed.capabilities_json["status_path"] == "/api/v1/device/status"
    assert refreshed.host == "10.150.94.130"
    assert refreshed.port == 18010
    assert refreshed.protocol == DeviceProtocol.HTTPS
    assert refreshed.callback_path == "/field/device/command"
    assert refreshed.device_status == DeviceStatus.ERROR
    assert refreshed.current_command_id == 123
    assert refreshed.error_code == "FIELD_DEBUG"
    assert refreshed.maintenance_mode is True


@pytest.mark.asyncio
async def test_sync_test_workline_devices_returns_rough_top_level_and_grouped_results(db_session) -> None:
    result = await sync_test_workline_devices(db_session)

    assert result["workline"]["line_code"] == TEST_ROUGH_SORTER_LINE_CODE
    assert set(result["devices"]) == {seed.device_code for seed in TEST_ROUGH_SORTER_DEVICES}
    assert set(result["rack_positions"]) == {seed.position_code for seed in TEST_ROUGH_SORTER_RACK_POSITIONS}

    assert set(result["worklines_by_code"]) == {
        TEST_ROUGH_SORTER_LINE_CODE,
        TEST_SMT_SORTING_INBOUND_LINE_CODE,
    }
    assert set(result["devices_by_workline"][TEST_ROUGH_SORTER_LINE_CODE]) == {
        seed.device_code for seed in TEST_ROUGH_SORTER_DEVICES
    }
    assert set(result["devices_by_workline"][TEST_SMT_SORTING_INBOUND_LINE_CODE]) == {
        seed.device_code for seed in TEST_SMT_SORTING_INBOUND_DEVICES
    }
    assert result["rack_positions_by_workline"][TEST_ROUGH_SORTER_LINE_CODE] == result["rack_positions"]
    assert set(result["rack_positions_by_workline"][TEST_SMT_SORTING_INBOUND_LINE_CODE]) == {
        seed.position_code for seed in TEST_SMT_SORTING_INBOUND_RACK_POSITIONS
    }
