"""
通用 API 基类 (glass_backend 风格)

基于 glass_backend 的优雅实现，提供更灵活的 API 生成器。
"""

from collections.abc import Callable
from typing import Annotated, Any, TypeVar

from fastapi import APIRouter, Body, Depends, Path, Query

from src.core.base_service import BaseService
from src.core.logger import logger
from src.core.query_models import QueryOptions
from src.core.rbac import RequirePermission
from src.core.response.response_code import BusinessErrorCode, ResourceErrorCode
from src.core.response.response_util import response_builder
from src.database.dependencies import AsyncSessionDep, CacheDep

ModelType = TypeVar("ModelType")
CreateModelType = TypeVar("CreateModelType")
UpdateModelType = TypeVar("UpdateModelType")


class BaseAPI[ModelType, CreateModelType, UpdateModelType]:
    """基础 API 生成器"""

    def __init__(
        self,
        module_name: str,
        model: type[ModelType],
        service: BaseService,
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
        custom_routes: list[Callable[[APIRouter, "BaseAPI"], None]] | None = None,
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
        self._custom_route_funcs = custom_routes or []

        if hasattr(service, "response_schema"):
            service.response_schema = response_schema

        self.router = APIRouter(prefix=prefix, tags=tags or [])  # type: ignore[arg-type]
        self._register_routes()

    def _register_routes(self) -> None:
        """注册所有路由

        路由注册顺序很重要：
        1. 先注册自定义路由（具体路径优先）
        2. 再注册标准 CRUD 路由
        """
        # 1. 先注册自定义路由（优先级高，避免被 /{id} 匹配）
        for route_func in self._custom_route_funcs:
            route_func(self.router, self)

        # 2. 再注册标准 CRUD 路由
        if self.gen_create:
            self._register_create()
        if self.gen_update:
            self._register_update()
        if self.gen_delete:
            self._register_delete()
        if self.gen_bulk_delete:
            self._register_bulk_delete()
        self._register_soft_delete_routes()
        self._register_get()
        self._register_list()

    def add_custom_route(self, route_func: Callable[[APIRouter, "BaseAPI"], None], insert_first: bool = False) -> None:
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

    def _register_create(self) -> None:
        """注册创建接口"""
        summary = self._build_summary("create", f"创建{self.resource_name}")

        @self.router.post(
            "",
            summary=summary,
            dependencies=[Depends(RequirePermission(f"{self.perm_prefix}:create"))] if self.enable_permission else [],
        )
        async def create(
            obj_in: Annotated[self.create_schema, Body(...)],  # type: ignore[type-var]
            db: AsyncSessionDep,
            cache: CacheDep,
        ) -> dict[str, Any]:
            data = obj_in.model_dump() if hasattr(obj_in, "model_dump") else obj_in
            resource = await self.service.create(db, data, cache)

            logger.info(f"创建{self.resource_name}成功: id={resource.id if resource else ''}")
            response_data = self.service.to_response(resource, self.response_schema)
            return response_builder.success(data=response_data)

    def _register_update(self) -> None:
        """注册更新接口"""
        summary = self._build_summary("update", f"更新{self.resource_name}")

        @self.router.put(
            "/{id}",
            summary=summary,
            dependencies=[Depends(RequirePermission(f"{self.perm_prefix}:update"))] if self.enable_permission else [],
        )
        async def update(
            id: Annotated[int, Path(...)],
            obj_in: Annotated[self.update_schema, Body(...)],  # type: ignore[type-var]
            db: AsyncSessionDep,
            cache: CacheDep,
        ) -> dict[str, Any]:
            # exclude_unset=True: 只包含用户显式设置的字段，忽略使用默认值的字段
            # 这样当用户不传递 product_lists 时，不会被包含在 data 中
            data = (
                {k: v for k, v in obj_in.model_dump(exclude_unset=True).items() if v is not None}
                if hasattr(obj_in, "model_dump")
                else obj_in
            )
            try:
                resource = await self.service.update(db, id, data, cache)
            except ValueError as e:
                # 处理记录不存在的情况
                if "不存在" in str(e):
                    return response_builder.fail(
                        code=ResourceErrorCode.NOT_FOUND, message=f"{self.resource_name} (ID: {id}) 不存在"
                    )
                # 其他验证错误
                return response_builder.fail(code=BusinessErrorCode.INVALID_STATE, message=str(e))

            logger.info(f"更新{self.resource_name}成功: id={id}")
            response_resource = await self.service.get_by_id(db, cache, id, max_depth=1) or resource
            response_data = self.service.to_response(response_resource, self.response_schema)
            return response_builder.success(data=response_data)

    def _register_delete(self) -> None:
        """注册删除接口（自动检测软删除支持）"""
        summary = self._build_summary("delete", f"删除{self.resource_name}")

        @self.router.delete(
            "/{id}",
            summary=summary,
            dependencies=[Depends(RequirePermission(f"{self.perm_prefix}:delete"))] if self.enable_permission else [],
        )
        async def delete(
            id: Annotated[int, Path(...)],
            db: AsyncSessionDep,
            cache: CacheDep,
            permanent: bool = Query(False, description="是否永久删除"),
        ) -> dict[str, Any]:
            if permanent:
                # 永久删除
                success = await self.service.permanent_delete(db, id, cache)
                if not success:
                    return response_builder.fail(
                        code=ResourceErrorCode.NOT_FOUND, message=f"{self.resource_name} (ID: {id}) 不存在或已被删除"
                    )
                logger.info(f"永久删除{self.resource_name}成功: id={id}")
                return response_builder.success(data={"message": f"{self.resource_name}已永久删除"})
            # 软删除或物理删除（根据模型支持情况）
            success = await self.service.delete(db, id, cache)
            if not success:
                return response_builder.fail(
                    code=ResourceErrorCode.NOT_FOUND, message=f"{self.resource_name} (ID: {id}) 不存在或已被删除"
                )
            logger.info(f"删除{self.resource_name}成功: id={id}")
            return response_builder.success(data={"message": f"{self.resource_name}删除成功"})

    def _register_bulk_delete(self) -> None:
        """注册批量删除接口"""
        summary = self._build_summary("bulk_delete", f"批量删除{self.resource_name}")

        @self.router.delete(
            "/bulk",
            summary=summary,
            dependencies=[Depends(RequirePermission(f"{self.perm_prefix}:bulk_delete"))]
            if self.enable_permission
            else [],  # dependencies=[PermissionDep(f"{self.perm_prefix}:bulk_delete")] if self.enable_permission else [],
        )
        async def bulk_delete(
            ids: list[int],
            db: AsyncSessionDep,
            cache: CacheDep,
        ) -> dict[str, Any]:
            success_count = 0
            failed_count = 0
            errors = []

            for id in ids:
                try:
                    await self.service.delete(db, id, cache)
                    success_count += 1
                except Exception as e:
                    failed_count += 1
                    errors.append({"id": id, "message": str(e)})

            logger.info(f"批量删除{self.resource_name}: success={success_count}, failed={failed_count}")
            return response_builder.batch_operation(
                success=success_count, failed=failed_count, errors=errors if errors else None
            )

    def _register_get(self) -> None:
        """注册获取单个接口"""
        summary = self._build_summary("get", f"获取{self.resource_name}")

        @self.router.get(
            "/{id}",
            summary=summary,
            dependencies=[Depends(RequirePermission(f"{self.perm_prefix}:detail"))] if self.enable_permission else [],
        )
        async def get(
            id: Annotated[int, Path(...)],
            db: AsyncSessionDep,
            cache: CacheDep,
            max_depth: int = Query(self.max_depth, ge=0, le=3, description="关系加载深度"),
            include_deleted: bool = Query(False, description="是否包含已删除记录（仅软删除模型生效）"),
        ) -> dict[str, Any]:
            resource = await self.service.get_by_id(
                db,
                cache,
                id,
                max_depth=max_depth,
                include_deleted=include_deleted if self.supports_soft_delete else False,
            )
            if not resource:
                return response_builder.fail(
                    code=ResourceErrorCode.NOT_FOUND, message=f"{self.resource_name} (ID: {id}) 不存在或已被删除"
                )
            response_data = self.service.to_response(resource, self.response_schema)
            return response_builder.success(data=response_data)

    def _register_list(self) -> None:
        """注册列表接口"""
        summary = self._build_summary("list", f"获取{self.resource_name}列表")

        @self.router.post(
            "/query",
            summary=summary,
            dependencies=[Depends(RequirePermission(f"{self.perm_prefix}:list"))] if self.enable_permission else [],
        )
        async def query_items(
            db: AsyncSessionDep,
            cache: CacheDep,
            options: Annotated[QueryOptions, Body(...)],
        ) -> dict[str, Any]:
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
            items_data = [item.model_dump() if hasattr(item, "model_dump") else item for item in items]

            logger.info(f"获取{self.resource_name}列表: limit={options.limit}, offset={options.offset}, total={total}")
            return response_builder.success(
                data={"total": total, "items": items_data, "limit": options.limit, "offset": options.offset}
            )

    def _register_soft_delete_routes(self) -> None:
        """注册软删除相关路由（仅当模型支持时）"""
        # 检测模型是否支持软删除
        if not self.supports_soft_delete:
            return

        # 1. 恢复接口
        restore_summary = self._build_summary("restore", f"恢复{self.resource_name}")

        @self.router.post(
            "/{id}/restore",
            summary=restore_summary,
            dependencies=[Depends(RequirePermission(f"{self.perm_prefix}:restore"))] if self.enable_permission else [],
        )
        async def restore(
            id: Annotated[int, Path(...)],
            db: AsyncSessionDep,
            cache: CacheDep,
        ) -> dict[str, Any]:
            resource = await self.service.restore(db, id, cache)
            logger.info(f"恢复{self.resource_name}成功: id={id}")
            response_data = self.service.to_response(resource, self.response_schema)
            return response_builder.success(data=response_data)

        # 2. 回收站接口
        trash_summary = self._build_summary("trash", f"获取已删除{self.resource_name}")

        @self.router.get(
            "/trash",
            summary=trash_summary,
            dependencies=[Depends(RequirePermission(f"{self.perm_prefix}:trash"))] if self.enable_permission else [],
        )
        async def get_deleted(
            db: AsyncSessionDep,
            limit: int = Query(10, ge=1, le=100),
            offset: int = Query(0, ge=0),
        ) -> dict[str, Any]:
            total, resources = await self.service.get_deleted(db, limit, offset)
            items = self.service.to_list_response(resources, self.response_schema)
            items_data = [item.model_dump() if hasattr(item, "model_dump") else item for item in items]

            logger.info(f"获取已删除{self.resource_name}: total={total}, limit={limit}, offset={offset}")
            return response_builder.success(
                data={"total": total, "items": items_data, "limit": limit, "offset": offset}
            )

        # 3. 批量恢复接口
        batch_restore_summary = self._build_summary("batch_restore", f"批量恢复{self.resource_name}")

        @self.router.post(
            "/trash/restore",
            summary=batch_restore_summary,
            dependencies=[Depends(RequirePermission(f"{self.perm_prefix}:restore"))] if self.enable_permission else [],
        )
        async def batch_restore(
            ids: Annotated[list[int], Body(...)],
            db: AsyncSessionDep,
            cache: CacheDep,
        ) -> dict[str, Any]:
            success_count = 0
            failed_count = 0
            errors = []

            for id in ids:
                try:
                    await self.service.restore(db, id, cache)
                    success_count += 1
                except Exception as e:
                    failed_count += 1
                    errors.append({"id": id, "message": str(e)})

            logger.info(f"批量恢复{self.resource_name}: success={success_count}, failed={failed_count}")
            return response_builder.batch_operation(
                success=success_count, failed=failed_count, errors=errors if errors else None
            )

        # 4. 批量永久删除接口
        batch_permanent_delete_summary = self._build_summary(
            "batch_permanent_delete", f"批量永久删除{self.resource_name}"
        )

        @self.router.delete(
            "/trash/permanent",
            summary=batch_permanent_delete_summary,
            dependencies=[Depends(RequirePermission(f"{self.perm_prefix}:delete"))] if self.enable_permission else [],
        )
        async def batch_permanent_delete(
            ids: Annotated[list[int], Body(...)],
            db: AsyncSessionDep,
            cache: CacheDep,
        ) -> dict[str, Any]:
            success_count = 0
            failed_count = 0
            errors = []

            for id in ids:
                try:
                    await self.service.permanent_delete(db, id, cache)
                    success_count += 1
                except Exception as e:
                    failed_count += 1
                    errors.append({"id": id, "message": str(e)})

            logger.info(f"批量永久删除{self.resource_name}: success={success_count}, failed={failed_count}")
            return response_builder.batch_operation(
                success=success_count, failed=failed_count, errors=errors if errors else None
            )


__all__ = ["BaseAPI"]
