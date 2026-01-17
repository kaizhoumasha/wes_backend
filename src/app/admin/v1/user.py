"""
用户 CRUD API（优化版）

改进：
1. 使用新的依赖注入（CacheDep, AsyncSessionDep）
2. 提取服务层，消除代码重复
3. 统一错误处理
4. 改进类型安全
5. 简化路由逻辑
"""

from typing import Annotated

from fastapi import APIRouter, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import (
    DatabaseException,
    NotFoundException,
    ConflictException,
    raise_not_found,
)
from src.core.logger import logger
from src.app.admin.models import User
from src.database.dependencies import AsyncSessionDep, CacheDep
from src.database.redis_cache import RedisCache
from src.app.admin.services.user_service import UserService
from src.app.admin.models import UserCreate, UserUpdate, UserRead as UserResponse, UserListResponse

router = APIRouter(prefix="/users", tags=["用户管理"])


# ==================== 辅助函数 ====================


async def get_user_with_cache(
    db: AsyncSession, cache: RedisCache, user_id: int
) -> UserResponse:
    """
    获取用户（带缓存）

    :raises NotFoundException: 如果用户不存在
    """
    cache_key = f"{UserService.USER_DETAIL_CACHE_PREFIX}:{user_id}"

    cached_data = await cache.get(cache_key)
    if cached_data is not None:
        logger.info(f"缓存命中: {cache_key}")
        return UserResponse(**cached_data)

    lock_acquired = await cache.acquire_lock(cache_key, timeout=10, wait_timeout=5)

    if lock_acquired:
        try:
            cached_data = await cache.get(cache_key)
            if cached_data is not None:
                return UserResponse(**cached_data)

            try:
                user = await UserService.get_user_by_id(db, user_id)
            except SQLAlchemyError as e:
                logger.error(f"查询用户失败: {e}")
                raise DatabaseException("查询用户失败")

            if not user:
                await cache.set(cache_key, None, expire=UserService.NULL_CACHE_EXPIRE)
                raise NotFoundException("用户不存在")

            response_data = UserService.user_to_response(user)
            await cache.set(
                cache_key,
                response_data.model_dump(mode='json'),
                expire=UserService.USER_CACHE_EXPIRE,
                is_hot=True,
            )

            logger.info(f"获取用户详情: {user.username}")
            return response_data
        finally:
            await cache.release_lock(cache_key)
    else:
        logger.warning(f"获取锁失败，降级到数据库查询: {cache_key}")
        try:
            user = await UserService.get_user_by_id(db, user_id)
        except SQLAlchemyError as e:
            logger.error(f"查询用户失败: {e}")
            raise DatabaseException("查询用户失败")

        if not user:
            raise NotFoundException("用户不存在")
        return UserService.user_to_response(user)


# ==================== CRUD 路由 ====================


@router.post(
    "",
    response_model=UserResponse,
    summary="创建用户",
    status_code=status.HTTP_201_CREATED,
)
async def create_user(user_in: UserCreate, db: AsyncSessionDep, cache: CacheDep):
    """
    创建新用户

    缓存策略：Cache-Aside（创建后删除列表缓存）

    - **username**: 用户名（3-50字符）
    - **email**: 邮箱地址
    - **full_name**: 全名（可选）
    - **password**: 密码（6-50字符）
    """
    try:
        user = await UserService.create_user(
            db,
            username=user_in.username,
            email=user_in.email,
            password=user_in.password,
            full_name=user_in.full_name,
        )
    except ValueError as e:
        raise ConflictException(str(e))

    # 失效列表缓存
    await UserService.invalidate_user_cache(cache, invalidate_list=True)

    return UserService.user_to_response(user)


@router.get("", response_model=UserListResponse, summary="获取用户列表")
async def get_users(
    db: AsyncSessionDep,
    cache: CacheDep,
    page: Annotated[int, Query(ge=1, description="页码")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="每页数量")] = 10,
):
    """
    获取用户列表（分页，带缓存）

    缓存策略：
    - 缓存键：user:list:{page}:{page_size}
    - 过期时间：5分钟
    - 不使用锁：列表数据允许短暂不一致

    - **page**: 页码，从 1 开始
    - **page_size**: 每页数量，最大 100
    """
    cache_key = f"{UserService.USER_LIST_CACHE_PREFIX}:{page}:{page_size}"

    # 尝试从缓存获取
    cached_data = await cache.get(cache_key)
    if cached_data is not None:
        logger.info(f"缓存命中: {cache_key}")
        return UserListResponse(**cached_data)

    try:
        total, users = await UserService.get_users_paginated(db, page, page_size)
    except SQLAlchemyError as e:
        logger.error(f"查询用户列表失败: {e}")
        raise DatabaseException("查询用户列表失败")

    items = UserService.users_to_list_response(users)
    response_data = {"total": total, "items": [item.model_dump(mode='json') for item in items]}

    await cache.set(cache_key, response_data, expire=UserService.USER_LIST_CACHE_EXPIRE)

    logger.info(f"获取用户列表: page={page}, page_size={page_size}, total={total}")
    return UserListResponse(total=total, items=items)


@router.get("/{user_id}", response_model=UserResponse, summary="获取用户详情")
async def get_user(
    db: AsyncSessionDep,
    cache: CacheDep,
    user_id: int,
):
    user_response = await get_user_with_cache(db, cache, user_id)
    return user_response


@router.put("/{user_id}", response_model=UserResponse, summary="更新用户")
async def update_user(
    db: AsyncSessionDep,
    cache: CacheDep,
    user_id: int,
    user_in: UserUpdate,
):
    """
    更新用户信息

    缓存策略：Cache-Aside（更新数据库后删除缓存）

    - **user_id**: 用户 ID
    - **email**: 邮箱（可选）
    - **full_name**: 全名（可选）
    - **is_active**: 是否激活（可选）
    """
    try:
        user = await UserService.update_user(
            db,
            user_id=user_id,
            email=user_in.email,
            full_name=user_in.full_name,
            is_active=user_in.is_active,
        )
    except ValueError as e:
        raise NotFoundException(str(e))

    # 失效相关缓存
    await UserService.invalidate_user_cache(cache, user_id=user_id)

    return UserService.user_to_response(user)


@router.delete("/{user_id}", summary="删除用户", status_code=status.HTTP_200_OK)
async def delete_user(
    db: AsyncSessionDep,
    cache: CacheDep,
    user_id: int,
):
    """
    删除用户

    缓存策略：Cache-Aside（删除数据库后删除缓存）

    - **user_id**: 用户 ID
    """
    try:
        username = await UserService.delete_user(db, user_id)
    except ValueError as e:
        raise NotFoundException(str(e))

    # 失效相关缓存
    await UserService.invalidate_user_cache(cache, user_id=user_id)

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
        from src.database.redis_client import is_redis_available, get_redis

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
