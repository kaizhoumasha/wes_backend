"""树形结构 API（继承 BaseAPI + 树形路由）"""

from collections.abc import Callable
from typing import Annotated, Any, TypeVar

from fastapi import APIRouter, Body, Depends, Path, Query

from src.core.base_api import BaseAPI
from src.core.rbac import RequirePermission
from src.core.response.response_util import response_builder
from src.core.service_protocols import TreeServiceProtocol
from src.database.dependencies import AsyncSessionDep

ModelType = TypeVar("ModelType")
CreateModelType = TypeVar("CreateModelType")
UpdateModelType = TypeVar("UpdateModelType")


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
        prefix: str = "",
        tags: list[str] | None = None,
        gen_create: bool = True,
        gen_update: bool = True,
        gen_delete: bool = True,
        gen_bulk_delete: bool = False,
        enable_permission: bool = True,
        max_depth: int = 2,
        # 使用与 BaseAPI 兼容的类型（函数类型参数是不变的）
        custom_routes: list[Callable[[APIRouter, "BaseAPI"], None]] | None = None,
    ) -> None:
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
        )
        # 设置 service.response_schema 用于关联数据加载
        if hasattr(service, "response_schema"):
            service.response_schema = response_schema
        self.service: TreeServiceProtocol = service

    def _register_routes(self) -> None:
        """注册所有路由（树形路由优先）"""
        self._register_tree_routes()
        super()._register_routes()

    def _register_tree_routes(self):
        """注册树形结构路由"""

        @self.router.get(
            "/tree",
            dependencies=[Depends(RequirePermission(f"{self.perm_prefix}:view"))] if self.enable_permission else [],
        )
        async def get_tree(
            db: AsyncSessionDep,
            root_id: Annotated[int | None, Query(description="根节点ID")] = None,
            max_depth: Annotated[int, Query(description="最大深度,-1表示不限制")] = -1,
        ):
            """获取树形结构"""
            items = await self.service.get_tree(db, root_id, max_depth)
            return response_builder.fast_success(data=items)

        @self.router.get(
            "/siblings/{node_id}",
            dependencies=[Depends(RequirePermission(f"{self.perm_prefix}:view"))] if self.enable_permission else [],
        )
        async def get_siblings(
            db: AsyncSessionDep,
            node_id: Annotated[int, Path(description="节点ID")],
            include_self: Annotated[bool, Query(description="是否包含自身")] = False,
        ):
            """获取同级节点"""
            items = await self.service.get_siblings(db, node_id, include_self)
            return response_builder.success(data=items)

        @self.router.get(
            "/ancestors/{node_id}",
            dependencies=[Depends(RequirePermission(f"{self.perm_prefix}:view"))] if self.enable_permission else [],
        )
        async def get_ancestors(
            db: AsyncSessionDep,
            node_id: Annotated[int, Path(description="节点ID")],
            include_self: Annotated[bool, Query(description="是否包含自身")] = False,
        ):
            """获取祖先节点"""
            items = await self.service.get_ancestors(db, node_id, include_self)
            return response_builder.success(data=items)

        @self.router.put(
            "/move",
            dependencies=[Depends(RequirePermission(f"{self.perm_prefix}:update"))] if self.enable_permission else [],
        )
        async def move_node(
            db: AsyncSessionDep,
            node_id: Annotated[int, Body(description="要移动的节点ID")],
            new_parent_id: Annotated[int | None, Body(description="新的父节点ID")],
        ):
            """移动节点"""
            result = await self.service.move_node(db, node_id, new_parent_id)
            return response_builder.success(data=result)


__all__ = ["TreeAPI"]
