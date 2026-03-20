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

## 架构说明

权限收集逻辑已移至 PermissionService.get_user_permissions()，本模块只负责：
1. 缓存管理
2. 权限验证
3. FastAPI 依赖注入

## 缓存策略

- 缓存键: `perms:user:{user_id}`
- 缓存时长: 300 秒（5 分钟）
- 自动失效: 权限变更时需手动调用 `invalidate_user_permissions()`
"""

from collections.abc import Iterable
from typing import Annotated, Any, cast

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import PermissionException
from src.core.security import require_auth
from src.database.cache_helpers import get_cached_value, parse_set_from_cached, set_cached_value
from src.database.dependencies import AsyncSessionDep, CacheDep

# ==================== 常量 ====================

SUPERUSER_PERMISSION = "*"  # 超级用户权限标识
PERM_CACHE_PREFIX = "perms:user"  # 权限缓存键前缀
PERM_CACHE_TTL = 300  # 权限缓存过期时间（秒）
PERM_EMPTY_CACHE_TTL = 120  # 空权限集缓存过期时间（秒）


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

    hit, permissions = await get_cached_value(
        cache,
        key,
        parser=parse_set_from_cached,
    )
    if not hit:
        return None
    return permissions or set()


async def _set_perms_to_cache(
    cache: CacheDep, user_id: int, permissions: set[str], expire: int = PERM_CACHE_TTL
) -> None:
    """将用户权限写入缓存

    Args:
        cache: 缓存服务
        user_id: 用户 ID
        permissions: 权限集合
    """
    key = _get_perm_cache_key(user_id)
    _ = await set_cached_value(cache, key, list(permissions), expire=expire)


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
    _ = await cache.delete(key)


async def invalidate_users_permissions(cache: CacheDep, user_ids: Iterable[int]) -> None:
    """批量清除多个用户的权限缓存

    Args:
        cache: 缓存服务
        user_ids: 用户 ID 可迭代对象
    """
    # 去重并过滤无效值，避免重复删除导致额外开销
    unique_user_ids = {uid for uid in user_ids if isinstance(uid, int) and uid > 0}
    if not unique_user_ids:
        return

    for user_id in unique_user_ids:
        await invalidate_user_permissions(cache, user_id)


# ==================== 核心函数 ====================


async def get_user_permissions(
    db: AsyncSession,
    user_id: int,
    cache: CacheDep | None = None,
) -> set[str]:
    """获取用户的所有权限（支持缓存）

    架构说明：
        权限收集逻辑已移至 PermissionService.get_user_permissions()
        本函数只负责缓存管理和调用 Service

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

    # 从数据库查询 - 调用 Service 层（懒加载导入避免循环依赖）
    from src.app.admin.services import permission_service

    permissions = await permission_service.get_user_permissions(db, user_id)

    # 写入缓存
    if cache:
        cache_ttl = PERM_CACHE_TTL if permissions else PERM_EMPTY_CACHE_TTL
        await _set_perms_to_cache(cache, user_id, permissions, expire=cache_ttl)

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

    # 挂载元数据，方便扫描器读取
    cast("Any", verify_permission).permission_required = permission_name
    cast("Any", verify_permission).is_rbac = True

    return verify_permission


async def require_superuser(
    request: Request,
    _user_id: Annotated[int, Depends(require_auth)],
) -> None:
    """超级用户验证依赖（性能优化：从 JWT Token 读取，无需数据库查询）

    Args:
        request: FastAPI 请求对象（包含 request.state.is_superuser）
        _user_id: 用户 ID（由 require_auth 验证，但不使用）

    Raises:
        PermissionException: 不是超级用户
    """
    # 从 request.state 读取 is_superuser（由 _verify_token 从 JWT Token 填充）
    is_superuser = getattr(request.state, "is_superuser", False)
    if not is_superuser:
        raise PermissionException("需要超级用户权限")


cast("Any", require_superuser).is_superuser = True


# ==================== 类型提示 ====================

# 超级用户依赖类型提示
SuperUserDep = Annotated[None, Depends(require_superuser)]


# 权限验证类型提示工厂
def PermissionDep(permission_name: str, use_cache: bool = True) -> Annotated[Any, Depends]:
    """权限验证类型提示工厂

    Args:
        permission_name: 权限标识
        use_cache: 是否使用缓存（默认 True）

    Returns:
        类型注解对象
    """
    return Annotated[Any, Depends(RequirePermission(permission_name, use_cache))]
