"""
权限管理 Service

提供权限相关的业务逻辑：
- 菜单树构建
- 按类型获取权限
- 权限缓存管理
- 用户菜单获取（前端集成）
"""

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.admin.models import Permission, PermissionTree
from src.app.admin.repositories.perm_repository import PermissionRepository, permission_repository
from src.common.cache_config import cache_settings
from src.core.base_service import BaseService
from src.core.tree_service import TreeServiceMixin


class PermissionService(BaseService[Permission, PermissionRepository], TreeServiceMixin):
    """权限 Service"""

    def __init__(self, repo: PermissionRepository = permission_repository):
        super().__init__(
            repo,
            enable_cache=True,
            cache_prefix=cache_settings.PERMISSION.prefix,
            cache_expire=cache_settings.PERMISSION.expire,
        )
        self.repo: PermissionRepository = repo

    async def get_menu_tree(
        self,
        db: AsyncSession,
        *,
        active_only: bool = True,
        include_hidden: bool = False,
    ) -> list[PermissionTree]:
        """获取菜单树结构（PermissionTree 对象，用于前端）

        Args:
            db: 数据库会话
            active_only: 是否只获取启用的权限
            include_hidden: 是否包含隐藏菜单

        Returns:
            菜单树列表（PermissionTree 对象）
        """
        menu_perms = await self.repo.get_menu_tree(db, include_inactive=not active_only)

        # 转换为 PermissionTree 对象
        return self._build_permission_tree(menu_perms, include_hidden)

    async def get_user_menu_tree(
        self,
        db: AsyncSession,
        user_id: int,
        *,
        include_hidden: bool = False,
    ) -> list[PermissionTree]:
        """获取用户菜单树（基于用户权限过滤）

        Args:
            db: 数据库会话
            user_id: 用户 ID
            include_hidden: 是否包含隐藏菜单

        Returns:
            用户有权限访问的菜单树
        """
        from src.core.rbac import get_user_permissions

        # 获取用户所有权限（get_user_permissions 已返回 set[str]，无需再转换）
        user_perm_names = await get_user_permissions(db, user_id)

        # 获取所有菜单权限
        all_menus = await self.repo.get_menu_permissions(db, active_only=True, include_hidden=include_hidden)

        # 超级用户检查：拥有 "*" 权限可以访问所有菜单
        if self._is_superuser(user_perm_names):
            # 超级用户直接返回所有菜单
            return self._build_permission_tree(all_menus, include_hidden)

        # 普通用户：过滤有权限的菜单
        perm_dict = {perm.id: perm for perm in all_menus}
        user_menus = [perm for perm in all_menus if self._check_menu_permission(perm, user_perm_names, perm_dict)]

        # 转换为 PermissionTree 对象
        return self._build_permission_tree(user_menus, include_hidden)

    def _is_superuser(self, user_permissions: set[str]) -> bool:
        """检查是否为超级用户（DRY 优化：统一超级用户检查逻辑）

        Args:
            user_permissions: 用户权限集合

        Returns:
            是否为超级用户
        """
        return "*" in user_permissions

    def _check_menu_permission(
        self,
        perm: Permission,
        user_permissions: set[str],
        perm_dict: dict[int, Permission],
    ) -> bool:
        """检查用户是否有访问该菜单的权限

        规则：
        1. 超级用户（拥有 "*" 权限）可以访问所有菜单
        2. 用户拥有该菜单的权限
        3. 或者用户拥有该菜单的任意子菜单的权限（显示父菜单）

        Args:
            perm: 权限对象
            user_permissions: 用户权限集合
            perm_dict: 权限字典（用于查找）

        Returns:
            是否有权限
        """
        # 超级用户检查：拥有 "*" 权限可以访问所有菜单
        if self._is_superuser(user_permissions):
            return True

        # 直接权限检查
        if perm.name in user_permissions:
            return True

        # 检查是否有子菜单权限（只检查未删除且未隐藏的菜单）
        if not perm.is_deleted and not perm.is_hidden:
            # 查找所有子菜单（type=menu 且 parent_id=perm.id）
            return any(
                child_perm.type == "menu" and child_perm.parent_id == perm.id and child_perm.name in user_permissions
                for child_perm in perm_dict.values()
            )

        return False

    def _build_permission_tree(
        self,
        permissions: list[Permission],
        include_hidden: bool = False,
    ) -> list[PermissionTree]:
        """构建 PermissionTree 对象树

        Args:
            permissions: 权限列表
            include_hidden: 是否包含隐藏菜单

        Returns:
            PermissionTree 对象树
        """
        # 转换为字典
        perm_dict = {perm.id: perm for perm in permissions}

        # 定义需要从 Permission 复制到 PermissionTree 的字段（DRY 优化：避免重复列举）
        tree_fields = [
            "id",
            "name",
            "title",
            "description",
            "path",
            "parent_id",
            "level",
            "sort_order",
            "tree_path",
            "type",
            "category",
            "resource",
            "action",
            "method",
            "component",
            "icon",
            "redirect",
            "is_active",
            "is_hidden",
            "is_cached",
            "is_affix",
            "is_external",
            "external_url",
            "meta",
            "api_permissions",
            "created_at",
            "updated_at",
        ]

        # 构建 PermissionTree 对象（完全避免访问 SQLAlchemy 关系）
        tree_dict: dict[int, PermissionTree] = {}
        for perm in permissions:
            if not include_hidden and perm.is_hidden:
                continue

            # 使用字典推导式提取字段值（优化：减少硬编码参数）
            field_values = {field: getattr(perm, field, None) for field in tree_fields}
            field_values["children"] = []
            tree_dict[perm.id] = PermissionTree.model_construct(**field_values)

        # 构建树形结构
        root_nodes: list[PermissionTree] = []
        for perm_id, tree_node in tree_dict.items():
            parent_id = perm_dict[perm_id].parent_id
            if parent_id and parent_id in tree_dict:
                tree_dict[parent_id].children.append(tree_node)
            else:
                root_nodes.append(tree_node)

        return root_nodes


permission_service = PermissionService()
