"""同步开发环境 WorkLine 与 Device 基础数据。"""

# ruff: noqa: E402

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from src.app.device.models import Device, DeviceProtocol, DeviceStatus
from src.app.resource.models import RackKind
from src.app.runtime.orchestration.models.rack_position import WorklineRackPosition, WorklineRackPositionRole
from src.app.workline.models import LineType, WorkLine, WorkLineRunMode
from src.app.workline.models.safety import WorkLineRuntimeStatus
from src.app.workline.plugins.run_mode import SANDBOX_ALLOWED_ENVS
from src.core.conf import settings
from src.utils.device_cache import workline_device_cache
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

TEST_ROUGH_SORTER_LINE_CODE = "WL-ROUGH-SORTER-TEST"
TEST_SMT_SORTING_INBOUND_LINE_CODE = "WL-SMT-SORTING-INBOUND-TEST"
DEFAULT_MOCK_ECS_HOST = "mock_ecs"
DEFAULT_MOCK_ECS_PORT = 8010
MOCK_ECS_COMMAND_PATH = "/api/v1/device/command"
MOCK_ECS_STATUS_PATH = "/api/v1/device/status"


@dataclass(frozen=True)
class TestWorklineSeed:
    """开发环境工作线基础信息种子。"""

    line_code: str
    line_name: str
    line_type: LineType
    zone_name: str
    plugin_key: str
    contract_version: str
    config: dict[str, Any]
    runtime_config_json: dict[str, Any]
    run_mode: WorkLineRunMode
    runtime_status: WorkLineRuntimeStatus
    diagnostic_profile: dict[str, Any]
    description: str
    devices: tuple["TestDeviceSeed", ...]
    rack_positions: tuple["TestRackPositionSeed", ...] = ()
    is_active: bool = True


@dataclass(frozen=True)
class TestDeviceSeed:
    """开发环境设备基础信息种子。"""

    device_code: str
    device_name: str
    device_role: str
    role_index: int
    sort_order: int
    capabilities_json: dict[str, Any]
    upstream_device_code: str | None = None


@dataclass(frozen=True)
class TestRackPositionSeed:
    """开发环境工作线货架停靠位基础信息种子。"""

    position_code: str
    position_name: str
    position_role: WorklineRackPositionRole
    allowed_rack_kind: RackKind
    capacity: int
    logic_location_code: str
    external_location_code: str
    device_role: str | None
    priority: int
    metadata_json: dict[str, Any]


@dataclass(frozen=True)
class MockEcsConnection:
    """测试设备连接到 ECS Mock 的地址配置。"""

    host: str
    port: int


TEST_ROUGH_SORTER_DEVICES: tuple[TestDeviceSeed, ...] = (
    TestDeviceSeed(
        device_code="RS-INPUT-ARM-01",
        device_name="测试粗分机入料机械臂",
        device_role=ROLE_INPUT_ARM,
        role_index=1,
        sort_order=10,
        capabilities_json={
            "supports_event_types": [EVENT_SCAN_COMPLETED, EVENT_ROUGH_SORTER_STORAGE_RETRY],
            "supports_command_types": [ACTION_PICK_AND_PUT, ACTION_MOVE_TO_NG],
            "supports_ack_response": True,
            "supports_result_callback": True,
            "status_path": MOCK_ECS_STATUS_PATH,
        },
    ),
    TestDeviceSeed(
        device_code="RS-CONVEYOR-01",
        device_name="测试粗分机输送线",
        device_role=ROLE_CONVEYOR,
        role_index=1,
        sort_order=20,
        upstream_device_code="RS-INPUT-ARM-01",
        capabilities_json={
            "supports_command_types": [ACTION_MOVE_FORWARD],
            "supports_ack_response": True,
            "supports_result_callback": True,
            "status_path": MOCK_ECS_STATUS_PATH,
        },
    ),
    TestDeviceSeed(
        device_code="RS-OUTPUT-ARM-01",
        device_name="测试粗分机出料机械臂",
        device_role=ROLE_OUTPUT_ARM,
        role_index=1,
        sort_order=30,
        upstream_device_code="RS-CONVEYOR-01",
        capabilities_json={
            "supports_command_types": [ACTION_PUT_TO_BIN],
            "supports_ack_response": True,
            "supports_result_callback": True,
            "status_path": MOCK_ECS_STATUS_PATH,
        },
    ),
)

