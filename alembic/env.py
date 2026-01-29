"""Alembic 环境配置文件

此文件配置 Alembic 以支持：
- 异步数据库操作（asyncpg）
- SQLModel 模型自动发现
- 从项目配置读取数据库 URL
"""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# 导入 SQLModel 以确保所有模型被注册
from sqlmodel import SQLModel

from alembic import context

# 导入所有模型以确保它们被 SQLModel.metadata 识别
# 这样 Alembic 才能自动生成迁移
from src.app.admin.models import Permission, Role, User  # noqa: F401
from src.app.admin.models.relationships import role_permission, user_role  # noqa: F401
from src.app.demo.models.demo_product import DemoProduct  # noqa: F401
from src.app.demo.models.demo_product_list import DemoProductList  # noqa: F401
from src.app.sys.models.audit_log import AuditLog  # noqa: F401

# 导入项目配置
from src.core.conf import settings

# 如果有使用传统 SQLAlchemy 模型，也需要导入
from src.database.db import Base

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
# 注意：这里假设 Base.metadata 和 SQLModel.metadata 是不同的
# 如果它们共享同一个 metadata，则不需要合并
if Base.metadata.tables:
    # 将 Base 的表添加到 SQLModel.metadata
    for table in Base.metadata.tables.values():
        if table.name not in target_metadata.tables:
            table.to_metadata(target_metadata)


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
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # 支持 PostgreSQL 特性
        compare_type=True,
        compare_server_default=True,
        # 渲染项目中使用的类型
        render_as_batch=False,
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

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
