"""
通用 Repository 基类

提供通用的 CRUD 操作，避免每个 Model 都要重复定义 Repository。

设计理念：
- 使用泛型支持任意 SQLModel
- 提供常用 CRUD 操作
- 支持自定义查询条件
- 保持扩展性，子类可添加特定方法
- Hook 系统支持扩展业务逻辑

使用示例：
    # 直接使用 BaseRepository
    user_repo = BaseRepository[User](User)

    # 或者创建特定的 Repository（推荐）
    class UserRepository(BaseRepository[User]):
        async def find_by_username(self, username: str):
            return await self.get_by_field("username", username)

    user_repo = UserRepository()
"""

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from inspect import iscoroutinefunction
from typing import Any, TypeVar

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logger import logger

# 泛型类型变量
T = TypeVar("T")


# ==================== Hook System ====================


class HookType(str, Enum):
    """Hook 类型枚举"""

    BEFORE_CREATE = "before_create"
    AFTER_CREATE = "after_create"
    BEFORE_UPDATE = "before_update"
    AFTER_UPDATE = "after_update"
    BEFORE_DELETE = "before_delete"
    AFTER_DELETE = "after_delete"


@dataclass
class HookContext:
    """Hook 执行上下文"""

    session: AsyncSession
    params: dict[str, Any]
    results: dict[str, Any]


HookFunc = Callable[[HookContext], Any]


@dataclass
class Hook:
    """Hook 配置"""

    func: HookFunc
    priority: int = 0
    condition: Callable[[HookContext], bool] | None = None
    error_handler: Callable[[Exception, HookContext], Any] | None = None