TEST_ROUGH_SORTER_RACK_POSITIONS: tuple[TestRackPositionSeed, ...] = (
    TestRackPositionSeed(
        position_code="SINGLE_LAYER_A",
        position_name="测试粗分机单层货架工作位 A",
        position_role=WorklineRackPositionRole.SMT_CLASSIFIER_SINGLE_RACK_WORK,
        allowed_rack_kind=RackKind.SINGLE_LAYER,
        capacity=1,
        logic_location_code="WL-ROUGH-SORTER-TEST:SINGLE_LAYER_A",
        external_location_code="SINGLE_LAYER_A",
        device_role=ROLE_OUTPUT_ARM,
        priority=100,
        metadata_json={"seed_source": "local-dev"},
    ),
)

TEST_SMT_SORTING_INBOUND_DEVICES: tuple[TestDeviceSeed, ...] = (
    TestDeviceSeed(
        device_code="SORT-SOURCE-ARM-01",
        device_name="测试 SMT 分拣入库源端机械臂",
        device_role=ROLE_SORTING_SOURCE_ARM,
        role_index=1,
        sort_order=10,
        capabilities_json={
            "supports_command_types": [COMMAND_SOURCE_PICK],
            "supports_event_types": [],
            "supports_ack_response": True,
            "supports_result_callback": True,
            "status_path": MOCK_ECS_STATUS_PATH,
        },
    ),
    TestDeviceSeed(
        device_code="SORT-TARGET-ARM-01",
        device_name="测试 SMT 分拣入库目标机械臂",
        device_role=ROLE_SORTING_TARGET_ARM,
        role_index=1,
        sort_order=20,
        capabilities_json={
            "supports_command_types": [COMMAND_TARGET_PLACE, COMMAND_NG_PLACE],
            "supports_event_types": [],
            "supports_ack_response": True,
            "supports_result_callback": True,
            "status_path": MOCK_ECS_STATUS_PATH,
        },
    ),
    TestDeviceSeed(
        device_code="SORT-SCAN-PLATFORM-01",
        device_name="测试 SMT 分拣入库扫码平台",
        device_role=ROLE_SORTING_SCAN_PLATFORM,
        role_index=1,
        sort_order=30,
        capabilities_json={
            "supports_command_types": [],
            "supports_event_types": [EVENT_WORKING_BIN_SCAN],
            "supports_ack_response": True,
            "supports_result_callback": True,
            "status_path": MOCK_ECS_STATUS_PATH,
        },
    ),
    TestDeviceSeed(
        device_code="SORT-WORKSTATION-01",
        device_name="测试 SMT 分拣入库工作站",
        device_role=ROLE_SORTING_WORKSTATION,
        role_index=1,
        sort_order=40,
        capabilities_json={
            "supports_command_types": [],
            "supports_event_types": [],
            "supports_ack_response": True,
            "supports_result_callback": True,
            "status_path": MOCK_ECS_STATUS_PATH,
        },
    ),
)

