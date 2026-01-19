"""
用户服务层（重构版）

处理用户相关的业务逻辑，现在使用分离的服务类。

架构改进：
- UserRepository: 负责 CRUD 数据访问
- PasswordHasher: 负责密码哈希和验证
- UserResponseBuilder: 负责响应转换
- UserService: 协调各个服务，提供高层次的业务逻辑

优势：
- 单一职责：每个类只负责一个领域
- 依赖倒置：依赖抽象而非具体实现
- 易于测试：可以轻松 mock 各个服务
- 易于扩展：可以独立替换各个服务
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.admin.models import User, UserRead

# 导入分离的服务
from src.app.admin.repositories.user_repository import user_repository
from src.app.admin.services.user_auth_service import password_hasher
from src.core.logger import logger
from src.core.schema_loader import get_all_with_schema, get_with_schema, model_to_schema

# ==================== 缓存配置常量 ====================

USER_DETAIL_CACHE_PREFIX = "user:detail"
USER_LIST_CACHE_PREFIX = "user:list"
USER_CACHE_EXPIRE = 7200  # 2小时
USER_LIST_CACHE_EXPIRE = 600  # 10分钟
NULL_CACHE_EXPIRE = 300  # 空值缓存5分钟


# ==================== 用户查询函数 ====================


async def get_user_by_id(db: AsyncSession, user_id: int) -> User | None:
    """
    根据 ID 获取用户

    Args:
        db: 数据库会话
        user_id: 用户 ID

    Returns:
        用户对象或 None
    """
    return await get_with_schema(db, User, UserRead, User.id == user_id, max_depth=2)


async def get_user_by_field(db: AsyncSession, field_name: str, value: str | int) -> User | None:
    """
    根据字段查询用户（通用方法）

    Args:
        db: 数据库会话
        field_name: 字段名（如 "username", "email"）
        value: 字段值

    Returns:
        用户对象或 None

    Examples:
        user = await get_user_by_field(db, "username", "admin")
        user = await get_user_by_field(db, "email", "admin@example.com")
    """
    result = await db.execute(select(User).where(getattr(User, field_name) == value))
    return result.scalars().first()


async def get_user_by_username(db: AsyncSession, username: str) -> User | None:
    """
    根据用户名获取用户

    Args:
        db: 数据库会话
        username: 用户名

    Returns:
        用户对象或 None
    """
    return await get_user_by_field(db, "username", username)


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    """
    根据邮箱获取用户

    Args:
        db: 数据库会话
        email: 邮箱地址

    Returns:
        用户对象或 None
    """
    return await get_user_by_field(db, "email", email)


async def get_users_paginated(db: AsyncSession, page: int = 1, page_size: int = 10) -> tuple[int, list[User]]:
    """
    分页获取用户列表

    Args:
        db: 数据库会话
        page: 页码（从 1 开始）
        page_size: 每页数量

    Returns:
        (总数, 用户列表)
    """
    count_result = await db.execute(select(func.count(User.id)))
    total = count_result.scalar()

    offset = (page - 1) * page_size
    users = await get_all_with_schema(db, User, UserRead, limit=page_size, offset=offset)

    return total, users


async def check_user_exists(
    db: AsyncSession,
    username: str | None = None,
    email: str | None = None,
    exclude_user_id: int | None = None,
) -> str | None:
    """
    检查用户是否存在

    Args:
        db: 数据库会话
        username: 用户名
        email: 邮箱
        exclude_user_id: 排除的用户 ID

    Returns:
        如果存在返回冲突字段名，否则返回 None
    """
    return await user_repository.exists(db, username=username, email=email)


# ==================== 用户 CRUD 函数 ====================


async def create_user(
    db: AsyncSession,
    username: str,
    email: str,
    password: str,
    full_name: str | None = None,
) -> User:
    """
    创建新用户

    协调各个服务完成用户创建：
    1. 验证用户不存在
    2. 哈希密码
    3. 创建用户记录

    Args:
        db: 数据库会话
        username: 用户名
        email: 邮箱
        password: 密码
        full_name: 全名（可选）

    Returns:
        创建的用户对象

    Raises:
        ValueError: 如果用户名或邮箱已存在
    """
    # 检查用户名和邮箱是否已存在
    conflict = await check_user_exists(db, username=username, email=email)
    if conflict:
        field_name = "用户名" if conflict == "username" else "邮箱"
        raise ValueError(f"{field_name}已存在")

    # 哈希密码
    hashed_password = await password_hasher.hash_async(password)

    # 创建用户数据字典
    user_data = {
        "username": username,
        "email": email,
        "hashed_password": hashed_password,
        "full_name": full_name,
    }

    # 使用仓库创建用户
    user = await user_repository.create(db, user_data)
    return user  # noqa: RET504  # 赋值提高代码可读性


async def update_user(
    db: AsyncSession,
    user_id: int,
    email: str | None = None,
    full_name: str | None = None,
    is_active: bool | None = None,
) -> User:
    """
    更新用户信息

    Args:
        db: 数据库会话
        user_id: 用户 ID
        email: 新邮箱（可选）
        full_name: 新全名（可选）
        is_active: 是否激活（可选）

    Returns:
        更新后的用户对象

    Raises:
        ValueError: 如果用户不存在或邮箱已被使用
    """
    # 检查邮箱是否被其他用户使用
    if email:
        conflict = await check_user_exists(db, email=email, exclude_user_id=user_id)
        if conflict:
            raise ValueError("邮箱已被使用")

    # 构建更新数据
    update_data = {}
    if email is not None:
        update_data["email"] = email
    if full_name is not None:
        update_data["full_name"] = full_name
    if is_active is not None:
        update_data["is_active"] = is_active

    # 使用仓库更新用户
    user = await user_repository.update(db, user_id, update_data)
    return user  # noqa: RET504  # 赋值提高代码可读性


async def delete_user(db: AsyncSession, user_id: int) -> str:
    """
    删除用户

    Args:
        db: 数据库会话
        user_id: 用户 ID

    Returns:
        被删除的用户名

    Raises:
        ValueError: 如果用户不存在
    """
    success = await user_repository.delete(db, user_id)
    if not success:
        raise ValueError("用户不存在")
    return "已删除"


# ==================== 响应转换函数 ====================


def user_to_response(user: User) -> UserRead:
    """
    将用户模型转换为响应对象

    Args:
        user: 用户模型

    Returns:
        用户响应对象
    """
    return model_to_schema(user, UserRead)


def users_to_list_response(users: list[User]) -> list[UserRead]:
    """
    将用户模型列表转换为响应对象列表

    Args:
        users: 用户模型列表

    Returns:
        用户响应对象列表
    """
    return [model_to_schema(u, UserRead) for u in users]


# ==================== 缓存管理函数 ====================


async def invalidate_user_cache(cache, user_id: int | None = None, invalidate_list: bool = True) -> None:
    """
    失效用户相关缓存

    Args:
        cache: 缓存实例
        user_id: 用户 ID（如果提供，失效该用户详情缓存）
        invalidate_list: 是否失效列表缓存
    """
    try:
        if user_id:
            cache_key = f"{USER_DETAIL_CACHE_PREFIX}:{user_id}"
            await cache.delete(cache_key)

        if invalidate_list:
            await cache.delete_pattern(f"{USER_LIST_CACHE_PREFIX}:*")
    except Exception as e:
        logger.error(f"失效缓存失败: {e}")
        # 缓存操作失败不影响主业务


# ==================== 向后兼容的类接口 ====================


class UserService:
    """
    用户服务类（向后兼容）

    所有方法都是新服务的别名，保持向后兼容。
    建议直接使用新的服务类以获得更好的架构。
    """

    # 缓存配置（类属性）
    USER_DETAIL_CACHE_PREFIX = USER_DETAIL_CACHE_PREFIX
    USER_LIST_CACHE_PREFIX = USER_LIST_CACHE_PREFIX
    USER_CACHE_EXPIRE = USER_CACHE_EXPIRE
    USER_LIST_CACHE_EXPIRE = USER_LIST_CACHE_EXPIRE
    NULL_CACHE_EXPIRE = NULL_CACHE_EXPIRE

    # 密码方法
    hash_password_async = staticmethod(password_hasher.hash_async)
    verify_password_async = staticmethod(password_hasher.verify_async)
    hash_password = staticmethod(password_hasher.hash)
    verify_password = staticmethod(password_hasher.verify)

    # 响应转换
    user_to_response = staticmethod(user_to_response)
    users_to_list_response = staticmethod(users_to_list_response)

    # 查询方法
    get_user_by_id = staticmethod(get_user_by_id)
    get_user_by_field = staticmethod(get_user_by_field)
    get_user_by_username = staticmethod(get_user_by_username)
    get_user_by_email = staticmethod(get_user_by_email)
    get_users_paginated = staticmethod(get_users_paginated)
    check_user_exists = staticmethod(check_user_exists)

    # CRUD 方法
    create_user = staticmethod(create_user)
    update_user = staticmethod(update_user)
    delete_user = staticmethod(delete_user)

    # 缓存管理
    invalidate_user_cache = staticmethod(invalidate_user_cache)


__all__ = [  # noqa: RUF022  # 按功能分组，非字母顺序
    # 缓存配置
    "NULL_CACHE_EXPIRE",
    "USER_CACHE_EXPIRE",
    "USER_DETAIL_CACHE_PREFIX",
    "USER_LIST_CACHE_EXPIRE",
    "USER_LIST_CACHE_PREFIX",
    # 缓存管理
    "invalidate_user_cache",
    # CRUD 函数
    "check_user_exists",
    "create_user",
    "delete_user",
    "update_user",
    # 查询函数
    "get_user_by_email",
    "get_user_by_field",
    "get_user_by_id",
    "get_user_by_username",
    "get_users_paginated",
    # 响应转换
    "user_to_response",
    "users_to_list_response",
    # 向后兼容
    "UserService",
    # 新服务类
    "password_hasher",
    "user_repository",
]
