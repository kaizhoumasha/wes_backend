"""
RBAC 权限控制模块

提供基于角色的访问控制（RBAC）装饰器和工具：
- require_auth: 认证装饰器
- RequirePermission: 权限装饰器工厂
- require_superuser: 超级用户装饰器
"""

from fastapi import Depends, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.app.admin.models import User
from src.core.exceptions import AuthException, DatabaseException, PermissionException
from src.core.security import jwt_decode, require_auth
from src.database.dependencies import AsyncSessionDep

security = HTTPBearer(auto_error=False)

# ==================== 权限装饰器 ====================


def RequirePermission(permission_name: str):
    """
    权限验证依赖工厂函数

    使用方式：
    ```python
    @router.post("/users", dependencies=[Security(RequirePermission("user:create"))])
    async def create_user(...):
        ...
    ```

    Args:
        permission_name: 权限标识，如 "user:read"

    Returns:
        权限验证依赖函数
    """

    async def verify_permission(
        credentials: HTTPAuthorizationCredentials = Security(security),
    ) -> None:
        """
        验证用户权限

        使用 Security + HTTPBearer 会在 Swagger UI 中显示认证图标

        Args:
            credentials: HTTP Bearer 认证凭证

        Raises:
            PermissionException: 权限不足
        """
        from src.database.db import AsyncSessionLocal

        # 验证 token
        if credentials is None:
            raise AuthException("未提供认证令牌")

        token = credentials.credentials
        token_payload = jwt_decode(token)
        user_id = token_payload.id

        # 直接创建 db session（不依赖 request.state，因为此时还未设置）
        if AsyncSessionLocal is None:
            raise DatabaseException("数据库未初始化")

        async with AsyncSessionLocal() as db:
            # 查询用户（预加载角色）
            result = await db.execute(select(User).where(User.id == user_id).options(selectinload(User.roles)))
            user = result.scalar_one_or_none()

            if not user:
                raise AuthException("用户不存在")

            # 超级用户拥有所有权限
            if user.is_superuser:
                return

            # 收集用户所有权限
            user_permissions = set()
            for role in user.roles:
                if role.is_active:
                    for perm in role.permissions:
                        user_permissions.add(perm.name)

            # 检查权限
            if permission_name not in user_permissions:
                raise PermissionException(f"需要权限: {permission_name}")

    return verify_permission


# ==================== 超级用户装饰器 ====================


async def require_superuser(
    request: Request,
    db: AsyncSessionDep,
    user_id: int = Depends(require_auth),
) -> None:
    """
    要求超级用户权限

    Args:
        request: FastAPI 请求对象
        user_id: 当前用户 ID
        db: 数据库会话

    Raises:
        PermissionException: 不是超级用户
    """
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user or not user.is_superuser:
        raise PermissionException("需要超级用户权限")


# ==================== 权限查询辅助函数 ====================


async def get_user_permissions(db: AsyncSession, user_id: int) -> set[str]:
    """
    获取用户的所有权限

    Args:
        db: 数据库会话
        user_id: 用户 ID

    Returns:
        权限标识集合
    """
    result = await db.execute(select(User).where(User.id == user_id).options(selectinload(User.roles)))
    user = result.scalar_one_or_none()

    if not user:
        return set()

    # 超级用户拥有所有权限（用特殊标记）
    if user.is_superuser:
        return {"*"}

    # 收集权限
    permissions = set()
    for role in user.roles:
        if role.is_active:
            for perm in role.permissions:
                permissions.add(perm.name)

    return permissions


async def has_permission(db: AsyncSession, user_id: int, permission_name: str) -> bool:
    """
    检查用户是否拥有指定权限

    Args:
        db: 数据库会话
        user_id: 用户 ID
        permission_name: 权限标识

    Returns:
        是否拥有权限
    """
    permissions = await get_user_permissions(db, user_id)
    return "*" in permissions or permission_name in permissions


# ==================== 便捷依赖注入 ====================

# 超级用户依赖注入
DependsSuperUser = Depends(require_superuser)
