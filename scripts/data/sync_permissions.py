"""
从后端路由自动同步权限到数据库，并按内置规则补齐角色权限。

使用方式：
    uv run python scripts/data/sync_permissions.py
    uv run python scripts/data/sync_permissions.py --dry-run
    uv run python scripts/data/sync_permissions.py --preview
    uv run python scripts/data/sync_permissions.py --permissions-only
"""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from src.app.admin.services.authorization_bootstrap_service import authorization_bootstrap_service
from src.app.admin.services.permission_catalog_service import permission_catalog_service
from src.database.db import get_db_context, init_db
from src.register import create_app
from src.utils.permission_scanner import build_permission_preview_rows, scan_routes_for_permissions


def preview_permissions() -> None:
    """预览从路由扫描到的权限数据"""
    app = create_app()
    permissions = scan_routes_for_permissions(app)

    print("\n📋 权限数据预览:")
    print("=" * 80)

    if not permissions:
        print("⚠️  未扫描到可同步的权限")
    else:
        for row in build_permission_preview_rows(permissions):
            print(row)
        print(f"📊 总计: {len(permissions)} 条权限")

    print("=" * 80)


async def main_async(args: argparse.Namespace) -> None:
    """异步主函数"""
    print("🚀 权限同步工具")
    print("=" * 80)

    if args.preview:
        preview_permissions()
        return

    app = create_app()
    permissions = scan_routes_for_permissions(app)
    print(f"🔍 已扫描后端路由权限 {len(permissions)} 条")

    await init_db()
    async with get_db_context() as session:
        if args.permissions_only:
            permission_result = await permission_catalog_service.sync(app, session, dry_run=args.dry_run)
            role_result: dict[str, int] | None = None
            changed = permission_result.created or permission_result.updated or permission_result.deleted
        else:
            authorization_result = await authorization_bootstrap_service.converge_authorization(
                app,
                session,
                dry_run=args.dry_run,
            )
            permission_result = authorization_result.permissions
            role_result = authorization_result.role_permissions
            changed = (
                authorization_result.roles["created"]
                or authorization_result.roles["updated"]
                or permission_result.created
                or permission_result.updated
                or permission_result.deleted
                or role_result["added"]
                or role_result["removed"]
            )
        if not args.dry_run and changed:
            await session.commit()

    if args.dry_run:
        print("🔍 Dry Run 模式：仅比较代码与数据库，不写入数据")

    print("\n📊 同步结果:")
    print("=" * 80)
    print(
        "权限同步: "
        f"新增 {permission_result.created} 条，"
        f"更新 {permission_result.updated} 条，"
        f"跳过 {permission_result.unchanged} 条，"
        f"扫描总数 {len(permissions)} 条"
    )

    if role_result is not None:
        print(
            "角色权限回填: "
            f"处理角色 {role_result['roles_processed']} 个，"
            f"新增关联 {role_result['added']} 条，"
            f"跳过 {role_result['skipped']} 条"
        )

    print("=" * 80)
    print("✅ 同步完成!")


def main() -> None:
    """命令行入口"""
    parser = argparse.ArgumentParser(description="从后端路由自动同步权限到数据库")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只比较不同步",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="仅预览路由扫描结果，不连接数据库",
    )
    parser.add_argument(
        "--permissions-only",
        action="store_true",
        help="只同步 permissions 表，不补内置角色权限",
    )

    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
