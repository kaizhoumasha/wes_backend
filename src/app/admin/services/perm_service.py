"""
API 权限管理 Service

提供 API 权限相关的业务逻辑：
- 权限缓存管理
- 用户权限收集
- API 权限查询
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.app.admin.models import Permission, Role, User
from src.app.admin.repositories.perm_repository import PermissionRepository, permission_repository
from src.common.cache_config import cache_settings
from src.core.base_service import BaseService

# ==================== 常量 ====================

SUPERUSER_PERMISSION = "*"  # 超级用户权限标识


class PermissionService(BaseService[Permission, PermissionRepository]):
    """API 权限 Service"""

    def __init__(self, repo: PermissionRepository = permission_repository):
        super().__init__(
            repo,
            enable_cache=True,
            cache_prefix=cache_settings.PERMISSION.prefix,
            cache_expire=cache_settings.PERMISSION.expire,
        )
        self.repo: PermissionRepository = repo

    # ==================== 用户权限收集 ====================

    async def get_user_permissions(self, db: AsyncSession, user_id: int) -> set[str]:
        """获取用户的所有 API 权限

        Args:
            db: 数据库会话
            user_id: 用户 ID

        Returns:
            权限标识集合，超级用户返回 {SUPERUSER_PERMISSION}
            用户不存在时返回空集合

        优化说明:
            - 使用 is_deleted 替代 is_active 简化状态管理
            - is_deleted=False 表示角色/权限启用
            - is_deleted=True 表示角色/权限已禁用/删除
        """
        # 查询用户（预加载 roles 和 permissions）
        result = await db.execute(
            select(User).where(User.id == user_id).options(selectinload(User.roles).selectinload(Role.permissions))
        )
        user = result.scalar_one_or_none()

        if not user:
            return set()

        # 收集权限（超级用户返回特殊标识）
        if user.is_superuser:
            return {SUPERUSER_PERMISSION}

        permissions = set()
        for role in user.roles:
            # 只收集未删除的角色权限
            if not role.is_deleted:
                for perm in role.permissions:
                    # 只收集未删除的权限
                    if not perm.is_deleted:
                        permissions.add(perm.name)
        return permissions

    async def get_api_permissions(
        self,
        db: AsyncSession,
        perm_type: str | None = None,
        exclude_deleted: bool = True,
    ) -> list[Permission]:
        """获取 API 权限列表

        Args:
            db: 数据库会话
            perm_type: 权限类型过滤（user_api/app_api），None 表示获取所有
            exclude_deleted: 是否排除已删除的权限

        Returns:
            API 权限列表
        """
        return await self.repo.get_api_permissions(db, perm_type=perm_type, exclude_deleted=exclude_deleted)


permission_service = PermissionService()
