"""
用户 CRUD API（类式服务架构）

架构设计：
API 层 → Service 层（UserService）→ Repository 层（UserRepository）

改进：
1. 使用类式服务 + 依赖注入
2. Service 层处理业务逻辑和缓存
3. Repository 层负责数据访问
4. 统一错误处理（依赖全局异常处理器）
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Security, status
from sqlalchemy import func, select

from src.app.admin.models import (
    User,
    UserCreate,
    UserListResponse,
    UserUpdate,
)
from src.app.admin.models import (
    UserRead as UserResponse,
)
from src.app.admin.services.user_service import (
    USER_LIST_CACHE_EXPIRE,
    USER_LIST_CACHE_PREFIX,
    UserService,
    get_user_service,
)
from src.core.exceptions import (
    ConflictException,
    NotFoundException,
)
from src.core.logger import logger
from src.core.rbac import RequirePermission
from src.core.security import require_auth
from src.database.dependencies import AsyncSessionDep, CacheDep

router = APIRouter(prefix="/users", tags=["用户管理"])


# ==================== CRUD 路由 ====================


@router.post(
    "",
    response_model=UserResponse,
    summary="创建用户",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Security(RequirePermission("user:create"))],
)
async def create_user(
    user_in: UserCreate,
    db: AsyncSessionDep,
    cache: CacheDep,
    service: Annotated[UserService, Depends(get_user_service)],
):
    """
    创建新用户（需要 user:create 权限）

    缓存策略：Cache-Aside（创建后删除列表缓存）

    - **username**: 用户名（3-50字符）
    - **email**: 邮箱地址
    - **full_name**: 全名（可选）
    - **password**: 密码（6-50字符）
    """
    try:
        user = await service.create_user(
            db,
            username=user_in.username,
            email=user_in.email,
            password=user_in.password,
            full_name=user_in.full_name,
        )
    except ValueError as e:
        raise ConflictException(str(e)) from e

    # 失效列表缓存
    await service.invalidate_user_cache(cache, invalidate_list=True)

    return service.user_to_response(user)


@router.get(
    "",
    response_model=UserListResponse,
    summary="获取用户列表",
    dependencies=[Depends(require_auth)],
)
async def get_users(
    db: AsyncSessionDep,
    cache: CacheDep,
    page: Annotated[int, Query(ge=1, description="页码")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="每页数量")] = 10,
    service: UserService = Depends(get_user_service),
):
    """
    获取用户列表（分页，带缓存，需要登录）

    缓存策略：
    - 缓存键：user:list:{page}:{page_size}
    - 过期时间：10分钟
    - 不使用锁：列表数据允许短暂不一致

    - **page**: 页码，从 1 开始
    - **page_size**: 每页数量，最大 100
    """
    cache_key = f"{USER_LIST_CACHE_PREFIX}:{page}:{page_size}"

    # 尝试从缓存获取
    cached_data = await cache.get(cache_key)
    if cached_data is not None:
        logger.info(f"缓存命中: {cache_key}")
        return UserListResponse(**cached_data)

    total, users = await service.get_users_paginated(db, page, page_size)

    items = service.users_to_list_response(users)
    response_data = {
        "total": total,
        "items": [item.model_dump(mode="json") for item in items],
    }

    await cache.set(cache_key, response_data, expire=USER_LIST_CACHE_EXPIRE)

    logger.info(f"获取用户列表: page={page}, page_size={page_size}, total={total}")
    return UserListResponse(total=total, items=items)


@router.get(
    "/{user_id}",
    response_model=UserResponse,
    summary="获取用户详情",
    dependencies=[Depends(require_auth)],
)
async def get_user(
    user_id: int,
    db: AsyncSessionDep,
    cache: CacheDep,
    service: UserService = Depends(get_user_service),
):
    """获取用户详情（需要登录）"""
    user = await service.get_user_by_id(db, cache, user_id)
    if not user:
        raise NotFoundException("用户不存在")
    return service.user_to_response(user)


@router.put(
    "/{user_id}",
    response_model=UserResponse,
    summary="更新用户",
    dependencies=[Security(RequirePermission("user:update"))],
)
async def update_user(
    user_id: int,
    user_in: UserUpdate,
    db: AsyncSessionDep,
    cache: CacheDep,
    service: UserService = Depends(get_user_service),
):
    """
    更新用户信息（需要 user:update 权限）

    缓存策略：Cache-Aside（更新数据库后删除缓存）

    - **user_id**: 用户 ID
    - **email**: 邮箱（可选）
    - **full_name**: 全名（可选）
    - **is_active**: 是否激活（可选）
    """
    try:
        user = await service.update_user(
            db,
            user_id=user_id,
            email=user_in.email,
            full_name=user_in.full_name,
            is_active=user_in.is_active,
        )
    except ValueError as e:
        raise NotFoundException(str(e)) from e

    # 失效相关缓存
    await service.invalidate_user_cache(cache, user_id=user_id)

    return service.user_to_response(user)


@router.delete(
    "/{user_id}",
    summary="删除用户",
    status_code=status.HTTP_200_OK,
    dependencies=[Security(RequirePermission("user:delete"))],
)
async def delete_user(
    user_id: int,
    db: AsyncSessionDep,
    cache: CacheDep,
    service: UserService = Depends(get_user_service),
):
    """
    删除用户（需要 user:delete 权限）

    缓存策略：Cache-Aside（删除数据库后删除缓存）

    - **user_id**: 用户 ID
    """
    try:
        await service.delete_user(db, user_id)
    except ValueError as e:
        raise NotFoundException(str(e)) from e

    # 失效相关缓存
    await service.invalidate_user_cache(cache, user_id=user_id)

    return {"message": "用户删除成功"}


# ==================== 统计路由 ====================


@router.get("/stats/cache", summary="获取缓存统计")
async def get_cache_stats(db: AsyncSessionDep, cache: CacheDep):
    """
    获取缓存统计信息

    返回：
    - total_users: 总用户数
    - cache_status: 缓存服务状态
    - cache_keys_count: 缓存键数量（如果 Redis 可用）
    """
    # 获取总用户数
    result = await db.execute(select(func.count(User.id)))
    total_users = result.scalar()

    # 获取缓存状态
    cache_status = cache.get_status()

    # 尝试获取 Redis 键数量
    cache_keys_count = None
    try:
        from src.database.redis_client import get_redis, is_redis_available

        if is_redis_available():
            redis_client = get_redis()
            cache_keys_count = await redis_client.dbsize()
    except Exception as e:
        logger.error(f"获取缓存键数量失败: {e}")

    return {
        "total_users": total_users,
        "cache_status": cache_status,
        "cache_keys_count": cache_keys_count,
    }
