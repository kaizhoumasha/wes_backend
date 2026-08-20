"""显式检查、应用、预览或修复权限缓存。"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

if TYPE_CHECKING:
    from src.app.admin.services.authorization_bootstrap_service import AuthorizationSyncResult

DATABASE_COMMITTED_CACHE_INVALIDATION_FAILED = "DATABASE_COMMITTED_CACHE_INVALIDATION_FAILED"
POSTCOMMIT_CACHE_FAILURE_EXIT_CODE = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="收敛代码权限目录与内置角色授权")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="只读检查数据库是否存在授权差异")
    mode.add_argument("--apply", action="store_true", help="应用授权差异并精确失效缓存")
    mode.add_argument("--preview", action="store_true", help="仅预览代码权限目录，不连接 PostgreSQL")
    mode.add_argument("--repair-cache", action="store_true", help="显式清理两个权限缓存命名空间")
    return parser


def _has_delta(result: AuthorizationSyncResult) -> bool:
    return bool(
        result.roles["created"]
        or result.roles["updated"]
        or result.permissions.created
        or result.permissions.updated
        or result.permissions.deleted
        or result.role_permissions["added"]
        or result.role_permissions["removed"]
    )


def _print_result(result: AuthorizationSyncResult) -> None:
    print("📊 授权收敛结果:")
    print(f"  roles={result.roles}")
    print(
        "  permissions="
        f"created:{result.permissions.created},updated:{result.permissions.updated},"
        f"deleted:{result.permissions.deleted},unchanged:{result.permissions.unchanged},"
        f"total:{result.permissions.total}"
    )
    print(f"  role_permissions={result.role_permissions}")


def _create_app() -> Any:
    from src.register import create_app

    return create_app()


async def _initialize_database() -> None:
    from src.database.db import init_db

    await init_db()


def _database_context() -> Any:
    from src.database.db import get_db_context

    return get_db_context()


def _authorization_service() -> Any:
    from src.app.admin.services.authorization_bootstrap_service import authorization_bootstrap_service

    return authorization_bootstrap_service


def _runtime_cache() -> Any:
    from src.database.redis_cache import get_cache

    return get_cache()


def _build_catalog(app: Any) -> list[dict[str, Any]]:
    from src.utils.permission_scanner import build_permission_catalog

    return build_permission_catalog(app)


def _build_preview_rows(catalog: list[dict[str, Any]]) -> list[str]:
    from src.utils.permission_scanner import build_permission_preview_rows

    return build_permission_preview_rows(catalog)


async def _repair_permission_cache_from_environment() -> None:
    from dotenv import load_dotenv

    from src.core.authorization_cache import repair_permission_cache_namespaces_from_environment

    load_dotenv(BACKEND_ROOT / ".env", override=False)
    await repair_permission_cache_namespaces_from_environment(os.environ)


def preview_permissions() -> None:
    catalog = _build_catalog(_create_app())
    print("📋 代码权限目录预览:")
    for row in _build_preview_rows(catalog):
        print(row)
    print(f"📊 总计: {len(catalog)} 条权限目录节点")


async def _repair_cache() -> int:
    try:
        await _repair_permission_cache_from_environment()
    except Exception as exc:
        print(f"PERMISSION_CACHE_REPAIR_FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print("✅ 权限缓存命名空间修复完成")
    return 0


async def main_async(args: argparse.Namespace) -> int:
    if args.preview:
        preview_permissions()
        return 0
    if args.repair_cache:
        return await _repair_cache()

    authorization_service = _authorization_service()
    app = _create_app()
    await _initialize_database()
    async with _database_context() as session:
        if args.check:
            result = await authorization_service.converge_authorization(app, session, dry_run=True)
            _print_result(result)
            if _has_delta(result):
                print("AUTHORIZATION_DELTA_DETECTED", file=sys.stderr)
                return 1
            print("✅ 数据库授权已收敛")
            return 0

        try:
            result = await authorization_service.converge_authorization(app, session, dry_run=False)
            await session.commit()
        except Exception:
            await session.rollback()
            raise

        try:
            await authorization_service.invalidate_caches(result, _runtime_cache())
        except Exception as exc:
            print(DATABASE_COMMITTED_CACHE_INVALIDATION_FAILED, file=sys.stderr)
            print(
                f"CACHE_INVALIDATION_FAILURE_DETAIL: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            return POSTCOMMIT_CACHE_FAILURE_EXIT_CODE

    _print_result(result)
    print("✅ 权限与内置角色授权同步完成")
    return 0


def main() -> None:
    args = build_parser().parse_args()
    try:
        exit_code = asyncio.run(main_async(args))
    except Exception as exc:
        print(f"PERMISSION_SYNC_FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    if exit_code:
        raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
