"""
菜单数据初始化脚本

初始化系统菜单数据，支持直接从前端 router 解析或使用默认配置。

使用方式：
    uv run python -m migrations.seed_data.seed_menus
    uv run python -m migrations.seed_data.seed_menus --use-frontend-data
"""

import asyncio
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.admin.models.menu import Menu
from src.app.admin.repositories.menu_repository import MenuRepository
from src.app.admin.services.menu_sync_service import menu_sync_service
from src.database.hooks import HookType
from src.utils.frontend_menu_parser import load_frontend_router_menus, resolve_frontend_root


def _disable_audit_hooks(repo) -> None:
    """
    禁用 Repository 的审计日志 Hook

    在种子数据初始化期间禁用审计日志，避免 ENUM 类型问题。
    只移除审计日志 hooks（priority=100），保留其他 hooks（如 tree_path 维护）。

    Args:
        repo: Repository 实例
    """
    for hook_type in [HookType.AFTER_CREATE, HookType.AFTER_UPDATE, HookType.AFTER_DELETE]:
        repo.hook_manager.hooks[hook_type] = [
            hook for hook in repo.hook_manager.hooks[hook_type] if hook.priority != 100
        ]


def get_default_menus() -> list[dict[str, Any]]:
    """
    获取默认菜单数据

    这些是系统基础菜单，对应前端路由配置
    """
    return [
        # ========== 仪表盘 ==========
        {
            "name": "system:dashboard:menu",
            "title": "仪表盘",
            "path": "/dashboard",
            "component": "views/dashboard/Dashboard.vue",
            "icon": "Dashboard",
            "sort_order": 1,
            "is_hidden": False,
        },
        # ========== 系统管理 ==========
        {
            "name": "admin:user:menu",
            "title": "用户管理",
            "path": "/admin/users",
            "component": "views/admin/users/UserListPage.vue",
            "icon": "User",
            "sort_order": 10,
            "is_hidden": False,
        },
        {
            "name": "admin:role:menu",
            "title": "角色管理",
            "path": "/admin/roles",
            "component": "views/admin/roles/RoleListPage.vue",
            "icon": "Lock",
            "sort_order": 11,
            "is_hidden": False,
        },
        {
            "name": "admin:permission:menu",
            "title": "权限管理",
            "path": "/admin/permissions",
            "component": "views/admin/permissions/PermissionListPage.vue",
            "icon": "Key",
            "sort_order": 12,
            "is_hidden": False,
        },
        {
            "name": "admin:menu:menu",
            "title": "菜单管理",
            "path": "/admin/menus",
            "component": "views/admin/menus/MenuListPage.vue",
            "icon": "Menu",
            "sort_order": 13,
            "is_hidden": False,
        },
        {
            "name": "admin:audit:menu",
            "title": "审计日志",
            "path": "/admin/audit-logs",
            "component": "views/admin/audit/AuditLogListPage.vue",
            "icon": "DocumentText",
            "sort_order": 14,
            "is_hidden": False,
        },
        # ========== 设备管理 ==========
        {
            "name": "biz:device:menu",
            "title": "设备管理",
            "path": "/biz/devices",
            "component": "views/biz/device/DeviceListPage.vue",
            "icon": "Box",
            "sort_order": 20,
            "is_hidden": False,
        },
        {
            "name": "biz:device:detail:menu",
            "title": "设备详情",
            "path": "/biz/devices/detail",
            "component": "views/biz/device/DeviceDetailPage.vue",
            "icon": "",
            "sort_order": 21,
            "is_hidden": True,
        },
        # ========== 工作流管理 ==========
        {
            "name": "biz:workline:menu",
            "title": "工作流管理",
            "path": "/biz/worklines",
            "component": "views/biz/workline/WorklineListPage.vue",
            "icon": "Workflow",
            "sort_order": 30,
            "is_hidden": False,
        },
        {
            "name": "biz:session:menu",
            "title": "会话管理",
            "path": "/biz/sessions",
            "component": "views/biz/session/SessionListPage.vue",
            "icon": "Clock",
            "sort_order": 31,
            "is_hidden": False,
        },
        # ========== API 管理 ==========
        {
            "name": "api:app:menu",
            "title": "应用管理",
            "path": "/api/applications",
            "component": "views/api/applications/AppListPage.vue",
            "icon": "App",
            "sort_order": 40,
            "is_hidden": False,
        },
        {
            "name": "api:log:menu",
            "title": "访问日志",
            "path": "/api/access-logs",
            "component": "views/api/logs/AccessLogListPage.vue",
            "icon": "Document",
            "sort_order": 41,
            "is_hidden": False,
        },
    ]


