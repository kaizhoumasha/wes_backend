"""DeviceRuntimeProjection DB-backed writer service tests."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from src.app.device.models import Device, DeviceProtocol, DeviceStatus
from src.app.runtime.orchestration.device_runtime_projection import DeviceRuntimeProjection
from src.app.runtime.orchestration.models.session import WorklineSession
from src.app.runtime.orchestration.repositories.device_runtime_projection_repository import (
    DeviceRuntimeProjectionRepository,
)
from src.utils.timezone import timezone


class _DeviceProjectionUniqueRaceRepository(DeviceRuntimeProjectionRepository):
    """模拟首次读取为空后并发插入同 device_code 的唯一冲突。"""

    def __init__(self) -> None:
        super().__init__()
        self.read_count = 0

    async def get_by_device_code(self, db, device_code: str):
        self.read_count += 1
        if self.read_count == 1:
            return None
        return await super().get_by_device_code(db, device_code)

    async def create(self, *_args, **_kwargs):
        raise IntegrityError("INSERT INTO device_runtime_projections", {}, Exception("unique device_code"))

    async def create_without_session_rollback(self, db, data):
        projection = DeviceRuntimeProjection(**data)
        db.add(projection)
        await db.flush()
        await db.refresh(projection)
        return projection


async def _projection_count(db_session) -> int:
    result = await db_session.execute(select(func.count()).select_from(DeviceRuntimeProjection))
    return int(result.scalar_one())


@pytest.mark.asyncio
async def test_device_runtime_projection_writer_upserts_single_projection(db_session) -> None:
    """同 device_code 的运行态投影必须持久 upsert，而不是追加多条 active 状态。"""

    from src.app.runtime.orchestration.services.device_runtime_projection_writer_service import (
        DeviceRuntimeProjectionWriterService,
    )

    service = DeviceRuntimeProjectionWriterService()

    first = await service.upsert_from_device(
        db_session,
        device=Device(
            id=10,
            device_code="DEV-RUNTIME-01",
            device_name="Scanner 01",
            device_role="SCANNER",
            role_index=1,
            vendor_type="ECS",
            protocol=DeviceProtocol.HTTP,
            device_status=DeviceStatus.IDLE,
            current_command_id=None,
            maintenance_mode=False,
            max_concurrent_tasks=1,
        ),
        evidence_json={"source": "initial_sync"},
        auto_commit=False,
    )
    replay = await service.upsert_from_device(
        db_session,
        device=Device(
            id=10,
            device_code="DEV-RUNTIME-01",
            device_name="Scanner 01",
            device_role="SCANNER",
            role_index=1,
            work_line_id=3,
            vendor_type="ECS",
            protocol=DeviceProtocol.HTTP,
            device_status=DeviceStatus.RUNNING,
            current_command_id=501,
            maintenance_mode=False,
            max_concurrent_tasks=1,
        ),
        evidence_json={"source": "command_ack"},
        auto_commit=False,
    )

    assert first.id == replay.id
    assert await _projection_count(db_session) == 1
    assert replay.device_id == 10
    assert replay.device_code == "DEV-RUNTIME-01"
    assert replay.workline_id == 3
    assert replay.provider_code == "ECS"
    assert replay.runtime_status == "RUNNING"
    assert replay.current_command_id == 501
    assert replay.in_flight_count == 1
    assert replay.concurrency_limit == 1
    assert replay.evidence_json["source"] == "command_ack"


@pytest.mark.asyncio
async def test_device_runtime_projection_writer_rereads_existing_after_unique_conflict(db_session) -> None:
    """并发首次 upsert 撞 device_code 唯一约束时必须重读 existing，不回滚外层事务。"""

    from src.app.runtime.orchestration.services.device_runtime_projection_writer_service import (
        DeviceRuntimeProjectionWriterService,
    )

    existing = DeviceRuntimeProjection(
        device_id=10,
        device_code="DEV-RUNTIME-RACE",
        provider_code="ECS",
        runtime_status="IDLE",
        status_observed_at=timezone.now_for_db(),
        status_valid_until=timezone.now_for_db(),
        in_flight_count=0,
        concurrency_limit=1,
        evidence_json={"source": "race-winner"},
    )
    db_session.add(existing)
    await db_session.flush()
    assert existing.id is not None

    service = DeviceRuntimeProjectionWriterService(repository=_DeviceProjectionUniqueRaceRepository())
    result = await service.upsert_from_device(
        db_session,
        device=Device(
            id=10,
            device_code="DEV-RUNTIME-RACE",
            device_name="Scanner Race",
            device_role="SCANNER",
            role_index=1,
            vendor_type="ECS",
            protocol=DeviceProtocol.HTTP,
            device_status=DeviceStatus.RUNNING,
            current_command_id=501,
            maintenance_mode=False,
            max_concurrent_tasks=1,
        ),
        evidence_json={"source": "race-loser"},
        auto_commit=False,
    )

    assert result.id == existing.id
    assert result.device_code == "DEV-RUNTIME-RACE"
    assert result.runtime_status == "RUNNING"
    assert result.current_command_id == 501
    assert result.evidence_json["source"] == "race-loser"
    assert await _projection_count(db_session) == 1


@pytest.mark.asyncio
async def test_device_service_mark_command_dispatched_syncs_runtime_projection(db_session) -> None:
    """DeviceService 运行态更新入口必须同步持久 DeviceRuntime 投影。"""

    from src.app.device.services.device_service import DeviceService

    device = Device(
        device_code="DEV-DISPATCH-01",
        device_name="Scanner 01",
        device_role="SCANNER",
        role_index=1,
        vendor_type="ECS",
        protocol=DeviceProtocol.HTTP,
        device_status=DeviceStatus.IDLE,
        current_command_id=None,
        maintenance_mode=False,
        max_concurrent_tasks=1,
    )
    db_session.add(device)
    await db_session.flush()
    assert device.id is not None

    service = DeviceService()

    updated = await service.mark_command_dispatched(
        db_session,
        device_id=device.id,
        command_id=701,
        auto_commit=False,
    )

    assert updated is not None
    result = await db_session.execute(
        select(DeviceRuntimeProjection).where(DeviceRuntimeProjection.device_code == "DEV-DISPATCH-01")
    )
    projection = result.scalar_one()
    assert projection.device_id == device.id
    assert projection.runtime_status == "RUNNING"
    assert projection.current_command_id == 701
    assert projection.in_flight_count == 1
    assert projection.evidence_json["source"] == "device_service_runtime_update"
