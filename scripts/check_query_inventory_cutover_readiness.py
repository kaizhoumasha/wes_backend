"""发布前验证 production inventory QUERY cutover 的 immutable READY+GO 授权。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


async def run_gate() -> None:
    from src.app.runtime.system_capabilities.query_inventory_cutover import (
        query_inventory_cutover_readiness_service,
    )
    from src.core.conf import settings
    from src.database import db as database

    await database.init_db()
    try:
        async with database.get_db_context() as db:
            await query_inventory_cutover_readiness_service.require_ready(db, app_env=settings.APP_ENV)
    finally:
        await database.close_db()


def main() -> int:
    try:
        asyncio.run(run_gate())
    except Exception as exc:
        # 非 readiness 异常（连接、schema、反序列化）同样 fail closed，且不打印连接凭据。
        print(f"inventory QUERY cutover readiness gate blocked: {type(exc).__name__}", file=sys.stderr)
        return 1
    print("inventory QUERY cutover readiness gate passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
