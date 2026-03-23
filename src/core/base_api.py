"""
通用 API 基类 (glass_backend 风格)

基于 glass_backend 的优雅实现，提供更灵活的 API 生成器。
"""

from collections.abc import Callable
from typing import Annotated, Any, TypeVar, cast

from fastapi import APIRouter, Body, Depends, Path, Query

from src.core.logger import logger
from src.core.query_models import QueryOptions
from src.core.rbac import RequirePermission
from src.core.response.response_code import BusinessErrorCode, ResourceErrorCode
from src.core.response.response_schema import BatchOperationResponseModel, ListResponseSchemaModel, ResponseSchemaModel
from src.core.response.response_util import response_builder
from src.core.service_protocols import CrudServiceProtocol
from src.database.dependencies import AsyncSessionDep, CacheDep

ModelType = TypeVar("ModelType")
CreateModelType = TypeVar("CreateModelType")
UpdateModelType = TypeVar("UpdateModelType")
RouteRegistrar = Callable[[APIRouter, Any], None]


class BaseAPI[ModelType, CreateModelType, UpdateModelType]:
    """基础 API 生成器"""

    def __init__(
        self,
        module_name: str,
        model: type[ModelType],
        service: CrudServiceProtocol,
        create_schema: type[CreateModelType] | None = None,
        update_schema: type[UpdateModelType] | None = None,
        response_schema: type[ModelType] | Any = Any,
        prefix: str = "",
        tags: list[str] | None = None,
        gen_create: bool = True,
        gen_update: bool = True,
        gen_delete: bool = True,
        gen_bulk_delete: bool = False,
        enable_permission: bool = True,
        max_depth: int = 2,
        custom_routes: list[RouteRegistrar] | None = None,
    ) -> None:
        self.module_name = module_name
        self.model = model
        self.service = service
        self.create_schema = create_schema
        self.update_schema = update_schema
        self.response_schema = response_schema
        self.prefix = prefix
        self.tags = tags
        self.gen_create = gen_create
        self.gen_update = gen_update
        self.gen_delete = gen_delete
        self.gen_bulk_delete = gen_bulk_delete
        self.enable_permission = enable_permission
        self.max_depth = max_depth
        self.perm_prefix = f"{module_name}:{model.__name__.lower()}"
        self.resource_name = model.__name__
        self.supports_soft_delete = all(hasattr(model, attr) for attr in ("is_deleted", "soft_delete", "restore"))
        # 自定义路由列表（接收 router 和 api 实例作为参数）
        self._custom_route_funcs: list[RouteRegistrar] = custom_routes or []

        if hasattr(service, "response_schema"):
            service.response_schema = response_schema

        self.router = APIRouter(prefix=prefix, tags=tags or [])  # type: ignore[arg-type]
        self._register_routes()

    def _register_routes(self) -> None:
        """注册所有路由

        路由注册顺序原则：从具体到抽象，避免路径冲突
        1. 自定义路由（用户定义的特定路径）
        2. 专用路由（软删除、批量操作等具体路径）
        3. 标准 CRUD 路由（通用路径如 /{id}）
        """
        # 1. 先注册自定义路由（具体路径优先）
        for route_func in self._custom_route_funcs:
            route_func(self.router, self)

        # 2. 注册专用路由（具体路径，避免被 /{id} 匹配）
        if self.gen_bulk_delete:
            self._register_bulk_delete()  # DELETE /bulk
        self._register_soft_delete_routes()  # GET /trash, POST /{id}/restore, etc.

        # 3. 注册标准 CRUD 路由（通用路径，最后注册）
        if self.gen_create:
            self._register_create()  # POST /
        if self.gen_update:
            self._register_update()  # PUT /{id}
        if self.gen_delete:
            self._register_delete()  # DELETE /{id}
        self._register_get()  # GET /{id}
        self._register_list()  # POST /query

    def add_custom_route(self, route_func: RouteRegistrar, insert_first: bool = False) -> None:
        """动态添加自定义路由

        Args:
            route_func: 路由注册函数，接收 (router, api) 参数
                        示例: def my_route(router, api): @router.get("/custom") ...
            insert_first: 是否插入到路由列表开头（默认追加到末尾）

        Example:
            >>> def register_custom(router, api):
            ...     @router.get("/available-permissions")
            ...     async def get_perms(...): ...
            >>> api.add_custom_route(register_custom, insert_first=True)
        """
        if insert_first:
            self._custom_route_funcs.insert(0, route_func)
        else:
            self._custom_route_funcs.append(route_func)
        # 立即注册到 router
        route_func(self.router, self)

    def get_permission_code(self, action: str) -> str | None:
        """获取权限码（供自定义路由使用）

        Args:
            action: 操作名称，如 "create", "update", "custom_action"

        Returns:
            权限码字符串，如 "module:model:action"
        """
        return self._get_permission_code(action)

    def _get_permission_code(self, action: str) -> str | None:
        """获取权限码"""
        if not self.enable_permission:
            return None
        return f"{self.perm_prefix}:{action}"

    def _build_summary(self, action: str, description: str) -> str:
        """构建接口摘要（包含权限码）"""
        perm_code = self._get_permission_code(action)
        return f"[{perm_code}] {description}" if perm_code else description

    def _permission_dependencies(self, action: str) -> list[Any]:
        perm_code = self._get_permission_code(action)
        return [Depends(RequirePermission(perm_code))] if perm_code else []

    @staticmethod
    def _response_builder() -> Any:
        return cast("Any", response_builder)

    @staticmethod
    def _request_data(payload: Any, *, exclude_unset: bool = False) -> Any:
        if not hasattr(payload, "model_dump"):
            return payload
        if exclude_unset:
            return cast("Any", payload).model_dump(exclude_unset=True)
        return cast("Any", payload).model_dump()

    def _missing_message(self, id: int) -> str:
        return f"{self.resource_name} (ID: {id}) 不存在"

    def _not_found_message(self, id: int) -> str:
        return f"{self.resource_name} (ID: {id}) 不存在或已被删除"

    def _list_response_data(self, total: int, items: list[Any], limit: int, offset: int) -> dict[str, Any]:
        return {
            "total": total,
            "items": self._dump_response_items(items),
            "limit": limit,
            "offset": offset,
        }

    @staticmethod
    def _dump_response_data(data: Any) -> Any:
        """将 Pydantic/ORM 响应对象转换为基础数据结构。"""
        return cast("Any", data).model_dump() if hasattr(data, "model_dump") else data

    def _dump_response_items(self, items: list[Any]) -> list[Any]:
        """批量转换列表响应对象。"""
        return [self._dump_response_data(item) for item in items]

    async def _run_batch_operation(
        self,
        ids: list[int],
        action: Callable[[int], Any],
        log_message: str,
    ) -> dict[str, Any]:
        response_builder_any = self._response_builder()
        success_count = 0
        failed_count = 0
        errors: list[dict[str, Any]] = []

        for id in ids:
            try:
                await action(id)
                success_count += 1
            except Exception as e:
                failed_count += 1
                errors.append({"id": id, "message": str(e)})

        logger.info(f"{log_message}: success={success_count}, failed={failed_count}")
        return response_builder_any.batch_operation(
            success=success_count, failed=failed_count, errors=errors if errors else None
        )

    def _register_create(self) -> None:
        """注册创建接口"""
        summary = self._build_summary("create", f"创建{self.resource_name}")

        @self.router.post(
            "",
            summary=summary,
            response_model=ResponseSchemaModel[self.response_schema],
            dependencies=self._permission_dependencies("create"),
        )
        async def create(  # pyright: ignore[reportUnusedFunction]
            obj_in: Annotated[self.create_schema, Body(...)],  # type: ignore[type-var]
            db: AsyncSessionDep,
            cache: CacheDep,
        ) -> dict[str, Any]:
            response_builder_any = self._response_builder()
            data = self._request_data(obj_in)
            resource = await self.service.create(db, data, cache)

            logger.info(f"创建{self.resource_name}成功: id={resource.id if resource else ''}")
            response_data = self.service.to_response(resource, self.response_schema)
            return response_builder_any.success(data=response_data)

    def _register_update(self) -> None:
        """注册更新接口"""
        summary = self._build_summary("update", f"更新{self.resource_name}")

        @self.router.put(
            "/{id}",
            summary=summary,
            response_model=ResponseSchemaModel[self.response_schema],
            dependencies=self._permission_dependencies("update"),
        )
        async def update(  # pyright: ignore[reportUnusedFunction]
            id: Annotated[int, Path(...)],
            obj_in: Annotated[self.update_schema, Body(...)],  # type: ignore[type-var]
            db: AsyncSessionDep,
            cache: CacheDep,
        ) -> dict[str, Any]:
            response_builder_any = self._response_builder()
            # exclude_unset=True: 只包含用户显式设置的字段，忽略使用默认值的字段。
            # 对于可选字段，显式传入 null 代表清空原值，不能再额外过滤掉 None。
            data = self._request_data(obj_in, exclude_unset=True)
            try:
                resource = await self.service.update(db, id, data, cache)
            except ValueError as e:
                # 处理记录不存在的情况
                if "不存在" in str(e):
                    return response_builder_any.fail(
                        code=ResourceErrorCode.NOT_FOUND, message=self._missing_message(id)
                    )
                # 其他验证错误
                return response_builder_any.fail(code=BusinessErrorCode.INVALID_STATE, message=str(e))

            logger.info(f"更新{self.resource_name}成功: id={id}")
            response_resource = await self.service.get_by_id(db, cache, id, max_depth=1) or resource
            response_data = self.service.to_response(response_resource, self.response_schema)
            return response_builder_any.success(data=response_data)

    def _register_delete(self) -> None:
        """注册删除接口（自动检测软删除支持）"""
        summary = self._build_summary("delete", f"删除{self.resource_name}")

        @self.router.delete(
            "/{id}",
            summary=summary,
            response_model=ResponseSchemaModel[dict[str, str]],
            dependencies=self._permission_dependencies("delete"),
        )
        async def delete(  # pyright: ignore[reportUnusedFunction]
            id: Annotated[int, Path(...)],
            db: AsyncSessionDep,
            cache: CacheDep,
            permanent: bool = Query(False, description="是否永久删除"),  # pyright: ignore[reportCallInDefaultInitializer]
        ) -> dict[str, Any]:
            response_builder_any = self._response_builder()
            if permanent:
                # 永久删除
                success = await self.service.permanent_delete(db, id, cache)
                if not success:
                    return response_builder_any.fail(
                        code=ResourceErrorCode.NOT_FOUND, message=self._not_found_message(id)
                    )
                logger.info(f"永久删除{self.resource_name}成功: id={id}")
                return response_builder_any.success(data={"message": f"{self.resource_name}已永久删除"})
            # 软删除或物理删除（根据模型支持情况）
            success = await self.service.delete(db, id, cache)
            if not success:
                return response_builder_any.fail(code=ResourceErrorCode.NOT_FOUND, message=self._not_found_message(id))
            logger.info(f"删除{self.resource_name}成功: id={id}")
            return response_builder_any.success(data={"message": f"{self.resource_name}删除成功"})

    def _register_bulk_delete(self) -> None:
        """注册批量删除接口"""
        summary = self._build_summary("bulk_delete", f"批量删除{self.resource_name}")

        @self.router.delete(
            "/bulk",
            summary=summary,
            response_model=BatchOperationResponseModel,
            dependencies=self._permission_dependencies("bulk_delete"),
        )
        async def bulk_delete(  # pyright: ignore[reportUnusedFunction]
            ids: list[int],
            db: AsyncSessionDep,
            cache: CacheDep,
        ) -> dict[str, Any]:
            async def delete_one(resource_id: int) -> Any:
                return await self.service.delete(db, resource_id, cache)

            return await self._run_batch_operation(
                ids,
                delete_one,
                f"批量删除{self.resource_name}",
            )

    def _register_get(self) -> None:
        """注册获取单个接口"""
        summary = self._build_summary("get", f"获取{self.resource_name}")

        @self.router.get(
            "/{id}",
            summary=summary,
            response_model=ResponseSchemaModel[self.response_schema],
            dependencies=self._permission_dependencies("detail"),
        )
        async def get(  # pyright: ignore[reportUnusedFunction]
            id: Annotated[int, Path(...)],
            db: AsyncSessionDep,
            cache: CacheDep,
            max_depth: int = Query(self.max_depth, ge=0, le=3, description="关系加载深度"),  # pyright: ignore[reportCallInDefaultInitializer]
            include_deleted: bool = Query(False, description="是否包含已删除记录（仅软删除模型生效）"),  # pyright: ignore[reportCallInDefaultInitializer]
        ) -> dict[str, Any]:
            response_builder_any = self._response_builder()
            resource = await self.service.get_by_id(
                db,
                cache,
                id,
                max_depth=max_depth,
                include_deleted=include_deleted if self.supports_soft_delete else False,
            )
            if not resource:
                return response_builder_any.fail(code=ResourceErrorCode.NOT_FOUND, message=self._not_found_message(id))
            response_data = self.service.to_response(resource, self.response_schema)
            return response_builder_any.success(data=response_data)

    def _register_list(self) -> None:
        """注册列表接口"""
        summary = self._build_summary("list", f"获取{self.resource_name}列表")

        @self.router.post(
            "/query",
            summary=summary,
            response_model=ListResponseSchemaModel[self.response_schema],
            dependencies=self._permission_dependencies("list"),
        )
        async def query_items(  # pyright: ignore[reportUnusedFunction]
            db: AsyncSessionDep,
            cache: CacheDep,
            options: Annotated[QueryOptions, Body(...)],
        ) -> dict[str, Any]:
            response_builder_any = self._response_builder()
            # 根据模型是否支持软删除来决定 include_deleted 的值
            include_deleted = options.include_deleted if self.supports_soft_delete else False

            total, resources = await self.service.get_list(
                db,
                cache,
                options.limit,
                options.offset,
                options.filters,
                options.sort,
                options.max_depth,
                include_deleted,
            )
            items = self.service.to_list_response(resources, self.response_schema)

            logger.info(f"获取{self.resource_name}列表: limit={options.limit}, offset={options.offset}, total={total}")
            return response_builder_any.success(
                data=self._list_response_data(total, items, options.limit, options.offset)
            )

    def _register_soft_delete_routes(self) -> None:
        """注册软删除相关路由（仅当模型支持时）"""
        # 检测模型是否支持软删除
        if not self.supports_soft_delete:
            return

        # 1. 批量恢复接口
        batch_restore_summary = self._build_summary("batch_restore", f"批量恢复{self.resource_name}")

        @self.router.post(
            "/trash/restore",
            summary=batch_restore_summary,
            response_model=BatchOperationResponseModel,
            dependencies=self._permission_dependencies("restore"),
        )
        async def batch_restore(  # pyright: ignore[reportUnusedFunction]
            ids: Annotated[list[int], Body(...)],
            db: AsyncSessionDep,
            cache: CacheDep,
        ) -> dict[str, Any]:
            async def restore_one(resource_id: int) -> Any:
                return await self.service.restore(db, resource_id, cache)

            return await self._run_batch_operation(
                ids,
                restore_one,
                f"批量恢复{self.resource_name}",
            )

        # 2. 批量永久删除接口
        batch_permanent_delete_summary = self._build_summary(
            "batch_permanent_delete", f"批量永久删除{self.resource_name}"
        )

        @self.router.delete(
            "/trash/permanent",
            summary=batch_permanent_delete_summary,
            response_model=BatchOperationResponseModel,
            dependencies=self._permission_dependencies("delete"),
        )
        async def batch_permanent_delete(  # pyright: ignore[reportUnusedFunction]
            ids: Annotated[list[int], Body(...)],
            db: AsyncSessionDep,
            cache: CacheDep,
        ) -> dict[str, Any]:
            async def permanent_delete_one(resource_id: int) -> Any:
                return await self.service.permanent_delete(db, resource_id, cache)

            return await self._run_batch_operation(
                ids,
                permanent_delete_one,
                f"批量永久删除{self.resource_name}",
            )

        # 3. 恢复接口
        restore_summary = self._build_summary("restore", f"恢复{self.resource_name}")

        @self.router.post(
            "/{id}/restore",
            summary=restore_summary,
            response_model=ResponseSchemaModel[self.response_schema],
            dependencies=self._permission_dependencies("restore"),
        )
        async def restore(  # pyright: ignore[reportUnusedFunction]
            id: Annotated[int, Path(...)],
            db: AsyncSessionDep,
            cache: CacheDep,
        ) -> dict[str, Any]:
            response_builder_any = self._response_builder()
            resource = await self.service.restore(db, id, cache)
            logger.info(f"恢复{self.resource_name}成功: id={id}")
            response_data = self.service.to_response(resource, self.response_schema)
            return response_builder_any.success(data=response_data)

        # 4. 回收站接口
        trash_summary = self._build_summary("trash", f"获取已删除{self.resource_name}")

        @self.router.get(
            "/trash",
            summary=trash_summary,
            response_model=ListResponseSchemaModel[self.response_schema],
            dependencies=self._permission_dependencies("trash"),
        )
        async def get_deleted(  # pyright: ignore[reportUnusedFunction]
            db: AsyncSessionDep,
            limit: int = Query(10, ge=1, le=100),  # pyright: ignore[reportCallInDefaultInitializer]
            offset: int = Query(0, ge=0),  # pyright: ignore[reportCallInDefaultInitializer]
        ) -> dict[str, Any]:
            response_builder_any = self._response_builder()
            total, resources = await self.service.get_deleted(db, limit, offset)
            items = self.service.to_list_response(resources, self.response_schema)

            logger.info(f"获取已删除{self.resource_name}: total={total}, limit={limit}, offset={offset}")
            return response_builder_any.success(data=self._list_response_data(total, items, limit, offset))


__all__ = ["BaseAPI"]
