"""菜单 Service"""

from datetime import datetime
from typing import Any, cast

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.admin.models import Menu, MenuTreeResponse
from src.app.admin.repositories.menu_repository import MenuRepository, menu_repository
from src.core.base_service import BaseService
from src.core.tree_service import TreeServiceMixin


class MenuService(TreeServiceMixin[Menu], BaseService[Menu, MenuRepository]):
    """菜单 Service（支持树形结构和 CRUD）"""

    def __init__(self, repo: MenuRepository = menu_repository):
        # 初始化 TreeServiceMixin（设置 self.repo）
        cast(Any, TreeServiceMixin.__init__)(self, repo)
        # 初始化 BaseService（启用缓存）
        cast(Any, BaseService.__init__)(self, repo, enable_cache=True)
        self.repo = repo

    async def get_user_menu_tree(self, db: AsyncSession, user_id: int) -> list[MenuTreeResponse]:
        """获取用户可访问的菜单树

        Args:
            db: 数据库会话
            user_id: 用户 ID

        Returns:
            菜单树列表（MenuTreeResponse，含 children）
        """
        # 1. 获取用户可访问的菜单列表
        menus = await self.repo.get_menus_by_user(db, user_id)

        # 2. 构建树形结构
        return self._build_tree(menus)

    def _build_tree(self, menus: list[Menu]) -> list[MenuTreeResponse]:
        """构建菜单树

        Args:
            menus: 菜单列表

        Returns:
            菜单树列表
        """
        # 转换为 Response Schema
        menu_map: dict[int, MenuTreeResponse] = {}
        for menu in menus:
            menu_response = MenuTreeResponse(
                id=menu.id,  # type: ignore[arg-type]
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
            )
            menu_map[menu.id] = menu_response  # type: ignore[arg-type]

        # 构建树
        tree: list[MenuTreeResponse] = []
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

    def _to_dict(self, item: Menu, schema: type | None = None) -> dict[str, Any]:
        """将模型转换为字典

        Args:
            item: Menu 模型实例
            schema: 响应 Schema（保持与基类签名一致，当前未使用）

        Returns:
            字典形式的 Menu 数据
        """
        _ = schema
        mapper = inspect(item.__class__)
        result: dict[str, Any] = {}
        for c in mapper.columns:
            value = getattr(item, c.key, None)
            result[c.key] = value.isoformat() if isinstance(value, datetime) else value
        return result


menu_service = MenuService(menu_repository)

__all__ = ["MenuService", "menu_service"]
