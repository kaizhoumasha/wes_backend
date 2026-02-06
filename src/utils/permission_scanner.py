import logging
from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.admin.models import Permission

logger = logging.getLogger(__name__)


def _clean_summary(summary: str | None) -> str | None:
    """去除 summary 中的权限码前缀 [xxx]"""
    if not summary:
        return None
    # 移除 [xxx] 前缀格式
    if summary.startswith("[") and "] " in summary:
        return summary.split("] ", 1)[1]
    return summary


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
            dependency_obj = dep.dependency

            # 清理 description（去除权限码前缀）
            description = _clean_summary(route.summary) or route.name

            # 统一获取元数据：优先检查实例属性，再检查函数属性
            is_api_auth = getattr(dependency_obj, "is_api_auth", False)
            is_rbac = getattr(dependency_obj, "is_rbac", False)
            is_superuser = getattr(dependency_obj, "is_superuser", False)
            perm_name = getattr(dependency_obj, "permission_required", None)

            # 1. 检查 API 认证权限 (RequireAPIPermission)
            if is_api_auth:
                if perm_name and perm_name not in seen_permissions:
                    seen_permissions.add(perm_name)
                    parts = perm_name.split(":")
                    permissions_found.append(
                        {
                            "name": perm_name,
                            "type": "external_api",  # 外部 API 应用权限
                            "category": parts[0] if len(parts) >= 2 else None,  # module = category
                            "description": description,
                            "resource": parts[1] if len(parts) >= 2 else "unknown",  # resource
                            "action": parts[-1] if len(parts) >= 3 else "unknown",  # action
                            "method": next(iter(route.methods)) if route.methods else None,  # 提取 HTTP 方法
                            "path": route.path,  # 提取路由路径
                        }
                    )

            # 2. 检查用户 RBAC 权限 (RequirePermission)
            elif is_rbac:
                if perm_name and perm_name not in seen_permissions:
                    seen_permissions.add(perm_name)
                    parts = perm_name.split(":")
                    permissions_found.append(
                        {
                            "name": perm_name,
                            "type": "user_api",  # 用户 RBAC 权限
                            "category": parts[0] if len(parts) >= 2 else None,  # module = category
                            "description": description,
                            "resource": parts[1] if len(parts) >= 2 else "unknown",  # resource
                            "action": parts[-1] if len(parts) >= 3 else "unknown",  # action
                            "method": next(iter(route.methods)) if route.methods else None,  # 提取 HTTP 方法
                            "path": route.path,  # 提取路由路径
                        }
                    )

            # 3. 检查超级用户权限 (require_superuser)
            elif is_superuser:
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

    # 查询现有权限（使用 scalars 避免 type ignore）
    result = await db.execute(select(Permission))
    existing_perms = {row.name for row in result.scalars()}

    new_perms = [Permission(**perm_data) for perm_data in scanned_perms if perm_data["name"] not in existing_perms]

    if new_perms:
        db.add_all(new_perms)
        await db.commit()
        logger.info(f"自动同步权限: 新增 {len(new_perms)} 条权限")

    return {"added": len(new_perms), "total": len(scanned_perms)}
