"""
Pytest 配置和共享 fixtures
"""

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from src.database.schema_conf import get_all_schemas

# 使用内存数据库进行测试
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(scope="session")
def event_loop():
    """创建事件循环"""
    import asyncio

    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="function")
async def db_engine():
    """创建测试数据库引擎"""
    engine = create_async_engine(
        TEST_DATABASE_URL,
        echo=False,
        poolclass=StaticPool,
    )

    # SQLite 不支持 PostgreSQL schema 语法，测试时通过 ATTACH 模拟 schema。
    @event.listens_for(engine.sync_engine, "connect")
    def _attach_test_schemas(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        try:
            for schema in get_all_schemas():
                cursor.execute(f"ATTACH DATABASE ':memory:' AS {schema}")
        finally:
            cursor.close()

    # 创建所有表
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    yield engine

    # 清理
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)

    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def db_session(db_engine):
    """创建测试数据库会话"""
    async_session = sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        yield session
        await session.rollback()
