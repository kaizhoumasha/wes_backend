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

import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from inspect import iscoroutinefunction
from typing import TYPE_CHECKING, Any, NoReturn, TypeVar

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logger import logger
from src.core.query_models import FilterGroup, SortField

if TYPE_CHECKING:
    from src.database.relation_metadata import RelationInfo
else:
    # 运行时使用字符串类型注解
    RelationInfo = "RelationInfo"

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

        自动功能：
        - 如果模型混入了状态验证 Mixin，自动注册状态验证 Hook
        - 如果模型混入了 AuditMixin，自动注册审计字段填充 Hook
        - 如果模型混入了 AuditModelMixin，自动注册审计日志记录 Hook
        """
        self.model = model
        self._model_name = model.__name__
        self._pk_column = "id"
        self._pk_attr = getattr(model, self._pk_column)
        self.hook_manager = HookManager()

        # 自动注册状态验证 Hook
        self._register_status_validation_hooks()

        # 自动注册审计字段填充 Hook
        self._register_audit_hooks()

        # 自动检测并注册审计日志 Hook
        if self._has_audit_model_mixin():
            self._register_audit_log_hooks()

    def _has_audit_model_mixin(self) -> bool:
        """
        检测模型是否混入了 AuditModelMixin

        通过检查模型的 MRO (Method Resolution Order) 来判断是否继承自 AuditModelMixin。

        Returns:
            如果模型混入了 AuditModelMixin 则返回 True，否则返回 False
        """
        # 检查模型的所有基类
        return any(base.__name__ in ("AuditModelMixin", "AuditMixin") for base in self.model.__mro__)

    def _register_status_validation_hooks(self) -> None:
        """
        自动注册状态验证 Hook

        检测模型是否混入了状态验证 Mixin（如 DocumentStatusMixin、ShelfStatusMixin 等），
        如果有，则自动注册相应的验证 Hook。

        支持的验证方法命名约定：
        - validate_document_status(operation: str)
        - validate_shelf_status(operation: str)
        - validate_container_status(operation: str)
        - validate_material_status(operation: str)
        - ... 其他 validate_xxx_status 方法

        这样设计的好处：
        1. 零性能开销：没有 Mixin 的模型不会执行任何检查
        2. 自动化：无需手动注册 Hook
        3. 可扩展：添加新的状态类型只需创建新的 Mixin
        4. 类型安全：Mixin 定义了必需的属性
        """
        # 查找所有 validate_xxx_status 方法
        status_validators = [
            attr
            for attr in dir(self.model)
            if attr.startswith("validate_") and attr.endswith("_status") and callable(getattr(self.model, attr))
        ]

        for validator_name in status_validators:
            # 为每个验证方法注册 BEFORE_UPDATE 和 BEFORE_DELETE Hook
            self.add_hook(
                HookType.BEFORE_UPDATE,
                self._create_status_validation_hook(validator_name, "edit"),
                priority=0,  # 最高优先级，确保在其他 Hook 之前执行
            )

            self.add_hook(
                HookType.BEFORE_DELETE,
                self._create_status_validation_hook(validator_name, "delete"),
                priority=0,
            )

    def _create_status_validation_hook(self, validator_name: str, operation: str) -> HookFunc:
        """
        创建状态验证 Hook 函数

        Args:
            validator_name: 验证方法名称（如 "validate_document_status"）
            operation: 操作类型（"edit" 或 "delete"）

        Returns:
            Hook 函数
        """

        async def status_validation_hook(ctx: HookContext) -> None:
            instance = ctx.params.get("instance")
            if instance and hasattr(instance, validator_name):
                validator = getattr(instance, validator_name)
                validator(operation)

        return status_validation_hook

    def _register_audit_hooks(self) -> None:
        """
        自动注册审计字段填充 Hook

        检测模型是否混入了 AuditMixin，如果有，则自动注册
        created_by 和 updated_by 的填充 Hook。
        """
        # 检查模型是否有 created_by 和 updated_by 字段
        has_created_by = hasattr(self.model, "created_by")
        has_updated_by = hasattr(self.model, "updated_by")

        if has_created_by:
            # 注册 BEFORE_CREATE Hook 填充 created_by
            self.add_hook(
                HookType.BEFORE_CREATE,
                self._create_audit_fill_hook("created_by"),
                priority=-1,  # 较高优先级，在其他 Hook 之前执行
            )

        if has_updated_by:
            # 注册 BEFORE_UPDATE Hook 填充 updated_by
            self.add_hook(
                HookType.BEFORE_UPDATE,
                self._create_audit_fill_hook("updated_by"),
                priority=-1,
            )

    def _create_audit_fill_hook(self, field_name: str) -> HookFunc:
        """
        创建审计字段填充 Hook 函数

        Args:
            field_name: 字段名称（created_by 或 updated_by）

        Returns:
            Hook 函数
        """

        async def audit_fill_hook(ctx: HookContext) -> None:
            from src.utils.audit import get_current_user_id

            user_id = get_current_user_id()
            if user_id is None:
                return

            # 对于 create 操作，从 data 中设置
            if field_name == "created_by" and "data" in ctx.params:
                data = ctx.params["data"]
                if isinstance(data, dict) and field_name not in data:
                    data[field_name] = user_id

            # 对于 update 操作，从 instance 或 data 中设置
            if field_name == "updated_by":
                if "instance" in ctx.params:
                    instance = ctx.params["instance"]
                    if hasattr(instance, field_name):
                        setattr(instance, field_name, user_id)
                if "data" in ctx.params:
                    data = ctx.params["data"]
                    if isinstance(data, dict):
                        data[field_name] = user_id

        return audit_fill_hook

    def _register_audit_log_hooks(self) -> None:
        """
        注册审计日志 Hook

        在 AFTER_CREATE、AFTER_UPDATE、AFTER_DELETE 时记录审计日志
        """
        # 注册 AFTER_CREATE Hook
        self.add_hook(
            HookType.AFTER_CREATE,
            self._create_audit_log_hook("create"),
            priority=100,  # 最低优先级，在所有其他 Hook 之后执行
        )

        # 注册 AFTER_UPDATE Hook
        self.add_hook(
            HookType.AFTER_UPDATE,
            self._create_audit_log_hook("update"),
            priority=100,
        )

        # 注册 AFTER_DELETE Hook
        self.add_hook(
            HookType.AFTER_DELETE,
            self._create_audit_log_hook("delete"),
            priority=100,
        )

    def _create_audit_log_hook(self, operation: str) -> HookFunc:
        """
        创建审计日志 Hook 函数

        Args:
            operation: 操作类型（create/update/delete）

        Returns:
            Hook 函数
        """

        async def audit_log_hook(ctx: HookContext) -> None:
            try:
                from src.app.sys.services.audit_service import audit_log_service

                instance = ctx.params.get("instance")
                data = ctx.params.get("data")
                record_id = getattr(instance, self._pk_column, None) if instance else None

                await audit_log_service.create_operation_log(
                    ctx.session,
                    operation=operation,
                    model_name=self._model_name,
                    record_id=record_id,
                    data=data,
                    success=True,
                )
            except Exception as e:
                # 审计日志失败不应该影响主业务
                from src.core.logger import logger

                logger.error(f"记录审计日志失败: {e}")

        return audit_log_hook

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

    def _handle_integrity_error(self, e: IntegrityError) -> NoReturn:
        """
        统一处理数据库完整性约束错误

        将数据库错误转换为用户友好的中文提示。

        Args:
            e: IntegrityError 异常

        Raises:
            ValueError: 转换后的友好错误提示
        """
        error_msg = str(e.orig) if hasattr(e, "orig") else str(e)

        # 尝试匹配各种约束错误
        self._check_foreign_key_constraint(error_msg)
        self._check_duplicate_key_constraint(error_msg)
        self._check_not_null_constraint(error_msg)

        # 如果都未命中，抛出原始异常
        raise ValueError(f"数据库操作失败: {error_msg}")

    def _check_foreign_key_constraint(self, error_msg: str) -> None:
        """
        检查外键约束错误

        Args:
            error_msg: 错误消息

        Raises:
            ValueError: 友好的外键约束错误提示
        """
        # PostgreSQL 外键约束错误模式
        # 示例: update or delete on table "xxx" violates foreign key constraint "yyy" on table "zzz"
        pattern = r'violates foreign key constraint.*on table "([^"]+)"'
        match = re.search(pattern, error_msg, re.IGNORECASE)

        if match:
            table_name = match.group(1)
            table_cn_name = self._get_table_cn_name(table_name)
            raise ValueError(f"当前删除的数据与[{table_cn_name}]中的数据有关联，请先删除[{table_cn_name}]关联的数据")

        # 另一种模式: still referenced from table "xxx"
        pattern2 = r'still referenced from table "([^"]+)"'
        match2 = re.search(pattern2, error_msg, re.IGNORECASE)

        if match2:
            table_name = match2.group(1)
            table_cn_name = self._get_table_cn_name(table_name)
            raise ValueError(f"当前删除的数据与[{table_cn_name}]中的数据有关联，请先删除[{table_cn_name}]关联的数据")

    def _check_duplicate_key_constraint(self, error_msg: str) -> None:
        """
        检查唯一约束错误

        Args:
            error_msg: 错误消息

        Raises:
            ValueError: 友好的唯一约束错误提示
        """
        # PostgreSQL 唯一约束错误模式
        # 示例: duplicate key value violates unique constraint "uq_xxx"
        # DETAIL:  Key (column1, column2)=(value1, value2) already exists.
        if "duplicate key value violates unique constraint" not in error_msg.lower():
            return

        # 提取字段名
        pattern = r"Key \(([^)]+)\)="
        match = re.search(pattern, error_msg)

        if match:
            columns = match.group(1)
            # 分割多个字段
            column_list = [col.strip() for col in columns.split(",")]
            # 转换为中文名称
            column_cn_names = [self._get_column_cn_name(col) for col in column_list]

            raise ValueError(f"[{', '.join(column_cn_names)}]已存在，不能重复添加")

        # 如果无法提取字段名，使用通用提示
        raise ValueError("数据已存在，不能重复添加")

    def _check_not_null_constraint(self, error_msg: str) -> None:
        """
        检查非空约束错误

        Args:
            error_msg: 错误消息

        Raises:
            ValueError: 友好的非空约束错误提示
        """
        # PostgreSQL 非空约束错误模式
        # 示例: null value in column "xxx" violates not-null constraint
        pattern = r'null value in column "([^"]+)" violates not-null constraint'
        match = re.search(pattern, error_msg, re.IGNORECASE)

        if match:
            column_name = match.group(1)
            column_cn_name = self._get_column_cn_name(column_name)
            raise ValueError(f"[{column_cn_name}]不能为空")

    def _get_table_cn_name(self, table_en_name: str) -> str:
        """
        通过英文表名找到中文表名

        Args:
            table_en_name: 英文表名

        Returns:
            中文表名（如果找不到则返回英文表名）
        """
        # 尝试从模型的 metadata 中查找表的 comment
        if hasattr(self.model, "__table__"):
            table = self.model.__table__  # type: ignore[attr-defined]
            if table.name == table_en_name and table.comment:
                return table.comment

        # 如果找不到，返回英文表名
        return table_en_name

    def _get_column_cn_name(self, column_en_name: str) -> str:
        """
        通过英文字段名找到中文字段名

        Args:
            column_en_name: 英文字段名

        Returns:
            中文字段名（如果找不到则返回英文字段名）
        """
        # 尝试从模型的字段定义中获取 description 或 comment
        model_fields = getattr(self.model, "model_fields", None)
        if model_fields:
            field = model_fields.get(column_en_name)
            if field and hasattr(field, "description") and field.description:
                return field.description

        # 尝试从 SQLAlchemy 的 column comment 中获取
        if hasattr(self.model, "__table__"):
            table = self.model.__table__  # type: ignore[attr-defined]
            if column_en_name in table.columns:
                column = table.columns[column_en_name]
                if column.comment:
                    return column.comment

        # 如果找不到，返回英文字段名
        return column_en_name

    def _add_relation_load(self, statement: Any, relation_name: str, relation_info: RelationInfo) -> Any:
        """
        根据关系类型选择最优加载策略

        Args:
            statement: SQLAlchemy 查询语句
            relation_name: 关联属性名
            relation_info: 关联关系信息

        Returns:
            添加了加载选项的查询语句
        """
        from sqlalchemy.orm import joinedload, selectinload

        from src.database.relation_metadata import RelationType

        relation_attr = getattr(self.model, relation_name, None)
        if relation_attr is None:
            return statement

        relation_type = relation_info.get("relation_type", "ONETOMANY")

        if relation_type == RelationType.ONETOONE:
            # 一对一：使用 joinedload（单次 JOIN 查询）
            statement = statement.options(joinedload(relation_attr))
        else:
            # 一对多/多对多：使用 selectinload（避免笛卡尔积）
            statement = statement.options(selectinload(relation_attr))

        return statement

    def _add_all_relation_loads(self, statement: Any) -> Any:
        """
        加载所有关联对象

        Args:
            statement: SQLAlchemy 查询语句

        Returns:
            添加了所有关联对象加载选项的查询语句
        """
        from src.database.relation_metadata import RelationMetadata

        if not RelationMetadata.has_relations(self.model):
            return statement

        relation_info = RelationMetadata.get_relation_info(self.model)

        for relation_name, info in relation_info.items():
            statement = self._add_relation_load(statement, relation_name, info)

        return statement

    def _add_specific_relation_loads(self, statement: Any, relation_names: list[str]) -> Any:
        """
        只加载指定的关联对象

        Args:
            statement: SQLAlchemy 查询语句
            relation_names: 需要加载的关联对象名称列表

        Returns:
            添加了指定关联对象加载选项的查询语句
        """
        from src.database.relation_metadata import RelationMetadata

        relation_info = RelationMetadata.get_relation_info(self.model)

        for relation_name in relation_names:
            if relation_name in relation_info:
                info = relation_info[relation_name]
                statement = self._add_relation_load(statement, relation_name, info)

        return statement

    async def _handle_relations(self, db: AsyncSession, instance: T, data: dict[str, Any]) -> None:
        """
        处理关联对象（自动处理主从表关系）

        根据模型的 __relation_info__ 元数据自动处理关联对象的创建和更新。
        支持一对多、一对一、多对多关系。

        Args:
            db: 数据库会话
            instance: 主表实例
            data: 包含关联对象数据的字典
        """
        from src.database.relation_metadata import RelationMetadata, RelationType

        if not RelationMetadata.has_relations(self.model):
            return

        relation_info = RelationMetadata.get_relation_info(self.model)

        for relation_name, info in relation_info.items():
            # 检查数据中是否包含此关联对象
            if relation_name not in data:
                continue

            relation_data = data[relation_name]
            if relation_data is None:
                continue

            relation_type = info.get("relation_type", "ONETOMANY")

            # 目前只处理一对多关系
            if relation_type == RelationType.ONETOMANY:
                await self._handle_one_to_many_relation(db, instance, relation_name, relation_data, info)

    async def _handle_one_to_many_relation(
        self,
        db: AsyncSession,
        instance: T,
        relation_name: str,
        relation_data: list[dict[str, Any]],
        relation_info: RelationInfo,
    ) -> None:
        """
        处理一对多关系的创建

        Args:
            db: 数据库会话
            instance: 主表实例
            relation_name: 关联属性名
            relation_data: 关联对象数据列表
            relation_info: 关联关系信息
        """
        relation_attr = getattr(self.model, relation_name, None)
        if relation_attr is None:
            return

        await self._create_relation_objects(db, relation_attr, instance, relation_data, relation_info)

    # ==================== 基础 CRUD 方法 ====================

    async def get_by_id(
        self,
        db: AsyncSession,
        id: int,
        schema: type | None = None,
        max_depth: int = 2,
        include_relations: bool = False,
        relation_names: list[str] | None = None,
    ) -> T | None:
        """
        根据 ID 获取单条记录

        Args:
            db: 数据库会话
            id: 主键 ID
            schema: 响应 Schema (用于自动加载关系)
            max_depth: 关系加载最大深度
            include_relations: 是否包含关联对象
            relation_names: 需要包含的关联对象名称列表，None 表示全部

        Returns:
            模型实例或 None

        Example:
            # 只加载明细，不加载附件和审批记录
            inbound = await repo.get_by_id(
                db,
                id=1,
                include_relations=True,
                relation_names=["items"]  # 只加载 items
            )

            # 加载所有关联对象
            inbound = await repo.get_by_id(
                db,
                id=1,
                include_relations=True,
                relation_names=None  # 加载全部
            )
        """
        if schema:
            from src.core.schema_loader import get_with_schema

            return await get_with_schema(db, self.model, schema, self._pk_attr == id, max_depth=max_depth)

        statement = select(self.model).where(self._pk_attr == id)

        # 添加关联对象加载
        if include_relations:
            if relation_names is None:
                # 加载所有关联对象
                statement = self._add_all_relation_loads(statement)
            else:
                # 只加载指定的关联对象
                statement = self._add_specific_relation_loads(statement, relation_names)

        result = await db.execute(statement)
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
        filters: FilterGroup | None = None,
        sort: list[SortField] | None = None,
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
                db,
                self.model,
                schema,
                *where_clauses,
                limit=limit,
                offset=offset,
                max_depth=max_depth,
                order_by=order_by,
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
            ValueError: 数据完整性约束冲突（友好提示）
        """
        await self._run_hooks(HookType.BEFORE_CREATE, session=db, data=data)

        try:
            instance = self.model(**data)
            db.add(instance)
            await db.flush()
            await db.refresh(instance)

            pk_value = getattr(instance, self._pk_column)
            logger.info(f"创建 {self._model_name} 成功: {self._pk_column}={pk_value}")

            await self._run_hooks(HookType.AFTER_CREATE, session=db, instance=instance)

            return instance
        except IntegrityError as e:
            await db.rollback()
            self._handle_integrity_error(e)

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
            ValueError: 记录不存在、状态不允许修改或数据完整性约束冲突（友好提示）
        """
        instance = await self.get_by_id(db, id)
        if not instance:
            raise ValueError(f"{self._model_name} 不存在")

        # 状态验证通过 Hook 系统自动执行（如果模型混入了状态验证 Mixin）
        await self._run_hooks(HookType.BEFORE_UPDATE, session=db, instance=instance, data=data)

        try:
            for field, value in data.items():
                if hasattr(instance, field):
                    setattr(instance, field, value)

            await db.flush()
            await db.refresh(instance)

            logger.info(f"更新 {self._model_name} 成功: id={id}")

            await self._run_hooks(HookType.AFTER_UPDATE, session=db, instance=instance)

            return instance
        except IntegrityError as e:
            await db.rollback()
            self._handle_integrity_error(e)

    async def delete(self, db: AsyncSession, id: int) -> bool:
        """
        删除记录

        Args:
            db: 数据库会话
            id: 主键 ID

        Returns:
            是否删除成功

        Raises:
            ValueError: 状态不允许删除或数据完整性约束冲突（友好提示）
        """
        instance = await self.get_by_id(db, id)
        if not instance:
            return False

        # 状态验证通过 Hook 系统自动执行（如果模型混入了状态验证 Mixin）
        await self._run_hooks(HookType.BEFORE_DELETE, session=db, instance=instance)

        try:
            await db.delete(instance)
            await db.flush()

            logger.info(f"删除 {self._model_name} 成功: id={id}")

            await self._run_hooks(HookType.AFTER_DELETE, session=db, instance=instance)

            return True
        except IntegrityError as e:
            await db.rollback()
            self._handle_integrity_error(e)

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

    # ==================== 主从表关系处理方法 ====================

    async def update_with_relations(
        self,
        db: AsyncSession,
        id: int,
        data: dict[str, Any],
    ) -> T:
        """
        更新记录及其关联对象（主从表 Diff 更新）

        自动处理主从表的增删改操作：
        - 有 ID 的项：更新现有记录
        - 无 ID 的项：创建新记录
        - 数据库中有但输入中没有的项：删除记录

        Args:
            db: 数据库会话
            id: 主键 ID
            data: 更新数据字典（包含关联对象）

        Returns:
            更新后的模型实例

        Example:
            # 更新入库单及其明细
            data = {
                "type": "purchase",
                "items": [
                    {"id": 1, "sku_code": "SKU001", "qty": 10},  # 更新
                    {"sku_code": "SKU002", "qty": 20},  # 新增
                    # ID=2 的项不在列表中，将被删除
                ]
            }
            inbound = await repo.update_with_relations(db, 1, data)
        """
        from src.database.relation_metadata import RelationMetadata

        # 获取主表实例
        instance = await self.get_by_id(db, id)
        if not instance:
            raise ValueError(f"{self._model_name} 不存在")

        await self._run_hooks(HookType.BEFORE_UPDATE, session=db, instance=instance, data=data)

        # 更新主表字段
        relation_info = RelationMetadata.get_relation_info(self.model)
        for field, value in data.items():
            # 跳过关联对象字段
            if field in relation_info:
                continue
            if hasattr(instance, field):
                setattr(instance, field, value)

        # 处理关联对象
        await self._update_relations(db, instance, data)

        await db.flush()
        await db.refresh(instance)

        logger.info(f"更新 {self._model_name}（含关联对象）成功: id={id}")

        await self._run_hooks(HookType.AFTER_UPDATE, session=db, instance=instance)

        return instance

    async def _update_relations(
        self,
        db: AsyncSession,
        instance: T,
        data: dict[str, Any],
    ) -> None:
        """
        更新关联对象（Diff 算法）

        Args:
            db: 数据库会话
            instance: 主表实例
            data: 包含关联对象数据的字典
        """
        from src.database.relation_metadata import RelationMetadata, RelationType

        if not RelationMetadata.has_relations(self.model):
            return

        relation_info = RelationMetadata.get_relation_info(self.model)

        for relation_name, info in relation_info.items():
            # 检查数据中是否包含此关联对象
            if relation_name not in data:
                continue

            new_relation_data = data[relation_name]
            if new_relation_data is None:
                continue

            relation_type = info.get("relation_type", "ONETOMANY")

            # 目前只处理一对多关系
            if relation_type != RelationType.ONETOMANY:
                continue

            # 获取关联模型类（从 Relationship 属性中获取）
            relation_attr = getattr(self.model, relation_name, None)
            if relation_attr is None:
                continue

            # 获取当前数据库中的关联对象
            current_relations = getattr(instance, relation_name, [])
            current_ids = {rel.id for rel in current_relations if hasattr(rel, "id") and rel.id is not None}

            # 分析操作类型
            new_ids = set()
            to_create = []
            to_update = []

            for item_data in new_relation_data:
                item_id = item_data.get("id") if isinstance(item_data, dict) else getattr(item_data, "id", None)

                if item_id is None:
                    to_create.append(item_data)  # 新增
                else:
                    new_ids.add(item_id)
                    to_update.append(item_data)  # 更新

            # 找出需要删除的 ID
            to_delete_ids = current_ids - new_ids

            # 执行操作
            if to_delete_ids:
                await self._delete_relation_objects(db, relation_attr, to_delete_ids)

            if to_update:
                await self._update_relation_objects(db, relation_attr, to_update)

            if to_create:
                await self._create_relation_objects(db, relation_attr, instance, to_create, info)

    async def _delete_relation_objects(
        self,
        db: AsyncSession,
        relation_attr: Any,
        ids_to_delete: set[int],
    ) -> None:
        """
        删除关联对象（使用 DELETE 语句，无需先查询）

        Args:
            db: 数据库会话
            relation_attr: 关联属性
            ids_to_delete: 要删除的 ID 集合
        """
        if not ids_to_delete:
            return

        from sqlalchemy import delete

        # 获取关联模型类
        relation_model = relation_attr.property.mapper.class_

        stmt = delete(relation_model).where(relation_model.id.in_(ids_to_delete))
        await db.execute(stmt)
        await db.flush()

        logger.info(f"删除关联对象: 数量={len(ids_to_delete)}")

    async def _update_relation_objects(
        self,
        db: AsyncSession,
        relation_attr: Any,
        objects_to_update: list[dict[str, Any]],
    ) -> None:
        """
        更新关联对象

        Args:
            db: 数据库会话
            relation_attr: 关联属性
            objects_to_update: 要更新的对象数据列表
        """
        if not objects_to_update:
            return

        from sqlalchemy import select

        # 获取关联模型类
        relation_model = relation_attr.property.mapper.class_

        for obj_data in objects_to_update:
            obj_id = obj_data.get("id") if isinstance(obj_data, dict) else obj_data.id

            # 查询对象
            stmt = select(relation_model).where(relation_model.id == obj_id)
            result = await db.execute(stmt)
            db_obj = result.scalar_one_or_none()

            if db_obj:
                # 更新字段
                update_data = obj_data if isinstance(obj_data, dict) else obj_data.model_dump(exclude_unset=True)
                for field, value in update_data.items():
                    if field != "id" and hasattr(db_obj, field):
                        setattr(db_obj, field, value)
                db.add(db_obj)

        await db.flush()
        logger.info(f"更新关联对象: 数量={len(objects_to_update)}")

    async def _create_relation_objects(
        self,
        db: AsyncSession,
        relation_attr: Any,
        parent_obj: T,
        objects_to_create: list[dict[str, Any]],
        relation_info: RelationInfo,  # noqa: ARG002
    ) -> None:
        """
        创建关联对象（自动设置外键）

        Args:
            db: 数据库会话
            relation_attr: 关联属性
            parent_obj: 父对象（主表实例）
            objects_to_create: 要创建的对象数据列表
            relation_info: 关联关系信息（保留用于未来扩展）
        """
        if not objects_to_create:
            return

        from src.database.relation_metadata import RelationMetadata

        # 获取关联模型类
        relation_model = relation_attr.property.mapper.class_

        for item_data in objects_to_create:
            # 自动设置外键
            if hasattr(relation_model, "__foreign_info__"):
                foreign_info = RelationMetadata.get_foreign_info(relation_model)
                parent_tablename = getattr(parent_obj.__class__, "__tablename__", None)
                for foreign_key, fk_info in foreign_info.items():
                    if parent_tablename == fk_info["target_table"]:
                        target_column = fk_info["target_column"]
                        if isinstance(item_data, dict):
                            item_data[foreign_key] = getattr(parent_obj, target_column)
                        else:
                            setattr(item_data, foreign_key, getattr(parent_obj, target_column))

            # 创建对象
            if isinstance(item_data, dict):
                new_obj = relation_model(**item_data)
            else:
                new_obj = relation_model(**item_data.model_dump())

            db.add(new_obj)

        await db.flush()
        logger.info(f"创建关联对象: 数量={len(objects_to_create)}")


__all__ = ["BaseRepository", "HookContext", "HookManager", "HookType"]
