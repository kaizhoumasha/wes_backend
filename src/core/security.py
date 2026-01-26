"""
JWT 认证和密码哈希核心模块

基于 fastapi_best_architecture 设计，适配项目现有架构：
- 使用 python-jose 实现 JWT
- 使用 pwdlib (Argon2) 实现密码哈希
- 集成 Redis 缓存 Token
- 支持访问令牌和刷新令牌
"""

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import ExpiredSignatureError, JWTError, jwt
from pwdlib import PasswordHash
from pwdlib.hashers import argon2

from src.core.conf import settings
from src.core.exceptions import (
    AuthException,
)
from src.core.logger import logger
from src.core.timezone import timezone
from src.database.redis_client import get_redis, is_redis_available

# Argon2 密码哈希器
pwd_hasher = PasswordHash([argon2.Argon2Hasher()])


# ==================== 数据类 ====================


@dataclass
class TokenPayload:
    """Token 载荷数据"""

    id: int  # 用户 ID
    session_uuid: str  # 会话 UUID
    exp: int  # 过期时间戳


@dataclass
class AccessTokenData:
    """访问令牌数据"""

    access_token: str
    access_token_expire_time: datetime
    session_uuid: str


@dataclass
class RefreshTokenData:
    """刷新令牌数据"""

    refresh_token: str
    refresh_token_expire_time: datetime


@dataclass
class NewTokenData:
    """新令牌数据"""

    new_access_token: str
    new_access_token_expire_time: datetime
    new_refresh_token: str
    new_refresh_token_expire_time: datetime
    session_uuid: str


