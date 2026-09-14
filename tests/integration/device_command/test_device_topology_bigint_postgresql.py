"""真实 PostgreSQL 验证雪花 ID 的工作线配置与设备拓扑。"""

from __future__ import annotations

import subprocess
from datetime import datetime

import asyncpg
import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.app.device.models.command import DeviceCommand
from src.app.device.models.device import Device
from src.app.workline.models.workline import LineType, WorkLine
from tests.support.postgresql_catalog import assert_database_head
from tests.support.postgresql_heavy import migrated_database, run_alembic, temporary_database

pytestmark = pytest.mark.integration
BASE_REVISION = "93deacda8c9c"
BIGINT_REVISION = "bebf575cca2b"
COMMAND_BIGINT_BASE = "e5c5dfb4373f"
COMMAND_BIGINT_REVISION = "70d00a14cbdf"


@pytest.mark.asyncio
async def test_upstream_device_accepts_snowflake_id_and_preserves_foreign_keys() -> None:
    async with migrated_database() as (_url, sessions):
        async with sessions() as db:
            upstream = Device(id=347454468883009, device_code="BIGINT-UP", device_name="上游")
            db.add(upstream)
            await db.flush()
            downstream = Device(
                id=347454468883010,
                device_code="BIGINT-DOWN",
                device_name="下游",
                upstream_device_id=upstream.id,
            )
            db.add(downstream)
            await db.commit()
            upstream_id, downstream_id = upstream.id, downstream.id

        async with sessions() as db:
            assert await db.scalar(select(Device.upstream_device_id).where(Device.id == downstream_id)) == upstream_id
            # 扩大类型后仍由原外键约束拒绝悬空关联。
            for field in ("work_line_id", "upstream_device_id"):
                with pytest.raises(IntegrityError):
                    async with db.begin_nested():
                        db.add(
                            Device(device_code=f"INVALID-{field}", device_name="无效关联", **{field: upstream_id + 100})
                        )
                        await db.flush()
            await db.execute(delete(Device).where(Device.id == upstream_id))
            await db.commit()

        async with sessions() as db:
            # 自关联原有 ON DELETE SET NULL 行为必须保留。
            assert await db.scalar(select(Device.upstream_device_id).where(Device.id == downstream_id)) is None


@pytest.mark.asyncio
async def test_migration_preserves_topology_and_rejects_lossy_downgrade() -> None:
    async with temporary_database() as (_database, url):
        run_alembic("upgrade", BASE_REVISION, database_url=url)
        engine = create_async_engine(url)
        try:
            async with async_sessionmaker(engine)() as db:
                db.add(WorkLine(id=1, line_code="EXISTING", line_name="已有工作线", line_type=LineType.AUTO))
                await db.flush()
                db.add(Device(id=2, device_code="EXISTING-UP", device_name="已有上游", work_line_id=1))
                await db.flush()
                db.add(
                    Device(
                        id=3, device_code="EXISTING-DOWN", device_name="已有下游", work_line_id=1, upstream_device_id=2
                    )
                )
                await db.commit()
        finally:
            await engine.dispose()

        column_types_sql = """SELECT count(*) FROM information_schema.columns
                WHERE data_type = 'bigint' AND (
                    (table_schema = 'wes_biz' AND table_name = 'devices'
                        AND column_name IN ('work_line_id', 'upstream_device_id') AND is_nullable = 'YES')
                    OR (table_schema = 'wes_biz' AND table_name IN ('material_executions', 'position_projections')
                        AND column_name = 'workline_id' AND is_nullable = 'NO')
                    OR (table_schema = 'wes_runtime' AND table_name = 'transport_tasks'
                        AND column_name = 'authority_workline_id' AND is_nullable = 'YES')
                    OR (table_schema = 'wes_biz' AND table_name = 'picking_tasks'
                        AND column_name = 'workline_id' AND is_nullable = 'YES'))"""
        connection = await asyncpg.connect(url.replace("postgresql+asyncpg", "postgresql", 1))
        try:
            before = await connection.fetch("SELECT * FROM wes_biz.devices ORDER BY id")
            run_alembic("upgrade", BIGINT_REVISION, database_url=url)
            run_alembic("downgrade", BASE_REVISION, database_url=url)
            run_alembic("upgrade", BIGINT_REVISION, database_url=url)
            await connection.reload_schema_state()
            assert await connection.fetch("SELECT * FROM wes_biz.devices ORDER BY id") == before
            assert await connection.fetchval(column_types_sql) == 6

            # 前面的列可收窄，最后一个关联列溢出，验证降级失败会回滚整个迁移。
            await connection.execute("UPDATE wes_biz.devices SET work_line_id = NULL")
            await connection.execute("UPDATE wes_biz.work_lines SET id = 347454468883008 WHERE id = 1")
            await connection.execute("UPDATE wes_biz.devices SET work_line_id = 347454468883008")
            before = await connection.fetch("SELECT * FROM wes_biz.devices ORDER BY id")
            with pytest.raises(subprocess.CalledProcessError) as rejected:
                run_alembic("downgrade", BASE_REVISION, database_url=url)
            assert "integer out of range" in rejected.value.stderr
            await connection.reload_schema_state()
            await assert_database_head(connection, BIGINT_REVISION)
            assert await connection.fetch("SELECT * FROM wes_biz.devices ORDER BY id") == before
            assert await connection.fetchval(column_types_sql) == 6
        finally:
            await connection.close()


