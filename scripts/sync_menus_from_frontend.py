"""
从前端 router 自动同步菜单到数据库

使用方式：
    uv run python scripts/sync_menus_from_frontend.py
    uv run python scripts/sync_menus_from_frontend.py --frontend-path ../wes_frontend
    uv run python scripts/sync_menus_from_frontend.py --dry-run
    uv run python scripts/sync_menus_from_frontend.py --preview
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from src.app.admin.services.menu_sync_service import menu_sync_service
from src.database.db import get_db_context, init_db
from src.utils.frontend_menu_parser import resolve_frontend_root


def preview_menus(frontend_path: str | None) -> None:
    """预览从前端 router 解析出的菜单数据"""

    menu_definitions = menu_sync_service.load_frontend_menu_definitions(frontend_path)

    print("\n📋 菜单数据预览:")
    print("=" * 80)

    if not menu_definitions:
        print("⚠️  未解析到可同步的菜单")
    else:
        for row in menu_sync_service.preview_rows(menu_definitions):
            print(row)
        print(f"📊 总计: {len(menu_definitions)} 条菜单")

    print("=" * 80)


async def main_async(args: argparse.Namespace) -> None:
    """异步主函数"""

    frontend_root = resolve_frontend_root(args.frontend_path)

    print("🚀 菜单同步工具")
    print("=" * 80)
    print(f"📦 前端路径: {frontend_root}")

    if not frontend_root.exists():
        print(f"\n❌ 前端路径不存在: {frontend_root}")
        sys.exit(1)

    try:
        if args.preview:
            preview_menus(args.frontend_path)
            return

        menu_definitions = menu_sync_service.load_frontend_menu_definitions(args.frontend_path)
        print(f"🔍 已从前端 router 解析 {len(menu_definitions)} 条菜单")

        await init_db()
        async with get_db_context() as session:
            result = await menu_sync_service.sync_menus(session, menu_definitions, dry_run=args.dry_run)
            if not args.dry_run:
                await session.commit()

        if args.dry_run:
            print("🔍 Dry Run 模式：仅比较前端 router 与数据库，不写入数据")

        print("\n📊 同步结果:")
        print("=" * 80)
        print(result.summary())

        if result.errors:
            print("\n❌ 错误详情:")
            for error in result.errors:
                print(f"  - {error['message']}")
                if error.get("data"):
                    print(f"    数据: {error['data']}")

        print("=" * 80)
        print("✅ 同步完成!")
    except FileNotFoundError as exc:
        print(f"\n❌ {exc}")
        sys.exit(1)
    except ValueError as exc:
        print(f"\n❌ {exc}")
        sys.exit(1)
    except Exception as exc:
        print(f"\n❌ 同步失败: {exc}")
        raise


def main() -> None:
    """命令行入口"""

    parser = argparse.ArgumentParser(description="从前端 router 自动同步菜单到数据库")
    parser.add_argument(
        "--frontend-path",
        type=str,
        default=None,
        help="前端项目路径（默认 ../wes_frontend 或环境变量 WES_FRONTEND_PATH）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只比较不同步",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="仅预览前端 router 解析结果，不连接数据库",
    )

    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
