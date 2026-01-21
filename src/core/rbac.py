"""
RBAC 权限控制模块

提供基于角色的访问控制（RBAC）功能：
- RequirePermission: 权限验证依赖工厂（支持缓存）
- require_superuser: 超级用户验证
- get_user_permissions: 获取用户权限集合（支持缓存）
- has_permission: 权限检查工具
- invalidate_user_permissions: 清除用户权限缓存
- PermissionDep: 权限依赖类型提示
- SuperUserDep: 超级用户依赖类型提示

## 缓存策略

- 缓存键: `perms:user:{user_id}`
- 缓存时长: 300 秒（5 分钟）
- 自动失效: 权限变更时需手动调用 `invalidate_user_permissions()`
"""

import json
from typing import Annotated

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.app.admin.models import Role, User
from src.core.exceptions import PermissionException
from src.core.security import require_auth
from src.database.dependencies import AsyncSessionDep, CacheDep

# ==================== 常量 ====================

SUPERUSER_PERMISSION = "*"  # 超级用户权限标识
PERM_CACHE_PREFIX = "perms:user"  # 权限缓存键前缀
PERM_CACHE_TTL = 300  # 权限缓存过期时间（秒）


# ==================== 缓存辅助函数 ====================


def _get_perm_cache_key(user_id: int) -> str:
    """生成用户权限缓存键"""
    return f"{PERM_CACHE_PREFIX}:{user_id}"


async def _get_perms_from_cache(cache: CacheDep, user_id: int) -> set[str] | None:
    """从缓存获取用户权限

    Args:
        cache: 缓存服务
        user_id: 用户 ID

    Returns:
        权限集合，缓存不存在时返回 None
    """
    key = _get_perm_cache_key(user_id)
    data = await cache.get(key)
    if data:
        try:
            return set(json.loads(data))
        except (json.JSONDecodeError, TypeError):
            return None
    return None


async def _set_perms_to_cache(cache: CacheDep, user_id: int, permissions: set[str]) -> None:
    """将用户权限写入缓存

    Args:
        cache: 缓存服务
        user_id: 用户 ID
        permissions: 权限集合
    """
    key = _get_perm_cache_key(user_id)
    await cache.set(key, json.dumps(list(permissions)), expire=PERM_CACHE_TTL)


async def invalidate_user_permissions(cache: CacheDep, user_id: int) -> None:
    """清除用户权限缓存

    在以下情况调用：
    - 分配/移除用户角色
    - 修改角色权限
    - 启用/禁用角色
    - 修改用户超级用户状态

    Args:
        cache: 缓存服务
        user_id: 用户 ID
    """
    key = _get_perm_cache_key(user_id)
    await cache.delete(key)


# ==================== 核心函数 ====================


async def _collect_user_permissions(user: User) -> set[str]:
    """收集用户的所有权限（内部函数）

    Args:
        user: 用户对象（必须预加载 roles 和 permissions）

    Returns:
        权限标识集合，超级用户返回 {SUPERUSER_PERMISSION}
    """
    if user.is_superuser:
        return {SUPERUSER_PERMISSION}

    permissions = set()
    for role in user.roles:
        if role.is_active:
            for perm in role.permissions:
                permissions.add(perm.name)
    return permissions


async def get_user_permissions(
    db: AsyncSession,
    user_id: int,
    cache: CacheDep | None = None,
) -> set[str]:
    """获取用户的所有权限（支持缓存）

    Args:
        db: 数据库会话
        user_id: 用户 ID
        cache: 缓存服务（可选，传入时启用缓存）

    Returns:
        权限标识集合
    """
    # 尝试从缓存获取
    if cache:
        cached_perms = await _get_perms_from_cache(cache, user_id)
        if cached_perms is not None:
            return cached_perms

    # 从数据库查询
    result = await db.execute(
        select(User).where(User.id == user_id).options(selectinload(User.roles).selectinload(Role.permissions))
    )
    user = result.scalar_one_or_none()
    permissions = await _collect_user_permissions(user) if user else set()

    # 写入缓存
    if cache and permissions:
        await _set_perms_to_cache(cache, user_id, permissions)

    return permissions


async def has_permission(
    db: AsyncSession,
    user_id: int,
    permission_name: str,
    cache: CacheDep | None = None,
) -> bool:
    """检查用户是否拥有指定权限（支持缓存）

    Args:
        db: 数据库会话
        user_id: 用户 ID
        permission_name: 权限标识
        cache: 缓存服务（可选）

    Returns:
        是否拥有权限
    """
    permissions = await get_user_permissions(db, user_id, cache)
    return SUPERUSER_PERMISSION in permissions or permission_name in permissions


# ==================== 权限验证依赖 ====================


def RequirePermission(permission_name: str, use_cache: bool = True):
    """权限验证依赖工厂函数

    Args:
        permission_name: 权限标识
        use_cache: 是否使用缓存（默认 True）

    Returns:
        FastAPI 依赖函数

    Raises:
        AuthException: 认证失败
        PermissionException: 权限不足
    """

    async def verify_permission(
        user_id: Annotated[int, Depends(require_auth)],
        db: AsyncSessionDep,
        cache: CacheDep,
    ) -> None:
        # 如果启用缓存且缓存服务可用，则使用缓存
        cache_service = cache if use_cache else None
        permissions = await get_user_permissions(db, user_id, cache_service)
        if SUPERUSER_PERMISSION not in permissions and permission_name not in permissions:
            raise PermissionException(f"需要权限: {permission_name}")

    return verify_permission


async def require_superuser(
    user_id: Annotated[int, Depends(require_auth)],
    db: AsyncSessionDep,
) -> None:
    """超级用户验证依赖

    Raises:
        PermissionException: 不是超级用户
    """
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user or not user.is_superuser:
        raise PermissionException("需要超级用户权限")


# ==================== 类型提示 ====================

# 超级用户依赖类型提示
SuperUserDep = Annotated[None, Depends(require_superuser)]


# 权限验证类型提示工厂
def PermissionDep(permission_name: str, use_cache: bool = True) -> Annotated[None, Depends]:
    """权限验证类型提示工厂

    Args:
        permission_name: 权限标识
        use_cache: 是否使用缓存（默认 True）

    Returns:
        类型注解对象
    """
    return Annotated[None, Depends(RequirePermission(permission_name, use_cache))]