# ==================== 密码哈希 ====================


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    验证密码

    Args:
        plain_password: 明文密码
        hashed_password: 哈希密码

    Returns:
        验证是否成功
    """
    try:
        return pwd_hasher.verify(plain_password, hashed_password)
    except Exception:
        return False


def get_password_hash(password: str) -> str:
    """
    生成密码哈希

    Args:
        password: 明文密码

    Returns:
        哈希密码
    """
    return pwd_hasher.hash(password)


# ==================== JWT 工具函数 ====================


def jwt_encode(payload: dict[str, Any]) -> str:
    """
    生成 JWT token

    Args:
        payload: 载荷

    Returns:
        JWT token 字符串
    """
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def jwt_decode(token: str) -> TokenPayload:
    """
    解析 JWT token

    Args:
        token: JWT token

    Returns:
        TokenPayload 对象

    Raises:
        AuthException: Token 无效或已过期
    """
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            options={"verify_exp": True},
        )
        session_uuid = payload.get("session_uuid")
        user_id = payload.get("sub")
        expire = payload.get("exp")
        if not session_uuid or not user_id or not expire:
            raise AuthException("Token 无效")
    except ExpiredSignatureError:
        raise AuthException("Token 已过期") from None
    except (JWTError, Exception) as e:
        logger.error(f"Token 解析失败: {e}")
        raise AuthException("Token 无效") from e

    return TokenPayload(
        id=int(user_id),
        session_uuid=session_uuid,
        exp=int(expire),
    )


async def create_access_token(user_id: int, *, multi_login: bool = True, **extra_info) -> AccessTokenData:
    """
    创建访问令牌

    Args:
        user_id: 用户 ID
        multi_login: 是否允许多端登录
        **extra_info: 额外信息（存储在 Redis）

    Returns:
        AccessTokenData 对象
    """
    expire = timezone.now() + timedelta(seconds=settings.JWT_ACCESS_TOKEN_EXPIRE_SECONDS)
    session_uuid = str(uuid.uuid4())

    # 生成 JWT token
    access_token = jwt_encode(
        {
            "session_uuid": session_uuid,
            "exp": expire.timestamp(),
            "sub": str(user_id),
        }
    )

    # Redis 存储
    if is_redis_available():
        redis_client = get_redis()

        # 如果不允许多端登录，删除该用户所有旧 token
        if not multi_login:
            # 使用 scan 删除所有匹配的键
            async for key in redis_client.scan_iter(match=f"{settings.JWT_ACCESS_TOKEN_REDIS_PREFIX}:{user_id}:*"):
                await redis_client.delete(key)

        # 存储新 token
        token_key = f"{settings.JWT_ACCESS_TOKEN_REDIS_PREFIX}:{user_id}:{session_uuid}"
        await redis_client.setex(
            token_key,
            settings.JWT_ACCESS_TOKEN_EXPIRE_SECONDS,
            access_token,
        )

        # 存储额外信息（可选）
        if extra_info:
            extra_key = f"{settings.JWT_USER_REDIS_PREFIX}:{user_id}:{session_uuid}"
            await redis_client.setex(
                extra_key,
                settings.JWT_ACCESS_TOKEN_EXPIRE_SECONDS,
                json.dumps(extra_info, ensure_ascii=False),
            )

    return AccessTokenData(
        access_token=access_token,
        access_token_expire_time=expire,
        session_uuid=session_uuid,
    )


async def create_refresh_token(session_uuid: str, user_id: int, *, multi_login: bool = True) -> RefreshTokenData:
    """
    创建刷新令牌

    Args:
        session_uuid: 会话 UUID
        user_id: 用户 ID
        multi_login: 是否允许多端登录

    Returns:
        RefreshTokenData 对象
    """
    expire = timezone.now() + timedelta(seconds=settings.JWT_REFRESH_TOKEN_EXPIRE_SECONDS)
    refresh_token = jwt_encode(
        {
            "session_uuid": session_uuid,
            "exp": expire.timestamp(),
            "sub": str(user_id),
        }
    )

    if is_redis_available():
        redis_client = get_redis()

        # 如果不允许多端登录，删除旧刷新令牌
        if not multi_login:
            async for key in redis_client.scan_iter(match=f"{settings.JWT_REFRESH_TOKEN_REDIS_PREFIX}:{user_id}:*"):
                await redis_client.delete(key)

        # 存储新刷新令牌
        refresh_key = f"{settings.JWT_REFRESH_TOKEN_REDIS_PREFIX}:{user_id}:{session_uuid}"
        await redis_client.setex(
            refresh_key,
            settings.JWT_REFRESH_TOKEN_EXPIRE_SECONDS,
            refresh_token,
        )

    return RefreshTokenData(
        refresh_token=refresh_token,
        refresh_token_expire_time=expire,
    )


async def create_new_token(
    refresh_token: str,
    session_uuid: str,
    user_id: int,
    *,
    multi_login: bool = True,
    **extra_info,
) -> NewTokenData:
    """
    创建新令牌（刷新令牌）

    Args:
        refresh_token: 刷新令牌
        session_uuid: 会话 UUID
        user_id: 用户 ID
        multi_login: 是否允许多端登录
        **extra_info: 额外信息

    Returns:
        NewTokenData 对象

    Raises:
        AuthException: 刷新令牌无效或已过期
    """
    if not is_redis_available():
        raise AuthException("Redis 不可用，无法刷新令牌")

    redis_client = get_redis()

    # 验证刷新令牌
    stored_refresh_token = await redis_client.get(f"{settings.JWT_REFRESH_TOKEN_REDIS_PREFIX}:{user_id}:{session_uuid}")
    if not stored_refresh_token or stored_refresh_token != refresh_token:
        raise AuthException("Refresh Token 已过期，请重新登录")

    # 删除旧令牌
    await redis_client.delete(f"{settings.JWT_REFRESH_TOKEN_REDIS_PREFIX}:{user_id}:{session_uuid}")
    await redis_client.delete(f"{settings.JWT_ACCESS_TOKEN_REDIS_PREFIX}:{user_id}:{session_uuid}")

    # 创建新令牌
    new_access = await create_access_token(user_id, multi_login=multi_login, **extra_info)
    new_refresh = await create_refresh_token(new_access.session_uuid, user_id, multi_login=multi_login)

    return NewTokenData(
        new_access_token=new_access.access_token,
        new_access_token_expire_time=new_access.access_token_expire_time,
        new_refresh_token=new_refresh.refresh_token,
        new_refresh_token_expire_time=new_refresh.refresh_token_expire_time,
        session_uuid=new_access.session_uuid,
    )


async def revoke_token(user_id: int, session_uuid: str) -> None:
    """
    撤销令牌

    Args:
        user_id: 用户 ID
        session_uuid: 会话 UUID
    """
    if not is_redis_available():
        return

    redis_client = get_redis()
    await redis_client.delete(f"{settings.JWT_ACCESS_TOKEN_REDIS_PREFIX}:{user_id}:{session_uuid}")
    await redis_client.delete(f"{settings.JWT_USER_REDIS_PREFIX}:{user_id}:{session_uuid}")


# ==================== FastAPI 依赖注入 ====================


security = HTTPBearer(auto_error=False)


async def _verify_token(token: str, request: Request) -> TokenPayload:
    """
    验证 JWT 令牌（内部辅助函数）

    Args:
        token: JWT 令牌字符串
        request: FastAPI 请求对象

    Returns:
        TokenPayload 对象

    Raises:
        AuthException: Token 无效、已过期或已失效
    """
    token_payload = jwt_decode(token)

    # 验证 Redis 中的 token
    if is_redis_available():
        redis_client = get_redis()
        stored_token = await redis_client.get(
            f"{settings.JWT_ACCESS_TOKEN_REDIS_PREFIX}:{token_payload.id}:{token_payload.session_uuid}"
        )
        if not stored_token or stored_token != token:
            raise AuthException("Token 已失效")

        # 获取用户额外信息（username, email 等）
        extra_key = f"{settings.JWT_USER_REDIS_PREFIX}:{token_payload.id}:{token_payload.session_uuid}"
        extra_info_str = await redis_client.get(extra_key)
        if extra_info_str:
            try:
                extra_info = json.loads(extra_info_str)
                # 将 username 设置到 request.state
                if "username" in extra_info:
                    request.state.username = extra_info["username"]
                if "email" in extra_info:
                    request.state.email = extra_info["email"]
            except json.JSONDecodeError:
                logger.warning(f"无法解析用户额外信息: {extra_info_str}")

    # 将用户信息附加到 request.state
    request.state.user_id = token_payload.id
    request.state.session_uuid = token_payload.session_uuid

    return token_payload


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> int | None:
    """
    获取当前用户 ID（依赖注入）

    Args:
        request: FastAPI 请求对象
        credentials: HTTP 认证凭证

    Returns:
        用户 ID，如果未认证则返回 None

    Raises:
        AuthException: Token 无效或已过期
    """
    if credentials is None:
        return None

    token_payload = await _verify_token(credentials.credentials, request)
    return token_payload.id


async def require_auth(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(HTTPBearer()),
) -> int:
    """
    要求认证（依赖注入）

    Args:
        request: FastAPI 请求对象
        credentials: HTTP 认证凭证

    Returns:
        用户 ID

    Raises:
        AuthException: 未认证或 Token 无效
    """
    if credentials is None:
        raise AuthException("未提供认证令牌")

    token_payload = await _verify_token(credentials.credentials, request)
    return token_payload.id


# 便捷依赖注入别名
DependsAuth = Depends(require_auth)
DependsOptionalAuth = Depends(get_current_user)
