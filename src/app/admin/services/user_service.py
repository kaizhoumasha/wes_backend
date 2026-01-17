"""
用户服务层

处理用户相关的业务逻辑，包括查询、缓存操作等
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor

from pwdlib import PasswordHash
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.admin.models import User, UserRead
from src.core.logger import logger
from src.core.schema_loader import get_all_with_schema, get_with_schema, model_to_schema
from src.database.redis_cache import RedisCache

# pwdlib - FastAPI 官方推荐，支持现代密码哈希算法（Argon2）
password_hash = PasswordHash.recommended()

# 线程池用于 CPU 密集型操作（密码哈希）
# 方案A优化: 增加到20个worker以支持500并发
_password_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="password_hash")


class UserService:
    """用户服务类"""

    # 缓存配置
    USER_DETAIL_CACHE_PREFIX = "user:detail"
    USER_LIST_CACHE_PREFIX = "user:list"
    USER_CACHE_EXPIRE = 7200  # 2小时（从1小时增加）
    USER_LIST_CACHE_EXPIRE = 600  # 10分钟（从5分钟增加）
    NULL_CACHE_EXPIRE = 300  # 空值缓存5分钟

    @staticmethod
    async def hash_password_async(password: str) -> str:
        """
        异步安全哈希密码

        使用 ThreadPoolExecutor 在独立线程中执行 CPU 密集型的密码哈希操作，
        避免阻塞事件循环，提升并发性能。

        使用 pwdlib 的 recommended() 模式，自动选择最佳算法（Argon2）
        - 无 72 字节限制
        - 无需手动预处理
        - 行业标准做法

        参考: https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(_password_executor, password_hash.hash, password)

    @staticmethod
    async def verify_password_async(plain_password: str, hashed_password: str) -> bool:
        """
        异步验证密码

        使用 ThreadPoolExecutor 在独立线程中执行密码验证，
        避免阻塞事件循环。

        与 hash_password_async 配套使用
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            _password_executor, password_hash.verify, plain_password, hashed_password
        )

    @staticmethod
    def hash_password(password: str) -> str:
        """
        同步密码哈希（已废弃，请使用 hash_password_async）

        保留此方法用于向后兼容，但建议使用异步版本以获得更好的性能。
        """
        logger.warning("使用了同步密码哈希方法，建议使用 hash_password_async")
        return password_hash.hash(password)

    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        """
        同步密码验证（已废弃，请使用 verify_password_async）

        保留此方法用于向后兼容，但建议使用异步版本以获得更好的性能。
        """
        logger.warning("使用了同步密码验证方法，建议使用 verify_password_async")
        return password_hash.verify(plain_password, hashed_password)

    @staticmethod
    def user_to_response(user: User) -> UserRead:
        return model_to_schema(user, UserRead)

    @staticmethod
    def users_to_list_response(users: list[User]) -> list[UserRead]:
        return [model_to_schema(u, UserRead) for u in users]

    @staticmethod
    async def check_user_exists(
        db: AsyncSession,
        username: str | None = None,
        email: str | None = None,
        exclude_user_id: int | None = None,
    ) -> str | None:
        """
        检查用户是否存在

        :return: 如果存在返回冲突字段名，否则返回 None
        """
        conditions = []
        if username:
            conditions.append(User.username == username)
        if email:
            conditions.append(User.email == email)

        if not conditions:
            return None

        query = select(User).where(*conditions)
        if exclude_user_id:
            query = query.where(User.id != exclude_user_id)

        result = await db.execute(query)
        user = result.scalars().first()  # 只调用一次，保存结果

        if user:
            if username and user.username == username:
                return "username"
            if email and user.email == email:
                return "email"
        return None

    @staticmethod
    async def invalidate_user_cache(
        cache: RedisCache, user_id: int | None = None, invalidate_list: bool = True
    ) -> None:
        """
        失效用户相关缓存

        :param cache: 缓存实例
        :param user_id: 用户ID（如果提供，失效该用户详情缓存）
        :param invalidate_list: 是否失效列表缓存
        """
        try:
            if user_id:
                cache_key = f"{UserService.USER_DETAIL_CACHE_PREFIX}:{user_id}"
                await cache.delete(cache_key)

            if invalidate_list:
                await cache.delete_pattern(f"{UserService.USER_LIST_CACHE_PREFIX}:*")
        except Exception as e:
            logger.error(f"失效缓存失败: {e}")
            # 缓存操作失败不影响主业务

    @staticmethod
    async def get_user_by_id(db: AsyncSession, user_id: int) -> User | None:
        return await get_with_schema(db, User, UserRead, User.id == user_id, max_depth=2)

    @staticmethod
    async def get_user_by_username(db: AsyncSession, username: str) -> User | None:
        """根据用户名获取用户"""
        result = await db.execute(select(User).where(User.username == username))
        return result.scalars().first()

    @staticmethod
    async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
        """根据邮箱获取用户"""
        result = await db.execute(select(User).where(User.email == email))
        return result.scalars().first()

    @staticmethod
    async def get_users_paginated(
        db: AsyncSession, page: int = 1, page_size: int = 10
    ) -> tuple[int, list[User]]:
        count_result = await db.execute(select(func.count(User.id)))
        total = count_result.scalar()

        offset = (page - 1) * page_size
        users = await get_all_with_schema(db, User, UserRead, limit=page_size, offset=offset)

        return total, users

    @staticmethod
    async def create_user(
        db: AsyncSession,
        username: str,
        email: str,
        password: str,
        full_name: str | None = None,
    ) -> User:
        """
        创建新用户

        :raises ValueError: 如果用户名或邮箱已存在
        """
        # 检查用户名和邮箱是否已存在
        conflict = await UserService.check_user_exists(db, username=username, email=email)
        if conflict:
            field_name = "用户名" if conflict == "username" else "邮箱"
            raise ValueError(f"{field_name}已存在")

        # 创建用户
        user = User(
            username=username,
            email=email,
            hashed_password=await UserService.hash_password_async(password),
            full_name=full_name,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

        logger.info(f"创建用户成功: {user.username}")
        return user

    @staticmethod
    async def update_user(
        db: AsyncSession,
        user_id: int,
        email: str | None = None,
        full_name: str | None = None,
        is_active: bool | None = None,
    ) -> User:
        """
        更新用户信息

        :raises ValueError: 如果用户不存在或邮箱已被使用
        """
        user = await UserService.get_user_by_id(db, user_id)
        if not user:
            raise ValueError("用户不存在")

        # 检查邮箱是否被其他用户使用
        if email and email != user.email:
            conflict = await UserService.check_user_exists(db, email=email, exclude_user_id=user_id)
            if conflict:
                raise ValueError("邮箱已被使用")

        # 更新字段
        if email is not None:
            user.email = email
        if full_name is not None:
            user.full_name = full_name
        if is_active is not None:
            user.is_active = is_active

        await db.commit()
        await db.refresh(user)

        logger.info(f"更新用户成功: {user.username}")
        return user

    @staticmethod
    async def delete_user(db: AsyncSession, user_id: int) -> str:
        """
        删除用户

        :return: 被删除的用户名
        :raises ValueError: 如果用户不存在
        """
        user = await UserService.get_user_by_id(db, user_id)
        if not user:
            raise ValueError("用户不存在")

        username = user.username
        await db.delete(user)
        await db.commit()

        logger.info(f"删除用户成功: {username}")
        return username
