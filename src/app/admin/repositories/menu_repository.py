"""菜单 Repository"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.app.admin.models import Menu, Role, User
from src.database.tree_repository import TreeRepository


class MenuRepository(TreeRepository[Menu]):
    """菜单 Repository（继承 TreeRepository 获得树形操作能力）"""

    def __init__(self):
        super().__init__(Menu)

    async def get_menus_by_user(self, db: AsyncSession, user_id: int) -> list[Menu]:
        """获取用户可访问的菜单列表（通过角色）

        Args:
            db: 数据库会话
            user_id: 用户 ID

        Returns:
            用户可访问的菜单列表，超级用户返回所有菜单
        """
        from src.app.admin.models import role_menu

        # 查询用户（预加载 roles.menus）
        result = await db.execute(
            select(User)
            .where(User.id == user_id)
            .options(
                selectinload(User.roles).selectinload(Role.menus),  # 预加载菜单
            )
        )
        user = result.scalar_one_or_none()

        if not user:
            return []

        # 超级用户返回所有菜单
        if user.is_superuser:
            result = await db.execute(
                select(Menu)
                .where(Menu.is_deleted == False)  # type: ignore[arg-type]
                .order_by(Menu.sort_order)  # type: ignore[arg-type]
            )
            return result.scalars().all()

        # 普通用户：收集角色关联的菜单
        menus = set()
        for role in user.roles:
            if not role.is_deleted:
                for menu in role.menus:
                    if not menu.is_deleted:  # type: ignore[arg-type]
                        menus.add(menu)

        return list(menus)


menu_repository = MenuRepository()

__all__ = ["MenuRepository", "menu_repository"]
