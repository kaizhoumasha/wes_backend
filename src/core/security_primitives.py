"""Security 公共原语。

承载与运行时认证流程解耦的纯工具层：
- Token 枚举与数据结构
- 密码哈希
- JWT 编解码
- Redis key 构造与会话 JSON 解析
- refresh token 清理辅助函数
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, cast

from jose import ExpiredSignatureError, JWTError, jwt
from pwdlib import PasswordHash
from pwdlib.hashers import argon2

from src.core.conf import settings
from src.core.exceptions import InvalidTokenException, TokenExpiredException
from src.core.logger import logger
from src.utils.timezone import timezone

# Argon2 密码哈希器（推荐配置）
pwd_hasher = PasswordHash(
    [
        argon2.Argon2Hasher(
            time_cost=3,
            memory_cost=65536,
            parallelism=4,
        )
    ]
)


class TokenType(str, Enum):
    """Token 类型枚举"""

    ACCESS = "access"
    REFRESH = "refresh"


ACCESS_TOKEN_PREFIX = "auth:access_token"  # noqa: S105  # nosec B105
REFRESH_TOKEN_PREFIX = "auth:refresh_token"  # noqa: S105  # nosec B105
USER_SESSION_PREFIX = "auth:user_session"
BLACKLIST_PREFIX = "auth:blacklist"
MULTI_LOGIN_SET_PREFIX = "auth:multiple_login"


@dataclass(frozen=True)
class TokenPayload:
    """Token 载荷数据（不可变）。"""

    iss: str
    sub: str
    jti: str
    iat: int
    nbf: int
    exp: int
    token_type: TokenType
    session_uuid: str
    is_superuser: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "iss": self.iss,
            "sub": self.sub,
            "jti": self.jti,
            "iat": self.iat,
            "nbf": self.nbf,
            "exp": self.exp,
            "type": self.token_type.value,
            "session_uuid": self.session_uuid,
            "is_superuser": self.is_superuser,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TokenPayload:
        return cls(
            iss=data["iss"],
            sub=data["sub"],
            jti=data["jti"],
            iat=int(data["iat"]),
            nbf=int(data["nbf"]),
            exp=int(data["exp"]),
            token_type=TokenType(data["type"]),
            session_uuid=data["session_uuid"],
            is_superuser=data["is_superuser"],
        )


@dataclass
class AccessTokenData:
    access_token: str
    jti: str
    access_token_expire_time: datetime
    session_uuid: str


@dataclass
class RefreshTokenData:
    refresh_token: str
    jti: str
    refresh_token_expire_time: datetime
    session_uuid: str


@dataclass
class NewTokenData:
    new_access_token: str
    new_access_jti: str
    new_access_token_expire_time: datetime
    new_refresh_token: str
    new_refresh_jti: str
    new_refresh_token_expire_time: datetime
    session_uuid: str


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证密码（使用 Argon2）。"""
    try:
        return pwd_hasher.verify(plain_password, hashed_password)
    except Exception as exc:
        logger.warning(f"密码验证失败: {exc}")
        return False


def get_password_hash(password: str) -> str:
    """生成密码哈希（使用 Argon2）。"""
    return pwd_hasher.hash(password)


def _decode_redis_text(value: str | bytes | None) -> str | None:
    """统一解码 Redis 返回值。"""
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value


def _load_session_data(value: str | bytes | None, *, context: str) -> dict[str, Any] | None:
    """统一解析 Redis 中的会话 JSON 数据。"""
    text = _decode_redis_text(value)
    if not text:
        return None

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning(f"{context}: JSON 解析失败")
        return None

    if not isinstance(data, dict):
        logger.warning(f"{context}: 数据格式错误")
        return None

    return cast("dict[str, Any]", data)