async def seed_menus(db: AsyncSession, use_frontend_data: bool = False) -> None:
    """
    初始化菜单数据

    Args:
        db: 数据库会话
        use_frontend_data: 是否直接使用前端 router 解析结果
    """
    repo = MenuRepository()
    _disable_audit_hooks(repo)

    print("  5️⃣ 初始化菜单数据...")

    # 检查是否已经初始化过
    existing_result = await db.execute(select(Menu).limit(1))
    existing = existing_result.scalar_one_or_none()

    if existing:
        menu_count_result = await db.execute(select(Menu))
        menu_count = len(menu_count_result.scalars().all())
        print(f"     ⚠️  菜单数据已存在 ({menu_count} 条)，跳过初始化")
        print("     💡 如需重新初始化，请先删除现有菜单数据")
        return

    # 尝试从前端 router 读取菜单数据
    menu_definitions = []
    menus = []
    if use_frontend_data:
        try:
            frontend_path = resolve_frontend_root()
            menu_definitions = load_frontend_router_menus(frontend_path)
            print(f"     📦 从前端 router 解析 {len(menu_definitions)} 条菜单数据")
        except Exception as e:
            print(f"     ⚠️  读取前端 router 失败: {e}")
            print("     📦 使用默认菜单数据")

    # 使用默认菜单数据
    if not menu_definitions:
        menus = get_default_menus()
        print(f"     📦 使用默认菜单数据 ({len(menus)} 条)")

    if menu_definitions:
        sync_result = await menu_sync_service.sync_menus(db, menu_definitions, dry_run=False)
        print(f"     {sync_result.summary().replace(chr(10), ' | ')}")
    else:
        for menu_data in menus:
            await repo.create(db, menu_data)

    # 提交事务
    await db.commit()

    # 统计结果
    menu_count_result = await db.execute(select(Menu))
    menu_count = len(menu_count_result.scalars().all())

    print(f"     ✅ 菜单数量: {menu_count}")


async def seed_menus_sync(use_frontend_data: bool = False) -> None:
    """同步版本的菜单初始化（用于非异步环境）"""
    from src.database.db import get_db_context, init_db

    # 初始化数据库连接
    await init_db()

    async with get_db_context() as session:
        await seed_menus(session, use_frontend_data=use_frontend_data)


def main():
    """命令行入口"""
    import argparse

    parser = argparse.ArgumentParser(description="初始化菜单数据")
    parser.add_argument(
        "--use-frontend-data",
        action="store_true",
        help="尝试从前端项目读取菜单数据",
    )

    args = parser.parse_args()

    print("🚀 菜单数据初始化工具")
    print("=" * 80)

    if args.use_frontend_data:
        print("📦 尝试从前端 router 解析菜单数据\n")
    else:
        print("📦 使用默认菜单数据\n")

    asyncio.run(seed_menus_sync(use_frontend_data=args.use_frontend_data))

    print("=" * 80)
    print("✅ 菜单数据初始化完成!")
    print("\n💡 提示:")
    print("   如需同步最新前端路由配置，请运行:")
    print("   uv run python scripts/sync_menus_from_frontend.py --preview")
    print("   uv run python scripts/sync_menus_from_frontend.py")


if __name__ == "__main__":
    main()
