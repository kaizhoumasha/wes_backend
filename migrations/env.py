"""Alembic 环境配置文件

此文件配置 Alembic 以支持：
- 异步数据库操作（asyncpg）
- SQLModel 模型自动发现
- 从项目配置读取数据库 URL
- 多 Schema 支持
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# 导入 SQLModel 以确保所有模型被注册
from sqlmodel import SQLModel
from sqlmodel.sql.sqltypes import AutoString

# 导入所有模型以确保它们被 SQLModel.metadata 识别
# 这样 Alembic 才能自动生成迁移
from src.app.admin.models import Permission, Role, User  # noqa: F401
from src.app.admin.models.relationships import role_permission, user_role  # noqa: F401
from src.app.api_auth.models import APIAccessLog, APIApplication  # noqa: F401
from src.app.api_auth.models.relationships import api_app_permissions  # noqa: F401
from src.app.callback.models.callback_log import CallbackLog  # noqa: F401
from src.app.device.models import (  # noqa: F401
    Device,
    DeviceCommand,
)
from src.app.handling.models import (  # noqa: F401
    HandlingMove,
    HandlingOperation,
    HandlingStep,
)
from src.app.rack.models import RackOperation, RackTask  # noqa: F401
from src.app.resource.models import (  # noqa: F401
    Bin,
    BinCellOccupancy,
    BinContentSnapshot,
    BinContentSnapshotItem,
    BinMaterialMount,
    BinPlacement,
    BinSlotTemplate,
    BinType,
    Rack,
    RackBinMount,
    RackPlacement,
    RackSlotTemplate,
    RackType,
    ResourceStateEvent,
)
from src.app.runtime.orchestration.conveyor_queue_membership import ConveyorQueueMembership  # noqa: F401
from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation  # noqa: F401
from src.app.runtime.orchestration.execution_session import ExecutionSession  # noqa: F401
from src.app.runtime.orchestration.execution_work_item import ExecutionWorkItem  # noqa: F401
from src.app.runtime.orchestration.idempotency_key import IdempotencyKey  # noqa: F401
from src.app.runtime.orchestration.runtime_hold import RuntimeHold as OrchestrationRuntimeHold  # noqa: F401
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox  # noqa: F401
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog  # noqa: F401
from src.app.runtime.orchestration.runtime_timeline import RuntimeTimeline  # noqa: F401
from src.app.sys.models.audit_log import AuditLog  # noqa: F401
from src.app.wms_integration.models import WmsCallEvidence, WmsCircuitBreakerState  # noqa: F401

# 导入所有 workline 模型
from src.app.workline.models import (  # noqa: F401
    WorkLine,
    WorklineBinCellReservation,
    WorklineInbox,
    WorklineRackPosition,
    WorklineSession,
    WorklineTimeline,
)

# 导入项目配置
from src.core.conf import settings

# 如果有使用传统 SQLAlchemy 模型，也需要导入
from src.database.db import Base

# 导入 Schema 配置
from src.database.schema_conf import get_all_schemas

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# 从项目配置中设置数据库 URL
# 这样就不需要在 alembic.ini 中硬编码数据库连接
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# 使用 SQLModel 的 metadata，它包含了所有 SQLModel 表定义
# 如果同时使用了传统 SQLAlchemy 模型，需要合并 metadata
target_metadata = SQLModel.metadata

# 如果有使用 Base 的传统 SQLAlchemy 模型，合并它们的 metadata
# 注意：只有当 Base.metadata 和 SQLModel.metadata 是不同对象时才需要合并
if Base.metadata is not SQLModel.metadata and Base.metadata.tables:
    # 将 Base 的表添加到 SQLModel.metadata
    for table in Base.metadata.tables.values():
        if table.name not in target_metadata.tables:
            table.to_metadata(target_metadata)


def render_item(type_, obj, autogen_context):
    """自定义类型渲染函数

    将 SQLModel 的 AutoString 类型渲染为标准的 sa.String()
    这样生成的迁移文件就不会依赖 SQLModel，更加标准化

    对于 Enum 类型，强制使用非原生模式（VARCHAR + CHECK 约束）
    避免 PostgreSQL ENUM 类型的各种限制（无法删除值、添加值不支持事务等）

    详见: CLAUDE.md - ENUM 类型规范
    """
    if type_ == "type" and isinstance(obj, AutoString):
        # 收集 AutoString 的所有参数
        params = []

        # 处理 length 参数
        if hasattr(obj, "length") and obj.length is not None:
            params.append(f"length={obj.length}")

        # 处理 collation 参数
        if hasattr(obj, "collation") and obj.collation is not None:
            params.append(f"collation={obj.collation!r}")

        # 处理其他可能的参数
        # SQLAlchemy String 还支持: _warn_on_bytestring, _expect_unicode 等
        # 但这些通常不需要在迁移中显式指定

        # 构建渲染字符串
        if params:
            return f"sa.String({', '.join(params)})"
        return "sa.String()"

    # ========== Enum 类型处理：强制使用非原生模式 ==========
    # 🔥 强制所有 ENUM 使用 VARCHAR + CHECK 约束
    # 避免 PostgreSQL ENUM 的限制：
    # - 无法删除 ENUM 值
    # - 添加值不支持事务
    # - 跨 schema 复杂
    #
    # 详见: CLAUDE.md - ENUM 类型规范
    if type_ == "type" and hasattr(obj, "__visit_name__") and obj.__visit_name__ == "enum":
        # 获取 ENUM 的值列表
        if hasattr(obj, "enums"):
            enums = obj.enums
            enum_name = getattr(obj, "name", None)

            # 构建非原生 ENUM 的参数
            params = [repr(e) for e in enums]

            # 添加 name 参数（如果有）
            if enum_name:
                params.append(f"name={enum_name!r}")

            # 🔥 关键参数：禁用原生 ENUM
            params.append("native_enum=False")
            params.append("create_constraint=True")
            params.append("length=50")  # VARCHAR 长度

            # 返回非原生 ENUM 定义
            return f"sa.Enum({', '.join(params)})"

        # 如果无法获取枚举值，返回 False 使用默认渲染
        return False

    # 对于其他类型，返回 False 使用默认渲染
    return False


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # 支持 PostgreSQL 特性
        compare_type=True,
        compare_server_default=True,
        # 支持多 schema
        include_schemas=True,
        # 忽略 TimescaleDB 内部 schema 和表
        include_object=lambda obj, _name, _type_, _reflected, _compare_to: (  # noqa: ARG005
            not (
                hasattr(obj, "schema")
                and obj.schema  # type: ignore[attr-defined]
                in ("_timescaledb_catalog", "_timescaledb_cache", "_timescaledb_internal", "_timescaledb_config")
            )
        ),
        # 支持 version_table_schema
        version_table_schema="wes_sys",
        # 自定义类型渲染
        render_item=render_item,
    )

    with context.begin_transaction():
        # 在 offline 模式下也需要生成创建 schema 的 SQL
        for schema in get_all_schemas():
            if schema != "public":
                context.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """
    运行在线迁移

    在执行迁移前，确保所有自定义 schema 都已创建。
    """
    # 在执行迁移前创建所有自定义 schema
    # 注意：public schema 默认存在，不需要创建
    for schema in get_all_schemas():
        if schema != "public":
            # 使用 exec_driver_sql 执行原生 SQL
            connection.exec_driver_sql(f"CREATE SCHEMA IF NOT EXISTS {schema}")

    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # 支持 PostgreSQL 特性
        compare_type=True,
        compare_server_default=True,
        # 渲染项目中使用的类型
        render_as_batch=False,
        # 支持多 schema
        include_schemas=True,
        # 忽略 TimescaleDB 内部 schema 和表
        include_object=lambda obj, _name, _type_, _reflected, _compare_to: (  # noqa: ARG005
            # 忽略 TimescaleDB 内部 schema 的所有对象
            not (
                hasattr(obj, "schema")
                and obj.schema  # type: ignore[attr-defined]
                in ("_timescaledb_catalog", "_timescaledb_cache", "_timescaledb_internal", "_timescaledb_config")
            )
        ),
        # 支持 version_table_schema（如果需要）
        version_table_schema="wes_sys",  # 将 alembic_version 表放在 wes_sys schema 下
        # 自定义类型渲染
        render_item=render_item,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine
    and associate a connection with the context.

    """

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.begin() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