TEST_SMT_SORTING_INBOUND_RACK_POSITIONS: tuple[TestRackPositionSeed, ...] = (
    TestRackPositionSeed(
        position_code="SOURCE_STATION_A",
        position_name="测试 SMT 分拣入库源端 Station A",
        position_role=WorklineRackPositionRole.SMT_SORTER_STATION,
        allowed_rack_kind=RackKind.SINGLE_LAYER,
        capacity=1,
        logic_location_code=f"{TEST_SMT_SORTING_INBOUND_LINE_CODE}:SOURCE_STATION_A",
        external_location_code="SOURCE_STATION_A",
        device_role=ROLE_SORTING_SOURCE_ARM,
        priority=100,
        metadata_json={
            "seed_source": "local-dev",
            "single_layer_boundary": True,
            "station_role": "SOURCE",
        },
    ),
    TestRackPositionSeed(
        position_code="SOURCE_STATION_B",
        position_name="测试 SMT 分拣入库源端 Station B",
        position_role=WorklineRackPositionRole.SMT_SORTER_STATION,
        allowed_rack_kind=RackKind.SINGLE_LAYER,
        capacity=1,
        logic_location_code=f"{TEST_SMT_SORTING_INBOUND_LINE_CODE}:SOURCE_STATION_B",
        external_location_code="SOURCE_STATION_B",
        device_role=ROLE_SORTING_SOURCE_ARM,
        priority=110,
        metadata_json={
            "seed_source": "local-dev",
            "single_layer_boundary": True,
            "station_role": "SOURCE",
        },
    ),
    TestRackPositionSeed(
        position_code="TARGET_STATION",
        position_name="测试 SMT 分拣入库目标 Station",
        position_role=WorklineRackPositionRole.SMT_SORTER_STATION,
        allowed_rack_kind=RackKind.FIVE_LAYER,
        capacity=1,
        logic_location_code=f"{TEST_SMT_SORTING_INBOUND_LINE_CODE}:TARGET_STATION",
        external_location_code="TARGET_STATION",
        device_role=ROLE_SORTING_TARGET_ARM,
        priority=120,
        metadata_json={
            "seed_source": "local-dev",
            "rack_boundary": "FIVE_LAYER",
            "station_role": "TARGET",
        },
    ),
)

TEST_ROUGH_SORTER_SEED = TestWorklineSeed(
    line_code=TEST_ROUGH_SORTER_LINE_CODE,
    line_name="测试粗分机作业线",
    line_type=LineType.AUTO,
    zone_name="开发库",
    plugin_key=ROUGH_SORTER_PLUGIN_KEY,
    contract_version=ROUGH_SORTER_CONTRACT_VERSION,
    config={"seed_source": "local-dev"},
    runtime_config_json={
        "run_mode": WorkLineRunMode.AUTO.value,
        "sandbox_enabled": False,
        "device_status_timeout_seconds": 2.0,
    },
    run_mode=WorkLineRunMode.AUTO,
    runtime_status=WorkLineRuntimeStatus.STOPPED,
    diagnostic_profile={
        "owner": "WES 开发环境",
        "seed_source": "local-dev",
    },
    description="本地开发环境自动同步的粗分机基础作业线",
    devices=TEST_ROUGH_SORTER_DEVICES,
    rack_positions=TEST_ROUGH_SORTER_RACK_POSITIONS,
)

TEST_SMT_SORTING_INBOUND_SEED = TestWorklineSeed(
    line_code=TEST_SMT_SORTING_INBOUND_LINE_CODE,
    line_name="测试 SMT 分拣入库作业线",
    line_type=LineType.AUTO,
    zone_name="开发库",
    plugin_key=SMT_SORTING_INBOUND_PLUGIN_KEY,
    contract_version=SMT_SORTING_INBOUND_CONTRACT_VERSION,
    config={"seed_source": "local-dev"},
    runtime_config_json={
        "run_mode": WorkLineRunMode.SIMULATION.value,
        "sandbox_enabled": True,
        "device_status_timeout_seconds": 2.0,
    },
    run_mode=WorkLineRunMode.SIMULATION,
    runtime_status=WorkLineRuntimeStatus.STOPPED,
    diagnostic_profile={
        "owner": "WES 开发环境",
        "seed_source": "local-dev",
    },
    description="本地开发环境自动同步的 SMT 分拣入库基础作业线",
    devices=TEST_SMT_SORTING_INBOUND_DEVICES,
    rack_positions=TEST_SMT_SORTING_INBOUND_RACK_POSITIONS,
)

