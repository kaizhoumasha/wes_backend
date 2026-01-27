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

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeVar

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logger import logger
from src.core.query_models import FilterGroup, SortField
from src.database.handlers.error_translator import ErrorTranslator
from src.database.hooks import Hook, HookContext, HookFunc, HookManager, HookType

if TYPE_CHECKING:
    from src.database.relation_metadata import RelationInfo
else:
    # 运行时使用字符串类型注解
    RelationInfo = "RelationInfo"

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

        # 组合模式：使用专门的处理器
        self.error_translator = ErrorTranslator(model)
        self.relation_manager = None  # 延迟初始化（避免循环导入）

        # 自动注册状态验证 Hook
        self._register_status_validation_hooks()

        # 自动注册审计字段填充 Hook
        self._register_audit_hooks()

        # 自动检测并注册审计日志 Hook（委托给 AuditHookRegistrar）
        if self._has_audit_model_mixin():
            from src.database.audit import AuditHookRegistrar

            audit_registrar = AuditHookRegistrar(self._model_name, self._pk_column, self.hook_manager)
            audit_registrar.register_hooks()

    def _get_relation_manager(self):
        """获取关系管理器（延迟初始化）"""
        if self.relation_manager is None:
            from src.database.relations import RelationManager

            self.relation_manager = RelationManager(self.model, self._pk_column)
        return self.relation_manager

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

    def _handle_integrity_error(self, e: IntegrityError) -> None:
        """
        统一处理数据库完整性约束错误（委托给 ErrorTranslator）

        Args:
            e: IntegrityError 异常

        Raises:
            ValueError: 转换后的友好错误提示
        """
        self.error_translator.handle_integrity_error(e)

    def _add_relation_load(self, statement: Any, relation_name: str, relation_info: RelationInfo) -> Any:
        return self._get_relation_manager().add_relation_load(statement, relation_name, relation_info)

    def _add_all_relation_loads(self, statement: Any) -> Any:
        return self._get_relation_manager().add_all_relation_loads(statement)

    def _add_specific_relation_loads(self, statement: Any, relation_names: list[str]) -> Any:
        return self._get_relation_manager().add_specific_relation_loads(statement, relation_names)

    async def _handle_relations(self, db: AsyncSession, instance: T, data: dict[str, Any]) -> None:
        await self._get_relation_manager().handle_relations(db, instance, data)

    async def _preload_relations(self, db: AsyncSession, instance: T, relation_info: dict[str, Any]) -> None:
        await self._get_relation_manager().preload_relations(db, instance, relation_info)

    async def _refresh_with_relations(self, db: AsyncSession, instance: T, relation_info: dict[str, Any]) -> None:
        await self._get_relation_manager().refresh_with_relations(db, instance, relation_info)

    async def _handle_one_to_many_relation(
        self,
        db: AsyncSession,
        instance: T,
        relation_name: str,
        relation_data: list[dict[str, Any]],
    ) -> None:
        await self._get_relation_manager().handle_one_to_many_relation(db, instance, relation_name, relation_data)

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

    async def create(self, db: AsyncSession, data: dict[str, Any]) -> T | None:
        """
        创建新记录（支持主从表关系）

        Args:
            db: 数据库会话
            data: 数据字典（可包含关联对象数据）

        Returns:
            创建的模型实例

        Raises:
            ValueError: 数据完整性约束冲突（友好提示）
        """
        import time

        from src.database.relation_metadata import RelationMetadata

        # 记录开始时间用于审计日志
        start_time = time.time()
        await self._run_hooks(HookType.BEFORE_CREATE, session=db, data=data, _audit_start_time=start_time)

        try:
            # 分离主表字段和关联对象字段
            relation_info = RelationMetadata.get_relation_info(self.model)
            main_data = {k: v for k, v in data.items() if k not in relation_info}
            relation_data = {k: v for k, v in data.items() if k in relation_info}

            # 创建主表实例
            instance = self.model(**main_data)
            db.add(instance)
            await db.flush()
            await db.refresh(instance)

            # 处理关联对象
            if relation_data:
                await self._handle_relations(db, instance, relation_data)
                await db.flush()
                # 刷新实例并加载关联对象
                await self._refresh_with_relations(db, instance, relation_info)

            pk_value = getattr(instance, self._pk_column)
            logger.info(f"创建 {self._model_name} 成功: {self._pk_column}={pk_value}")

            await self._run_hooks(
                HookType.AFTER_CREATE,
                session=db,
                instance=instance,
                data=data,
                _audit_start_time=start_time,
            )

            return instance
        except IntegrityError as e:
            await db.rollback()
            self._handle_integrity_error(e)

    async def update(self, db: AsyncSession, id: int, data: dict[str, Any]) -> T | None:
        """
        更新记录（支持主从表关系）

        Args:
            db: 数据库会话
            id: 主键 ID
            data: 更新数据字典（可包含关联对象数据）

        Returns:
            更新后的模型实例

        Raises:
            ValueError: 记录不存在、状态不允许修改或数据完整性约束冲突（友好提示）
        """
        import time

        start_time = time.time()
        relation_info, has_relations = self._analyze_update_data(data)

        instance = await self._load_instance_for_update(db, id, relation_info, has_relations)
        if not instance:
            raise ValueError(f"{self._model_name} 不存在")

        old_values = self._capture_old_values(instance, data, relation_info)
        await self._run_before_update_hooks(db, instance, data, start_time, old_values)

        try:
            await self._execute_update(db, instance, data, relation_info, has_relations)
            await self._finalize_update(db, instance, data, start_time, old_values)
            return instance
        except IntegrityError as e:
            await db.rollback()
            self._handle_integrity_error(e)

    def _analyze_update_data(self, data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        from src.database.relation_metadata import RelationMetadata

        relation_info = RelationMetadata.get_relation_info(self.model)
        has_relations = any(k in relation_info for k in data)
        return relation_info, has_relations

    async def _load_instance_for_update(
        self, db: AsyncSession, id: int, relation_info: dict[str, Any], has_relations: bool
    ) -> T | None:
        if has_relations:
            from sqlalchemy import select
            from sqlalchemy.orm import selectinload

            options = [
                selectinload(getattr(self.model, relation_name))
                for relation_name in relation_info
                if hasattr(self.model, relation_name)
            ]

            stmt = select(self.model).where(getattr(self.model, self._pk_column) == id)
            for option in options:
                stmt = stmt.options(option)

            result = await db.execute(stmt)
            return result.scalar_one_or_none()
        else:
            return await self.get_by_id(db, id)

    def _capture_old_values(self, instance: T, data: dict[str, Any], relation_info: dict[str, Any]) -> dict[str, Any]:
        old_values = {}
        for key in data:
            if key not in relation_info and hasattr(instance, key):
                old_values[key] = getattr(instance, key)
        return old_values

    async def _run_before_update_hooks(
        self, db: AsyncSession, instance: T, data: dict[str, Any], start_time: float, old_values: dict[str, Any]
    ) -> None:
        await self._run_hooks(
            HookType.BEFORE_UPDATE,
            session=db,
            instance=instance,
            data=data,
            _audit_start_time=start_time,
            _audit_old_values=old_values,
        )

    async def _execute_update(
        self, db: AsyncSession, instance: T, data: dict[str, Any], relation_info: dict[str, Any], has_relations: bool
    ) -> None:
        for field, value in data.items():
            if field in relation_info:
                continue
            if hasattr(instance, field):
                setattr(instance, field, value)

        if has_relations:
            await self._update_relations(db, instance, data)

        await db.flush()

    async def _finalize_update(
        self, db: AsyncSession, instance: T, data: dict[str, Any], start_time: float, old_values: dict[str, Any]
    ) -> None:
        from src.database.relation_metadata import RelationMetadata

        relation_info = RelationMetadata.get_relation_info(self.model)
        has_relations = any(k in relation_info for k in data)

        if has_relations:
            for relation_name in relation_info:
                if hasattr(instance, relation_name):
                    db.expire(instance, [relation_name])
            await self._refresh_with_relations(db, instance, relation_info)
        else:
            await db.refresh(instance)

        logger.info(f"更新 {self._model_name} 成功: id={getattr(instance, self._pk_column)}")

        await self._run_hooks(
            HookType.AFTER_UPDATE,
            session=db,
            instance=instance,
            data=data,
            _audit_start_time=start_time,
            _audit_old_values=old_values,
        )

    async def delete(self, db: AsyncSession, id: int) -> bool | None:
        """
        删除记录（支持主从表关系）

        Args:
            db: 数据库会话
            id: 主键 ID

        Returns:
            是否删除成功

        Raises:
            ValueError: 状态不允许删除或数据完整性约束冲突（友好提示）
        """
        import time

        instance = await self.get_by_id(db, id)
        if not instance:
            return False

        start_time = time.time()
        old_values = self._capture_old_values_for_delete(instance)

        await self._run_before_delete_hooks(db, instance, start_time, old_values)

        try:
            await self._delete_related_objects(db, instance)
            await self._delete_main_record(db, instance, id)
            await self._run_after_delete_hooks(db, instance, start_time, old_values)
            return True
        except IntegrityError as e:
            await db.rollback()
            self._handle_integrity_error(e)

    def _capture_old_values_for_delete(self, instance: T) -> dict[str, Any]:
        old_values = {}
        model_fields = getattr(instance, "model_fields", None)
        if model_fields:
            for field_name in model_fields:
                if hasattr(instance, field_name):
                    try:
                        value = getattr(instance, field_name)
                        old_values[field_name] = value
                    except Exception as e:
                        logger.debug(f"无法获取字段 {field_name} 的值: {e}")
                        continue
        return old_values

    async def _run_before_delete_hooks(
        self, db: AsyncSession, instance: T, start_time: float, old_values: dict[str, Any]
    ) -> None:
        await self._run_hooks(
            HookType.BEFORE_DELETE,
            session=db,
            instance=instance,
            _audit_start_time=start_time,
            _audit_old_values=old_values,
        )

    async def _delete_related_objects(self, db: AsyncSession, instance: T) -> None:
        from src.database.relation_metadata import RelationMetadata, RelationType

        if not RelationMetadata.has_relations(self.model):
            return

        relation_info = RelationMetadata.get_relation_info(self.model)
        for relation_name, info in relation_info.items():
            relation_type = info.get("relation_type", "ONETOMANY")

            if relation_type == RelationType.ONETOMANY:
                relation_attr = getattr(self.model, relation_name, None)
                if relation_attr:
                    current_relations = getattr(instance, relation_name, [])
                    if current_relations:
                        ids_to_delete = {
                            rel.id for rel in current_relations if hasattr(rel, "id") and rel.id is not None
                        }
                        if ids_to_delete:
                            await self._delete_relation_objects(db, relation_attr, ids_to_delete)
                            logger.info(f"删除 {self._model_name} 的关联对象: 数量={len(ids_to_delete)}")

    async def _delete_main_record(self, db: AsyncSession, instance: T, id: int) -> None:
        await db.delete(instance)
        await db.flush()
        logger.info(f"删除 {self._model_name} 成功: id={id}")

    async def _run_after_delete_hooks(
        self, db: AsyncSession, instance: T, start_time: float, old_values: dict[str, Any]
    ) -> None:
        await self._run_hooks(
            HookType.AFTER_DELETE,
            session=db,
            instance=instance,
            _audit_start_time=start_time,
            _audit_old_values=old_values,
        )

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
    ) -> T | None:
        return await self.update(db, id, data)

    async def _update_relations(
        self,
        db: AsyncSession,
        instance: T,
        data: dict[str, Any],
    ) -> None:
        await self._get_relation_manager().update_relations(db, instance, data)

    async def _delete_relation_objects(
        self,
        db: AsyncSession,
        relation_attr: Any,
        ids_to_delete: set[int],
    ) -> None:
        await self._get_relation_manager().delete_relation_objects(db, relation_attr, ids_to_delete)

    async def _update_relation_objects(
        self,
        db: AsyncSession,
        relation_attr: Any,
        objects_to_update: list[dict[str, Any]],
    ) -> None:
        await self._get_relation_manager().update_relation_objects(db, relation_attr, objects_to_update)

    async def _create_relation_objects(
        self,
        db: AsyncSession,
        relation_attr: Any,
        parent_obj: T,
        objects_to_create: list[dict[str, Any]],
    ) -> None:
        await self._get_relation_manager().create_relation_objects(db, relation_attr, parent_obj, objects_to_create)

    def _set_foreign_key_value(
        self,
        item_data: dict[str, Any] | Any,
        foreign_key_field: str,
        parent_obj: T,
        parent_tablename: str | None,
    ) -> None:
        self._get_relation_manager()._set_foreign_key_value(item_data, foreign_key_field, parent_obj, parent_tablename)

    def _create_model_instance(self, model: type, item_data: dict[str, Any] | Any) -> Any:
        return self._get_relation_manager()._create_model_instance(model, item_data)


__all__ = ["BaseRepository", "Hook", "HookContext", "HookFunc", "HookManager", "HookType"]
