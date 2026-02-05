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

from sqlalchemy import func, select

from src.app.admin.models import User, UserCreate, UserResponse, UserUpdate
from src.app.admin.services.user_service import user_service
from src.core.base_api import BaseAPI
from src.core.logger import logger
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
            cache_keys_count = await redis_client.dbsize() if redis_client else None
    except Exception as e:
        logger.error(f"获取缓存键数量失败: {e}")

    return {
        "total_users": total_users,
        "cache_status": cache_status,
        "cache_keys_count": cache_keys_count,
    }
