"""同步测试环境 WorkLine 与 Device 基础数据。"""

# ruff: noqa: E402

import argparse
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from src.app.device.models import Device, DeviceProtocol, DeviceStatus
from src.app.workline.models import LineType, WorkLine, WorkLineRunMode
from src.app.workline.models.safety import WorkLineRuntimeStatus
from src.utils.device_cache import workline_device_cache
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

TEST_ROUGH_SORTER_LINE_CODE = "WL-ROUGH-SORTER-TEST"


@dataclass(frozen=True)
class TestDeviceSeed:
    """测试环境设备基础信息种子。"""

    device_code: str
    device_name: str
    device_role: str
    role_index: int
    sort_order: int
    capabilities_json: dict[str, Any]
    upstream_device_code: str | None = None


TEST_ROUGH_SORTER_DEVICES: tuple[TestDeviceSeed, ...] = (
    TestDeviceSeed(
        device_code="RS-INPUT-ARM-01",
        device_name="测试粗分机入料机械臂",
        device_role=ROLE_INPUT_ARM,
        role_index=1,
        sort_order=10,
        capabilities_json={
            "supports_event_types": [EVENT_SCAN_COMPLETED],
            "supports_command_types": [ACTION_PICK_AND_PUT, ACTION_MOVE_TO_NG],
            "supports_ack_response": True,
            "supports_result_callback": True,
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
        },
    ),
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


async def _upsert_test_workline(db: AsyncSession) -> tuple[WorkLine, str]:
    values = {
        "line_code": TEST_ROUGH_SORTER_LINE_CODE,
        "line_name": "测试粗分机作业线",
        "line_type": LineType.AUTO,
        "zone_name": "测试库",
        "plugin_key": ROUGH_SORTER_PLUGIN_KEY,
        "contract_version": ROUGH_SORTER_CONTRACT_VERSION,
        "config": {"seed_source": "jenkins-testing"},
        "runtime_config_json": {
            "run_mode": WorkLineRunMode.SIMULATION.value,
            "sandbox_enabled": True,
        },
        "run_mode": WorkLineRunMode.SIMULATION,
        "diagnostic_profile": {
            "owner": "WES 测试环境",
            "seed_source": "jenkins-testing",
        },
        "description": "Jenkins testing 部署自动同步的粗分机基础作业线",
        "is_active": True,
    }

    workline = await _get_workline_by_code(db, TEST_ROUGH_SORTER_LINE_CODE)
    if workline is None:
        workline = WorkLine(**values, runtime_status=WorkLineRuntimeStatus.READY)
        db.add(workline)
        await db.flush()
        return workline, "created"

    changed = _set_attrs(workline, values)
    await db.flush()
    return workline, "updated" if changed else "unchanged"


async def _upsert_test_devices(db: AsyncSession, workline: WorkLine) -> dict[str, str]:
    workline_id = workline.id
    if workline_id is None:
        raise RuntimeError("测试 WorkLine 缺少主键，无法同步 Device")

    states: dict[str, str] = {}
    devices_by_code: dict[str, Device] = {}

    for seed in TEST_ROUGH_SORTER_DEVICES:
        values = {
            "device_code": seed.device_code,
            "device_name": seed.device_name,
            "work_line_id": workline_id,
            "description": "Jenkins testing 部署自动同步的粗分机基础设备",
            "is_active": True,
            "sort_order": seed.sort_order,
            "device_role": seed.device_role,
            "role_index": seed.role_index,
            "vendor_type": "SANDBOX",
            "capabilities_json": seed.capabilities_json,
            "host": seed.device_code,
            "port": 8080,
            "protocol": DeviceProtocol.HTTP,
            "timeout": 300000,
            "callback_path": "/api/v1/device/command",
            "max_concurrent_tasks": 1,
            "idempotency_ttl": 3600,
            "diagnostic_profile": {
                "owner": "WES 测试环境",
                "seed_source": "jenkins-testing",
            },
        }

        device = await _get_device_by_code(db, seed.device_code)
        if device is None:
            device = Device(
                **values,
                device_status=DeviceStatus.IDLE,
                current_command_id=None,
                error_code=None,
                maintenance_mode=False,
            )
            db.add(device)
            await db.flush()
            states[seed.device_code] = "created"
        else:
            changed = _set_attrs(device, values)
            await db.flush()
            states[seed.device_code] = "updated" if changed else "unchanged"
        devices_by_code[seed.device_code] = device

    for seed in TEST_ROUGH_SORTER_DEVICES:
        device = devices_by_code[seed.device_code]
        upstream_id = None
        if seed.upstream_device_code:
            upstream = devices_by_code[seed.upstream_device_code]
            upstream_id = upstream.id
        if device.upstream_device_id != upstream_id:
            device.upstream_device_id = upstream_id
            if states[seed.device_code] == "unchanged":
                states[seed.device_code] = "updated"

    await db.flush()
    workline_device_cache.invalidate(workline_id)
    return states


def _summarize(states: list[str]) -> dict[str, int]:
    return {
        "created": states.count("created"),
        "updated": states.count("updated"),
        "unchanged": states.count("unchanged"),
    }


async def sync_test_workline_devices(db: AsyncSession, *, commit: bool = True) -> dict[str, Any]:
    """幂等同步 testing 环境粗分机 WorkLine 与 Device 基础信息。"""

    workline, workline_state = await _upsert_test_workline(db)
    device_states = await _upsert_test_devices(db, workline)
    if commit:
        await db.commit()
    else:
        await db.flush()

    workline_count_result = await db.execute(select(func.count()).select_from(WorkLine))
    device_count_result = await db.execute(select(func.count()).select_from(Device))
    return {
        "workline": {
            "line_code": TEST_ROUGH_SORTER_LINE_CODE,
            "state": workline_state,
            "id": workline.id,
        },
        "devices": device_states,
        "summary": {
            "worklines": _summarize([workline_state]),
            "devices": _summarize(list(device_states.values())),
            "total_worklines": int(workline_count_result.scalar_one() or 0),
            "total_devices": int(device_count_result.scalar_one() or 0),
        },
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
    print(f"✅ testing WorkLine/Device 基础数据同步完成{dry_run_label}: {result['summary']}")


if __name__ == "__main__":
    main()
