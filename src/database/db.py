import asyncio
import os
import socket
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

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
from src.database.metadata import metadata as shared_metadata
from src.database.schema_conf import get_schema_search_path
from src.database.sqlite_schema import configure_sqlite_schemas


class Base(DeclarativeBase):
    metadata = shared_metadata


# 全局数据库引擎
engine: AsyncEngine | None = None
AsyncSessionLocal: async_sessionmaker[AsyncSession] | None = None
_engine_owner_pid: int | None = None
_engine_owner_loop_id: int | None = None
_engine_owner_role: str | None = None
_engine_owner_loop: asyncio.AbstractEventLoop | None = None


def _runtime_role() -> str:
    """读取当前数据库运行角色。"""
    return str(settings.DATABASE_RUNTIME_ROLE)


def _printable_ascii_segment(value: object) -> str:
    """将 application_name 分段清洗为不含分隔符的可打印 ASCII。"""
    return "".join(character if 32 <= ord(character) <= 126 and character != ":" else "_" for character in str(value))


def build_database_application_name(
    *, prefix: str, role: str, hostname: str, pid: int, run_id: str | None = None
) -> str:
    """生成不超过 PostgreSQL 63 字符限制且保留运行身份的 application_name。"""
    clean_prefix = _printable_ascii_segment(prefix) or "app"
    clean_role = _printable_ascii_segment(role)
    clean_hostname = _printable_ascii_segment(hostname) or "host"
    clean_run_id = _printable_ascii_segment(run_id) if run_id else None
    identity_suffix = f":{pid}" + (f":{clean_run_id}" if clean_run_id else "")
    fixed_length = len(clean_role) + len(identity_suffix) + 3
    if fixed_length + 2 > 63:
        raise ValueError("database application_name 的 role/PID/run-id 无法放入 PostgreSQL 63 字符限制")

    variable_budget = 63 - fixed_length
    prefix_length = min(len(clean_prefix), max(1, variable_budget // 2))
    hostname_length = min(len(clean_hostname), max(1, variable_budget - prefix_length))
    remaining = variable_budget - prefix_length - hostname_length
    if remaining > 0:
        prefix_length += min(remaining, len(clean_prefix) - prefix_length)
        remaining = variable_budget - prefix_length - hostname_length
        hostname_length += min(remaining, len(clean_hostname) - hostname_length)
    return f"{clean_prefix[:prefix_length]}:{clean_role}:{clean_hostname[:hostname_length]}{identity_suffix}"


def _assert_engine_owner() -> None:
    """确保数据库资源只被创建它的进程、事件循环和运行角色使用。"""
    current_pid = os.getpid()
    current_role = _runtime_role()

    if _engine_owner_pid != current_pid:
        raise RuntimeError(
            f"Database engine owner PID mismatch (owner={_engine_owner_pid}, current={current_pid}); "
            "refusing fork-inherited resource access"
        )
    if _engine_owner_loop is not asyncio.get_running_loop():
        raise RuntimeError("Database engine owner event loop mismatch")
    if _engine_owner_role != current_role:
        raise RuntimeError(f"Database engine owner role mismatch (owner={_engine_owner_role}, current={current_role})")


async def init_db() -> None:
    """
    初始化数据库连接

    配置自定义 schema 搜索路径，避免使用默认的 public schema。
    """
    global AsyncSessionLocal, _engine_owner_loop, _engine_owner_loop_id, _engine_owner_pid, _engine_owner_role, engine

    if engine is not None or AsyncSessionLocal is not None:
        if engine is None or AsyncSessionLocal is None:
            raise RuntimeError("Database engine is partially initialized")
        _assert_engine_owner()
        return

    current_pid = os.getpid()
    current_loop_id = id(asyncio.get_running_loop())
    current_role = _runtime_role()

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
        application_name = build_database_application_name(
            prefix=settings.DATABASE_APPLICATION_NAME or settings.APP_ENV,
            role=current_role,
            hostname=socket.gethostname(),
            pid=current_pid,
            run_id=settings.DATABASE_APPLICATION_RUN_ID,
        )
        engine_kwargs.update(
            {
                "pool_size": settings.DATABASE_POOL_SIZE,
                "max_overflow": settings.DATABASE_MAX_OVERFLOW,
                "pool_timeout": settings.DATABASE_POOL_TIMEOUT,
                "connect_args": {
                    "server_settings": {
                        "search_path": get_schema_search_path(),
                        "application_name": application_name,
                    }
                },
            }
        )

    candidate_engine: AsyncEngine | None = None
    try:
        candidate_engine = create_async_engine(
            settings.DATABASE_URL,
            **engine_kwargs,
        )

        if is_sqlite:
            configure_sqlite_schemas(candidate_engine.sync_engine)

        candidate_session_factory = async_sessionmaker(
            candidate_engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autocommit=False,
            autoflush=False,
        )
    except BaseException:
        if candidate_engine is not None:
            await candidate_engine.dispose()
        raise

    # Engine、SessionFactory 和 owner metadata 必须一次性发布，避免失败后留下半初始化全局状态。
    engine = candidate_engine
    AsyncSessionLocal = candidate_session_factory
    _engine_owner_pid = current_pid
    _engine_owner_loop_id = current_loop_id
    _engine_owner_role = current_role
    _engine_owner_loop = asyncio.get_running_loop()

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
    global AsyncSessionLocal, _engine_owner_loop, _engine_owner_loop_id, _engine_owner_pid, _engine_owner_role, engine

    if engine is not None or AsyncSessionLocal is not None:
        _assert_engine_owner()

    owned_engine = engine
    # child 退出有硬时间边界：先撤销全局发布，
    # 避免 dispose 超时期间新消息继续取得旧 Engine/SessionFactory。
    engine = None
    AsyncSessionLocal = None
    _engine_owner_pid = None
    _engine_owner_loop_id = None
    _engine_owner_role = None
    _engine_owner_loop = None
    try:
        if owned_engine is not None:
            await owned_engine.dispose()
            logger.info("Database connection closed")
    except BaseException as exc:
        logger.warning(f"Database connection dispose 未完成: type={type(exc).__name__}, error={exc!r}")
        raise


async def get_db() -> AsyncGenerator[AsyncSession]:
    """
    获取数据库会话依赖

    用于 FastAPI 依赖注入系统。

    注意：这里不再在请求结束时自动 commit。
    写入操作必须由 Service / Application Service 显式提交，
    避免路由层、依赖层和业务层同时决定事务边界。
    """
    if AsyncSessionLocal is None:
        raise RuntimeError("Database is not initialized")
    _assert_engine_owner()

    async with AsyncSessionLocal() as session:
        try:
            yield session
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
    _assert_engine_owner()

    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
