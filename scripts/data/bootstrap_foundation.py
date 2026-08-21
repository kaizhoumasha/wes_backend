"""收敛生产基础授权目录、内置角色与首个超级管理员。"""

# ruff: noqa: E402

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from src.app.admin.services.authorization_bootstrap_service import (
    BootstrapFoundationConfig,
    authorization_bootstrap_service,
)
from src.database.db import get_db_context, init_db
from src.database.redis_cache import get_cache
from src.register import create_app

if TYPE_CHECKING:
    from collections.abc import Mapping


DATABASE_COMMITTED_CACHE_INVALIDATION_FAILED = "DATABASE_COMMITTED_CACHE_INVALIDATION_FAILED"
POSTCOMMIT_CACHE_FAILURE_EXIT_CODE = 3


def load_bootstrap_foundation_config(env: Mapping[str, str] | None = None) -> BootstrapFoundationConfig:
    values = env or os.environ
    username = values.get("BOOTSTRAP_ADMIN_USERNAME", "").strip()
    password = values.get("BOOTSTRAP_ADMIN_PASSWORD", "").strip()

    if not username:
        raise ValueError("缺少环境变量 BOOTSTRAP_ADMIN_USERNAME")
    if not password:
        raise ValueError("缺少环境变量 BOOTSTRAP_ADMIN_PASSWORD")
    if len(password) < 8:
        raise ValueError("BOOTSTRAP_ADMIN_PASSWORD 长度必须至少为 8")

    return BootstrapFoundationConfig(
        username=username,
        password=password,
        full_name=values.get("BOOTSTRAP_ADMIN_FULL_NAME"),
        email=values.get("BOOTSTRAP_ADMIN_EMAIL"),
    )


async def main_async(config: BootstrapFoundationConfig) -> int:
    app = create_app()
    await init_db()

    async with get_db_context() as session:
        try:
            result = await authorization_bootstrap_service.bootstrap(app, session, config)
            await session.commit()
        except Exception:
            await session.rollback()
            raise

        try:
            await authorization_bootstrap_service.invalidate_caches(result.authorization, get_cache())
        except Exception as exc:
            print(DATABASE_COMMITTED_CACHE_INVALIDATION_FAILED, file=sys.stderr)
            print(
                f"CACHE_INVALIDATION_FAILURE_DETAIL: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            return POSTCOMMIT_CACHE_FAILURE_EXIT_CODE

    print(
        "✅ 基础授权初始化完成: "
        f"admin={result.admin_username},action={result.admin_action},role_added={result.admin_role_added}"
    )
    return 0


def main() -> None:
    try:
        exit_code = asyncio.run(main_async(load_bootstrap_foundation_config()))
    except Exception as exc:
        print(f"BOOTSTRAP_FOUNDATION_FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    if exit_code:
        raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
