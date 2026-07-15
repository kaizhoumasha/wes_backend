"""在只读 PostgreSQL 快照中生成工作线迁移清单。"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from src.app.workline.models import WorklineMigrationInventoryReport

# 支持从任意当前目录直接执行 `python scripts/workline_migration_inventory.py`。
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from src.app.workline.services import (  # noqa: E402
    WorklineMigrationInventoryInvariantError,
    WorklineMigrationInventoryLimitExceeded,
    workline_migration_inventory_service,
)
from src.core.conf import settings  # noqa: E402

EXIT_OK = 0
EXIT_RUNTIME_ERROR = 1
EXIT_USAGE_ERROR = 2
EXIT_FOUNDATION_BLOCKED = 3

STATEMENT_TIMEOUT_SECONDS = 5
IDLE_IN_TRANSACTION_TIMEOUT_SECONDS = 15
TOTAL_TIMEOUT_SECONDS = 60

_READ_ONLY_STATEMENTS = (
    "SET TRANSACTION READ ONLY",
    f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT_SECONDS}s'",
    f"SET LOCAL idle_in_transaction_session_timeout = '{IDLE_IN_TRANSACTION_TIMEOUT_SECONDS}s'",
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="生成工作线活动迁移清单")
    parser.add_argument(
        "--expected-environment",
        required=True,
        choices=("dev", "test", "prod"),
        help="调用方预期环境；必须与应用配置一致",
    )
    parser.add_argument("--output", type=Path, help="报告文件路径；省略时写入 stdout")
    parser.add_argument(
        "--check-foundation",
        action="store_true",
        help="foundation_ready=false 时在成功输出报告后返回退出码 3",
    )
    return parser


async def build_report() -> WorklineMigrationInventoryReport:
    """在 caller-owned REPEATABLE READ、READ ONLY 事务中构建报告。"""

    engine = create_async_engine(str(settings.DATABASE_URL), isolation_level="REPEATABLE READ")
    try:
        session_factory = async_sessionmaker(engine)
        async with asyncio.timeout(TOTAL_TIMEOUT_SECONDS):
            async with session_factory() as db:
                async with db.begin():
                    for statement in _READ_ONLY_STATEMENTS:
                        await db.execute(text(statement))
                    return await workline_migration_inventory_service.build_report(
                        db,
                        environment=settings.APP_ENV,
                    )
    finally:
        await engine.dispose()


def _serialize_report(report: WorklineMigrationInventoryReport) -> str:
    """生成稳定且可机器读取的 UTF-8 JSON 文本。"""

    return report.model_dump_json(indent=2) + "\n"


def _write_report_atomically(target: Path, payload: str) -> None:
    """同目录原子替换报告；失败时清理临时文件并保留旧目标。"""

    parent = target.parent
    if not parent.is_dir():
        raise OSError("报告输出目录不存在")

    fd, temporary_name = tempfile.mkstemp(
        dir=parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, target)  # noqa: PTH105 -- 合同要求显式 os.replace 原子替换。
    except BaseException:
        # fdopen 接管 fd；若它在进入上下文前失败，则兜底关闭仍开放的描述符。
        with suppress(OSError):
            os.close(fd)
        temporary_path.unlink(missing_ok=True)
        raise


def _emit_report(report: WorklineMigrationInventoryReport, output: Path | None) -> None:
    payload = _serialize_report(report)
    if output is None:
        sys.stdout.write(payload)
        return
    _write_report_atomically(output, payload)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    arguments = parser.parse_args(argv)
    if arguments.expected_environment != settings.APP_ENV:
        parser.error("--expected-environment 与当前应用环境不一致")

    try:
        report = asyncio.run(build_report())
        _emit_report(report, arguments.output)
    except (
        TimeoutError,
        WorklineMigrationInventoryLimitExceeded,
        WorklineMigrationInventoryInvariantError,
        OSError,
        SQLAlchemyError,
    ):
        # 不拼接异常文本，避免数据库连接串或 SQL 参数进入部署日志。
        print("迁移清单生成失败；请检查数据库连接、只读快照和源数据合同", file=sys.stderr)
        return EXIT_RUNTIME_ERROR

    if arguments.check_foundation and not report.foundation_ready:
        return EXIT_FOUNDATION_BLOCKED
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
