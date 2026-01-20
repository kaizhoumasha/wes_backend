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

from src.app.admin.models import User
from src.app.admin.repositories.user_repository import UserRepository, user_repository
from src.app.admin.services.user_auth_service import PasswordHasher, password_hasher
from src.core.base_service import BaseService
from src.database.base_repository import HookContext, HookType

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

        # 注册 before_create hook - 在创建用户前哈希密码
        self.add_hook(HookType.BEFORE_CREATE, self._before_create_password_hash)

    async def _before_create_password_hash(self, context: HookContext) -> None:
        """
        before_create hook - 在创建用户前对明文密码进行哈希

        Args:
            context: Hook 上下文，包含 session 和 params
                   params 结构: {"session": db, "data": {...}}
        """
        # context.params 包含 kwargs，其中 data 是实际数据字典
        data = context.params.get("data", {})
        password = data.get("password")

        if password:
            # 将明文密码哈希后赋值给 hashed_password 字段
            data["hashed_password"] = await self.password_hasher.hash_async(password)
            # 从 data 中移除明文密码，避免污染数据
            data.pop("password", None)


# ==================== 单例实例 ====================

user_service = UserService()
