"""
API 权限管理 Service

提供 API 权限相关的业务逻辑：
- 权限缓存管理
- 用户权限收集
- API 权限查询
"""

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.admin.models import Permission
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

        说明:
            - 委托给 Repository 层处理数据访问
            - Service 层只负责业务逻辑协调
        """
        return await self.repo.get_permission_names_by_user_id(db, user_id)

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

    async def get_user_api_permissions(
        self,
        db: AsyncSession,
        user_id: int,
        perm_type: str = "user_api",
    ) -> list[Permission]:
        """获取用户的 API 权限列表（用于前端动态路由）

        Args:
            db: 数据库会话
            user_id: 用户 ID
            perm_type: 权限类型过滤（默认 user_api，前端只需要内部管理 API）

        Returns:
            用户有权限访问的 API 权限列表（包含 method、path 等信息）

        使用场景：
            - 前端登录后获取用户可访问的 API 列表
            - 前端根据 API 权限动态显示/隐藏功能按钮
            - 前端根据 API 权限控制路由访问

        说明：
            - user_api: 内部管理 API（前端管理系统使用）
            - app_api: 外部应用 API（第三方应用集成，前端不需要）
        """
        # 获取用户的所有权限标识
        permission_names = await self.get_user_permissions(db, user_id)

        # 超级用户返回所有 API 权限
        if SUPERUSER_PERMISSION in permission_names:
            return await self.get_api_permissions(db, perm_type=perm_type, exclude_deleted=True)

        # 普通用户：只返回有权限的 API
        # 1. 获取所有 API 权限
        all_api_perms = await self.get_api_permissions(db, perm_type=perm_type, exclude_deleted=True)

        # 2. 过滤出用户有权限的 API（直接返回，避免不必要的变量赋值）
        return [perm for perm in all_api_perms if perm.name in permission_names]


permission_service = PermissionService()
