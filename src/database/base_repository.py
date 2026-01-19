"""
通用 Repository 基类

提供通用的 CRUD 操作，避免每个 Model 都要重复定义 Repository。

设计理念：
- 使用泛型支持任意 SQLModel
- 提供常用 CRUD 操作
- 支持自定义查询条件
- 保持扩展性，子类可添加特定方法

使用示例：
    # 直接使用 BaseRepository
    user_repo = BaseRepository[User](User)

    # 或者创建特定的 Repository（推荐）
    class UserRepository(BaseRepository[User]):
        async def find_by_username(self, username: str):
            return await self.get_by_field("username", username)

    user_repo = UserRepository()
"""

from typing import Any, TypeVar

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logger import logger

# 泛型类型变量
T = TypeVar("T")


class BaseRepository[T]:
    """
    通用 Repository 基类

    提供标准的 CRUD 操作，支持任意 SQLModel。

    类型参数:
        T: SQLModel 类型（如 User、Product 等）

    使用示例:
        # 方式1：直接实例化
        user_repo = BaseRepository[User](User)
        user = await user_repo.get_by_id(db, 1)

        # 方式2：继承扩展（推荐）
        class UserRepository(BaseRepository[User]):
            async def find_active_users(self, db):
                return await self.get_all(db, where_clauses=[User.is_active == True])

        user_repo = UserRepository()
    """

    def __init__(self, model: type[T]):
        """
        初始化 Repository

        Args:
            model: SQLModel 类（如 User）
        """
        self.model = model
        self._model_name = model.__name__
        # 假设主键名为 'id'（这是最常见的约定）
        # 如果需要支持其他主键名，可以在子类中覆盖
        self._pk_column = "id"

    # ==================== 基础 CRUD 方法 ====================

    async def get_by_id(self, db: AsyncSession, id: int) -> T | None:
        """
        根据 ID 获取单条记录

        Args:
            db: 数据库会话
            id: 主键 ID

        Returns:
            模型实例或 None
        """
        pk_column = getattr(self.model, self._pk_column)
        result = await db.execute(select(self.model).where(pk_column == id))
        return result.scalars().first()

    async def get_by_field(
        self, db: AsyncSession, field_name: str, value: Any
    ) -> T | None:
        """
        根据字段获取单条记录

        Args:
            db: 数据库会话
            field_name: 字段名
            value: 字段值

        Returns:
            模型实例或 None
        """
        result = await db.execute(
            select(self.model).where(getattr(self.model, field_name) == value)
        )
        return result.scalars().first()

    async def get_all(
        self,
        db: AsyncSession,
        *,
        where_clauses: list[Any] | None = None,
        limit: int | None = None,
        offset: int | None = None,
        order_by: Any | None = None,
    ) -> list[T]:
        """
        获取所有记录（支持过滤和分页）

        Args:
            db: 数据库会话
            where_clauses: WHERE 条件列表
            limit: 限制数量
            offset: 偏移量
            order_by: 排序字段

        Returns:
            模型实例列表

        Example:
            # 获取所有
            users = await repo.get_all(db)

            # 带条件
            active_users = await repo.get_all(
                db,
                where_clauses=[User.is_active == True]
            )

            # 分页
            users = await repo.get_all(
                db,
                where_clauses=[User.is_active == True],
                limit=10,
                offset=20,
                order_by=User.created_at.desc()
            )
        """
        query = select(self.model)

        if where_clauses:
            query = query.where(*where_clauses)

        if order_by is not None:
            query = query.order_by(order_by)

        if offset is not None:
            query = query.offset(offset)

        if limit is not None:
            query = query.limit(limit)

        result = await db.execute(query)
        return list(result.scalars().all())

    async def get_paginated(
        self, db: AsyncSession, page: int = 1, page_size: int = 10
    ) -> tuple[int, list[T]]:
        """
        分页获取记录

        Args:
            db: 数据库会话
            page: 页码（从 1 开始）
            page_size: 每页数量

        Returns:
            (总数, 记录列表)
        """
        # 获取总数
        pk_column = getattr(self.model, self._pk_column)
        count_result = await db.execute(select(func.count(pk_column)))
        total = count_result.scalar() or 0

        # 获取分页数据
        offset = (page - 1) * page_size
        items = await self.get_all(db, offset=offset, limit=page_size)

        return total, items

    async def create(self, db: AsyncSession, data: dict[str, Any]) -> T:
        """
        创建新记录

        Args:
            db: 数据库会话
            data: 数据字典

        Returns:
            创建的模型实例

        Raises:
            IntegrityError: 数据完整性约束冲突
        """
        instance = self.model(**data)
        db.add(instance)
        await db.commit()
        await db.refresh(instance)

        pk_value = getattr(instance, self._pk_column)
        logger.info(f"创建 {self._model_name} 成功: {self._pk_column}={pk_value}")
        return instance

    async def update(
        self, db: AsyncSession, id: int, data: dict[str, Any]
    ) -> T:
        """
        更新记录

        Args:
            db: 数据库会话
            id: 主键 ID
            data: 更新数据字典

        Returns:
            更新后的模型实例

        Raises:
            ValueError: 记录不存在
        """
        instance = await self.get_by_id(db, id)
        if not instance:
            raise ValueError(f"{self._model_name} 不存在")

        for field, value in data.items():
            if hasattr(instance, field):
                setattr(instance, field, value)

        await db.commit()
        await db.refresh(instance)

        logger.info(f"更新 {self._model_name} 成功: id={id}")
        return instance

    async def delete(self, db: AsyncSession, id: int) -> bool:
        """
        删除记录

        Args:
            db: 数据库会话
            id: 主键 ID

        Returns:
            是否删除成功
        """
        instance = await self.get_by_id(db, id)
        if not instance:
            return False

        await db.delete(instance)
        await db.commit()

        logger.info(f"删除 {self._model_name} 成功: id={id}")
        return True

    async def exists(
        self, db: AsyncSession, **kwargs: Any
    ) -> bool:
        """
        检查记录是否存在

        Args:
            db: 数据库会话
            **kwargs: 字段名和值的键值对

        Returns:
            是否存在

        Example:
            exists = await repo.exists(db, username="test")
            exists = await repo.exists(db, email="test@example.com", is_active=True)
        """
        if not kwargs:
            return False

        conditions = [getattr(self.model, k) == v for k, v in kwargs.items()]
        result = await db.execute(select(self.model).where(*conditions).limit(1))
        return result.scalars().first() is not None

    async def count(
        self, db: AsyncSession, where_clauses: list[Any] | None = None
    ) -> int:
        """
        统计记录数量

        Args:
            db: 数据库会话
            where_clauses: WHERE 条件列表

        Returns:
            记录数量

        Example:
            total = await repo.count(db)
            active_count = await repo.count(db, where_clauses=[User.is_active == True])
        """
        query = select(func.count(getattr(self.model, self._pk_column)))

        if where_clauses:
            query = query.where(*where_clauses)

        result = await db.execute(query)
        return result.scalar() or 0

    async def bulk_create(
        self, db: AsyncSession, items: list[dict[str, Any]]
    ) -> list[T]:
        """
        批量创建记录

        Args:
            db: 数据库会话
            items: 数据字典列表

        Returns:
            创建的模型实例列表
        """
        instances = [self.model(**item) for item in items]
        db.add_all(instances)
        await db.commit()

        for instance in instances:
            await db.refresh(instance)

        logger.info(f"批量创建 {self._model_name} 成功: 数量={len(instances)}")
        return instances


__all__ = ["BaseRepository"]
