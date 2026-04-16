"""
用户 CRUD API（零代码架构）

架构设计：
API 层 → Service 层（UserService）→ Repository 层（UserRepository）

改进：
1. 使用 BaseAPI 实现零代码 CRUD
2. Service 层处理业务逻辑和缓存
3. Repository 层负责数据访问
4. 统一错误处理（依赖全局异常处理器）
"""

from typing import Any, cast

from fastapi import APIRouter, Depends

from src.app.admin.models import (
    AssignRolesRequest,
    ResetPasswordRequest,
    User,
    UserCreate,
    UserResponse,
    UserSimpleResponse,
    UserUpdate,
)
from src.app.admin.services.user_service import user_service
from src.core.base_api import BaseAPI
from src.core.logger import logger
from src.core.rbac import RequirePermission
from src.core.response import ResponseSchemaModel, response_builder
from src.database.dependencies import AsyncSessionDep, CacheDep

# ==================== 自定义路由注册函数 ====================


def register_custom_routes(router: APIRouter, api: BaseAPI[Any, Any, Any]) -> None:
    """注册用户相关的自定义路由

    Args:
        router: FastAPI 路由器
        api: BaseAPI 实例，可获取权限码等辅助信息
    """

    @router.get(
        "/stats/cache",
        summary="[admin:user:stats] 获取缓存统计",
        response_model=ResponseSchemaModel[dict[str, Any]],
        dependencies=[Depends(RequirePermission("admin:user:stats"))],
    )
    async def get_cache_stats(
        db: AsyncSessionDep,
        cache: CacheDep,
    ) -> ResponseSchemaModel[dict[str, Any]]:
        """
        获取缓存统计信息

        返回：
        - total_users: 总用户数
        - cache_status: 缓存服务状态
        - cache_keys_count: 缓存键数量（如果 Redis 可用）
        """
        # 获取总用户数（通过Service层）
        total_users = await user_service.count(db)

        # 获取缓存状态
        cache_status = cast("dict[str, Any]", cast("Any", cache).get_status())

        # 尝试获取 Redis 键数量
        cache_keys_count = None
        try:
            from src.database.redis_client import get_redis, is_redis_available

            if is_redis_available():
                redis_client = get_redis()
                cache_keys_count = await cast("Any", redis_client).dbsize() if redis_client else None
        except Exception as e:
            logger.error(f"获取缓存键数量失败: {e}")

        return cast(
            "ResponseSchemaModel[dict[str, Any]]",
            response_builder.success(
                data={
                    "total_users": total_users,
                    "cache_status": cache_status,
                    "cache_keys_count": cache_keys_count,
                }
            ),
        )

    @router.put(  # pyright: ignore[reportUnknownMemberType]
        "/{id}/reset-password",
        summary="[admin:user:reset-password] 重置用户密码",
        dependencies=[Depends(RequirePermission("admin:user:reset-password"))],
    )
    async def reset_password(  # pyright: ignore[reportUnusedFunction]
        id: int,
        data: ResetPasswordRequest,
        db: AsyncSessionDep,
        cache: CacheDep,
    ) -> ResponseSchemaModel[UserSimpleResponse]:
        """
        管理员重置用户密码

        重置密码后，用户需要重新登录。

        **权限要求**：`admin:user:reset-password`

        **安全措施**：
        - 重置后自动撤销所有活跃会话
        - 清除权限缓存

        Args:
            id: 用户 ID
            data: 重置密码请求数据
            db: 数据库会话
            cache: 缓存服务

        Returns:
            更新后的用户信息
        """
        user = await user_service.reset_password(
            db=db,
            user_id=id,
            new_password=data.new_password,
            cache=cache,
        )
        return cast(
            "ResponseSchemaModel[UserSimpleResponse]",
            response_builder.success(data=UserSimpleResponse.model_validate(user)),
        )

    @router.put(  # pyright: ignore[reportUnknownMemberType]
        "/{id}/assign-roles",
        summary="[admin:user:assign-roles] 为用户分配角色",
        dependencies=[Depends(RequirePermission("admin:user:assign-roles"))],
    )
    async def assign_roles(  # pyright: ignore[reportUnusedFunction]
        id: int,
        data: AssignRolesRequest,
        db: AsyncSessionDep,
        cache: CacheDep,
    ) -> ResponseSchemaModel[UserResponse]:
        """
        为用户分配角色

        分配角色后：
        - 用户的权限会立即更新
        - 如果用户当前已登录，权限变更会在下次请求时生效

        **权限要求**：`admin:user:assign-roles`

        Args:
            id: 用户 ID
            data: 角色分配请求数据
            db: 数据库会话
            cache: 缓存服务

        Returns:
            更新后的用户信息（包含角色列表）
        """
        user = await user_service.assign_roles(
            db=db,
            user_id=id,
            role_ids=data.role_ids,
            cache=cache,
        )
        return cast(
            "ResponseSchemaModel[UserResponse]",
            response_builder.success(data=UserResponse.model_validate(user)),
        )


# ==================== 零代码 CRUD API ====================

# 创建 API 实例，包含自定义路由
user_api = BaseAPI(
    module_name="admin",
    model=User,
    service=user_service,
    create_schema=UserCreate,
    update_schema=UserUpdate,
    response_schema=UserResponse,
    prefix="/users",
    tags=["用户管理"],
    gen_create=True,
    gen_update=True,
    gen_delete=True,
    gen_bulk_delete=True,
    max_depth=2,
    custom_routes=[register_custom_routes],  # 注册自定义路由
)

router = user_api.router
