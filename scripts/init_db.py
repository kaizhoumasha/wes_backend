"""
数据库初始化脚本

创建所有数据表
"""

import asyncio

from sqlalchemy.ext.asyncio import create_async_engine

from src.app.models import Base
from src.core.conf import settings
from src.core.logger import logger


async def init_tables():
    """初始化数据库表"""
    engine = create_async_engine(settings.DATABASE_URL)

    async with engine.begin() as conn:
        # 删除所有表（谨慎使用！）
        # await conn.run_sync(Base.metadata.drop_all)

        # 创建所有表
        await conn.run_sync(Base.metadata.create_all)
        logger.info("数据库表创建成功")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(init_tables())
