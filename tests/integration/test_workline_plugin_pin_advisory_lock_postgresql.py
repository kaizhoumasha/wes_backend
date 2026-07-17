"""WorkLine 插件 pin advisory 读写锁的 PostgreSQL 并发合同。"""

from __future__ import annotations

import asyncio
import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.app.runtime.orchestration.repository_wiring import workline_repository
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