@pytest.mark.asyncio
async def test_device_command_workline_migration_preserves_rows_and_rejects_lossy_downgrade() -> None:
    now = datetime(2026, 9, 14)

    def command(code: str, workline_id: int) -> DeviceCommand:
        return DeviceCommand(
            device_code="STATION_SCAN9",
            workline_id=workline_id,
            execution_ref_type="TEST_EXECUTION",
            execution_ref_id=code,
            contract_key="test.scan",
            contract_version="1.0",
            task_type="MOVE_FORWARD",
            deadline_at=now,
            endpoint_base_url="http://ecs-mock:8080",
            command_timeout_ms=30000,
            status_max_age_ms=1000,
            command_code=code,
            payload_digest="a" * 64,
        )

    async with temporary_database() as (_database, url):
        run_alembic("upgrade", COMMAND_BIGINT_BASE, database_url=url)
        engine = create_async_engine(url)
        try:
            async with async_sessionmaker(engine)() as db:
                db.add(WorkLine(id=1, line_code="COMMAND-OLD", line_name="原工作线", line_type=LineType.AUTO))
                await db.flush()
                db.add(command("COMMAND-OLD", 1))
                await db.commit()
        finally:
            await engine.dispose()

        run_alembic("upgrade", COMMAND_BIGINT_REVISION, database_url=url)
        connection = await asyncpg.connect(url.replace("postgresql+asyncpg", "postgresql", 1))
        try:
            assert (
                await connection.fetchval(
                    """SELECT data_type FROM information_schema.columns
                WHERE table_schema = 'wes_biz' AND table_name = 'device_commands' AND column_name = 'workline_id'"""
                )
                == "bigint"
            )
            assert (
                await connection.fetchval(
                    "SELECT workline_id FROM wes_biz.device_commands WHERE command_code = 'COMMAND-OLD'"
                )
                == 1
            )
        finally:
            await connection.close()

        engine = create_async_engine(url)
        snowflake = 348950323769920
        try:
            async with async_sessionmaker(engine)() as db:
                db.add(WorkLine(id=snowflake, line_code="COMMAND-BIG", line_name="雪花工作线", line_type=LineType.AUTO))
                await db.flush()
                db.add(command("COMMAND-BIG", snowflake))
                await db.commit()
        finally:
            await engine.dispose()

        with pytest.raises(subprocess.CalledProcessError) as rejected:
            run_alembic("downgrade", COMMAND_BIGINT_BASE, database_url=url)
        assert "integer out of range" in rejected.value.stderr
        connection = await asyncpg.connect(url.replace("postgresql+asyncpg", "postgresql", 1))
        try:
            await assert_database_head(connection, COMMAND_BIGINT_REVISION)
            rows = await connection.fetch(
                "SELECT command_code, workline_id FROM wes_biz.device_commands ORDER BY command_code"
            )
            assert [(row["command_code"], row["workline_id"]) for row in rows] == [
                ("COMMAND-BIG", snowflake),
                ("COMMAND-OLD", 1),
            ]
        finally:
            await connection.close()
