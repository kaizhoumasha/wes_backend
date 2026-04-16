"""JWT 认证 Facade。

该模块保留历史导入路径与 FastAPI 依赖入口：
- 维持 `from src.core.security import ...` 的兼容性
- 暴露 `get_current_user` / `require_auth` 依赖函数
- 将纯工具层与运行时 token 流程分别委托给子模块
"""

from __future__ import annotations

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.core import security_primitives, security_runtime
from src.core.exceptions import TokenMissingException

# ===== primitives re-export =====
ACCESS_TOKEN_PREFIX = security_primitives.ACCESS_TOKEN_PREFIX
BLACKLIST_PREFIX = security_primitives.BLACKLIST_PREFIX
MULTI_LOGIN_SET_PREFIX = security_primitives.MULTI_LOGIN_SET_PREFIX
REFRESH_TOKEN_PREFIX = security_primitives.REFRESH_TOKEN_PREFIX
USER_SESSION_PREFIX = security_primitives.USER_SESSION_PREFIX
AccessTokenData = security_primitives.AccessTokenData
NewTokenData = security_primitives.NewTokenData
RefreshTokenData = security_primitives.RefreshTokenData
TokenPayload = security_primitives.TokenPayload
TokenType = security_primitives.TokenType
_create_token_payload = security_primitives._create_token_payload  # pyright: ignore[reportPrivateUsage]
_decode_redis_text = security_primitives._decode_redis_text  # pyright: ignore[reportPrivateUsage]
_delete_refresh_token_and_mapping = security_primitives._delete_refresh_token_and_mapping  # pyright: ignore[reportPrivateUsage]
_load_session_data = security_primitives._load_session_data  # pyright: ignore[reportPrivateUsage]
_make_access_token_key = security_primitives._make_access_token_key  # pyright: ignore[reportPrivateUsage]
_make_blacklist_key = security_primitives._make_blacklist_key  # pyright: ignore[reportPrivateUsage]
_make_multi_login_set_key = security_primitives._make_multi_login_set_key  # pyright: ignore[reportPrivateUsage]
_make_refresh_mapping_key = security_primitives._make_refresh_mapping_key  # pyright: ignore[reportPrivateUsage]
_make_refresh_token_key = security_primitives._make_refresh_token_key  # pyright: ignore[reportPrivateUsage]
_make_user_session_key = security_primitives._make_user_session_key  # pyright: ignore[reportPrivateUsage]
_safe_user_id_from_token = security_primitives._safe_user_id_from_token  # pyright: ignore[reportPrivateUsage]
get_password_hash = security_primitives.get_password_hash
jwt_decode = security_primitives.jwt_decode
jwt_encode = security_primitives.jwt_encode
verify_password = security_primitives.verify_password

# ===== runtime re-export =====
_safe_redis_operation = security_runtime._safe_redis_operation  # pyright: ignore[reportPrivateUsage]
_verify_token = security_runtime._verify_token  # pyright: ignore[reportPrivateUsage]
create_access_token = security_runtime.create_access_token
create_new_token = security_runtime.create_new_token
create_refresh_token = security_runtime.create_refresh_token
revoke_all_user_tokens = security_runtime.revoke_all_user_tokens
revoke_token = security_runtime.revoke_token


security = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),  # pyright: ignore[reportCallInDefaultInitializer]
) -> int | None:
    """获取当前用户 ID（可选认证依赖）。"""
    if credentials is None:
        return None

    token_payload = await _verify_token(credentials.credentials, request)
    return int(token_payload.sub) if token_payload else None


async def require_auth(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),  # pyright: ignore[reportCallInDefaultInitializer]
) -> int:
    """要求认证（必须有 Bearer token）。"""
    if credentials is None:
        raise TokenMissingException("缺少访问令牌")

    token_payload = await _verify_token(credentials.credentials, request)
    return int(token_payload.sub)


DependsAuth = Depends(require_auth)
DependsOptionalAuth = Depends(get_current_user)


__all__ = [
    "ACCESS_TOKEN_PREFIX",
    "BLACKLIST_PREFIX",
    "MULTI_LOGIN_SET_PREFIX",
    "REFRESH_TOKEN_PREFIX",
    "USER_SESSION_PREFIX",
    "AccessTokenData",
    "DependsAuth",
    "DependsOptionalAuth",
    "NewTokenData",
    "RefreshTokenData",
    "TokenPayload",
    "TokenType",
    "_create_token_payload",
    "_decode_redis_text",
    "_delete_refresh_token_and_mapping",
    "_load_session_data",
    "_make_access_token_key",
    "_make_blacklist_key",
    "_make_multi_login_set_key",
    "_make_refresh_mapping_key",
    "_make_refresh_token_key",
    "_make_user_session_key",
    "_safe_redis_operation",
    "_safe_user_id_from_token",
    "_verify_token",
    "create_access_token",
    "create_new_token",
    "create_refresh_token",
    "get_current_user",
    "get_password_hash",
    "jwt_decode",
    "jwt_encode",
    "require_auth",
    "revoke_all_user_tokens",
    "revoke_token",
    "security",
    "verify_password",
]
