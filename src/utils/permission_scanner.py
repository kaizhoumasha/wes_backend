import logging
from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.admin.models import Permission

logger = logging.getLogger(__name__)


def scan_routes_for_permissions(app: FastAPI) -> list[dict[str, Any]]:
    """
    扫描 FastAPI 应用中的所有路由，提取权限信息
    """
    permissions_found = []
    seen_permissions = set()

    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue

        # 检查路由的依赖项
        for dep in route.dependencies:
            func = dep.dependency

            # 1. 检查 API 认证权限 (RequireAPIPermission)
            if getattr(func, "is_api_auth", False):
                perm_name = getattr(func, "permission_required", None)
                if perm_name and perm_name not in seen_permissions:
                    seen_permissions.add(perm_name)
                    permissions_found.append(
                        {
                            "name": perm_name,
                            "type": "api",  # API 权限类型
                            "description": route.summary or route.name,
                            "resource": perm_name.split(":")[0] if ":" in perm_name else "unknown",
                            "action": perm_name.split(":")[-1] if ":" in perm_name else "unknown",
                        }
                    )

            # 2. 检查用户 RBAC 权限 (RequirePermission)
            elif getattr(func, "is_rbac", False):
                perm_name = getattr(func, "permission_required", None)
                if perm_name and perm_name not in seen_permissions:
                    seen_permissions.add(perm_name)
                    permissions_found.append(
                        {
                            "name": perm_name,
                            "type": "menu",  # 默认为菜单/功能权限
                            "description": route.summary or route.name,
                            "resource": perm_name.split(":")[0] if ":" in perm_name else "unknown",
                            "action": perm_name.split(":")[-1] if ":" in perm_name else "unknown",
                        }
                    )

            # 3. 检查超级用户权限 (require_superuser)
            elif getattr(func, "is_superuser", False):
                # 超级用户权限通常不需要录入数据库，或者录入为特殊类型
                pass

    return permissions_found


async def sync_permissions_to_db(app: FastAPI, db: AsyncSession) -> dict[str, int]:
    """
    将扫描到的权限同步到数据库
    """
    scanned_perms = scan_routes_for_permissions(app)
    if not scanned_perms:
        return {"added": 0, "total": 0}

    # 查询现有权限
    result = await db.execute(select(Permission.name))
    existing_perms = {row[0] for row in result.all()}

    new_perms = []
    for perm_data in scanned_perms:
        if perm_data["name"] not in existing_perms:
            new_perms.append(Permission(**perm_data))

    if new_perms:
        db.add_all(new_perms)
        await db.commit()
        logger.info(f"自动同步权限: 新增 {len(new_perms)} 条权限")

    return {"added": len(new_perms), "total": len(scanned_perms)}
