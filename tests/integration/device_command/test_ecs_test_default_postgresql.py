"""独占 PostgreSQL 上验证 ECS_TEST 默认值迁移、持久化和并发写入。"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from typing import TYPE_CHECKING

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.app.device.models.command import ECS_TEST_REF_TYPE, CommandStatus, DeviceCommand
from src.app.device.models.device import Device
from src.app.device.repositories.device_repository import device_repository
from src.app.device.services.device_service import device_service
from src.app.workline.models.workline import LineType, WorkLine, WorkLineRunMode
from src.core import rbac
from src.core.exceptions import OptimisticLockException
from src.core.security import require_auth
from src.database.dependencies import _get_cache_service, get_db
from src.register import register_exception, register_routers
from src.utils.timezone import timezone
from tests.support.postgresql_heavy import migrated_database, run_alembic, temporary_database

pytestmark = pytest.mark.integration
DEVICE_DEFAULT_BASE_REVISION = "5bac3de5c2b5"

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _default(target_device_code: str, params: dict[str, object]) -> dict[str, object]:
    return {"target_device_code": target_device_code, "task_type": "MOVE_FORWARD", "params": params}


@pytest.mark.asyncio
async def test_migration_preserves_existing_device_and_downgrades_in_temporary_database() -> None:
    async with temporary_database() as (_database, url):
        run_alembic("upgrade", DEVICE_DEFAULT_BASE_REVISION, database_url=url)
        connection = await asyncpg.connect(url.replace("postgresql+asyncpg://", "postgresql://", 1))
        try:
            columns = await connection.fetch(
                """SELECT column_name, is_nullable, column_default
                FROM information_schema.columns
                WHERE table_schema = 'wes_biz' AND table_name = 'devices'"""
            )
            column_names = {row["column_name"] for row in columns}
            required = {
                row["column_name"]
                for row in columns
                if row["is_nullable"] == "NO" and row["column_default"] is None and row["column_name"] != "id"
            }
            values: dict[str, object] = {
                "device_code": "EXISTING-MIGRATION-SOURCE",
                "device_name": "迁移前来源设备",
                "created_at": timezone.now_for_db(),
                "is_active": True,
                "sort_order": 0,
                "diagnostic_profile": json.dumps({}),
                "device_role": "SCAN",
                "role_index": 0,
            }
            values = {name: value for name, value in values.items() if name in column_names}
            assert required <= values.keys()
            names = list(values)
            sql = (
                f"INSERT INTO wes_biz.devices ({', '.join(names)}) "
                f"VALUES ({', '.join(f'${index}' for index in range(1, len(names) + 1))})"
            )
            await connection.execute(sql, *values.values())
        finally:
            await connection.close()

        run_alembic("upgrade", "head", database_url=url)
        engine = create_async_engine(
            url,
            poolclass=NullPool,
            connect_args={"server_settings": {"search_path": "wes_biz, wes_runtime, wes_sys, public"}},
        )
        try:
            async with async_sessionmaker(engine)() as db:
                existing = await db.scalar(select(Device).where(Device.device_code == "EXISTING-MIGRATION-SOURCE"))
                assert existing is not None
                assert existing.ecs_test_default_json is None
        finally:
            await engine.dispose()

        run_alembic("downgrade", DEVICE_DEFAULT_BASE_REVISION, database_url=url)
        connection = await asyncpg.connect(url.replace("postgresql+asyncpg://", "postgresql://", 1))
        try:
            assert (
                await connection.fetchval(
                    """SELECT count(*) FROM information_schema.columns
                WHERE table_schema = 'wes_biz' AND table_name = 'devices'
                AND column_name = 'ecs_test_default_json'"""
                )
                == 0
            )
            assert (
                await connection.fetchval(
                    "SELECT count(*) FROM wes_biz.devices WHERE device_code = 'EXISTING-MIGRATION-SOURCE'"
                )
                == 1
            )
        finally:
            await connection.close()


@pytest.mark.asyncio
async def test_save_and_clear_leave_active_workline_and_inflight_command_unchanged() -> None:
    original_rules = {
        "ecs_test_rules": [
            {
                "source_device_code": "SOURCE-1",
                "target_device_code": "OLD-TARGET",
                "task_type": "MOVE_FORWARD",
                "params": {},
            }
        ]
    }
    now = timezone.now_for_db()

    async with migrated_database() as (_url, sessions):
        async with sessions() as db:
            workline = WorkLine(
                line_code="ECS-DEFAULT-ACTIVE-LINE",
                line_name="ECS default active line",
                line_type=LineType.AUTO,
                is_active=True,
                run_mode=WorkLineRunMode.ECS_TEST,
                runtime_config_json=original_rules,
            )
            db.add(workline)
            await db.flush()
            device = Device(device_code="SOURCE-1", device_name="来源设备", work_line_id=workline.id)
            db.add(device)
            await db.flush()
            command = DeviceCommand(
                command_code="CMD-ECS-DEFAULT-IN-FLIGHT",
                device_code=device.device_code,
                workline_id=workline.id,
                execution_ref_type=ECS_TEST_REF_TYPE,
                execution_ref_id="ECS-DEFAULT-IN-FLIGHT",
                contract_key="ecs_test.scan",
                contract_version="1.0",
                task_type="MOVE_FORWARD",
                params={"original": "payload"},
                deadline_at=now + timedelta(minutes=5),
                endpoint_base_url="http://ecs-test:8080",
                command_timeout_ms=30_000,
                status_max_age_ms=1_000,
                payload_digest="a" * 64,
                status=CommandStatus.DISPATCHING,
            )
            db.add(command)
            await db.commit()
            device_id = device.id
            command_id = command.id
            workline_id = workline.id
            version_before = device.version
            workline_version_before = workline.version
            command_params_before = dict(command.params)

        async with sessions() as db:
            saved = await device_service.save_ecs_test_default(
                db,
                "SOURCE-1",
                _default("TARGET-2", {}),
            )
            assert saved is not None
            assert saved.ecs_test_default_json == _default("TARGET-2", {})
            assert saved.version == version_before + 1

            cleared = await device_service.save_ecs_test_default(db, "SOURCE-1", None)
            assert cleared is not None
            assert cleared.ecs_test_default_json is None
            assert cleared.version == version_before + 2

            with pytest.raises(OptimisticLockException):
                await device_service.update(
                    db,
                    device_id,
                    {"device_name": "stale update", "version": version_before},
                )

        async with sessions() as db:
            workline = await db.get(WorkLine, workline_id)
            command = await db.get(DeviceCommand, command_id)
            device = await db.get(Device, device_id)
            assert device is not None and device.ecs_test_default_json is None
            assert workline is not None
            assert workline.is_active is True
            assert workline.runtime_config_json == original_rules
            assert workline.version == workline_version_before
            assert command is not None
            assert command.status == CommandStatus.DISPATCHING
            assert command.params == command_params_before


@pytest.mark.asyncio
async def test_saving_default_updates_device_audit_user(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = 8642
    monkeypatch.setattr("src.utils.audit.get_current_user_id", lambda: user_id)

    async with migrated_database() as (_url, sessions):
        async with sessions() as db:
            db.add(Device(device_code="SOURCE-AUDIT", device_name="审计来源设备"))
            await db.commit()

        async with sessions() as db:
            saved = await device_service.save_ecs_test_default(
                db,
                "SOURCE-AUDIT",
                _default("TARGET-AUDIT", {}),
            )

        assert saved is not None
        assert saved.updated_by == user_id


@pytest.mark.asyncio
async def test_repository_returns_none_for_missing_device_in_temporary_database() -> None:
    async with migrated_database() as (_url, sessions):
        async with sessions() as db:
            assert await device_repository.set_ecs_test_default(db, "MISSING-SOURCE", _default("TARGET", {})) is None
            assert await db.scalar(select(Device).where(Device.device_code == "MISSING-SOURCE")) is None


@pytest.mark.asyncio
async def test_asgi_save_then_get_returns_persisted_default_in_temporary_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with migrated_database() as (_url, sessions):
        async with sessions() as db:
            db.add(Device(device_code="SOURCE-ASGI", device_name="ASGI 来源设备"))
            await db.commit()

        app = FastAPI()
        register_exception(app)
        register_routers(app)
        app.dependency_overrides[require_auth] = lambda: 42
        app.dependency_overrides[_get_cache_service] = lambda: None

        async def override_db() -> AsyncIterator[AsyncSession]:
            async with sessions() as db:
                yield db

        async def get_permissions(*_args: object) -> set[str]:
            return {"biz:device:detail", "biz:device:update"}

        app.dependency_overrides[get_db] = override_db
        monkeypatch.setattr(rbac, "get_user_permissions", get_permissions)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            put_response = await client.put(
                "/api/v1/device/devices/SOURCE-ASGI/ecs-test-default",
                json={"default": _default("TARGET-ASGI", {})},
            )
            get_response = await client.get("/api/v1/device/devices/SOURCE-ASGI/ecs-test-default")

        assert put_response.status_code == 200
        assert put_response.json()["data"]["default"] == _default("TARGET-ASGI", {})
        assert get_response.status_code == 200
        assert get_response.json()["data"]["default"] == _default("TARGET-ASGI", {})


@pytest.mark.asyncio
async def test_concurrent_saves_wait_on_device_row_and_replace_full_default() -> None:
    first_default = _default("TARGET-FIRST", {"source": "first"})
    second_default = _default("TARGET-SECOND", {"source": "second"})
    async with migrated_database() as (url, sessions):
        async with sessions() as db:
            device = Device(device_code="SOURCE-CONCURRENT", device_name="并发来源设备")
            db.add(device)
            await db.commit()
            device_id = device.id
            initial_version = device.version

        connection = await asyncpg.connect(url.replace("postgresql+asyncpg://", "postgresql://", 1))
        second_pid: asyncio.Future[int] = asyncio.get_running_loop().create_future()

        async def second_save() -> Device | None:
            async with sessions() as db:
                pid = int(await db.scalar(text("SELECT pg_backend_pid()")))
                second_pid.set_result(pid)
                return await device_service.save_ecs_test_default(db, "SOURCE-CONCURRENT", second_default)

        async with sessions() as first:
            await device_repository.set_ecs_test_default(first, "SOURCE-CONCURRENT", first_default)
            second_task = asyncio.create_task(second_save())
            waiting_on_lock = False
            try:
                pid = await asyncio.wait_for(second_pid, timeout=5)
                for _ in range(100):
                    wait_event = await connection.fetchval(
                        "SELECT wait_event_type FROM pg_stat_activity WHERE pid = $1",
                        pid,
                    )
                    if wait_event == "Lock":
                        waiting_on_lock = True
                        break
                    await asyncio.sleep(0.02)
            finally:
                await first.commit()
            second_result = await asyncio.wait_for(second_task, timeout=10)

        try:
            assert waiting_on_lock is True
            assert second_result is not None
            assert second_result.ecs_test_default_json == second_default
            assert second_result.version == initial_version + 2
            async with sessions() as db:
                persisted = await db.get(Device, device_id)
                assert persisted is not None
                assert persisted.ecs_test_default_json == second_default
                assert persisted.version == initial_version + 2
        finally:
            await connection.close()