TEST_WORKLINE_SEEDS: tuple[TestWorklineSeed, ...] = (
    TEST_ROUGH_SORTER_SEED,
    TEST_SMT_SORTING_INBOUND_SEED,
)


def _assert_debug_seed_allowed() -> None:
    configured_env = str(settings.APP_ENV).strip().lower()
    process_env = os.getenv("APP_ENV")
    checked_envs = [configured_env]
    if process_env is not None:
        checked_envs.append(process_env.strip().lower())

    blocked_env = next((app_env for app_env in checked_envs if app_env not in SANDBOX_ALLOWED_ENVS), None)
    if blocked_env is not None:
        allowed_envs = ", ".join(sorted(SANDBOX_ALLOWED_ENVS))
        raise RuntimeError(
            f"APP_ENV={blocked_env} 不允许同步开发/测试调试 WorkLine/Device 主数据；仅允许: {allowed_envs}"
        )


def _mock_ecs_connection_from_env() -> MockEcsConnection:
    mock_ecs_url = os.getenv("MOCK_ECS_URL")
    if mock_ecs_url:
        parsed = urlparse(mock_ecs_url)
        if not parsed.hostname:
            raise ValueError("MOCK_ECS_URL 必须包含可解析的主机名")
        default_port = 443 if parsed.scheme == "https" else 80
        return MockEcsConnection(host=parsed.hostname, port=parsed.port or default_port)

    raw_port = os.getenv("MOCK_ECS_PORT")
    port = int(raw_port) if raw_port else DEFAULT_MOCK_ECS_PORT
    return MockEcsConnection(
        host=os.getenv("MOCK_ECS_HOST", DEFAULT_MOCK_ECS_HOST),
        port=port,
    )


def _set_attrs(entity: Any, values: dict[str, Any]) -> bool:
    changed = False
    for field_name, value in values.items():
        if getattr(entity, field_name) != value:
            setattr(entity, field_name, value)
            changed = True
    return changed


async def _get_workline_by_code(db: AsyncSession, line_code: str) -> WorkLine | None:
    result = await db.execute(
        select(WorkLine).where(
            WorkLine.line_code == line_code,  # type: ignore[arg-type]
            WorkLine.is_deleted.is_(False),  # type: ignore[arg-type]
        )
    )
    return result.scalar_one_or_none()


async def _get_device_by_code(db: AsyncSession, device_code: str) -> Device | None:
    result = await db.execute(
        select(Device).where(
            Device.device_code == device_code,  # type: ignore[arg-type]
            Device.is_deleted.is_(False),  # type: ignore[arg-type]
        )
    )
    return result.scalar_one_or_none()


async def _get_rack_position_by_code(
    db: AsyncSession,
    *,
    workline_code: str,
    position_code: str,
) -> WorklineRackPosition | None:
    result = await db.execute(
        select(WorklineRackPosition).where(
            WorklineRackPosition.workline_code == workline_code,  # type: ignore[arg-type]
            WorklineRackPosition.position_code == position_code,  # type: ignore[arg-type]
        )
    )
    return result.scalar_one_or_none()


async def _upsert_test_workline(db: AsyncSession, seed: TestWorklineSeed) -> tuple[WorkLine, str]:
    values = {
        "line_code": seed.line_code,
        "line_name": seed.line_name,
        "line_type": seed.line_type,
        "zone_name": seed.zone_name,
        "plugin_key": seed.plugin_key,
        "contract_version": seed.contract_version,
        "config": seed.config,
        "runtime_config_json": seed.runtime_config_json,
        "run_mode": seed.run_mode,
        "diagnostic_profile": seed.diagnostic_profile,
        "description": seed.description,
        "is_active": seed.is_active,
    }

    workline = await _get_workline_by_code(db, seed.line_code)
    if workline is None:
        workline = WorkLine(**values, runtime_status=seed.runtime_status)
        db.add(workline)
        await db.flush()
        return workline, "created"

    changed = _set_attrs(workline, values)
    await db.flush()
    return workline, "updated" if changed else "unchanged"


