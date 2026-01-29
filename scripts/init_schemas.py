#!/usr/bin/env python3
"""
数据库 Schema 初始化脚本

用于创建项目所需的所有 PostgreSQL schema。

使用方式:
    # 直接运行（使用 .env 中的数据库配置）
    python scripts/init_schemas.py

    # 验证 schema 是否已创建
    python scripts/init_schemas.py --check

    # 删除所有自定义 schema（危险操作！）
    python scripts/init_schemas.py --drop
"""

import argparse
import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from src.core.conf import settings
from src.core.logger import logger
from src.database.schema_conf import SCHEMA_DESCRIPTIONS, get_all_schemas


async def create_schemas(engine) -> None:
    """
    创建所有自定义 schema

    Args:
        engine: SQLAlchemy 异步引擎
    """
    async with engine.begin() as conn:
        for schema, description in SCHEMA_DESCRIPTIONS.items():
            # schema 是枚举类型，需要取其值
            schema_name = schema.value if hasattr(schema, "value") else schema
            logger.info(f"Creating schema: {schema_name} - {description}")
            await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema_name}"))
            logger.info(f"✓ Schema '{schema_name}' created successfully")

    logger.info("All schemas created successfully")


async def check_schemas(engine) -> None:
    """
    检查所有 schema 是否已存在

    Args:
        engine: SQLAlchemy 异步引擎
    """
    async with engine.begin() as conn:
        # 查询所有存在的 schema
        result = await conn.execute(
            text(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name NOT IN ('pg_catalog', 'information_schema', 'pg_toast')"
            )
        )
        existing_schemas = {row[0] for row in result}

        logger.info("Checking schemas...")
        all_ok = True
        for schema in get_all_schemas():
            if schema in existing_schemas:
                logger.info(f"✓ Schema '{schema}' exists")
            else:
                logger.warning(f"✗ Schema '{schema}' does NOT exist")
                all_ok = False

        if all_ok:
            logger.info("All required schemas exist ✓")
        else:
            logger.warning("Some schemas are missing")

        # 显示所有 schema
        logger.info(f"\nExisting schemas in database: {sorted(existing_schemas)}")


async def drop_schemas(engine, confirm: bool = False) -> None:
    """
    删除所有自定义 schema（危险操作！）

    Args:
        engine: SQLAlchemy 异步引擎
        confirm: 是否已确认删除操作
    """
    if not confirm:
        logger.error("Dropping schemas requires --confirm flag")
        return

    logger.warning("Dropping all custom schemas - this is a destructive operation!")
    async with engine.begin() as conn:
        for schema in reversed(get_all_schemas()):  # 反向删除，避免依赖问题
            if schema != "public":
                logger.warning(f"Dropping schema: {schema}")
                # CASCADE 会删除 schema 中的所有对象
                await conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
                logger.warning(f"✗ Schema '{schema}' dropped")

    logger.warning("All custom schemas dropped")


async def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="数据库 Schema 初始化工具")
    parser.add_argument(
        "--check",
        action="store_true",
        help="检查 schema 是否已创建",
    )
    parser.add_argument(
        "--drop",
        action="store_true",
        help="删除所有自定义 schema（危险操作！）",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="确认执行危险操作（如 --drop）",
    )

    args = parser.parse_args()

    # 创建数据库引擎
    engine = create_async_engine(settings.DATABASE_URL, echo=False)

    try:
        if args.drop:
            await drop_schemas(engine, confirm=args.confirm)
        elif args.check:
            await check_schemas(engine)
        else:
            await create_schemas(engine)
            # 创建后验证
            await check_schemas(engine)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
