"""
Pytest 配置和共享 fixtures
"""

import pytest
import pytest_asyncio
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

# 注册 WorklineSession 元数据：runtime_inbox 在 SQLite 中通过外键引用它。
from src.app.runtime.orchestration.models.session import WorklineSession
from src.database.sqlite_schema import configure_sqlite_schemas

# 使用内存数据库进行测试
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, _compiler, **_kw):
    """让隔离的 SQLite 单测也能创建带 JSONB 字段的表。"""
    return "JSON"


@pytest.fixture(scope="session")
def event_loop():
    """创建事件循环"""
    import asyncio

    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def db_engine():
    """创建一次 FAST 测试共享数据库引擎与完整 schema。"""
    engine = create_async_engine(
        TEST_DATABASE_URL,
        echo=False,
        poolclass=StaticPool,
    )

    # SQLite 不支持 PostgreSQL schema 语法，测试时通过 ATTACH 模拟 schema。
    configure_sqlite_schemas(engine.sync_engine)

    # 创建所有表
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def db_session(db_engine):
    """在可回滚的外层事务中隔离每个 FAST 数据库用例。"""
    async with db_engine.connect() as connection:
        # aiosqlite 会延迟物理 BEGIN；先显式开启事务，避免首个 SAVEPOINT 被释放时提交测试数据。
        await connection.exec_driver_sql("BEGIN")
        async_session = async_sessionmaker(
            connection,
            class_=AsyncSession,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )

        async with async_session() as session:
            yield session

        if connection.in_transaction():
            await connection.rollback()
