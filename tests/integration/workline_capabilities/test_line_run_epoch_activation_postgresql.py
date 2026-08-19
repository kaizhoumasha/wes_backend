"""LineRunEpoch 完整激活的 PostgreSQL schema 与事务证据。"""

from __future__ import annotations

import asyncio
import subprocess
from datetime import datetime

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.app.device.models.device import Device
from src.app.workline.epoch_activation import (
    LineRunEpochDeviceBindingInput,
    LineRunEpochPositionBindingInput,
)
from src.app.workline.models.line_run_epoch import (
    LineRunEpoch,
    LineRunEpochDeviceBinding,
    LineRunEpochPositionBinding,
)
from src.app.workline.models.workline import LineType, WorkLine
from src.app.workline.repositories.line_run_epoch_repository import LineRunEpochRepository
from src.app.workline.services.line_run_epoch_service import ActiveLineRunEpochExistsError, LineRunEpochService
from tests.support.runtime_inbox_postgresql import run_alembic, temporary_database

PARENT_REVISION = "ec18b2a79400"
PACKAGE_TWO_HEAD = "a05b2676f681"


async def _seed_line_and_device(session_factory: async_sessionmaker[AsyncSession]) -> tuple[int, int]:
    async with session_factory.begin() as db:
        line = WorkLine(line_code="ATOMIC-EPOCH-PG", line_name="Atomic Epoch PG", line_type=LineType.AUTO)
        db.add(line)
        await db.flush()
        device = Device(
            device_code="ATOMIC-EPOCH-PG-DEVICE",
            device_name="Atomic Epoch PG Device",
            work_line_id=line.id,
            device_role="DEVICE_ROLE",
        )
        db.add(device)
        await db.flush()
        assert line.id is not None
        assert device.id is not None
        return line.id, device.id


