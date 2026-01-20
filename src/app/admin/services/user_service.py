"""
用户服务层（User Service）

处理用户相关的业务逻辑，协调 Repository 和其他服务组件。

架构设计：
API 层 → Service 层（UserService）→ Repository 层（UserRepository）

职责:
1. 协调多个 Repository 和服务组件
2. 实现业务逻辑和规则
3. 缓存管理
4. 事务协调

优化：
- 继承 BaseService 获得通用 CRUD 方法
- 使用 @cached 装饰器实现缓存
- 单例模式提高性能
"""

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.admin.models import User
from src.app.admin.repositories.user_repository import UserRepository, user_repository
from src.app.admin.services.user_auth_service import PasswordHasher, password_hasher
from src.core.base_service import BaseService
from src.core.logger import logger
from src.database.cache_decorator import cached

# ==================== 缓存配置常量 ====================

USER_DETAIL_CACHE_PREFIX = "user:detail"
USER_LIST_CACHE_PREFIX = "user:list"
USER_CACHE_EXPIRE = 7200  # 2小时
USER_LIST_CACHE_EXPIRE = 600  # 10分钟
NULL_CACHE_EXPIRE = 300  # 空值缓存5分钟


# ==================== 用户服务类 ====================


class UserService(BaseService[User, UserRepository]):
    """
    用户服务类

    继承 BaseService 获得通用 CRUD 方法：
    - get_by_id(db, cache, id): 根据 ID 获取用户（带缓存）
    - get_paginated(db, page, page_size): 分页查询
    - create(db, data): 创建用户
    - update(db, id, data): 更新用户
    - delete(db, id): 删除用户
    - exists(db, **kwargs): 检查用户是否存在
    - count(db, where_clauses): 统计用户数量
    - to_response(model, schema): 转换为响应对象
    - to_list_response(models, schema): 批量转换

    扩展用户特定的业务方法。
    """

    CACHE_PREFIX = USER_DETAIL_CACHE_PREFIX
    CACHE_EXPIRE = USER_CACHE_EXPIRE

    def __init__(
        self,
        user_repo: UserRepository = user_repository,
        password_hasher: PasswordHasher = password_hasher,
    ):
        """
        初始化用户服务

        Args:
            user_repo: 用户仓库实例
            password_hasher: 密码哈希服务实例
        """
        super().__init__(
            user_repo,
            enable_cache=True,
            cache_prefix=USER_DETAIL_CACHE_PREFIX,
            cache_expire=USER_CACHE_EXPIRE,
        )
        self.password_hasher = password_hasher

    # ==================== 查询方法（使用缓存）====================

    @cached(
        key_prefix=USER_DETAIL_CACHE_PREFIX,
        expire=USER_CACHE_EXPIRE,
        null_expire=NULL_CACHE_EXPIRE,
        lock=True,
    )
    async def get_user_by_id(
        self,
        db: AsyncSession,
        cache: object,
        user_id: int,  # noqa: ARG002
    ) -> User | None:
        """
        根据 ID 获取用户（带缓存）

        Args:
            db: 数据库会话
            cache: 缓存实例（由 @cached 装饰器使用）
            user_id: 用户 ID

        Returns:
            用户对象或 None
        """
        return await self.repo.get_by_id(db, user_id)

    async def get_user_by_username(self, db: AsyncSession, username: str) -> User | None:
        """
        根据用户名获取用户

        Args:
            db: 数据库会话
            username: 用户名

        Returns:
            用户对象或 None
        """
        return await self.repo.get_by_username(db, username)

    async def get_user_by_email(self, db: AsyncSession, email: str) -> User | None:
        """
        根据邮箱获取用户

        Args:
            db: 数据库会话
            email: 邮箱

        Returns:
            用户对象或 None
        """
        return await self.repo.get_by_email(db, email)

    async def get_active_users(self, db: AsyncSession, limit: int | None = None) -> list[User]:
        """
        获取激活用户列表

        Args:
            db: 数据库会话
            limit: 限制数量

        Returns:
            用户列表
        """
        return await self.repo.get_active_users(db, limit)

    # ==================== 业务方法 ====================

    async def create_user(
        self,
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
        if await self.exists(db, username=username):
            raise ValueError("用户名已存在")

        if await self.exists(db, email=email):
            raise ValueError("邮箱已存在")

        # 哈希密码
        hashed_password = await self.password_hasher.hash_async(password)

        # 创建用户数据字典
        user_data = {
            "username": username,
            "email": email,
            "hashed_password": hashed_password,
            "full_name": full_name,
        }

        # 使用父类 create 方法
        return await self.create(db, user_data)

    async def update_user(
        self,
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
            existing = await self.get_user_by_email(db, email)
            if existing and existing.id != user_id:
                raise ValueError("邮箱已被使用")

        # 构建更新数据（只包含非 None 的字段）
        update_data = {}
        if email is not None:
            update_data["email"] = email
        if full_name is not None:
            update_data["full_name"] = full_name
        if is_active is not None:
            update_data["is_active"] = is_active

        # 使用父类 update 方法
        return await self.update(db, user_id, update_data)

    async def check_user_exists(
        self,
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
            exclude_user_id: 排除的用户 ID（更新时使用）

        Returns:
            如果存在返回冲突字段名，否则返回 None
        """
        if username:
            existing = await self.get_user_by_username(db, username)
            if existing and (exclude_user_id is None or existing.id != exclude_user_id):
                return "username"

        if email:
            existing = await self.get_user_by_email(db, email)
            if existing and (exclude_user_id is None or existing.id != exclude_user_id):
                return "email"

        return None

    # ==================== 缓存管理 ====================
    # BaseService 已提供 invalidate_cache 方法，无需重写


# ==================== 单例实例 ====================

user_service = UserService()


# ==================== 依赖注入 ====================


def get_user_service() -> UserService:
    """
    获取 UserService 实例（FastAPI 依赖注入）

    Returns:
        UserService 实例
    """
    return user_service


# ==================== 导出 ====================

__all__ = [
    # 缓存配置
    "NULL_CACHE_EXPIRE",
    "USER_CACHE_EXPIRE",
    "USER_DETAIL_CACHE_PREFIX",
    "USER_LIST_CACHE_EXPIRE",
    "USER_LIST_CACHE_PREFIX",
    # 服务类
    "UserService",
    # 依赖注入
    "get_user_service",
    # 单例
    "user_service",
]
