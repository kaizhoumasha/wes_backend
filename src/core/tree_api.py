"""树形结构 API（继承 BaseAPI + 树形路由）"""

from collections.abc import Callable
from typing import Annotated, Any, TypeVar

from fastapi import APIRouter, Body, Path, Query
from pydantic import BaseModel, Field

from src.core.base_api import BaseAPI
from src.core.response.response_schema import ResponseSchemaModel
from src.core.service_protocols import TreeServiceProtocol
from src.database.dependencies import AsyncSessionDep, CacheDep

ModelType = TypeVar("ModelType")
CreateModelType = TypeVar("CreateModelType")
UpdateModelType = TypeVar("UpdateModelType")


class SortItem(BaseModel):
    """批量排序项"""

    id: int = Field(description="节点ID")
    parent_id: int | None = Field(None, description="父节点ID")
    sort_order: int = Field(0, description="排序值")


class BatchSortRequest(BaseModel):
    """批量排序请求"""

    items: list[SortItem] = Field(description="排序项列表")


class TreeAPI(BaseAPI[ModelType, CreateModelType, UpdateModelType]):
    """树形结构 API（包含 CRUD + 树形操作）"""

    def __init__(
        self,
        module_name: str,
        model: type[ModelType],
        service: TreeServiceProtocol,
        create_schema: type[CreateModelType] | None = None,
        update_schema: type[UpdateModelType] | None = None,
        response_schema: type[ModelType] | Any = Any,
        *,
        tree_response_schema: type[Any],
        prefix: str = "",
        tags: list[str] | None = None,
        gen_create: bool = True,
        gen_update: bool = True,
        gen_delete: bool = True,
        gen_bulk_delete: bool = False,
        enable_permission: bool = True,
        max_depth: int = 2,
        tree_node_response_schema: type[Any] | None = None,
        # 使用与 BaseAPI 兼容的类型（函数类型参数是不变的）
        custom_routes: list[Callable[[APIRouter, Any], None]] | None = None,
        permission_resource: str | None = None,
    ) -> None:
        self.tree_response_schema = tree_response_schema
        self.tree_node_response_schema = (
            tree_node_response_schema if tree_node_response_schema is not None else response_schema
        )
        super().__init__(
            module_name,
            model,
            service,
            create_schema,
            update_schema,
            response_schema,
            prefix,
            tags,
            gen_create,
            gen_update,
            gen_delete,
            gen_bulk_delete,
            enable_permission,
            max_depth,
            custom_routes=custom_routes,
            permission_resource=permission_resource,
        )
        # 设置 service.response_schema 用于关联数据加载
        if hasattr(service, "response_schema"):
            service.response_schema = response_schema
        self.service: TreeServiceProtocol = service

    def _register_routes(self) -> None:
        """注册所有路由（树形路由优先）"""
        self._register_tree_routes()
        super()._register_routes()

    def _register_tree_routes(self) -> None:
        """注册树形结构路由"""

        @self.router.get(
            "/tree",
            response_model=ResponseSchemaModel[list[self.tree_response_schema]],
            dependencies=self._permission_dependencies("tree"),
        )
        async def get_tree(  # pyright: ignore[reportUnusedFunction]
            db: AsyncSessionDep,
            cache: CacheDep,
            root_id: Annotated[int | None, Query(description="根节点ID")] = None,
            tree_depth: Annotated[
                int,
                Query(
                    description="树深度：-1=完整树(节点少时用), 0=仅顶层(懒加载，节点多时用)",
                    ge=-1,
                    le=0,
                ),
            ] = 0,
            max_depth: Annotated[
                int,
                Query(
                    description="关联数据加载深度",
                    ge=0,
                    le=3,
                ),
            ] = 1,
        ) -> dict[str, Any]:
            """获取树形结构（默认懒加载模式）"""
            response_builder_any = self._response_builder()
            items = await self.service.get_tree(
                db, root_id, max_depth, tree_depth, schema=self.tree_response_schema, cache=cache
            )
            return response_builder_any.success(data=items)

        @self.router.get(
            "/siblings/{node_id}",
            response_model=ResponseSchemaModel[list[self.tree_node_response_schema]],
            dependencies=self._permission_dependencies("siblings"),
        )
        async def get_siblings(  # pyright: ignore[reportUnusedFunction]
            db: AsyncSessionDep,
            node_id: Annotated[int, Path(description="节点ID")],
            include_self: Annotated[bool, Query(description="是否包含自身")] = False,
        ) -> dict[str, Any]:
            """获取同级节点"""
            response_builder_any = self._response_builder()
            items = await self.service.get_siblings(db, node_id, include_self)
            return response_builder_any.success(data=items)

        @self.router.get(
            "/ancestors/{node_id}",
            response_model=ResponseSchemaModel[list[self.tree_node_response_schema]],
            dependencies=self._permission_dependencies("ancestors"),
        )
        async def get_ancestors(  # pyright: ignore[reportUnusedFunction]
            db: AsyncSessionDep,
            node_id: Annotated[int, Path(description="节点ID")],
            include_self: Annotated[bool, Query(description="是否包含自身")] = False,
        ) -> dict[str, Any]:
            """获取祖先节点"""
            response_builder_any = self._response_builder()
            items = await self.service.get_ancestors(db, node_id, include_self)
            return response_builder_any.success(data=items)

        @self.router.get(
            "/children/{node_id}",
            response_model=ResponseSchemaModel[list[self.tree_node_response_schema]],
            dependencies=self._permission_dependencies("children"),
        )
        async def get_children(  # pyright: ignore[reportUnusedFunction]
            db: AsyncSessionDep,
            node_id: Annotated[int, Path(description="节点ID")],
        ) -> dict[str, Any]:
            """获取子级节点"""
            response_builder_any = self._response_builder()
            items = await self.service.get_children(db, node_id)
            return response_builder_any.success(data=items)

        if self.gen_update:

            @self.router.put(
                "/move",
                response_model=ResponseSchemaModel[self.tree_node_response_schema],
                dependencies=self._permission_dependencies("move"),
            )
            async def move_node(  # pyright: ignore[reportUnusedFunction]
                db: AsyncSessionDep,
                cache: CacheDep,
                node_id: Annotated[int, Body(description="要移动的节点ID")],
                new_parent_id: Annotated[int | None, Body(description="新的父节点ID")],
            ) -> dict[str, Any]:
                """移动节点"""
                response_builder_any = self._response_builder()
                try:
                    result = await self.service.move_node(db, node_id, new_parent_id, cache=cache)
                except ValueError as e:
                    return self._value_error_response(e, resource_id=node_id)
                return response_builder_any.success(data=result)

            @self.router.put(
                "/batch-sort",
                response_model=ResponseSchemaModel[None],
                dependencies=self._permission_dependencies("batch_sort"),
            )
            async def batch_sort(  # pyright: ignore[reportUnusedFunction]
                db: AsyncSessionDep,
                cache: CacheDep,
                request: BatchSortRequest,
            ) -> dict[str, Any]:
                """批量排序节点

                适用于拖拽排序场景，一次请求更新多个节点的 parent_id 和 sort_order
                """
                items = [item.model_dump() for item in request.items]
                try:
                    await self.service.batch_sort(db, items, cache=cache)
                except ValueError as e:
                    return self._value_error_response(e)
                return self._response_builder().success(data=None)


__all__ = ["TreeAPI"]
