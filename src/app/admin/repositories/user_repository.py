"""
用户仓库（User Repository）

负责用户数据访问层（CRUD 操作），遵循仓库模式。

职责：
1. 数据库查询和操作
2. 数据持久化
3. 事务管理
4. 数据完整性验证

分离原因：
- 单一职责原则：只负责数据访问
- 便于测试：可以轻松 mock 数据库
- 依赖倒置：依赖协议接口而非具体实现
"""

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.admin.models import User
from src.core.logger import logger


class UserRepository:
    """用户仓库类"""

    async def get_by_id(self, db: AsyncSession, user_id: int) -> User | None:
        """
        根据 ID 获取用户

        Args:
            db: 数据库会话
            user_id: 用户 ID

        Returns:
            用户对象或 None
        """
        result = await db.execute(select(User).where(User.id == user_id))
        return result.scalars().first()

    async def get_by_username(self, db: AsyncSession, username: str) -> User | None:
        """
        根据用户名获取用户

        Args:
            db: 数据库会话
            username: 用户名

        Returns:
            用户对象或 None
        """
        result = await db.execute(select(User).where(User.username == username))
        return result.scalars().first()

    async def get_by_email(self, db: AsyncSession, email: str) -> User | None:
        """
        根据邮箱获取用户

        Args:
            db: 数据库会话
            email: 邮箱

        Returns:
            用户对象或 None
        """
        result = await db.execute(select(User).where(User.email == email))
        return result.scalars().first()

    async def get_by_field(self, db: AsyncSession, field_name: str, value: str | int) -> User | None:
        """
        根据字段查询用户（通用方法）

        Args:
            db: 数据库会话
            field_name: 字段名
            value: 字段值

        Returns:
            用户对象或 None
        """
        result = await db.execute(select(User).where(getattr(User, field_name) == value))
        return result.scalars().first()

    async def get_paginated(self, db: AsyncSession, page: int = 1, page_size: int = 10) -> tuple[int, list[User]]:
        """
        分页获取用户列表

        Args:
            db: 数据库会话
            page: 页码（从 1 开始）
            page_size: 每页数量

        Returns:
            (总数, 用户列表)
        """
        count_result = await db.execute(select(func.count(User.id)))
        total = count_result.scalar()

        offset = (page - 1) * page_size
        result = await db.execute(select(User).offset(offset).limit(page_size))
        users = result.scalars().all()

        return total, users

    async def create(self, db: AsyncSession, user_data: dict[str, Any]) -> User:
        """
        创建用户

        Args:
            db: 数据库会话
            user_data: 用户数据字典

        Returns:
            创建的用户对象

        Raises:
            IntegrityError: 数据完整性约束冲突
        """
        user = User(**user_data)
        db.add(user)
        await db.commit()
        await db.refresh(user)

        logger.info(f"创建用户成功: {user.username}")
        return user

    async def update(self, db: AsyncSession, user_id: int, user_data: dict[str, Any]) -> User:
        """
        更新用户

        Args:
            db: 数据库会话
            user_id: 用户 ID
            user_data: 更新数据字典

        Returns:
            更新后的用户对象

        Raises:
            ValueError: 用户不存在
        """
        user = await self.get_by_id(db, user_id)
        if not user:
            raise ValueError("用户不存在")

        for field, value in user_data.items():
            if hasattr(user, field):
                setattr(user, field, value)

        await db.commit()
        await db.refresh(user)

        logger.info(f"更新用户成功: {user.username}")
        return user

    async def delete(self, db: AsyncSession, user_id: int) -> bool:
        """
        删除用户

        Args:
            db: 数据库会话
            user_id: 用户 ID

        Returns:
            是否删除成功
        """
        user = await self.get_by_id(db, user_id)
        if not user:
            return False

        username = user.username
        await db.delete(user)
        await db.commit()

        logger.info(f"删除用户成功: {username}")
        return True

    async def exists(self, db: AsyncSession, username: str | None = None, email: str | None = None) -> str | None:
        """
        检查用户是否存在

        Args:
            db: 数据库会话
            username: 用户名
            email: 邮箱

        Returns:
            存在的字段名（"username" 或 "email"），或 None
        """
        conditions = []
        if username:
            conditions.append(User.username == username)
        if email:
            conditions.append(User.email == email)

        if not conditions:
            return None

        query = select(User).where(*conditions)
        result = await db.execute(query)
        user = result.scalars().first()

        if user:
            if username and user.username == username:
                return "username"
            if email and user.email == email:
                return "email"
        return None


# 单例模式的仓库实例
user_repository = UserRepository()


__all__ = ["UserRepository", "user_repository"]
