"""菜单 Service"""

from collections.abc import Sequence
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.admin.models import Menu, MenuTreeResponseSimple
from src.app.admin.repositories.menu_repository import MenuRepository, menu_repository
from src.core.base_service import BaseService
from src.core.tree_service import TreeServiceMixin


class _MenuLike(Protocol):
    @property
    def id(self) -> int | None: ...


class MenuService(TreeServiceMixin[Menu], BaseService[Menu, MenuRepository]):
    """菜单 Service（支持树形结构和 CRUD）"""

    def __init__(self, repo: MenuRepository = menu_repository):
        super().__init__(repo, enable_cache=True)

    async def get_user_menu_tree(self, db: AsyncSession, user_id: int) -> list[MenuTreeResponseSimple]:
        """获取用户可访问的菜单树

        Args:
            db: 数据库会话
            user_id: 用户 ID

        Returns:
            菜单树列表（MenuTreeResponseSimple，含 children）
        """
        # 1. 获取用户可访问的菜单列表
        menus = await self.repo.get_menus_by_user(db, user_id)

        # 2. 构建树形结构
        return self._build_tree(menus)

    def _build_tree(self, menus: Sequence[_MenuLike]) -> list[MenuTreeResponseSimple]:
        """构建菜单树

        Args:
            menus: 菜单列表

        Returns:
            菜单树列表
        """
        # 转换为 Response Schema
        menu_map: dict[int, MenuTreeResponseSimple] = {}
        for menu in menus:
            menu_id = menu.id
            if menu_id is None:
                continue
            menu_response = MenuTreeResponseSimple.model_validate(menu)
            menu_map[menu_id] = menu_response

        # 构建树
        tree: list[MenuTreeResponseSimple] = []
        for menu_response in menu_map.values():
            if menu_response.parent_id is None:
                tree.append(menu_response)
            else:
                parent = menu_map.get(menu_response.parent_id)
                if parent:
                    parent.children.append(menu_response)
                else:
                    # 兜底：父节点缺失时，避免菜单节点被静默丢弃
                    tree.append(menu_response)

        # 按 sort_order 排序
        for menu_response in menu_map.values():
            menu_response.children.sort(key=lambda x: x.sort_order)
        tree.sort(key=lambda x: x.sort_order)

        return tree

menu_service = MenuService(menu_repository)

__all__ = ["MenuService", "menu_service"]
