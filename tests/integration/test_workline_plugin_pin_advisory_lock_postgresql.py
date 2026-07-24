"""WorkLine 插件 pin advisory 读写锁的 PostgreSQL 并发合同。"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.app.runtime.orchestration.repository_wiring import workline_repository
from src.app.workline.services.safety_service import WorkLineSafetyService
from tests.support.runtime_inbox_postgresql import temporary_database


def _integration_environment() -> dict[str, str]:
    database_url = os.getenv("INTEGRATION_DATABASE_URL", "").strip()
    if not database_url:
        raise AssertionError(
            "必须设置 INTEGRATION_DATABASE_URL 指向本地 PostgreSQL test 数据库；本测试会创建并清理随机隔离数据库"
        )
    return {**os.environ, "INTEGRATION_DATABASE_URL": database_url}


@pytest.mark.integration
def test_plugin_pin_shared_locks_are_compatible_for_same_workline() -> None:
    async def scenario() -> None:
        async with temporary_database(environ=_integration_environment()) as (_database, database_url):
            engine = create_async_engine(database_url, pool_size=2, max_overflow=0)
            sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            first_acquired = asyncio.Event()
            release_first = asyncio.Event()

            async def first_reader() -> None:
                async with sessions() as db:
                    await workline_repository.acquire_plugin_pin_shared(db, 9007199254740993)
                    first_acquired.set()
                    await release_first.wait()
                    await db.commit()

            async def second_reader() -> None:
                await first_acquired.wait()
                async with sessions() as db:
                    await asyncio.wait_for(
                        workline_repository.acquire_plugin_pin_shared(db, 9007199254740993),
                        timeout=1,
                    )
                    await db.commit()

            first = asyncio.create_task(first_reader())
            try:
                await second_reader()
            finally:
                release_first.set()
                await first
                await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.integration
def test_plugin_pin_exclusive_switch_blocks_new_reader_until_new_pin_commits() -> None:
    async def scenario() -> None:
        async with temporary_database(environ=_integration_environment()) as (_database, database_url):
            engine = create_async_engine(database_url, pool_size=2, max_overflow=0)
            sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with engine.begin() as connection:
                await connection.execute(
                    text("CREATE TABLE plugin_pin_probe (workline_id BIGINT PRIMARY KEY, pin INTEGER)")
                )
                await connection.execute(
                    text("INSERT INTO plugin_pin_probe (workline_id, pin) VALUES (:workline_id, 1)"),
                    {"workline_id": 9007199254740993},
                )

            exclusive_acquired = asyncio.Event()
            allow_commit = asyncio.Event()
            reader_started = asyncio.Event()

            async def activate_next_pin() -> None:
                async with sessions() as db:
                    await workline_repository.acquire_plugin_pin_exclusive(db, 9007199254740993)
                    await db.execute(
                        text("UPDATE plugin_pin_probe SET pin = 2 WHERE workline_id = :workline_id"),
                        {"workline_id": 9007199254740993},
                    )
                    exclusive_acquired.set()
                    await allow_commit.wait()
                    await db.commit()

            async def create_new_session() -> int:
                await exclusive_acquired.wait()
                async with sessions() as db:
                    reader_started.set()
                    await workline_repository.acquire_plugin_pin_shared(db, 9007199254740993)
                    return int(
                        await db.scalar(
                            text("SELECT pin FROM plugin_pin_probe WHERE workline_id = :workline_id"),
                            {"workline_id": 9007199254740993},
                        )
                    )

            activation = asyncio.create_task(activate_next_pin())
            reader = asyncio.create_task(create_new_session())
            try:
                await reader_started.wait()
                await asyncio.sleep(0.1)
                assert not reader.done()
                allow_commit.set()
                assert await asyncio.wait_for(reader, timeout=1) == 2
                await activation
            finally:
                allow_commit.set()
                await asyncio.gather(activation, reader, return_exceptions=True)
                await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.integration
def test_plugin_pin_shared_reader_prevents_next_pin_from_committing_first() -> None:
    async def scenario() -> None:
        async with temporary_database(environ=_integration_environment()) as (_database, database_url):
            engine = create_async_engine(database_url, pool_size=2, max_overflow=0)
            sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            shared_acquired = asyncio.Event()
            allow_reader_commit = asyncio.Event()
            exclusive_acquired = asyncio.Event()
            order: list[str] = []

            async def create_old_pin_session() -> None:
                async with sessions() as db:
                    await workline_repository.acquire_plugin_pin_shared(db, 9007199254740993)
                    order.append("session-n-acquired")
                    shared_acquired.set()
                    await allow_reader_commit.wait()
                    await db.commit()
                    order.append("session-n-committed")

            async def activate_next_pin() -> None:
                await shared_acquired.wait()
                async with sessions() as db:
                    await workline_repository.acquire_plugin_pin_exclusive(db, 9007199254740993)
                    order.append("activation-n-plus-1-acquired")
                    exclusive_acquired.set()
                    await db.commit()
                    order.append("activation-n-plus-1-committed")

            reader = asyncio.create_task(create_old_pin_session())
            activation = asyncio.create_task(activate_next_pin())
            try:
                await shared_acquired.wait()
                await asyncio.sleep(0.1)
                assert not exclusive_acquired.is_set()
                allow_reader_commit.set()
                await asyncio.wait_for(asyncio.gather(reader, activation), timeout=1)
                assert order == [
                    "session-n-acquired",
                    "session-n-committed",
                    "activation-n-plus-1-acquired",
                    "activation-n-plus-1-committed",
                ]
            finally:
                allow_reader_commit.set()
                await asyncio.gather(reader, activation, return_exceptions=True)
                await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.integration
def test_runtime_safety_precheck_and_deactivate_share_advisory_before_row_lock() -> None:
    async def scenario() -> None:
        async with temporary_database(environ=_integration_environment()) as (_database, database_url):
            engine = create_async_engine(database_url, pool_size=2, max_overflow=0)
            sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            workline_id = 9007199254740993
            async with engine.begin() as connection:
                await connection.execute(
                    text("CREATE TABLE workline_lock_probe (workline_id BIGINT PRIMARY KEY, is_active BOOLEAN)")
                )
                await connection.execute(
                    text("INSERT INTO workline_lock_probe (workline_id, is_active) VALUES (:workline_id, TRUE)"),
                    {"workline_id": workline_id},
                )

            shared_row_locked = asyncio.Event()
            allow_runtime_commit = asyncio.Event()
            deactivate_finished = asyncio.Event()

            class Repository:
                async def acquire_plugin_pin_shared(self, db: AsyncSession, target_id: int) -> None:
                    await workline_repository.acquire_plugin_pin_shared(db, target_id)

                async def get_for_update(
                    self,
                    db: AsyncSession,
                    target_id: int,
                    *,
                    populate_existing: bool = False,
                ) -> SimpleNamespace:
                    assert populate_existing is True
                    active = await db.scalar(
                        text("SELECT is_active FROM workline_lock_probe WHERE workline_id = :workline_id FOR UPDATE"),
                        {"workline_id": target_id},
                    )
                    shared_row_locked.set()
                    return SimpleNamespace(id=target_id, is_active=bool(active))

            class Projection:
                async def assert_accepting_runtime_work(self, *_args: object, **_kwargs: object) -> None:
                    await allow_runtime_commit.wait()

            safety_service = WorkLineSafetyService(
                workline_repository=Repository(),  # type: ignore[arg-type]
                workline_status_projection_service=Projection(),
            )

            async def runtime_precheck() -> None:
                async with sessions() as db:
                    await safety_service.assert_accepting_work(db, workline_id=workline_id)
                    await db.commit()

            async def deactivate() -> None:
                await shared_row_locked.wait()
                async with sessions() as db:
                    await workline_repository.acquire_plugin_pin_exclusive(db, workline_id)
                    await db.execute(
                        text("SELECT is_active FROM workline_lock_probe WHERE workline_id = :workline_id FOR UPDATE"),
                        {"workline_id": workline_id},
                    )
                    await db.commit()
                    deactivate_finished.set()

            runtime_task = asyncio.create_task(runtime_precheck())
            deactivate_task = asyncio.create_task(deactivate())
            try:
                await shared_row_locked.wait()
                await asyncio.sleep(0.1)
                assert not deactivate_finished.is_set()
                allow_runtime_commit.set()
                await asyncio.wait_for(asyncio.gather(runtime_task, deactivate_task), timeout=1)
                assert deactivate_finished.is_set()
            finally:
                allow_runtime_commit.set()
                await asyncio.gather(runtime_task, deactivate_task, return_exceptions=True)
                await engine.dispose()

    asyncio.run(scenario())
