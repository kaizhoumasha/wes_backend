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

from sqlalchemy.orm.attributes import set_attribute

from src.app.admin.models import Role, User
from src.app.admin.repositories.user_repository import UserRepository, user_repository
from src.common.cache_config import cache_settings
from src.core.base_service import BaseService
from src.core.rbac import invalidate_user_permissions
from src.database.base_repository import HookContext, HookType
from src.database.db import AsyncSession
from src.database.redis_cache import get_cache
from src.utils.password_hasher import PasswordHasher, password_hasher


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
            cache_prefix=cache_settings.USER.prefix,
            cache_expire=cache_settings.USER.expire,
            list_cache_prefix=cache_settings.USER_LIST.prefix,
            list_cache_expire=cache_settings.USER_LIST.expire,
        )
        self.password_hasher = password_hasher

        self.add_hook(HookType.BEFORE_CREATE, self._before_create_password_hash)

    async def _before_create_password_hash(self, context: HookContext) -> None:
        """
        before_create hook - 在创建用户前对明文密码进行哈希

        Args:
            context: Hook 上下文，包含 session 和 params
                   params 结构: {"session": db, "data": {...}}
        """
        data = context.params.get("data", {})
        password = data.get("password")

        if password:
            data["hashed_password"] = await self.password_hasher.hash_async(password)
            data.pop("password", None)

    async def create(self, db: AsyncSession, data: dict[str, object], cache: object | None = None) -> User | None:
        user = await super().create(db, data, cache)
        if user is not None and getattr(user, "id", None) is not None:
            await self._invalidate_permissions_for_user(int(user.id))
        return user

    async def update(
        self, db: AsyncSession, id: int, data: dict[str, object], cache: object | None = None
    ) -> User | None:
        user = await super().update(db, id, data, cache)
        if user is not None:
            await self._invalidate_permissions_for_user(id)
        return user

    async def delete(self, db: AsyncSession, id: int, cache: object | None = None) -> bool | None:
        success = await super().delete(db, id, cache)
        if success:
            await self._invalidate_permissions_for_user(id)
        return success

    async def soft_delete(self, db: AsyncSession, id: int, cache: object | None = None) -> User | None:
        user = await super().soft_delete(db, id, cache)
        if user is not None:
            await self._invalidate_permissions_for_user(id)
        return user

    async def restore(self, db: AsyncSession, id: int, cache: object | None = None) -> User | None:
        user = await super().restore(db, id, cache)
        if user is not None:
            await self._invalidate_permissions_for_user(id)
        return user

    async def permanent_delete(self, db: AsyncSession, id: int, cache: object | None = None) -> bool:
        success = await super().permanent_delete(db, id, cache)
        if success:
            await self._invalidate_permissions_for_user(id)
        return success

    async def _after_user_changed_invalidate_permission_cache(self, context: HookContext) -> None:
        user = context.params.get("instance")
        if user is None or getattr(user, "id", None) is None:
            return
        await self._invalidate_permissions_for_user(int(user.id))

    async def _invalidate_permissions_for_user(self, user_id: int) -> None:
        cache = get_cache()
        await invalidate_user_permissions(cache, user_id)

    async def reset_password(
        self,
        db: AsyncSession,
        user_id: int,
        new_password: str,
        cache: object | None = None,
    ) -> User:
        """
        管理员重置用户密码

        重置密码后：
        1. 失效用户详情缓存
        2. 刷新用户关系（roles）
        3. 撤销用户所有 Token（强制重新登录）
        4. 清除权限缓存

        Args:
            db: 数据库会话
            user_id: 用户 ID
            new_password: 新密码（明文）
            cache: 缓存服务（用于失效缓存）

        Returns:
            更新后的用户对象

        Raises:
            NotFoundException: 用户不存在
        """
        from src.core.exceptions import NotFoundException
        from src.core.security import revoke_all_user_tokens

        # 1. 获取用户（检查是否存在）
        user = await self.repo.get_by_id_with_roles(db, user_id)
        if user is None:
            raise NotFoundException(f"用户 {user_id} 不存在")

        # 2. 哈希新密码
        hashed_password = await self.password_hasher.hash_async(new_password)

        # 3. 更新密码（通过 BaseService.update 失效缓存）
        # 包含 version 字段以满足乐观锁验证
        updated_user = await self.update(
            db,
            user_id,
            {
                "hashed_password": hashed_password,
                "version": user.version,  # 乐观锁验证
            },
            cache=cache,
        )

        if updated_user is None:
            raise NotFoundException(f"用户 {user_id} 更新失败")

        # 4. 刷新用户对象并加载 roles 关系（解决异步关系加载问题）
        await db.refresh(updated_user)

        # 5. 撤销所有 Token（强制重新登录）
        _ = await revoke_all_user_tokens(user_id)

        # 6. 清除权限缓存
        await self._invalidate_permissions_for_user(user_id)

        return updated_user

    async def assign_roles(
        self,
        db: AsyncSession,
        user_id: int,
        role_ids: list[int],
        cache: object | None = None,
    ) -> User:
        """
        为用户分配角色

        分配角色后：
        1. 失效用户详情缓存
        2. 刷新用户关系（roles）
        3. 清除权限缓存

        Args:
            db: 数据库会话
            user_id: 用户 ID
            role_ids: 角色 ID 列表
            cache: 缓存服务（用于失效缓存）

        Returns:
            更新后的用户对象

        Raises:
            NotFoundException: 用户不存在或角色不存在
        """
        from src.app.admin.services.role_service import role_service
        from src.core.exceptions import NotFoundException

        # 1. 获取用户（检查是否存在）
        user = await self.repo.get_by_id_with_roles(db, user_id)
        if user is None:
            raise NotFoundException(f"用户 {user_id} 不存在")

        # 2. 通过 RoleService 校验所有角色并获取角色对象
        valid_roles: list[Role] = await role_service.get_active_roles_by_ids(db, role_ids)

        # 3. 更新用户的角色集合（SQLAlchemy 会自动处理关联表）
        set_attribute(user, "roles", valid_roles)
        await db.flush()
        await db.commit()

        # 4. 失效缓存（详情 + 列表）
        await self.invalidate_cache(cache, user_id, invalidate_list=True)

        # 5. 清除权限缓存
        await self._invalidate_permissions_for_user(user_id)

        return user


user_service = UserService()
