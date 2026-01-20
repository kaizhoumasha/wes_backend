"""
通用 API 基类 (glass_backend 风格)

基于 glass_backend 的优雅实现，提供更灵活的 API 生成器。
"""

from typing import Annotated, Any, Generic, TypeVar

from fastapi import APIRouter, Body, Depends, Path, Query

from src.core.base_service import BaseService
from src.core.logger import logger
from src.core.query_models import QueryOptions
from src.core.response.response_util import response_builder
from src.database.dependencies import AsyncSessionDep, CacheDep

ModelType = TypeVar("ModelType")
CreateModelType = TypeVar("CreateModelType")
UpdateModelType = TypeVar("UpdateModelType")


class BaseAPI(Generic[ModelType, CreateModelType, UpdateModelType]):
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

        if hasattr(service, "response_schema"):
            service.response_schema = response_schema

        self.router = APIRouter(prefix=prefix, tags=tags or [])
        self._register_routes()

    def _register_routes(self) -> None:
        """注册所有路由"""
        if self.gen_create:
            self._register_create()
        if self.gen_update:
            self._register_update()
        if self.gen_delete:
            self._register_delete()
        if self.gen_bulk_delete:
            self._register_bulk_delete()
        self._register_get()
        self._register_list()

    def _get_permission_code(self, action: str) -> str | None:
        """获取权限码"""
        if not self.enable_permission:
            return None
        return f"{self.perm_prefix}:{action}"

    def _build_summary(self, action: str, description: str) -> str:
        """构建接口摘要（包含权限码）"""
        perm_code = self._get_permission_code(action)
        return f"[{perm_code}] {description}" if perm_code else description

    def _get_permission_deps(self, action: str) -> list[Any]:
        """获取权限依赖"""
        logger.debug(f"_get_permission_deps called: action={action}, enable_permission={self.enable_permission}")
        if not self.enable_permission:
            logger.debug(f"权限已禁用，跳过: {self.perm_prefix}:{action}")
            return []
        try:
            from src.core.rbac import RequirePermission

            logger.info(f"获取权限依赖: {self.perm_prefix}:{action}")
            return [Depends(RequirePermission(f"{self.perm_prefix}:{action}"))]
        except ImportError as e:
            logger.warning(f"导入权限模块失败: {e}")
            return []

    def _register_create(self) -> None:
        """注册创建接口"""
        summary = self._build_summary("create", f"创建{self.resource_name}")

        @self.router.post(
            "",
            summary=summary,
            dependencies=self._get_permission_deps("create"),
        )
        async def create(
            obj_in: Annotated[self.create_schema, Body(...)],  # type: ignore
            db: AsyncSessionDep,
            cache: CacheDep,
        ) -> dict[str, Any]:
            data = obj_in.model_dump() if hasattr(obj_in, "model_dump") else obj_in
            resource = await self.service.create(db, data, cache)

            logger.info(f"创建{self.resource_name}成功: id={resource.id}")
            response_data = self.service.to_response(resource, self.response_schema)
            return response_builder.success(data=response_data)

    def _register_update(self) -> None:
        """注册更新接口"""
        summary = self._build_summary("update", f"更新{self.resource_name}")

        @self.router.put(
            "/{id}",
            summary=summary,
            dependencies=self._get_permission_deps("update"),
        )
        async def update(
            id: Annotated[int, Path(...)],
            obj_in: Annotated[self.update_schema, Body(...)],  # type: ignore
            db: AsyncSessionDep,
            cache: CacheDep,
        ) -> dict[str, Any]:
            data = (
                {k: v for k, v in obj_in.model_dump().items() if v is not None}
                if hasattr(obj_in, "model_dump")
                else obj_in
            )  # type: ignore
            resource = await self.service.update(db, id, data, cache)

            logger.info(f"更新{self.resource_name}成功: id={id}")
            response_data = self.service.to_response(resource, self.response_schema)
            return response_builder.success(data=response_data)

    def _register_delete(self) -> None:
        """注册删除接口"""
        summary = self._build_summary("delete", f"删除{self.resource_name}")

        @self.router.delete(
            "/{id}",
            summary=summary,
            dependencies=self._get_permission_deps("delete"),
        )
        async def delete(
            id: Annotated[int, Path(...)],
            db: AsyncSessionDep,
            cache: CacheDep,
        ) -> dict[str, Any]:
            await self.service.delete(db, id, cache)

            logger.info(f"删除{self.resource_name}成功: id={id}")
            return response_builder.success(data={"message": f"{self.resource_name}删除成功"})

    def _register_bulk_delete(self) -> None:
        """注册批量删除接口"""
        summary = self._build_summary("bulk_delete", f"批量删除{self.resource_name}")

        @self.router.delete(
            "/bulk",
            summary=summary,
            dependencies=self._get_permission_deps("bulk_delete"),
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
            dependencies=self._get_permission_deps("get"),
        )
        async def get(
            id: Annotated[int, Path(...)],
            db: AsyncSessionDep,
            cache: CacheDep,
            max_depth: int = Query(self.max_depth, ge=0, le=3, description="关系加载深度"),
        ) -> dict[str, Any]:
            resource = await self.service.get_by_id(db, cache, id, max_depth=max_depth)
            if not resource:
                return response_builder.fail(
                    code=response_builder._build_response_dict("4004", f"{self.resource_name}不存在")  # type: ignore
                )
            response_data = self.service.to_response(resource, self.response_schema)
            return response_builder.success(data=response_data)

    def _register_list(self) -> None:
        """注册列表接口"""
        summary = self._build_summary("list", f"获取{self.resource_name}列表")

        @self.router.post(
            "/query",
            summary=summary,
            dependencies=self._get_permission_deps("list"),
        )
        async def query_items(
            db: AsyncSessionDep,
            cache: CacheDep,
            options: Annotated[QueryOptions, Body(...)],
        ) -> dict[str, Any]:
            total, resources = await self.service.get_list(
                db, cache, options.limit, options.offset, options.filters, options.sort, options.max_depth
            )
            items = self.service.to_list_response(resources, self.response_schema)
            items_data = [item.model_dump() if hasattr(item, "model_dump") else item for item in items]  # type: ignore

            logger.info(f"获取{self.resource_name}列表: limit={options.limit}, offset={options.offset}, total={total}")
            return response_builder.success(
                data={"total": total, "items": items_data, "limit": options.limit, "offset": options.offset}
            )


__all__ = ["BaseAPI"]
