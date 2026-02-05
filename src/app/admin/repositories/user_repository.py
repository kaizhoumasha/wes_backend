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

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.admin.models import User
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
