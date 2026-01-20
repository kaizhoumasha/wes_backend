"""
认证相关 Schema

包含登录、令牌刷新等认证相关的 Pydantic Schemas

注意：LoginResponse.user 引用 admin 模块的 UserRead
在 src/app/auth/models/__init__.py 中会处理跨模块引用
"""

from datetime import datetime

from src.core.mixins import BaseMixin, Field


class LoginRequest(BaseMixin):
    """登录请求 Schema"""

    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=6, max_length=100)


class LoginResponse(BaseMixin):
    """
    登录响应 Schema

    注意：user 字段引用 admin 模块的 UserRead
    使用字符串形式的前向引用避免循环导入
    """

    access_token: str
    refresh_token: str
    access_token_expire_time: datetime
    refresh_token_expire_time: datetime
    session_uuid: str
    user: "UserRead"  # 跨模块引用，在 __init__.py 中重建


class RefreshTokenResponse(BaseMixin):
    """刷新令牌响应 Schema"""

    access_token: str
    refresh_token: str
    access_token_expire_time: datetime
    refresh_token_expire_time: datetime
    session_uuid: str
