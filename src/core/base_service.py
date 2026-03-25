"""
通用 Service 基类

提供通用的业务逻辑协调能力，避免每个 Service 重复编写相同代码。

适用场景:
- WMS/WES 系统中的基础数据模型 (仓库、货架、容器、设备等)
- 标准的 CRUD 业务逻辑
- 通用的缓存管理和响应转换

设计理念:
- 复用通用的 CRUD 流程 (get/create/update/delete)
- 子类扩展特定业务逻辑
- 保持灵活性，不强制所有方法
- 支持 Hook 系统扩展业务逻辑

使用示例:
    # 基础使用 (获得完整 CRUD 能力)
    class WarehouseService(BaseService[Warehouse, WarehouseRepository]):
        def __init__(self):
            super().__init__(WarehouseRepository())

        # 可选: 扩展特定业务逻辑
        async def get_by_name(self, db, name: str):
            return await self.repo.get_by_field(db, "name", name)

    # 使用
    service = WarehouseService()
    warehouse = await service.get_by_id(db, cache, 1)
    warehouses = await service.get_paginated(db, page=1, page_size=10)

    # 扩展业务逻辑
    class ContainerService(BaseService[Container, ContainerRepository]):
        async def create_with_inventory(self, db, container_data, inventory_data):
            # 容器特定的创建逻辑
            container = await self.create(db, container_data)
            # ... 创建库存记录
            return container
"""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypedDict, TypeVar, cast

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logger import logger
from src.core.schema_loader import model_to_schema
from src.database.base_repository import BaseRepository, HookContext, HookType
from src.database.cache_helpers import (
    CACHE_NULL_MARKER,
    get_cached_value,
    set_cached_value,
)

if TYPE_CHECKING:
    from src.core.query_models import FilterGroup, SortField

# ==================== 类型定义 ====================

# Model 类型 (如 Warehouse, Container 等)
M = TypeVar("M")

# Repository 类型 (如 WarehouseRepository, ContainerRepository 等)
R = TypeVar("R", bound=BaseRepository)

_NULL_CACHE_MARKER = CACHE_NULL_MARKER


class ListCachePayload(TypedDict):
    total: int
    items: list[Any]


def _infer_model_name(repository: object) -> str:
    model_name = getattr(repository, "_model_name", None)
    if isinstance(model_name, str):
        return model_name

    model = getattr(repository, "model", None)
    inferred_name = getattr(model, "__name__", None)
    if isinstance(inferred_name, str):
        return inferred_name

    return type(repository).__name__


# ==================== 通用 Service 基类 ====================


