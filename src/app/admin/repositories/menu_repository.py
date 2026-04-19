"""菜单 Repository"""

from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.app.admin.models import Menu, Role, User
from src.database.tree_repository import TreeRepository


def _roles(user: User) -> list[Role]:
    return cast("list[Role]", getattr(cast("Any", user), "roles", []))


def _menus(role: Role) -> list[Menu]:
    return cast("list[Menu]", getattr(cast("Any", role), "menus", []))


def _is_deleted(instance: Any) -> bool:
    return cast("bool", getattr(cast("Any", instance), "is_deleted", False))


def _menu_id(menu: Menu) -> int | None:
    return cast("int | None", getattr(cast("Any", menu), "id", None))


def _menu_sort_key(menu: Menu) -> tuple[int, int]:
    menu_id = _menu_id(menu)
    return (menu.sort_order, menu_id if menu_id is not None else 0)


class MenuRepository(TreeRepository[Menu]):
    """菜单 Repository（继承 TreeRepository 获得树形操作能力）"""

    def __init__(self):
        super().__init__(Menu)

    async def list_for_sync(self, db: AsyncSession) -> list[Menu]:
        """获取菜单同步所需的完整菜单列表。"""
        result = await db.execute(
            select(Menu).order_by(Menu.sort_order, Menu.id)  # type: ignore[arg-type]
        )
        return list(result.scalars().all())

    async def get_menus_by_user(self, db: AsyncSession, user_id: int) -> list[Menu]:
        """获取用户可访问的菜单列表（通过角色）

        Args:
            db: 数据库会话
            user_id: 用户 ID

        Returns:
            用户可访问的菜单列表，超级用户返回所有菜单
        """

        # 查询用户（预加载 roles.menus）
        result = await db.execute(
            select(User)
            .where(User.id == user_id)  # type: ignore[arg-type]
            .options(
                selectinload(User.roles).selectinload(Role.menus),  # type: ignore[arg-type]  # 预加载菜单
            )
        )
        user = result.scalar_one_or_none()

        if not user:
            return []

        # 超级用户返回所有菜单
        if user.is_superuser:
            result = await db.execute(
                select(Menu)
                .where(Menu.is_deleted.is_(False))  # type: ignore[arg-type]
                .order_by(Menu.sort_order, Menu.id)  # type: ignore[arg-type]
            )
            return result.scalars().all()  # type: ignore[return-value]

        # 普通用户：收集角色关联的菜单（按 menu.id 去重，避免 ORM 实例不可哈希）
        menu_map: dict[int, Menu] = {}
        for role in _roles(user):
            if _is_deleted(role):
                continue
            for menu in _menus(role):
                menu_id = _menu_id(menu)
                if not _is_deleted(menu) and menu_id is not None:
                    menu_map[menu_id] = menu

        menus = list(menu_map.values())
        menus.sort(key=_menu_sort_key)
        return menus


menu_repository = MenuRepository()

__all__ = ["MenuRepository", "menu_repository"]