async def _upsert_test_devices(db: AsyncSession, workline: WorkLine, seed: TestWorklineSeed) -> dict[str, str]:
    workline_id = workline.id
    if workline_id is None:
        raise RuntimeError("测试 WorkLine 缺少主键，无法同步 Device")

    states: dict[str, str] = {}
    devices_by_code: dict[str, Device] = {}

    mock_ecs_connection = _mock_ecs_connection_from_env()

    for device_seed in seed.devices:
        synced_values = {
            "device_code": device_seed.device_code,
            "device_name": device_seed.device_name,
            "work_line_id": workline_id,
            "description": f"本地开发环境自动同步的 {seed.line_name} 基础设备",
            "is_active": True,
            "sort_order": device_seed.sort_order,
            "device_role": device_seed.device_role,
            "role_index": device_seed.role_index,
            "vendor_type": "SANDBOX",
            "capabilities_json": device_seed.capabilities_json,
            "max_concurrent_tasks": 1,
            "idempotency_ttl": 3600,
            "diagnostic_profile": {
                "owner": "WES 开发环境",
                "seed_source": "local-dev",
            },
        }
        create_values = {
            **synced_values,
            "host": mock_ecs_connection.host,
            "port": mock_ecs_connection.port,
            "protocol": DeviceProtocol.HTTP,
            "timeout": 300000,
            "callback_path": MOCK_ECS_COMMAND_PATH,
        }

        device = await _get_device_by_code(db, device_seed.device_code)
        if device is None:
            device = Device(
                **create_values,
                device_status=DeviceStatus.IDLE,
                current_command_id=None,
                error_code=None,
                maintenance_mode=False,
            )
            db.add(device)
            await db.flush()
            states[device_seed.device_code] = "created"
        else:
            # 防御性保留：已有设备的通信配置可能指向现场联调硬件，不能被默认种子值覆盖。
            changed = _set_attrs(device, synced_values)
            await db.flush()
            states[device_seed.device_code] = "updated" if changed else "unchanged"
        devices_by_code[device_seed.device_code] = device

    for device_seed in seed.devices:
        device = devices_by_code[device_seed.device_code]
        upstream_id = None
        if device_seed.upstream_device_code:
            upstream = devices_by_code[device_seed.upstream_device_code]
            upstream_id = upstream.id
        if device.upstream_device_id != upstream_id:
            device.upstream_device_id = upstream_id
            if states[device_seed.device_code] == "unchanged":
                states[device_seed.device_code] = "updated"

    await db.flush()
    workline_device_cache.invalidate(workline_id)
    return states


async def _upsert_test_rack_positions(db: AsyncSession, workline: WorkLine, seed: TestWorklineSeed) -> dict[str, str]:
    workline_id = workline.id
    if workline_id is None:
        raise RuntimeError("测试 WorkLine 缺少主键，无法同步货架停靠位")

    states: dict[str, str] = {}
    for position_seed in seed.rack_positions:
        values = {
            "workline_id": workline_id,
            "workline_code": seed.line_code,
            "position_code": position_seed.position_code,
            "position_name": position_seed.position_name,
            "position_role": position_seed.position_role,
            "allowed_rack_kind": position_seed.allowed_rack_kind,
            "capacity": position_seed.capacity,
            "logic_location_code": position_seed.logic_location_code,
            "external_location_code": position_seed.external_location_code,
            "device_role": position_seed.device_role,
            "priority": position_seed.priority,
            "enabled": True,
            "metadata_json": position_seed.metadata_json,
        }
        position = await _get_rack_position_by_code(
            db,
            workline_code=seed.line_code,
            position_code=position_seed.position_code,
        )
        if position is None:
            db.add(WorklineRackPosition(**values))
            await db.flush()
            states[position_seed.position_code] = "created"
            continue

        changed = _set_attrs(position, values)
        await db.flush()
        states[position_seed.position_code] = "updated" if changed else "unchanged"
    return states