class BaseService[M, R]:
    """
    通用 Service 基类

    提供标准的 CRUD 业务逻辑协调能力，适用于 WMS/WES 基础数据模型。

    类型参数:
        M: Model 类型 (SQLModel)
        R: Repository 类型

    职责:
        1. 协调 Repository 和其他服务
        2. 提供通用 CRUD 方法
        3. 缓存管理 (子类通过装饰器实现)
        4. 响应转换 (Model → Schema)

    提供的方法:
        - get_by_id: 根据 ID 获取记录
        - get_paginated: 分页查询
        - create: 创建记录
        - update: 更新记录
        - delete: 删除记录
        - exists: 检查记录是否存在
        - count: 统计记录数量

    使用示例:
        class WarehouseService(BaseService[Warehouse, WarehouseRepository]):
            def __init__(self):
                super().__init__(WarehouseRepository())

        service = WarehouseService()
        warehouse = await service.get_by_id(db, cache, 1)
    """

    @property
    def repo(self) -> R:
        return self._repo

    @repo.setter
    def repo(self, repository: R) -> None:
        self._repo = repository
        self._repo_base = cast(BaseRepository[M], repository)
        self._model_name = _infer_model_name(repository)

    def __init__(
        self,
        repository: R,
        enable_cache: bool = False,
        cache_prefix: str | None = None,
        cache_expire: int = 3600,
        list_cache_prefix: str | None = None,
        list_cache_expire: int | None = None,
        null_cache_expire: int | None = 300,
        response_schema: type | None = None,
    ):
        """
        初始化 Service

        Args:
            repository: Repository 实例
            enable_cache: 是否启用缓存
            cache_prefix: 详情缓存键前缀
            cache_expire: 详情缓存过期时间(秒)
            list_cache_prefix: 列表缓存键前缀
            list_cache_expire: 列表缓存过期时间(秒)，默认继承详情缓存 TTL
            null_cache_expire: 空值缓存过期时间(秒)，None 表示不缓存空值
            response_schema: 响应 Schema (用于自动加载关系)
        """
        self.repo = repository
        self.enable_cache = enable_cache
        self.cache_prefix = cache_prefix or f"{self._model_name.lower()}:detail"
        self.cache_expire = cache_expire
        self.list_cache_prefix = list_cache_prefix or self._get_list_cache_prefix(self.cache_prefix)
        self.list_cache_expire = list_cache_expire if list_cache_expire is not None else cache_expire
        self.null_cache_expire = null_cache_expire
        self.response_schema = response_schema

    @staticmethod
    def _get_list_cache_prefix(cache_prefix: str) -> str:
        if ":detail" in cache_prefix:
            return cache_prefix.replace(":detail", ":list")
        return f"{cache_prefix}:list"

    def _get_response_model(self) -> type[BaseModel] | None:
        """返回可用于序列化/反序列化的响应 Schema。"""
        if isinstance(self.response_schema, type) and issubclass(self.response_schema, BaseModel):
            return self.response_schema
        return None

    def _serialize_item_for_cache(self, item: Any) -> Any:
        """缓存优先使用 response_schema 序列化，保留已加载的关联字段。"""
        response_model = self._get_response_model()
        if response_model is not None:
            payload = self.to_response(item, response_model)
            return payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
        return item.model_dump(mode="json") if isinstance(item, BaseModel) else item

    def _deserialize_item_from_cache(self, item: Any) -> Any:
        """从缓存恢复条目。存在 response_schema 时优先恢复为该 schema。"""
        response_model = self._get_response_model()
        if response_model is not None:
            return self.to_response(item, response_model)
        model_validate = getattr(self._repo_base.model, "model_validate", None)
        if callable(model_validate):
            return cast(Any, model_validate)(item)
        return item

    def add_hook(
        self,
        hook_type: HookType,
        func: Callable[[HookContext], Any],
        priority: int = 0,
        condition: Callable[[HookContext], bool] | None = None,
        error_handler: Callable[[Exception, HookContext], Any] | None = None,
    ) -> None:
        """
        添加 hook 到 repository

        Args:
            hook_type: Hook 类型
            func: Hook 函数
            priority: 优先级
            condition: 执行条件
            error_handler: 错误处理器
        """
        self._repo_base.add_hook(hook_type, func, priority, condition, error_handler)

    async def invalidate_cache(self, cache: object, id: int | None = None, invalidate_list: bool = False) -> None:
        """
        失效缓存

        Args:
            cache: 缓存实例
            id: 记录 ID (None 表示失效所有)
            invalidate_list: 是否失效列表缓存
        """
        if not self.enable_cache or not cache:
            return

        try:
            from src.database.redis_cache import RedisCache

            if not isinstance(cache, RedisCache):
                return

            if id is not None:
                _ = await cache.delete_pattern(f"{self.cache_prefix}:{id}:*")
                logger.debug(f"缓存失效: {self.cache_prefix}:{id}:*")

            if invalidate_list:
                _ = await cache.delete_pattern(f"{self.list_cache_prefix}:*")
                logger.debug(f"列表缓存失效: {self.list_cache_prefix}")
        except ImportError:
            pass

    # ==================== 查询方法 ====================

    # - ARG002: cache 参数由缓存逻辑使用
    async def get_by_id(
        self, db: AsyncSession, cache: object, id: int, max_depth: int = 2, include_deleted: bool = False
    ) -> M | None:
        """
        根据 ID 获取记录（支持缓存）

        Args:
            db: 数据库会话
            cache: 缓存实例
            id: 记录 ID
            max_depth: 关系加载最大深度
            include_deleted: 是否包含已删除记录

        Returns:
            模型实例或 None
        """
        if not self.enable_cache or not cache:
            return await self._repo_base.get_by_id(
                db, id, schema=self.response_schema, max_depth=max_depth, include_deleted=include_deleted
            )

        try:
            from src.database.redis_cache import RedisCache

            if not isinstance(cache, RedisCache):
                return await self._repo_base.get_by_id(
                    db, id, schema=self.response_schema, max_depth=max_depth, include_deleted=include_deleted
                )

            cache_key = f"{self.cache_prefix}:{id}:depth{max_depth}:del{include_deleted}"
            hit, cached_data = await get_cached_value(cache, cache_key)
            if hit:
                logger.debug(f"缓存命中: {cache_key}")
                if cached_data is None:
                    return None
                return cast(M, self._deserialize_item_from_cache(cached_data))

            result = await self._repo_base.get_by_id(
                db, id, schema=self.response_schema, max_depth=max_depth, include_deleted=include_deleted
            )

            if result:
                _ = await set_cached_value(
                    cache,
                    cache_key,
                    result,
                    expire=self.cache_expire,
                    serializer=self._serialize_item_for_cache,
                )
            else:
                _ = await set_cached_value(cache, cache_key, None, null_expire=self.null_cache_expire)

            return result  # type: ignore[return-value]
        except ImportError:
            logger.warning("Redis缓存模块未安装，跳过缓存")
            return await self._repo_base.get_by_id(
                db, id, schema=self.response_schema, max_depth=max_depth, include_deleted=include_deleted
            )

    async def get_list(
        self,
        db: AsyncSession,
        cache: object,
        limit: int = 10,
        offset: int = 0,
        filters: "FilterGroup | None" = None,
        sort: "list[SortField] | None" = None,
        max_depth: int = 1,
        include_deleted: bool = False,
    ) -> tuple[int, list[M]]:
        if not self.enable_cache or not cache:
            return await self._repo_base.get_list(
                db,
                limit,
                offset,
                filters,
                sort,
                schema=self.response_schema,
                max_depth=max_depth,
                include_deleted=include_deleted,
            )

        try:
            from src.database.redis_cache import RedisCache

            if not isinstance(cache, RedisCache):
                return await self._repo_base.get_list(
                    db,
                    limit,
                    offset,
                    filters,
                    sort,
                    schema=self.response_schema,
                    max_depth=max_depth,
                    include_deleted=include_deleted,
                )

            import hashlib
            import json

            filter_hash = hashlib.sha256(
                json.dumps(filters.model_dump() if filters else {}, sort_keys=True).encode()
            ).hexdigest()[:8]
            sort_hash = hashlib.sha256(
                json.dumps([s.model_dump() for s in sort] if sort else [], sort_keys=True).encode()
            ).hexdigest()[:8]
            cache_key = f"{self.list_cache_prefix}:l{limit}:o{offset}:f{filter_hash}:s{sort_hash}:d{max_depth}:del{include_deleted}"
            cached_data = await cache.get(cache_key)

            if cached_data is not None:
                logger.debug(f"缓存命中: {cache_key}")
                if isinstance(cached_data, dict):
                    payload = cast(ListCachePayload, cached_data)
                    total = payload["total"] if isinstance(payload.get("total"), int) else 0
                    items_data = payload["items"] if isinstance(payload.get("items"), list) else []
                    items = [self._deserialize_item_from_cache(item) for item in items_data]
                    return total, items
                return 0, []  # 缓存数据格式不符，返回空列表

            total, items = await self._repo_base.get_list(
                db,
                limit,
                offset,
                filters,
                sort,
                schema=self.response_schema,
                max_depth=max_depth,
                include_deleted=include_deleted,
            )

            items_data = [self._serialize_item_for_cache(item) for item in items]
            _ = await cache.set(cache_key, {"total": total, "items": items_data}, expire=self.list_cache_expire)

            return total, items
        except ImportError:
            logger.warning("Redis缓存模块未安装，跳过缓存")
            return await self._repo_base.get_list(
                db, limit, offset, filters, sort, schema=self.response_schema, max_depth=max_depth
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
            exists = await service.exists(db, name=\"仓库A\")
            exists = await service.exists(db, code=\"WH001\", is_deleted=False)
        """
        return await self._repo_base.exists(db, **kwargs)

    async def count(self, db: AsyncSession, where_clauses: list[Any] | None = None) -> int:
        """
        统计记录数量

        Args:
            db: 数据库会话
            where_clauses: WHERE 条件列表

        Returns:
            记录数量

        Example:
            total = await service.count(db)
            active_count = await service.count(db, where_clauses=[Warehouse.is_deleted == False])
        """
        return await self._repo_base.count(db, where_clauses)

    # ==================== CRUD 方法 ====================

    async def create(self, db: AsyncSession, data: dict[str, Any], cache: object | None = None) -> M | None:
        """
        创建记录

        子类可以重写此方法添加业务逻辑:
            class ContainerService(BaseService[Container, ContainerRepository]):
                async def create(self, db, data):
                    # 验证逻辑
                    if data.get('capacity', 0) <= 0:
                        raise ValueError("容量必须大于0")

                    # 调用父类创建
                    return await super().create(db, data)

        Args:
            db: 数据库会话
            data: 数据字典
            cache: 缓存实例

        Returns:
            创建的模型实例

        Raises:
            IntegrityError: 数据完整性约束冲突
        """
        result = await self._repo_base.create(db, data)
        if cache:
            await self.invalidate_cache(cache, invalidate_list=True)
        return result

    async def update(self, db: AsyncSession, id: int, data: dict[str, Any], cache: object | None = None) -> M | None:
        """
        更新记录

        Args:
            db: 数据库会话
            id: 记录 ID
            data: 更新数据字典
            cache: 缓存实例

        Returns:
            更新后的模型实例

        Raises:
            ValueError: 记录不存在
        """
        result = await self._repo_base.update(db, id, data)
        if cache:
            await self.invalidate_cache(cache, id, invalidate_list=True)
        return result

    async def delete(self, db: AsyncSession, id: int, cache: object | None = None) -> bool | None:
        """
        删除记录

        Args:
            db: 数据库会话
            id: 记录 ID
            cache: 缓存实例

        Returns:
            是否删除成功
        """
        success = await self._repo_base.delete(db, id)
        if not success:
            logger.warning(f"删除 {self._model_name} 失败: id={id} 不存在")
        elif cache:
            await self.invalidate_cache(cache, id, invalidate_list=True)
        return success

    # ==================== 响应转换方法 ====================

    def to_response(self, model: M, response_schema: type) -> Any:
        """
        将模型转换为响应对象

        Args:
            model: 模型实例
            response_schema: 响应 Schema 类

        Returns:
            响应对象
        """
        if model is None:
            return None
        if not isinstance(response_schema, type) or not issubclass(response_schema, BaseModel):
            return model
        if isinstance(model, response_schema):
            return model
        if isinstance(model, dict):
            return response_schema.model_validate(model)
        return cast(Any, model_to_schema(model, response_schema))

    def to_list_response(self, models: list[M], response_schema: type) -> list[Any]:
        """
        将模型列表转换为响应对象列表

        Args:
            models: 模型实例列表
            response_schema: 响应 Schema 类

        Returns:
            响应对象列表
        """
        return [self.to_response(m, response_schema) for m in models]

    # ==================== 软删除方法 ====================

    async def soft_delete(self, db: AsyncSession, id: int, cache: object | None = None) -> M | None:
        """
        软删除记录

        Args:
            db: 数据库会话
            id: 记录 ID
            cache: 缓存实例

        Returns:
            删除后的模型实例
        """
        from src.utils.audit import get_current_user_id

        result = await self._repo_base.soft_delete(db, id, get_current_user_id())
        if cache:
            await self.invalidate_cache(cache, id, invalidate_list=True)
        return result

    async def restore(self, db: AsyncSession, id: int, cache: object | None = None) -> M | None:
        """
        恢复已删除记录

        Args:
            db: 数据库会话
            id: 记录 ID
            cache: 缓存实例

        Returns:
            恢复后的模型实例
        """
        result = await self._repo_base.restore(db, id)
        if cache:
            await self.invalidate_cache(cache, id, invalidate_list=True)
        return result

    async def get_deleted(self, db: AsyncSession, limit: int = 10, offset: int = 0) -> tuple[int, list[M]]:
        """
        获取已删除记录列表

        Args:
            db: 数据库会话
            limit: 限制数量
            offset: 偏移量

        Returns:
            (总数, 记录列表)
        """
        return await self._repo_base.get_deleted(db, limit, offset)

    async def permanent_delete(self, db: AsyncSession, id: int, cache: object | None = None) -> bool:
        """
        永久删除记录（物理删除）

        ⚠️ 警告：此操作不可逆！

        Args:
            db: 数据库会话
            id: 记录 ID
            cache: 缓存实例

        Returns:
            是否删除成功
        """
        success = await self._repo_base.permanent_delete(db, id)
        if success and cache:
            await self.invalidate_cache(cache, id, invalidate_list=True)
        return success


__all__ = ["BaseService", "HookContext", "HookType"]