def _create_token_payload(
    user_id: int,
    token_type: TokenType,
    session_uuid: str,
    expire_seconds: int,
    issuer: str = "wes_backend",
    is_superuser: bool = False,
) -> TokenPayload:
    """创建 Token Payload（遵循 JWT RFC 7519）。"""
    now = timezone.now_utc()
    expire = now + timedelta(seconds=expire_seconds)

    return TokenPayload(
        iss=issuer,
        sub=str(user_id),
        jti=str(uuid.uuid4()),
        iat=int(now.timestamp()),
        nbf=int(now.timestamp()),
        exp=int(expire.timestamp()),
        token_type=token_type,
        session_uuid=session_uuid,
        is_superuser=is_superuser,
    )


def jwt_encode(payload: TokenPayload) -> str:
    """生成 JWT token。"""
    return jwt.encode(payload.to_dict(), settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def jwt_decode(token: str) -> TokenPayload:
    """解析 JWT token。"""
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            options={
                "verify_exp": True,
                "verify_nbf": True,
                "require": ["iss", "sub", "jti", "iat", "nbf", "exp", "type", "session_uuid", "is_superuser"],
            },
        )
        return TokenPayload.from_dict(payload)
    except ExpiredSignatureError:
        raise TokenExpiredException("Token 已过期") from None
    except JWTError as exc:
        logger.error(f"Token 解析失败: {exc}")
        raise InvalidTokenException("Token 无效") from exc
    except (KeyError, ValueError, TypeError) as exc:
        logger.error(f"Token 格式错误: {exc}")
        raise InvalidTokenException("Token 格式无效") from exc


def _safe_user_id_from_token(token_payload: TokenPayload) -> int:
    """安全地将 token subject 转换为用户 ID。"""
    try:
        return int(token_payload.sub)
    except (ValueError, TypeError) as exc:
        logger.error(f"Invalid user ID in token: {token_payload.sub}")
        raise InvalidTokenException("Token 包含无效的用户 ID") from exc


def _make_access_token_key(user_id: int, jti: str) -> str:
    return f"{ACCESS_TOKEN_PREFIX}:{user_id}:{jti}"


def _make_refresh_token_key(user_id: int, jti: str) -> str:
    return f"{REFRESH_TOKEN_PREFIX}:{user_id}:{jti}"


def _make_user_session_key(user_id: int, session_uuid: str) -> str:
    return f"{USER_SESSION_PREFIX}:{user_id}:{session_uuid}"


def _make_blacklist_key(jti: str) -> str:
    return f"{BLACKLIST_PREFIX}:{jti}"


def _make_multi_login_set_key(user_id: int | str) -> str:
    return f"{MULTI_LOGIN_SET_PREFIX}:{user_id}"


def _make_refresh_mapping_key(user_id: int, access_jti: str) -> str:
    return f"{REFRESH_TOKEN_PREFIX}:mapping:{user_id}:{access_jti}"


async def _delete_refresh_token_and_mapping(redis_client: Any, user_id: int, access_jti: str) -> None:
    """删除 refresh token 及其映射（原子批量操作）。"""
    mapping_key = _make_refresh_mapping_key(user_id, access_jti)
    refresh_jti = await redis_client.get(mapping_key)

    if refresh_jti:
        refresh_jti_str = _decode_redis_text(refresh_jti)
        if not refresh_jti_str:
            return

        pipe = redis_client.pipeline()
        pipe.delete(_make_refresh_token_key(user_id, refresh_jti_str))
        pipe.srem(_make_multi_login_set_key(f"{user_id}:refresh"), refresh_jti_str)
        pipe.delete(mapping_key)
        await pipe.execute()

        logger.info(f"Deleted refresh token for user {user_id}: {refresh_jti_str}")
        return

    logger.debug(f"No refresh token mapping found for access_jti: {access_jti}")


__all__ = [
    "ACCESS_TOKEN_PREFIX",
    "BLACKLIST_PREFIX",
    "MULTI_LOGIN_SET_PREFIX",
    "REFRESH_TOKEN_PREFIX",
    "USER_SESSION_PREFIX",
    "AccessTokenData",
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
    "_safe_user_id_from_token",
    "get_password_hash",
    "jwt_decode",
    "jwt_encode",
    "verify_password",
]
