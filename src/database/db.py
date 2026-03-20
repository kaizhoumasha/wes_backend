from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.pool import StaticPool

# 导入 SQLModel 以确保模型被注册
from src.core.conf import settings
from src.core.logger import logger
from src.database.schema_conf import get_schema_search_path
from src.database.sqlite_schema import configure_sqlite_schemas

# 推荐的命名约定，防止 Alembic 自动生成迁移时出现未命名约束问题
naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=naming_convention)


# 全局数据库引擎
engine: AsyncEngine | None = None
AsyncSessionLocal: async_sessionmaker[AsyncSession] | None = None


async def init_db() -> None:
    """
    初始化数据库连接

    配置自定义 schema 搜索路径，避免使用默认的 public schema。
    """
    global engine, AsyncSessionLocal

    database_url = str(settings.DATABASE_URL)
    is_sqlite = database_url.startswith("sqlite")

    engine_kwargs: dict[str, object] = {
        "echo": False,
        "pool_pre_ping": True,
        "pool_recycle": 3600,
    }

    if is_sqlite:
        engine_kwargs["poolclass"] = StaticPool
    else:
        engine_kwargs.update(
            {
                "pool_size": 50,
                "max_overflow": 50,
                "pool_timeout": 30,
                "connect_args": {"server_settings": {"search_path": get_schema_search_path()}},
            }
        )

    engine = create_async_engine(
        settings.DATABASE_URL,
        **engine_kwargs,
    )

    if is_sqlite:
        configure_sqlite_schemas(engine.sync_engine)

    AsyncSessionLocal = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )

    # # 尝试连接数据库，验证连接是否成功
    # async with engine.begin() as conn:
    #     # 仅在开发环境或测试环境可以考虑自动创建表，生产环境建议使用 Alembic
    #     if settings.APP_DEBUG:
    #         # 使用 SQLModel.metadata 创建所有 SQLModel 表
    #         await conn.run_sync(SQLModel.metadata.create_all)
    #         # 如果有使用 Base 的传统 SQLAlchemy 模型，也创建它们
    #         await conn.run_sync(Base.metadata.create_all)
    logger.info(f"Database connection initialized successfully with search_path: {get_schema_search_path()}")


async def close_db() -> None:
    """
    关闭数据库连接
    """
    if engine:
        await engine.dispose()
        logger.info("Database connection closed")


async def get_db() -> AsyncGenerator[AsyncSession]:
    """
    获取数据库会话依赖

    用于 FastAPI 依赖注入系统。
    """
    if AsyncSessionLocal is None:
        raise RuntimeError("Database is not initialized")

    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except StaleDataError as e:
            await session.rollback()
            from src.core.exceptions import OptimisticLockException

            raise OptimisticLockException from e
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@asynccontextmanager
async def get_db_context() -> AsyncGenerator[AsyncSession]:
    """
    获取数据库会话上下文管理器

    用于在后台任务或其他非依赖注入场景中创建数据库会话。

    Usage:
        async with get_db_context() as session:
            # 使用 session 进行数据库操作
            await session.execute(...)
            await session.commit()

    Note:
        - 需要手动调用 commit() 或 rollback()
        - 会话会在退出上下文时自动关闭
    """
    if AsyncSessionLocal is None:
        raise RuntimeError("Database is not initialized")

    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