def _device(device_id: int) -> LineRunEpochDeviceBindingInput:
    return LineRunEpochDeviceBindingInput(
        device_id=device_id,
        device_code="ATOMIC-EPOCH-PG-DEVICE",
        device_role="DEVICE_ROLE",
        endpoint_base_url="http://ecs-epoch-pg:8080",
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


async def _activate(
    db: AsyncSession,
    *,
    epoch_code: str,
    workline_id: int,
    device_id: int,
) -> LineRunEpoch:
    return await LineRunEpochService(repository=LineRunEpochRepository()).activate_epoch(
        db,
        epoch_code=epoch_code,
        workline_id=workline_id,
        plugin_key="example_plugin",
        plugin_version="1.0",
        flow_mode="GENERIC_FLOW",
        configuration_snapshot={"mode": "GENERIC"},
        device_bindings=(_device(device_id),),
        position_bindings=(_position(),),
        started_at=datetime(2026, 8, 19),
    )


def test_complete_epoch_activation_is_atomic_and_serialized_by_workline_lock() -> None:
    async def scenario() -> None:
        async with temporary_database() as (_database, database_url):
            run_alembic("upgrade", "head", database_url=database_url)
            engine = create_async_engine(database_url, pool_pre_ping=True)
            session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            try:
                workline_id, device_id = await _seed_line_and_device(session_factory)

                async with session_factory.begin() as db:
                    epoch = await _activate(
                        db,
                        epoch_code="ATOMIC-EPOCH-COMMITTED",
                        workline_id=workline_id,
                        device_id=device_id,
                    )
                    committed_epoch_id = epoch.id
                assert committed_epoch_id is not None

                async with session_factory() as db:
                    assert (
                        await db.scalar(
                            select(func.count())
                            .select_from(LineRunEpochDeviceBinding)
                            .where(LineRunEpochDeviceBinding.line_run_epoch_id == committed_epoch_id)
                        )
                        == 1
                    )
                    assert (
                        await db.scalar(
                            select(func.count())
                            .select_from(LineRunEpochPositionBinding)
                            .where(LineRunEpochPositionBinding.line_run_epoch_id == committed_epoch_id)
                        )
                        == 1
                    )

                async with session_factory.begin() as db:
                    committed = await db.get(LineRunEpoch, committed_epoch_id, with_for_update=True)
                    assert committed is not None
                    committed.status = "CLOSED"

                with pytest.raises(RuntimeError, match="rollback marker"):
                    async with session_factory.begin() as db:
                        await _activate(
                            db,
                            epoch_code="ATOMIC-EPOCH-ROLLED-BACK",
                            workline_id=workline_id,
                            device_id=device_id,
                        )
                        raise RuntimeError("rollback marker")

                async with session_factory() as db:
                    assert (
                        await db.scalar(
                            select(func.count())
                            .select_from(LineRunEpoch)
                            .where(LineRunEpoch.epoch_code == "ATOMIC-EPOCH-ROLLED-BACK")
                        )
                        == 0
                    )

                ready = asyncio.Barrier(2)

                async def concurrent_activate(epoch_code: str) -> str:
                    async with session_factory.begin() as db:
                        await ready.wait()
                        await db.execute(select(WorkLine).where(WorkLine.id == workline_id).with_for_update())
                        try:
                            await _activate(
                                db,
                                epoch_code=epoch_code,
                                workline_id=workline_id,
                                device_id=device_id,
                            )
                        except ActiveLineRunEpochExistsError:
                            return "CONFLICT"
                        return "ACTIVATED"

                outcomes = await asyncio.gather(
                    concurrent_activate("ATOMIC-EPOCH-CONCURRENT-1"),
                    concurrent_activate("ATOMIC-EPOCH-CONCURRENT-2"),
                )
                assert sorted(outcomes) == ["ACTIVATED", "CONFLICT"]
                async with session_factory() as db:
                    assert (
                        await db.scalar(
                            select(func.count())
                            .select_from(LineRunEpoch)
                            .where(
                                LineRunEpoch.workline_id == workline_id,
                                LineRunEpoch.status == "ACTIVE",
                            )
                        )
                        == 1
                    )
            finally:
                await engine.dispose()

    asyncio.run(scenario())


def test_configuration_snapshot_migration_is_non_nullable_and_rejects_existing_epoch() -> None:
    async def scenario() -> None:
        async with temporary_database() as (_database, database_url):
            run_alembic("upgrade", "head", database_url=database_url)
            engine = create_async_engine(database_url, pool_pre_ping=True)
            try:
                async with engine.connect() as connection:
                    row = (
                        await connection.execute(
                            text(
                                """
                                SELECT data_type, is_nullable
                                FROM information_schema.columns
                                WHERE table_schema = 'wes_biz'
                                  AND table_name = 'line_run_epochs'
                                  AND column_name = 'configuration_snapshot_json'
                                """
                            )
                        )
                    ).one_or_none()
                assert row == ("json", "NO")
            finally:
                await engine.dispose()

        async with temporary_database() as (_database, database_url):
            run_alembic("upgrade", PARENT_REVISION, database_url=database_url)
            engine = create_async_engine(database_url, pool_pre_ping=True)
            try:
                async with engine.begin() as connection:
                    workline_id = (
                        await connection.execute(
                            WorkLine.__table__.insert()
                            .values(line_code="MIGRATION-REJECT", line_name="Migration reject", line_type="AUTO")
                            .returning(WorkLine.id)
                        )
                    ).scalar_one()
                    await connection.execute(
                        LineRunEpoch.__table__.insert().values(
                            epoch_code="MIGRATION-REJECT-EPOCH",
                            workline_id=workline_id,
                            plugin_key="example_plugin",
                            plugin_version="1.0",
                            flow_mode="GENERIC_FLOW",
                            topology_digest="a" * 64,
                            configuration_digest="b" * 64,
                            status="ACTIVE",
                            started_at=datetime(2026, 8, 19),
                        )
                    )
            finally:
                await engine.dispose()

            with pytest.raises(subprocess.CalledProcessError) as error:
                run_alembic("upgrade", "head", database_url=database_url)
            assert "配置快照 direct cutover 要求清空" in error.value.stderr

            engine = create_async_engine(database_url, pool_pre_ping=True)
            try:
                async with engine.connect() as connection:
                    assert (
                        await connection.scalar(text("SELECT version_num FROM wes_sys.alembic_version"))
                        == PARENT_REVISION
                    )
            finally:
                await engine.dispose()

    asyncio.run(scenario())


def test_endpoint_migration_is_non_nullable_and_rejects_existing_epoch_or_binding() -> None:
    async def scenario() -> None:
        async with temporary_database() as (_database, database_url):
            run_alembic("upgrade", "head", database_url=database_url)
            engine = create_async_engine(database_url, pool_pre_ping=True)
            try:
                async with engine.connect() as connection:
                    columns = {
                        (row.table_name, row.column_name): row.is_nullable
                        for row in (
                            await connection.execute(
                                text(
                                    """
                                    SELECT table_name, column_name, is_nullable
                                    FROM information_schema.columns
                                    WHERE table_schema = 'wes_biz'
                                      AND column_name = 'endpoint_base_url'
                                      AND table_name IN ('devices', 'line_run_epoch_device_bindings')
                                    """
                                )
                            )
                        )
                    }
                assert columns == {
                    ("devices", "endpoint_base_url"): "YES",
                    ("line_run_epoch_device_bindings", "endpoint_base_url"): "NO",
                }
            finally:
                await engine.dispose()

        for with_binding in (False, True):
            async with temporary_database() as (_database, database_url):
                run_alembic("upgrade", PACKAGE_TWO_HEAD, database_url=database_url)
                engine = create_async_engine(database_url, pool_pre_ping=True)
                try:
                    async with engine.begin() as connection:
                        workline_id = (
                            await connection.execute(
                                WorkLine.__table__.insert()
                                .values(
                                    line_code="ENDPOINT-MIGRATION", line_name="Endpoint migration", line_type="AUTO"
                                )
                                .returning(WorkLine.id)
                            )
                        ).scalar_one()
                        epoch_id = (
                            await connection.execute(
                                LineRunEpoch.__table__.insert()
                                .values(
                                    epoch_code="ENDPOINT-MIGRATION-EPOCH",
                                    workline_id=workline_id,
                                    plugin_key="example_plugin",
                                    plugin_version="1.0",
                                    flow_mode="GENERIC_FLOW",
                                    topology_digest="a" * 64,
                                    configuration_digest="b" * 64,
                                    configuration_snapshot_json={},
                                    status="ACTIVE",
                                    started_at=datetime(2026, 8, 19),
                                )
                                .returning(LineRunEpoch.id)
                            )
                        ).scalar_one()
                        if with_binding:
                            device_id = (
                                await connection.execute(
                                    Device.__table__.insert()
                                    .values(
                                        device_code="ENDPOINT-MIGRATION-DEVICE",
                                        device_name="Endpoint migration device",
                                        work_line_id=workline_id,
                                        device_role="DEVICE_ROLE",
                                    )
                                    .returning(Device.id)
                                )
                            ).scalar_one()
                            await connection.execute(
                                LineRunEpochDeviceBinding.__table__.insert().values(
                                    line_run_epoch_id=epoch_id,
                                    device_id=device_id,
                                    device_code="ENDPOINT-MIGRATION-DEVICE",
                                    device_role="DEVICE_ROLE",
                                    contract_key="generic.contract",
                                    contract_version="1.0",
                                    status_max_age_ms=1_000,
                                    command_timeout_ms=5_000,
                                )
                            )
                finally:
                    await engine.dispose()

                with pytest.raises(subprocess.CalledProcessError):
                    run_alembic("upgrade", "head", database_url=database_url)

                engine = create_async_engine(database_url, pool_pre_ping=True)
                try:
                    async with engine.connect() as connection:
                        assert (
                            await connection.scalar(text("SELECT version_num FROM wes_sys.alembic_version"))
                            == PACKAGE_TWO_HEAD
                        )
                finally:
                    await engine.dispose()

    asyncio.run(scenario())
