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

from fastapi import Depends

from src.app.admin.models import ResetPasswordRequest, User, UserCreate, UserResponse, UserSimpleResponse, UserUpdate
from src.app.admin.services.user_service import user_service
from src.core.base_api import BaseAPI
from src.core.logger import logger
from src.core.rbac import RequirePermission
from src.core.response import ResponseSchemaModel, response_builder
from src.database.dependencies import AsyncSessionDep, CacheDep

# ==================== 零代码 CRUD API ====================

# 创建 API 实例
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
    gen_bulk_delete=False,
    max_depth=2,
)

router = user_api.router


# ==================== 自定义路由 ====================


@router.get("/stats/cache", summary="获取缓存统计")
async def get_cache_stats(db: AsyncSessionDep, cache: CacheDep) -> dict[str, Any]:
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

    return {
        "total_users": total_users,
        "cache_status": cache_status,
        "cache_keys_count": cache_keys_count,
    }


@router.put(
    "/{id}/reset-password",
    summary="重置用户密码",
    dependencies=[Depends(RequirePermission("admin:user:reset-password"))],
)
async def reset_password(
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