def _summarize(states: list[str]) -> dict[str, int]:
    return {
        "created": states.count("created"),
        "updated": states.count("updated"),
        "unchanged": states.count("unchanged"),
    }


async def sync_test_workline_devices(db: AsyncSession, *, commit: bool = True) -> dict[str, Any]:
    """按 line_code/device_code 幂等同步本地开发基础信息。"""

    _assert_debug_seed_allowed()

    worklines_by_code: dict[str, dict[str, Any]] = {}
    devices_by_workline: dict[str, dict[str, str]] = {}
    rack_positions_by_workline: dict[str, dict[str, str]] = {}
    summary_by_workline: dict[str, dict[str, dict[str, int]]] = {}

    for seed in TEST_WORKLINE_SEEDS:
        workline, workline_state = await _upsert_test_workline(db, seed)
        device_states = await _upsert_test_devices(db, workline, seed)
        rack_position_states = await _upsert_test_rack_positions(db, workline, seed)
        worklines_by_code[seed.line_code] = {
            "line_code": seed.line_code,
            "state": workline_state,
            "id": workline.id,
        }
        devices_by_workline[seed.line_code] = device_states
        rack_positions_by_workline[seed.line_code] = rack_position_states
        summary_by_workline[seed.line_code] = {
            "worklines": _summarize([workline_state]),
            "devices": _summarize(list(device_states.values())),
            "rack_positions": _summarize(list(rack_position_states.values())),
        }

    if commit:
        await db.commit()
    else:
        await db.flush()

    workline_count_result = await db.execute(select(func.count()).select_from(WorkLine))
    device_count_result = await db.execute(select(func.count()).select_from(Device))
    rough_summary = summary_by_workline[TEST_ROUGH_SORTER_LINE_CODE]
    return {
        "workline": worklines_by_code[TEST_ROUGH_SORTER_LINE_CODE],
        "devices": devices_by_workline[TEST_ROUGH_SORTER_LINE_CODE],
        "rack_positions": rack_positions_by_workline[TEST_ROUGH_SORTER_LINE_CODE],
        "summary": {
            "worklines": rough_summary["worklines"],
            "devices": rough_summary["devices"],
            "rack_positions": rough_summary["rack_positions"],
            "total_worklines": int(workline_count_result.scalar_one() or 0),
            "total_devices": int(device_count_result.scalar_one() or 0),
        },
        "worklines_by_code": worklines_by_code,
        "devices_by_workline": devices_by_workline,
        "rack_positions_by_workline": rack_positions_by_workline,
        "summary_by_workline": summary_by_workline,
    }


async def _run(*, dry_run: bool = False) -> dict[str, Any]:
    from src.core.conf import settings

    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async_session_maker = async_sessionmaker(engine, expire_on_commit=False, autocommit=False, autoflush=False)

    try:
        async with async_session_maker() as db:
            result = await sync_test_workline_devices(db, commit=not dry_run)
            if dry_run:
                await db.rollback()
            return result
    finally:
        await engine.dispose()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="同步 testing 环境 WorkLine 与 Device 基础数据")
    parser.add_argument("--dry-run", action="store_true", help="执行同步逻辑但回滚事务")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    result = asyncio.run(_run(dry_run=args.dry_run))
    dry_run_label = " (dry-run)" if args.dry_run else ""
    print(f"✅ dev WorkLine/Device 基础数据同步完成{dry_run_label}: {result['summary']}")


if __name__ == "__main__":
    main()