class HookManager:
    """Hook 管理器"""

    def __init__(self):
        self.hooks: dict[HookType, list[Hook]] = defaultdict(list)

    def add_hook(
        self,
        hook_type: HookType,
        func: HookFunc,
        priority: int = 0,
        condition: Callable[[HookContext], bool] | None = None,
        error_handler: Callable[[Exception, HookContext], Any] | None = None,
    ) -> None:
        """添加 hook"""
        hook = Hook(
            func=func,
            priority=priority,
            condition=condition,
            error_handler=error_handler,
        )
        self.hooks[hook_type].append(hook)
        self.hooks[hook_type].sort(key=lambda x: x.priority)

    async def execute_hooks(self, hook_type: HookType, context: HookContext) -> None:
        """执行指定类型的 hooks"""
        for hook in self.hooks[hook_type]:
            if hook.condition and not hook.condition(context):
                continue

            try:
                if iscoroutinefunction(hook.func):
                    await hook.func(context)
                else:
                    hook.func(context)
            except Exception as e:
                if hook.error_handler:
                    hook.error_handler(e, context)
                else:
                    raise


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
        self._pk_column = "id"
        self._pk_attr = getattr(model, self._pk_column)
        self.hook_manager = HookManager()

    async def _run_hooks(self, hook_type: HookType, **kwargs: Any) -> dict[str, Any]:
        """运行指定类型的 hooks"""
        context = HookContext(
            session=kwargs.get("session"),  # type: ignore[arg-type]
            params=kwargs,
            results={},
        )
        await self.hook_manager.execute_hooks(hook_type, context)
        return context.results

    def add_hook(
        self,
        hook_type: HookType,
        func: HookFunc,
        priority: int = 0,
        condition: Callable[[HookContext], bool] | None = None,
        error_handler: Callable[[Exception, HookContext], Any] | None = None,
    ) -> None:
        """添加 hook"""
        self.hook_manager.add_hook(hook_type, func, priority, condition, error_handler)

    async def _handle_relations(self, db: AsyncSession, instance: T, data: dict[str, Any]) -> None:
        """处理关联对象（简化版本，子类可重写）"""
        pass

    # ==================== 基础 CRUD 方法 ====================

    async def get_by_id(self, db: AsyncSession, id: int, schema: type | None = None, max_depth: int = 2) -> T | None:
        """
        根据 ID 获取单条记录

        Args:
            db: 数据库会话
            id: 主键 ID
            schema: 响应 Schema (用于自动加载关系)
            max_depth: 关系加载最大深度

        Returns:
            模型实例或 None
        """
        if schema:
            from src.core.schema_loader import get_with_schema

            return await get_with_schema(db, self.model, schema, self._pk_attr == id, max_depth=max_depth)

        result = await db.execute(select(self.model).where(self._pk_attr == id))
        return result.scalars().first()

    async def get_by_field(self, db: AsyncSession, field_name: str, value: Any) -> T | None:
        """
        根据字段获取单条记录

        Args:
            db: 数据库会话
            field_name: 字段名
            value: 字段值

        Returns:
            模型实例或 None
        """
        result = await db.execute(select(self.model).where(getattr(self.model, field_name) == value))
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

    async def get_list(
        self,
        db: AsyncSession,
        limit: int = 10,
        offset: int = 0,
        filters: "FilterGroup | None" = None,
        sort: "list[SortField] | None" = None,
        schema: type | None = None,
        max_depth: int = 1,
    ) -> tuple[int, list[T]]:
        """
        获取记录列表

        Args:
            db: 数据库会话
            limit: 限制数量
            offset: 偏移量
            filters: 过滤条件组
            sort: 排序字段列表
            schema: 响应 Schema (用于自动加载关系)
            max_depth: 关系加载最大深度

        Returns:
            (总数, 记录列表)
        """
        from src.core.query_builder import QueryBuilder

        builder = QueryBuilder(self.model)

        where_clauses = []
        if filters:
            filter_clause = builder.build_filters(filters)
            if filter_clause is not None:
                where_clauses.append(filter_clause)

        count_query = select(func.count(self._pk_attr))
        if where_clauses:
            count_query = count_query.where(*where_clauses)
        count_result = await db.execute(count_query)
        total = count_result.scalar() or 0

        order_by = builder.build_sort(sort) if sort else []

        if schema:
            from src.core.schema_loader import get_all_with_schema

            items = await get_all_with_schema(
                db, self.model, schema, *where_clauses, limit=limit, offset=offset, max_depth=max_depth
            )
        else:
            query = select(self.model)
            if where_clauses:
                query = query.where(*where_clauses)
            if order_by:
                query = query.order_by(*order_by)
            query = query.offset(offset).limit(limit)
            result = await db.execute(query)
            items = list(result.scalars().all())

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
            IntegrityError: 数据完整性约束冲突（由上层处理）
        """
        await self._run_hooks(HookType.BEFORE_CREATE, session=db, data=data)

        instance = self.model(**data)
        db.add(instance)
        await db.flush()
        await db.refresh(instance)

        pk_value = getattr(instance, self._pk_column)
        logger.info(f"创建 {self._model_name} 成功: {self._pk_column}={pk_value}")

        await self._run_hooks(HookType.AFTER_CREATE, session=db, instance=instance)

        return instance

    async def update(self, db: AsyncSession, id: int, data: dict[str, Any]) -> T:
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
            IntegrityError: 数据完整性约束冲突（由上层处理）
        """
        instance = await self.get_by_id(db, id)
        if not instance:
            raise ValueError(f"{self._model_name} 不存在")

        await self._run_hooks(HookType.BEFORE_UPDATE, session=db, instance=instance, data=data)

        for field, value in data.items():
            if hasattr(instance, field):
                setattr(instance, field, value)

        await db.flush()
        await db.refresh(instance)

        logger.info(f"更新 {self._model_name} 成功: id={id}")

        await self._run_hooks(HookType.AFTER_UPDATE, session=db, instance=instance)

        return instance

    async def delete(self, db: AsyncSession, id: int) -> bool:
        """
        删除记录

        Args:
            db: 数据库会话
            id: 主键 ID

        Returns:
            是否删除成功

        Raises:
            IntegrityError: 数据完整性约束冲突（由上层处理）
        """
        instance = await self.get_by_id(db, id)
        if not instance:
            return False

        await self._run_hooks(HookType.BEFORE_DELETE, session=db, instance=instance)

        await db.delete(instance)
        await db.flush()

        logger.info(f"删除 {self._model_name} 成功: id={id}")

        await self._run_hooks(HookType.AFTER_DELETE, session=db, instance=instance)

        return True

    async def exists(self, db: AsyncSession, **kwargs: Any) -> bool:
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

    async def count(self, db: AsyncSession, where_clauses: list[Any] | None = None) -> int:
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
        query = select(func.count(self._pk_attr))

        if where_clauses:
            query = query.where(*where_clauses)

        result = await db.execute(query)
        return result.scalar() or 0

    async def bulk_create(self, db: AsyncSession, items: list[dict[str, Any]]) -> list[T]:
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
        await db.flush()

        for instance in instances:
            await db.refresh(instance)

        logger.info(f"批量创建 {self._model_name} 成功: 数量={len(instances)}")
        return instances


__all__ = ["BaseRepository", "HookContext", "HookManager", "HookType"]
