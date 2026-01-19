"""
用户服务层（类式架构）

处理用户相关的业务逻辑，协调 Repository 和其他服务组件。

架构设计：
API 层 → Service 层（UserService）→ Repository 层（UserRepository）

职责：
1. 协调多个 Repository 和服务组件
2. 实现业务逻辑和规则
3. 缓存管理
4. 事务协调

优势：
- 依赖注入：通过 __init__ 注入依赖，符合 DIP 原则
- 易于测试：可以轻松 mock 依赖
- 可扩展性：通过继承扩展功能
- 清晰的职责：Service 层专注于业务逻辑
"""

# ruff: noqa: ARG002
# - ARG002: cache 参数由 @cached 装饰器使用，exclude_user_id 是预留参数

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.admin.models import User, UserRead
from src.app.admin.repositories.user_repository import UserRepository, user_repository
from src.app.admin.services.user_auth_service import PasswordHasher, password_hasher
from src.core.logger import logger
from src.core.schema_loader import get_with_schema, model_to_schema
from src.database.cache_decorator import cached

# ==================== 缓存配置常量 ====================

USER_DETAIL_CACHE_PREFIX = "user:detail"
USER_LIST_CACHE_PREFIX = "user:list"
USER_CACHE_EXPIRE = 7200  # 2小时
USER_LIST_CACHE_EXPIRE = 600  # 10分钟
NULL_CACHE_EXPIRE = 300  # 空值缓存5分钟


# ==================== 用户服务类 ====================


class UserService:
    """
    用户服务类

    协调 Repository 和其他服务组件，实现用户相关的业务逻辑。

    示例：
        service = UserService(user_repository, password_hasher)
        user = await service.get_user_by_id(db, cache, user_id)
    """

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
        self.user_repo = user_repo
        self.password_hasher = password_hasher

    # ==================== 查询方法 ====================

    @cached(
        key_prefix=USER_DETAIL_CACHE_PREFIX,
        expire=USER_CACHE_EXPIRE,
        null_expire=NULL_CACHE_EXPIRE,
        lock=True,
    )
    async def get_user_by_id(
        self, db: AsyncSession, cache: object, user_id: int
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
        return await get_with_schema(db, User, UserRead, User.id == user_id, max_depth=2)

    async def get_user_by_username(self, db: AsyncSession, username: str) -> User | None:
        """
        根据用户名获取用户

        Args:
            db: 数据库会话
            username: 用户名

        Returns:
            用户对象或 None
        """
        return await self.user_repo.get_by_username(db, username)

    async def get_user_by_email(self, db: AsyncSession, email: str) -> User | None:
        """
        根据邮箱获取用户

        Args:
            db: 数据库会话
            email: 邮箱

        Returns:
            用户对象或 None
        """
        return await self.user_repo.get_by_email(db, email)

    async def get_users_paginated(
        self, db: AsyncSession, page: int = 1, page_size: int = 10
    ) -> tuple[int, list[User]]:
        """
        分页获取用户列表

        Args:
            db: 数据库会话
            page: 页码（从 1 开始）
            page_size: 每页数量

        Returns:
            (总数, 用户列表)
        """
        return await self.user_repo.get_paginated(db, page, page_size)

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
            exclude_user_id: 排除的用户 ID（预留参数）

        Returns:
            如果存在返回冲突字段名，否则返回 None
        """
        return await self.user_repo.exists(db, username=username, email=email)

    # ==================== CRUD 方法 ====================

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
        conflict = await self.check_user_exists(db, username=username, email=email)
        if conflict:
            field_name = "用户名" if conflict == "username" else "邮箱"
            raise ValueError(f"{field_name}已存在")

        # 哈希密码
        hashed_password = await self.password_hasher.hash_async(password)

        # 创建用户数据字典
        user_data = {
            "username": username,
            "email": email,
            "hashed_password": hashed_password,
            "full_name": full_name,
        }

        # 使用仓库创建用户
        return await self.user_repo.create(db, user_data)

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
            conflict = await self.check_user_exists(db, email=email, exclude_user_id=user_id)
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
        return await self.user_repo.update(db, user_id, update_data)

    async def delete_user(self, db: AsyncSession, user_id: int) -> str:
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
        success = await self.user_repo.delete(db, user_id)
        if not success:
            raise ValueError("用户不存在")
        return "已删除"

    # ==================== 响应转换方法 ====================

    @staticmethod
    def user_to_response(user: User) -> UserRead:
        """
        将用户模型转换为响应对象

        Args:
            user: 用户模型

        Returns:
            用户响应对象
        """
        return model_to_schema(user, UserRead)

    @staticmethod
    def users_to_list_response(users: list[User]) -> list[UserRead]:
        """
        将用户模型列表转换为响应对象列表

        Args:
            users: 用户模型列表

        Returns:
            用户响应对象列表
        """
        return [model_to_schema(u, UserRead) for u in users]

    # ==================== 缓存管理方法 ====================

    async def invalidate_user_cache(
        self, cache, user_id: int | None = None, invalidate_list: bool = True
    ) -> None:
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


# ==================== 依赖注入函数 ====================


def get_user_service() -> UserService:
    """
    获取 UserService 实例（FastAPI 依赖注入）

    用于 FastAPI 的 Depends() 依赖注入系统。

    Returns:
        UserService 实例
    """
    return UserService()


# ==================== 导出 ====================

__all__ = [  # noqa: RUF022  # 按功能分组，非字母顺序
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
    # 向后兼容（单例实例）
    "user_repository",
    "password_hasher",
]
