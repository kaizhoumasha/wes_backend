"""
认证相关 Schema

包含登录、令牌刷新、会话管理等认证相关的 Pydantic Schemas

注意：LoginResponse.user 引用 admin 模块的 UserResponse
在 src/app/auth/models/__init__.py 中会处理跨模块引用
"""

from datetime import datetime
from typing import Any

from pydantic import computed_field
from sqlmodel import Field
from sqlmodel._compat import SQLModelConfig

from src.app.admin.models import UserResponse
from src.core.mixins import BaseMixin
from src.utils.timezone import timezone


def _seconds_until(expire_time: datetime) -> int:
    """按 UTC 计算剩余秒数；naive datetime 按项目约定视为 UTC。"""
    delta = timezone.to_utc(expire_time) - timezone.now_utc()
    return max(0, int(delta.total_seconds()))


# ==================== 请求 Schema ====================


class LoginRequest(BaseMixin):
    """登录请求 Schema"""

    username: str = Field(min_length=3, max_length=50, description="用户名")
    password: str = Field(min_length=6, max_length=100, description="密码")

    model_config = SQLModelConfig(
        json_schema_extra={
            "example": {
                "username": "admin",
                "password": "admin123",  # nosec B105 - schema example only
            }
        }
    )


# ==================== 响应 Schema ====================


class LoginResponse(BaseMixin):
    """
    登录响应 Schema

    包含访问令牌、刷新令牌元数据和用户信息
    """

    access_token: str = Field(description="访问令牌")
    access_token_jti: str = Field(description="访问令牌唯一标识符（用于撤销）")
    refresh_token_jti: str = Field(description="刷新令牌唯一标识符（用于撤销）")
    access_token_expire_time: datetime = Field(description="访问令牌过期时间")
    refresh_token_expire_time: datetime = Field(description="刷新令牌过期时间（令牌仅存储于 HttpOnly Cookie）")
    session_uuid: str = Field(description="会话 UUID")
    user: UserResponse = Field(description="用户信息")

    @computed_field
    @property
    def expires_in(self) -> int:
        """访问令牌过期时间（秒）- OAuth 2.0 标准字段"""
        return _seconds_until(self.access_token_expire_time)

    @computed_field
    @property
    def refresh_expires_in(self) -> int:
        """刷新令牌过期时间（秒）"""
        return _seconds_until(self.refresh_token_expire_time)


class RefreshTokenResponse(BaseMixin):
    """
    刷新令牌响应 Schema

    包含新的访问令牌和刷新令牌元数据
    """

    access_token: str = Field(description="新的访问令牌")
    access_token_jti: str = Field(description="访问令牌唯一标识符")
    refresh_token_jti: str = Field(description="刷新令牌唯一标识符")
    access_token_expire_time: datetime = Field(description="访问令牌过期时间")
    refresh_token_expire_time: datetime = Field(description="刷新令牌过期时间（令牌仅存储于 HttpOnly Cookie）")
    session_uuid: str = Field(description="会话 UUID")

    @computed_field
    @property
    def expires_in(self) -> int:
        """访问令牌过期时间（秒）- OAuth 2.0 标准字段"""
        return _seconds_until(self.access_token_expire_time)

    @computed_field
    @property
    def refresh_expires_in(self) -> int:
        """刷新令牌过期时间（秒）"""
        return _seconds_until(self.refresh_token_expire_time)


class SessionInfo(BaseMixin):
    """
    会话信息 Schema

    描述一个活跃的用户会话
    """

    session_uuid: str = Field(description="会话 UUID")
    jti: str = Field(description="JWT ID")
    created_at: datetime = Field(description="会话创建时间")
    device_info: dict[str, Any] | None = Field(default=None, description="设备信息（可选）")
    last_active: datetime | None = Field(default=None, description="最后活跃时间")


class ActiveSessionsResponse(BaseMixin):
    """
    活跃会话列表响应 Schema

    包含用户所有活跃会话
    """

    total: int = Field(description="活跃会话总数")
    sessions: list[SessionInfo] = Field(description="会话列表")


class LogoutResponse(BaseMixin):
    """登出响应 Schema"""

    message: str = Field(description="响应消息")
    revoked_count: int = Field(default=0, description="撤销的令牌数量")


class RevokeSessionResponse(BaseMixin):
    """撤销会话响应 Schema"""

    message: str = Field(description="响应消息")
    session_uuid: str = Field(description="被撤销的会话 UUID")


class ApiPermissionInfo(BaseMixin):
    """
    API 权限信息 Schema

    描述单个 API 权限的详细信息
    """

    id: int = Field(description="权限 ID")
    name: str = Field(description="权限标识，如 admin:user:create")
    description: str | None = Field(default=None, description="权限描述")
    type: str = Field(description="权限类型：user_api（内部管理API）、app_api（外部应用API）")
    category: str | None = Field(default=None, description="权限分类：admin、system、business 等")
    resource: str | None = Field(default=None, description="资源类型：user、role、permission、warehouse 等")
    action: str | None = Field(default=None, description="操作：create、read、update、delete、list 等")
    method: str | None = Field(default=None, description="HTTP 方法：GET、POST、PUT、DELETE、PATCH 等")
    path: str | None = Field(default=None, description="API 路径：/admin/users/{id}、/api/v1/warehouses 等")


class UserPermissionsResponse(BaseMixin):
    """
    用户权限列表响应 Schema

    包含用户有权限访问的所有 API 权限
    """

    total: int = Field(description="权限总数")
    permissions: list[ApiPermissionInfo] = Field(description="用户有权限访问的 API 列表")


class AuthMyResponse(BaseMixin):
    """
    当前登录用户上下文响应 Schema

    一次性返回前端初始化所需核心数据：
    - 当前用户信息
    - API 权限列表
    """

    user: UserResponse = Field(description="当前用户信息")
    permissions: list[ApiPermissionInfo] = Field(description="当前用户 API 权限列表")
