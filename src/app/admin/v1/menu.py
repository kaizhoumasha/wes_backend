"""
菜单管理 API

使用 TreeAPI 提供完整的 CRUD + 树形操作能力
"""

from typing import Annotated, cast

from fastapi import APIRouter, Depends

from src.app.admin.models import Menu, MenuCreate, MenuResponse, MenuTreeResponse, MenuUpdate
from src.app.admin.services.menu_service import menu_service
from src.core.rbac import require_auth
from src.core.response.response_schema import ResponseSchemaModel
from src.core.response.response_util import response_builder
from src.core.tree_api import TreeAPI
from src.database.dependencies import AsyncSessionDep

# ==================== 自定义路由注册函数 ====================


def register_my_menu_route(router: APIRouter, api) -> None:
    """注册用户菜单路由（使用 add_custom_route 时会被调用）

    Args:
        router: APIRouter 实例
        api: TreeAPI 实例
    """

    @router.get(
        "/my",
        response_model=ResponseSchemaModel[list[MenuTreeResponse]],
        summary="获取当前用户的菜单树",
        description="返回当前用户可访问的菜单树（基于角色权限过滤）",
    )
    @router.get(
        "/my_menu",
        response_model=ResponseSchemaModel[list[MenuTreeResponse]],
        summary="获取当前用户的菜单树（兼容旧路径）",
        description="兼容旧版前端，返回当前用户可访问的菜单树（基于角色权限过滤）",
        deprecated=True,
    )
    async def get_my_menus(
        db: AsyncSessionDep,
        current_user_id: Annotated[int, Depends(require_auth)],
    ) -> ResponseSchemaModel[list[MenuTreeResponse]]:
        """
        获取当前用户的菜单树

        返回用户可访问的菜单树结构，包含：
        - 菜单标识（name）：system:users
        - 显示标题（title）：用户管理
        - 路由路径（path）：/system/users
        - 组件路径（component）：views/system/users.vue
        - 图标（icon）
        - 是否隐藏（is_hidden）
        - 子菜单（children）

        **权限规则**：
        - 超级用户返回所有菜单
        - 普通用户只返回其角色关联的菜单
        - 已删除的菜单不会返回

        **使用场景**：
        - 前端登录后获取菜单树
        - 前端根据菜单树动态生成导航栏
        """
        menus = await menu_service.get_user_menu_tree(db, current_user_id)
        return cast("ResponseSchemaModel[list[MenuTreeResponse]]", response_builder.success(data=menus))


# ==================== 创建 TreeAPI 实例 ====================

menu_api = TreeAPI(
    module_name="admin",
    model=Menu,
    service=menu_service,  # type: ignore[arg-type]
    create_schema=MenuCreate,
    update_schema=MenuUpdate,
    response_schema=MenuResponse,
    prefix="/menus",
    tags=["菜单管理"],
    gen_create=True,
    gen_update=True,
    gen_delete=True,
    enable_permission=True,
    max_depth=2,
    custom_routes=[register_my_menu_route],  # 注册自定义路由
)

router = menu_api.router
