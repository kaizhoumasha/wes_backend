"""
数据库初始化数据脚本

用于执行 SQL 初始化脚本，初始化系统基础数据:
- 权限 (Permissions)
- 角色 (Roles)
- 用户 (Users)
- 角色权限关联
- 用户角色关联

使用方法:
    python scripts/init_db_data.py

环境要求:
    - 数据库已启动并可连接
    - .env 配置正确
"""

import asyncio
import os
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.core.conf import settings
from src.core.logger import logger

# 创建数据库引擎和会话工厂
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
)
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


def read_sql_file_sync(sql_file_path: Path) -> str:
    """同步读取 SQL 文件"""
    with open(sql_file_path, "r", encoding="utf-8") as f:
        return f.read()


async def execute_sql_file(session: AsyncSession, sql_file_path: Path) -> None:
    """
    执行 SQL 文件

    Args:
        session: 数据库会话
        sql_file_path: SQL 文件路径
    """
    logger.info(f"开始执行 SQL 文件: {sql_file_path}")

    # 使用 asyncio.to_thread 在单独的线程中读取文件，避免阻塞事件循环
    sql_content = await asyncio.to_thread(read_sql_file_sync, sql_file_path)

    # 执行 SQL（使用 text() 包装以支持原生 SQL）
    from sqlalchemy import text

    try:
        # 分割 SQL 语句（按分号分割，但要注意 DO $$ ... END $$ 块）
        # 简单处理：直接执行整个文件
        await session.execute(text(sql_content))
        await session.commit()
        logger.info("SQL 文件执行成功")
    except Exception as e:
        await session.rollback()
        logger.error(f"SQL 文件执行失败: {e}")
        raise


async def init_all_data():
    """
    初始化所有基础数据
    """
    logger.info("=" * 60)
    logger.info("开始初始化数据库基础数据")
    logger.info("=" * 60)

    # SQL 文件路径
    sql_file = Path(__file__).parent / "database" / "init_db.sql"

    if not sql_file.exists():
        logger.error(f"SQL 文件不存在: {sql_file}")
        raise FileNotFoundError(f"SQL 文件不存在: {sql_file}")

    try:
        # 使用 docker exec 在容器内执行 SQL 文件
        import subprocess

        # 从环境变量中读取数据库连接信息
        from dotenv import load_dotenv

        load_dotenv()
        db_user = os.getenv("POSTGRES_USER", "postgres")
        db_name = os.getenv("POSTGRES_DB", "wes_db")
        container_name = "wes_postgres"

        # 构建 docker exec psql 命令
        # 使用 -i 参数以便可以通过 stdin 传递 SQL
        cmd = [
            "docker",
            "exec",
            "-i",
            container_name,
            "psql",
            "-U",
            db_user,
            "-d",
            db_name,
            "-f",
            "/dev/stdin",  # 从 stdin 读取
        ]

        # 读取 SQL 文件内容
        with open(sql_file, "r", encoding="utf-8") as f:
            sql_content = f.read()

        # 执行命令，将 SQL 内容通过 stdin 传递
        result = subprocess.run(
            cmd,
            input=sql_content,
            capture_output=True,
            text=True,
            check=True,
        )

        logger.info("SQL 文件执行成功")
        if result.stdout:
            # 打印 NOTICE 信息
            for line in result.stdout.split("\n"):
                if "NOTICE" in line or "用户数量" in line or "角色数量" in line or "权限数量" in line:
                    logger.info(line.strip())

        logger.info("=" * 60)
        logger.info("数据库基础数据初始化完成!")
        logger.info("=" * 60)
        logger.info("")
        logger.info("默认登录账号:")
        logger.info("  - admin / admin123")
        logger.info("  - manager / admin123")
        logger.info("  - operator / admin123")
        logger.info("  - finance / admin123")
        logger.info("  - user1 / admin123")
        logger.info("  - user2 / admin123")
        logger.info("")
        logger.warning("⚠️  生产环境请立即修改默认密码!")

    except subprocess.CalledProcessError as e:
        logger.error(f"SQL 文件执行失败: {e}")
        if e.stderr:
            logger.error(f"错误输出:\n{e.stderr}")
        if e.stdout:
            logger.error(f"标准输出:\n{e.stdout}")
        raise
    except Exception as e:
        logger.error(f"初始化数据失败: {e}")
        raise


def main():
    """
    主函数
    """
    try:
        # 运行异步初始化
        asyncio.run(init_all_data())
    except KeyboardInterrupt:
        logger.info("初始化被用户中断")
        sys.exit(1)
    except Exception as e:
        logger.error(f"初始化失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
