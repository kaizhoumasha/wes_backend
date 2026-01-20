"""
认证相关 Schema

包含登录、令牌刷新等认证相关的 Pydantic Schemas
"""

from datetime import datetime

from src.core.mixins import BaseMixin, Field


class LoginRequest(BaseMixin):
    """登录请求 Schema"""

    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=6, max_length=100)


class LoginResponse(BaseMixin):
    """登录响应 Schema"""

    access_token: str
    refresh_token: str
    access_token_expire_time: datetime
    refresh_token_expire_time: datetime
    session_uuid: str
    user: "UserRead"  # 前向引用，将在 __init__.py 中重建


class RefreshTokenResponse(BaseMixin):
    """刷新令牌响应 Schema"""

    access_token: str
    refresh_token: str
    access_token_expire_time: datetime
    refresh_token_expire_time: datetime
    session_uuid: str
