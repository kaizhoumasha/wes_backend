from typing import AsyncGenerator

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from src.core.conf import settings
from src.core.logger import logger

# 导入 SQLModel 以确保模型被注册
from sqlmodel import SQLModel

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
    """
    global engine, AsyncSessionLocal

    engine = create_async_engine(
        settings.DATABASE_URL,
        echo=False,  # 通过 loguru 统一管理日志，不使用 sqlalchemy 自带的 echo
        pool_pre_ping=True,  # 每次从连接池获取连接前预先 ping 一下，防止连接失效
        pool_size=20,
        max_overflow=10,
        pool_recycle=3600,
    )

    AsyncSessionLocal = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )

    # 尝试连接数据库，验证连接是否成功
    try:
        async with engine.begin() as conn:
            # 仅在开发环境或测试环境可以考虑自动创建表，生产环境建议使用 Alembic
            if settings.APP_DEBUG:
                # 使用 SQLModel.metadata 创建所有 SQLModel 表
                await conn.run_sync(SQLModel.metadata.create_all)
                # 如果有使用 Base 的传统 SQLAlchemy 模型，也创建它们
                await conn.run_sync(Base.metadata.create_all)
            pass
        logger.info("Database connection initialized successfully")
    except Exception as e:
        logger.error(f"Database connection initialization failed: {e}")
        raise e


async def close_db() -> None:
    """
    关闭数据库连接
    """
    global engine

    if engine:
        await engine.dispose()
        logger.info("Database connection closed")


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    获取数据库会话依赖
    """
    if AsyncSessionLocal is None:
        raise RuntimeError("Database is not initialized")

    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
