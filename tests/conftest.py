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

from src.app.runtime.capability_catalog import WORKLINE_CAPABILITY_CATALOG, WorklineCapabilityDefinition

# 注册 WorklineSession 元数据：runtime_inbox 在 SQLite 中通过外键引用它。
from src.app.runtime.orchestration.models.session import WorklineSession
from src.database.sqlite_schema import configure_sqlite_schemas
from tests.helpers.workline_test_plugin import CONTRACT_VERSION, PLUGIN_KEY, TestWorklinePlugin

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


@pytest_asyncio.fixture(scope="function")
async def db_engine():
    """创建测试数据库引擎"""
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

    # 清理
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)

    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def db_session(db_engine):
    """创建测试数据库会话"""
    async_session = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        yield session
        await session.rollback()


@pytest.fixture
def registered_test_workline_plugin():
    """临时注册测试专用能力，避免生产 catalog 保留测试合同。"""

    old_catalog = dict(WORKLINE_CAPABILITY_CATALOG)
    capability = TestWorklinePlugin()
    WORKLINE_CAPABILITY_CATALOG[PLUGIN_KEY] = WorklineCapabilityDefinition(
        capability_key=PLUGIN_KEY,
        contract_version=CONTRACT_VERSION,
        manifest=TestWorklinePlugin.manifest,
        business_key_resolver=capability.resolve_business_key,
        result_classifier=capability.classify_result,
        material_identity_resolver=capability.resolve_material_identity,
        ng_reason_resolver=capability.list_ng_reasons,
    )
    try:
        yield
    finally:
        WORKLINE_CAPABILITY_CATALOG.clear()
        WORKLINE_CAPABILITY_CATALOG.update(old_catalog)
