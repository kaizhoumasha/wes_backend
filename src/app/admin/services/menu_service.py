"""菜单 Service"""

from datetime import datetime

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.admin.models import Menu, MenuResponse
from src.app.admin.repositories.menu_repository import MenuRepository, menu_repository
from src.common.cache_config import cache_settings
from src.core.tree_service import TreeServiceMixin


class MenuService(TreeServiceMixin):
    """菜单 Service（混入树形服务能力）"""

    def __init__(self, repo: MenuRepository = menu_repository):
        self.repo = repo
        self._cache_prefix = cache_settings.USER.prefix  # 复用用户缓存配置
        self._cache_expire = cache_settings.USER.expire

    async def get_user_menu_tree(
        self, db: AsyncSession, user_id: int
    ) -> list[MenuResponse]:
        """获取用户可访问的菜单树

        Args:
            db: 数据库会话
            user_id: 用户 ID

        Returns:
            菜单树列表（MenuResponse，含 children）
        """
        # 1. 获取用户可访问的菜单列表
        menus = await self.repo.get_menus_by_user(db, user_id)

        # 2. 构建树形结构
        return self._build_tree(menus)

    def _build_tree(self, menus: list[Menu]) -> list[MenuResponse]:
        """构建菜单树

        Args:
            menus: 菜单列表

        Returns:
            菜单树列表
        """
        # 转换为 Response Schema
        menu_map: dict[int, MenuResponse] = {}
        for menu in menus:
            menu_response = MenuResponse(
                id=menu.id,
                name=menu.name,
                title=menu.title,
                path=menu.path,
                component=menu.component,
                icon=menu.icon,
                parent_id=menu.parent_id,
                tree_path=menu.tree_path,
                level=menu.level,
                sort_order=menu.sort_order,
                is_hidden=menu.is_hidden,
                roles=[],  # 树形响应不加载角色
            )
            menu_map[menu.id] = menu_response

        # 构建树
        tree: list[MenuResponse] = []
        for menu_id, menu_response in menu_map.items():
            if menu_response.parent_id is None:
                tree.append(menu_response)
            else:
                parent = menu_map.get(menu_response.parent_id)
                if parent:
                    parent.children.append(menu_response)

        # 按 sort_order 排序
        for menu_response in menu_map.values():
            menu_response.children.sort(key=lambda x: x.sort_order)
        tree.sort(key=lambda x: x.sort_order)

        return tree

    def _to_dict(self, item: Menu) -> dict:
        """将模型转换为字典

        Args:
            item: Menu 模型实例

        Returns:
            字典形式的 Menu 数据
        """
        mapper = inspect(item.__class__)
        result = {}
        for c in mapper.columns:
            value = getattr(item, c.key, None)
            result[c.key] = value.isoformat() if isinstance(value, datetime) else value
        return result


menu_service = MenuService()

__all__ = ["MenuService", "menu_service"]
