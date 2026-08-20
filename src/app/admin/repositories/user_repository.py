"""
用户仓库（User Repository）

负责用户数据访问层（CRUD 操作），继承通用 BaseRepository。

职责:
1. 数据库查询和操作
2. 数据持久化
3. 事务管理
4. 用户特定的查询方法

架构:
- 继承 BaseRepository 获得通用 CRUD 能力
- 扩展用户特定的业务查询方法
"""

from sqlalchemy import desc
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.admin.models import User, user_role
from src.database.base_repository import BaseRepository


class UserRepository(BaseRepository[User]):
    """
    用户仓库类

    继承 BaseRepository 获得通用 CRUD 方法：
    - get_by_id(db, id)
    - get_by_field(db, field_name, value)
    - get_all(db, where_clauses, limit, offset, order_by)
    - get_paginated(db, page, page_size)
    - create(db, data)
    - update(db, id, data)
    - delete(db, id)
    - exists(db, **kwargs)
    - count(db, where_clauses)

    扩展用户特定的查询方法。
    """

    def __init__(self):
        """初始化用户仓库"""
        super().__init__(User)

    # ==================== 用户特定的查询方法 ====================

    async def get_by_username(self, db: AsyncSession, username: str) -> User | None:
        """
        根据用户名获取用户

        Args:
            db: 数据库会话
            username: 用户名

        Returns:
            用户对象或 None
        """
        return await self.get_by_field(db, "username", username)

    async def get_by_username_with_roles(self, db: AsyncSession, username: str) -> User | None:
        """
        根据用户名获取用户（预加载 roles）

        用于认证场景，需要同时获取用户及其角色信息。

        Args:
            db: 数据库会话
            username: 用户名

        Returns:
            用户对象（含 roles）或 None
        """
        return await self.get_by_field(db, "username", username, relationships=["roles"])

    async def get_by_id_with_roles(self, db: AsyncSession, user_id: int) -> User | None:
        """
        根据 ID 获取用户（预加载 roles）

        用于认证场景，需要同时获取用户及其角色信息。

        Args:
            db: 数据库会话
            user_id: 用户 ID

        Returns:
            用户对象（含 roles）或 None
        """
        return await self.get_by_field(db, "id", user_id, relationships=["roles"])

    async def get_by_email(self, db: AsyncSession, email: str) -> User | None:
        """
        根据邮箱获取用户

        Args:
            db: 数据库会话
            email: 邮箱

        Returns:
            用户对象或 None
        """
        return await self.get_by_field(db, "email", email)

    async def get_active_users(self, db: AsyncSession, limit: int | None = None) -> list[User]:
        """
        获取激活用户列表（未被软删除的用户）

        Args:
            db: 数据库会话
            limit: 限制数量

        Returns:
            用户列表
        """
        _, users = await self.get_list(
            db,
            limit=limit or 1000,
            where_clauses_raw=[User.is_deleted == False],  # noqa: E712
        )
        return users

    async def get_first_superuser(self, db: AsyncSession) -> User | None:
        """
        获取任意一个现存超级管理员

        生产 bootstrap 场景只需要确认系统中是否已经存在超级管理员，
        不应重复创建默认管理员账号。
        """
        _, users = await self.get_list(
            db,
            limit=1,
            where_clauses_raw=[User.is_superuser == True],  # noqa: E712
            order_by_raw=[desc(User.id)],  # type: ignore[arg-type]
        )
        return users[0] if users else None

    async def ensure_role_link(self, db: AsyncSession, user_id: int, role_id: int) -> bool:
        """幂等补齐用户-角色关系，flush 但不提交。"""
        result = await db.execute(
            insert(user_role)
            .values(user_id=user_id, role_id=role_id)
            .on_conflict_do_nothing(index_elements=[user_role.c.user_id, user_role.c.role_id])
        )
        await db.flush()
        return result.rowcount == 1

    async def count_active(self, db: AsyncSession) -> int:
        """
        统计激活用户数量（未被软删除的用户）

        Args:
            db: 数据库会话

        Returns:
            激活用户数量
        """
        return await self.count(db, where_clauses=[User.is_deleted == False])  # noqa: E712


# 单例模式的仓库实例
user_repository = UserRepository()


__all__ = ["UserRepository", "user_repository"]
