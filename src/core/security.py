"""
JWT 认证和密码哈希核心模块

基于 JWT 最佳实践和 OWASP 建议：
- 使用 python-jose 实现 JWT
- 使用 pwdlib (Argon2) 实现密码哈希
- 集成 Redis 缓存 Token 和黑名单机制
- 支持访问令牌和刷新令牌
- 添加 JTI (JWT ID) 用于精确撤销
- 实现 Token 黑名单和会话管理

安全特性：
1. JWT 标准声明：iss, sub, jti, iat, nbf, exp, type
2. Token 黑名单机制（支持强制登出）
3. 会话 UUID 用于多设备管理
4. Argon2 密码哈希（抗 GPU/ASIC 破解）
5. Redis 自动降级（优雅降级）
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable  # noqa: TC003
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, TypeVar, cast

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import ExpiredSignatureError, JWTError, jwt
from pwdlib import PasswordHash
from pwdlib.hashers import argon2

from src.core.conf import settings
from src.core.exceptions import AuthException, InvalidTokenException, TokenExpiredException, TokenMissingException
from src.core.logger import logger
from src.database.redis_client import get_redis, is_redis_available
from src.utils.timezone import timezone

RedisResultT = TypeVar("RedisResultT")

# Argon2 密码哈希器（推荐配置）
pwd_hasher = PasswordHash(
    [
        argon2.Argon2Hasher(
            time_cost=3,  # 迭代次数
            memory_cost=65536,  # 内存使用 (64 MB)
            parallelism=4,  # 并行线程数
        )
    ]
)


# ==================== 常量定义 ====================


class TokenType(str, Enum):
    """Token 类型枚举"""

    ACCESS = "access"
    REFRESH = "refresh"


# Redis Key 前缀
ACCESS_TOKEN_PREFIX = "auth:access_token"  # noqa: S105  # nosec B105
REFRESH_TOKEN_PREFIX = "auth:refresh_token"  # noqa: S105  # nosec B105
USER_SESSION_PREFIX = "auth:user_session"
BLACKLIST_PREFIX = "auth:blacklist"
MULTI_LOGIN_SET_PREFIX = "auth:multiple_login"


# ==================== 数据类 ====================


@dataclass(frozen=True)
class TokenPayload:
    """
    Token 载荷数据（不可变）

    遵循 JWT RFC 7519 标准声明
    """

    iss: str  # issuer (签发者)
    sub: str  # subject (用户ID)
    jti: str  # JWT ID (唯一标识符)
    iat: int  # issued at (签发时间)
    nbf: int  # not before (生效时间)
    exp: int  # expiration (过期时间)
    token_type: TokenType  # token 类型
    session_uuid: str  # 会话 UUID (多设备管理)
    is_superuser: bool = False  # 超级用户标识（性能优化：避免数据库查询）

    def to_dict(self) -> dict[str, Any]:
        """转换为字典（用于 JWT 编码）"""
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
        """从字典创建（用于 JWT 解码）"""
        token_type = TokenType(data["type"])
        return cls(
            iss=data["iss"],
            sub=data["sub"],
            jti=data["jti"],
            iat=int(data["iat"]),
            nbf=int(data["nbf"]),
            exp=int(data["exp"]),
            token_type=token_type,
            session_uuid=data["session_uuid"],
            is_superuser=data["is_superuser"],
        )


@dataclass
class AccessTokenData:
    """访问令牌数据"""

    access_token: str
    jti: str  # JWT ID (用于撤销)
    access_token_expire_time: datetime
    session_uuid: str


@dataclass
class RefreshTokenData:
    """刷新令牌数据"""

    refresh_token: str
    jti: str  # JWT ID (用于撤销)
    refresh_token_expire_time: datetime
    session_uuid: str


@dataclass
class NewTokenData:
    """新令牌数据"""

    new_access_token: str
    new_access_jti: str
    new_access_token_expire_time: datetime
    new_refresh_token: str
    new_refresh_jti: str
    new_refresh_token_expire_time: datetime
    session_uuid: str


# ==================== 密码哈希 ====================


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    验证密码（使用 Argon2）

    Args:
        plain_password: 明文密码
        hashed_password: 哈希密码

    Returns:
        验证是否成功
    """
    try:
        return pwd_hasher.verify(plain_password, hashed_password)
    except Exception as e:
        logger.warning(f"密码验证失败: {e}")
        return False


def get_password_hash(password: str) -> str:
    """
    生成密码哈希（使用 Argon2）

    Args:
        password: 明文密码

    Returns:
        哈希密码
    """
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


# ==================== JWT 工具函数 ====================


def _create_token_payload(
    user_id: int,
    token_type: TokenType,
    session_uuid: str,
    expire_seconds: int,
    issuer: str = "wes_backend",
    is_superuser: bool = False,
) -> TokenPayload:
    """
    创建 Token Payload（遵循 JWT RFC 7519）

    Args:
        user_id: 用户 ID
        token_type: Token 类型
        session_uuid: 会话 UUID
        expire_seconds: 过期时间（秒）
        issuer: 签发者
        is_superuser: 是否为超级用户（性能优化：避免数据库查询）

    Returns:
        TokenPayload 对象
    """
    now = timezone.now_utc()
    expire = now + timedelta(seconds=expire_seconds)

    return TokenPayload(
        iss=issuer,
        sub=str(user_id),
        jti=str(uuid.uuid4()),  # 唯一 ID 用于撤销
        iat=int(now.timestamp()),
        nbf=int(now.timestamp()),
        exp=int(expire.timestamp()),
        token_type=token_type,
        session_uuid=session_uuid,
        is_superuser=is_superuser,
    )


def jwt_encode(payload: TokenPayload) -> str:
    """
    生成 JWT token

    Args:
        payload: TokenPayload 对象

    Returns:
        JWT token 字符串
    """
    return jwt.encode(payload.to_dict(), settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


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
            options={
                "verify_exp": True,
                "verify_nbf": True,
                "require": ["iss", "sub", "jti", "iat", "nbf", "exp", "type", "session_uuid", "is_superuser"],
            },
        )
        return TokenPayload.from_dict(payload)
    except ExpiredSignatureError:
        raise TokenExpiredException("Token 已过期") from None
    except JWTError as e:
        logger.error(f"Token 解析失败: {e}")
        raise InvalidTokenException("Token 无效") from e
    except (KeyError, ValueError, TypeError) as e:
        logger.error(f"Token 格式错误: {e}")
        raise InvalidTokenException("Token 格式无效") from e


def _safe_user_id_from_token(token_payload: TokenPayload) -> int:
    """
    安全地将 token subject 转换为用户 ID

    Args:
        token_payload: Token payload 包含用户 ID 在 sub 字段

    Returns:
        用户 ID（整数）

    Raises:
        AuthException: 如果用户 ID 无效或无法转换
    """
    try:
        return int(token_payload.sub)
    except (ValueError, TypeError) as e:
        logger.error(f"Invalid user ID in token: {token_payload.sub}")
        raise InvalidTokenException("Token 包含无效的用户 ID") from e


# ==================== Redis 辅助函数 ====================


def _make_access_token_key(user_id: int, jti: str) -> str:
    """生成 Access Token 的 Redis Key"""
    return f"{ACCESS_TOKEN_PREFIX}:{user_id}:{jti}"


def _make_refresh_token_key(user_id: int, jti: str) -> str:
    """生成 Refresh Token 的 Redis Key"""
    return f"{REFRESH_TOKEN_PREFIX}:{user_id}:{jti}"


def _make_user_session_key(user_id: int, session_uuid: str) -> str:
    """生成用户会话的 Redis Key"""
    return f"{USER_SESSION_PREFIX}:{user_id}:{session_uuid}"


def _make_blacklist_key(jti: str) -> str:
    """生成黑名单的 Redis Key"""
    return f"{BLACKLIST_PREFIX}:{jti}"


def _make_multi_login_set_key(user_id: int | str) -> str:
    """生成多端登录 SET 的 Redis Key"""
    return f"{MULTI_LOGIN_SET_PREFIX}:{user_id}"


def _make_refresh_mapping_key(user_id: int, access_jti: str) -> str:
    """
    生成包含 user_id 的映射键，便于高效清理

    Args:
        user_id: 用户 ID
        access_jti: Access Token JTI

    Returns:
        Redis key for access_jti -> refresh_jti mapping
    """
    return f"{REFRESH_TOKEN_PREFIX}:mapping:{user_id}:{access_jti}"


async def _delete_refresh_token_and_mapping(  # pyright: ignore[reportUnusedFunction]
    redis_client: Any,
    user_id: int,
    access_jti: str,
) -> None:
    """
    删除 refresh token 及其映射（原子批量操作）

    此辅助函数整合了删除 refresh token 和相关映射的逻辑，
    用于 logout 和 revoke_session 方法。

    Args:
        redis_client: Redis 客户端实例
        user_id: 用户 ID
        access_jti: Access Token JTI（用于查找映射）

    Returns:
        None
    """
    # 获取映射键（包含 user_id 前缀）
    mapping_key = _make_refresh_mapping_key(user_id, access_jti)
    refresh_jti = await redis_client.get(mapping_key)

    if refresh_jti:
        refresh_jti_str = _decode_redis_text(refresh_jti)
        if not refresh_jti_str:
            return

        # 使用 pipeline 批量删除
        pipe = redis_client.pipeline()

        # 删除 refresh token
        refresh_key = _make_refresh_token_key(user_id, refresh_jti_str)
        pipe.delete(refresh_key)

        # 从多端登录 SET 移除
        refresh_set_key = _make_multi_login_set_key(f"{user_id}:refresh")
        pipe.srem(refresh_set_key, refresh_jti_str)

        # 删除映射
        pipe.delete(mapping_key)

        await pipe.execute()

        logger.info(f"Deleted refresh token for user {user_id}: {refresh_jti_str}")
    else:
        logger.debug(f"No refresh token mapping found for access_jti: {access_jti}")


async def _safe_redis_operation(
    operation_name: str,
    operation: Callable[[Any], Awaitable[RedisResultT]],
    fallback_result: RedisResultT | None = None,
) -> RedisResultT | None:
    """
    安全执行 Redis 操作（自动降级）

    注意：此函数会传播 AuthException，不会吞掉关键的安全异常！

    Args:
        operation_name: 操作名称（用于日志）
        operation: Redis 操作协程函数
        fallback_result: 失败时的返回值

    Returns:
        操作结果或 fallback_result

    Raises:
        AuthException: 认证异常（会传播给调用者）
    """
    if not is_redis_available():
        logger.debug(f"Redis 不可用，跳过操作: {operation_name}")
        return fallback_result

    redis_client = get_redis()
    if redis_client is None:
        logger.warning(f"Redis 客户端为 None，跳过操作: {operation_name}")
        return fallback_result

    try:
        return await operation(redis_client)
    except AuthException:
        # 关键安全异常：必须传播出去！
        raise
    except Exception as e:
        # 系统异常（网络、Redis 错误等）：记录并降级
        logger.error(f"Redis 操作失败 [{operation_name}]: {e}")
        return fallback_result


def _require_redis_client(operation_name: str) -> Any:
    """安全关键路径必须要求 Redis 可用，禁止降级放行。"""
    if not is_redis_available():
        logger.error(f"Redis 不可用，拒绝执行安全关键操作: {operation_name}")
        raise AuthException("认证服务暂时不可用，请稍后重试")

    redis_client = get_redis()
    if redis_client is None:
        logger.error(f"Redis 客户端不可用，拒绝执行安全关键操作: {operation_name}")
        raise AuthException("认证服务暂时不可用，请稍后重试")

    return redis_client


# ==================== Token 创建函数 ====================


async def create_access_token(
    user_id: int, *, multi_login: bool = True, is_superuser: bool = False, **extra_info: Any
) -> AccessTokenData:
    """
    创建访问令牌

    Args:
        user_id: 用户 ID
        multi_login: 是否允许多端登录
        **extra_info: 额外信息（存储在 Redis）

    Returns:
        AccessTokenData 对象
    """
    session_uuid = str(uuid.uuid4())
    payload = _create_token_payload(
        user_id=user_id,
        token_type=TokenType.ACCESS,
        session_uuid=session_uuid,
        expire_seconds=settings.JWT_ACCESS_TOKEN_EXPIRE_SECONDS,
        is_superuser=is_superuser,
    )
    access_token = jwt_encode(payload)

    # Redis 存储
    async def _store_redis(redis_client: Any) -> None:
        # 如果不允许多端登录，删除该用户所有旧 token
        if not multi_login:
            # 使用 SET 存储所有活跃 token 的 JTI（O(1) 查找）
            set_key = _make_multi_login_set_key(user_id)
            # 获取并删除所有旧的 access tokens
            old_jtis = await redis_client.smembers(set_key)
            if old_jtis:
                # 批量删除旧 token
                pipe = redis_client.pipeline()
                for jti in old_jtis:
                    pipe.delete(_make_access_token_key(user_id, jti))
                await pipe.execute()
                logger.info(f"删除用户 {user_id} 的 {len(old_jtis)} 个旧 access token")

            # 清空 SET 并添加新 JTI
            await redis_client.delete(set_key)
            await redis_client.sadd(set_key, payload.jti)
            await redis_client.expire(set_key, settings.JWT_ACCESS_TOKEN_EXPIRE_SECONDS)
        else:
            # 多端登录：添加到 SET
            set_key = _make_multi_login_set_key(user_id)
            await redis_client.sadd(set_key, payload.jti)
            await redis_client.expire(set_key, settings.JWT_ACCESS_TOKEN_EXPIRE_SECONDS * 2)

        # 存储新 token
        token_key = _make_access_token_key(user_id, payload.jti)
        await redis_client.setex(
            token_key,
            settings.JWT_ACCESS_TOKEN_EXPIRE_SECONDS,
            access_token,
        )

        # 存储会话信息
        session_key = _make_user_session_key(user_id, session_uuid)
        session_data = {
            "jti": payload.jti,
            "access_jti": payload.jti,  # 新增：用于映射清理
            "iat": payload.iat,
            "extra": extra_info,
        }
        await redis_client.setex(
            session_key,
            settings.JWT_ACCESS_TOKEN_EXPIRE_SECONDS,
            json.dumps(session_data, ensure_ascii=False),
        )

        logger.debug(f"创建 access token: user_id={user_id}, jti={payload.jti}")

    await _safe_redis_operation("创建 access token", _store_redis)

    return AccessTokenData(
        access_token=access_token,
        jti=payload.jti,
        access_token_expire_time=timezone.to_utc(payload.exp),
        session_uuid=session_uuid,
    )


async def create_refresh_token(
    session_uuid: str, user_id: int, *, multi_login: bool = True, access_jti: str | None = None
) -> RefreshTokenData:
    """
    创建刷新令牌

    Args:
        session_uuid: 会话 UUID
        user_id: 用户 ID
        multi_login: 是否允许多端登录
        access_jti: 关联的 access token JTI

    Returns:
        RefreshTokenData 对象
    """
    payload = _create_token_payload(
        user_id=user_id,
        token_type=TokenType.REFRESH,
        session_uuid=session_uuid,
        expire_seconds=settings.JWT_REFRESH_TOKEN_EXPIRE_SECONDS,
    )
    refresh_token = jwt_encode(payload)

    # Redis 存储
    async def _store_redis(redis_client: Any) -> None:
        # 如果不允许多端登录，删除旧 refresh tokens
        if not multi_login:
            # 使用 SET 存储所有活跃 refresh token 的 JTI
            set_key = _make_multi_login_set_key(f"{user_id}:refresh")
            old_jtis = await redis_client.smembers(set_key)
            if old_jtis:
                pipe = redis_client.pipeline()
                for jti in old_jtis:
                    pipe.delete(_make_refresh_token_key(user_id, jti))
                await pipe.execute()
                logger.info(f"删除用户 {user_id} 的 {len(old_jtis)} 个旧 refresh token")

            await redis_client.delete(set_key)
            await redis_client.sadd(set_key, payload.jti)
            await redis_client.expire(set_key, settings.JWT_REFRESH_TOKEN_EXPIRE_SECONDS)
        else:
            set_key = _make_multi_login_set_key(f"{user_id}:refresh")
            await redis_client.sadd(set_key, payload.jti)
            await redis_client.expire(set_key, settings.JWT_REFRESH_TOKEN_EXPIRE_SECONDS * 2)

        # 存储新 refresh token
        refresh_key = _make_refresh_token_key(user_id, payload.jti)
        await redis_client.setex(
            refresh_key,
            settings.JWT_REFRESH_TOKEN_EXPIRE_SECONDS,
            refresh_token,
        )

        # 如果有关联的 access_jti，建立映射
        if access_jti:
            mapping_key = _make_refresh_mapping_key(user_id, access_jti)
            await redis_client.setex(
                mapping_key,
                settings.JWT_REFRESH_TOKEN_EXPIRE_SECONDS,
                payload.jti,
            )

        logger.debug(f"创建 refresh token: user_id={user_id}, jti={payload.jti}")

    await _safe_redis_operation("创建 refresh token", _store_redis)

    return RefreshTokenData(
        refresh_token=refresh_token,
        jti=payload.jti,
        refresh_token_expire_time=timezone.to_utc(payload.exp),
        session_uuid=session_uuid,
    )


async def create_new_token(
    refresh_token: str,
    session_uuid: str,
    user_id: int,
    *,
    is_superuser: bool = False,
    multi_login: bool = True,
    **extra_info: Any,
) -> NewTokenData:
    """
    创建新令牌（刷新令牌）

    Args:
        refresh_token: 刷新令牌
        session_uuid: 会话 UUID
        user_id: 用户 ID
        is_superuser: 是否为超级用户
        multi_login: 是否允许多端登录
        **extra_info: 额外信息

    Returns:
        NewTokenData 对象

    Raises:
        AuthException: 刷新令牌无效或已过期
    """
    redis_client = _require_redis_client("刷新令牌")

    # 验证 refresh token
    token_payload = jwt_decode(refresh_token)

    # 验证 token 类型
    if token_payload.token_type != TokenType.REFRESH:
        raise InvalidTokenException("Token 类型错误，期望 refresh token")

    # 验证用户 ID 和 session_uuid
    token_user_id = _safe_user_id_from_token(token_payload)
    if token_user_id != user_id or token_payload.session_uuid != session_uuid:
        raise InvalidTokenException("Token 与用户信息不匹配")

    # 检查黑名单
    blacklist_key = _make_blacklist_key(token_payload.jti)
    if await redis_client.exists(blacklist_key):
        raise InvalidTokenException("Refresh Token 已被撤销")

    # 验证并原子性删除 Redis 中的 token（使用 GETDEL）
    stored_refresh_token = _decode_redis_text(
        await redis_client.getdel(_make_refresh_token_key(user_id, token_payload.jti))
    )
    if not stored_refresh_token or stored_refresh_token != refresh_token:
        raise InvalidTokenException("Refresh Token 已过期或不存在")

    # 删除旧令牌和相关数据
    old_session_uuid: str = token_payload.session_uuid
    old_jti: str = token_payload.jti

    # 注意：Refresh Token 已被 GETDEL 删除，无需再次删除

    # 1. 获取旧会话信息以获取旧 access_jti
    old_session_key = _make_user_session_key(user_id, old_session_uuid)
    old_access_jti = None
    old_session_data = _load_session_data(
        await redis_client.get(old_session_key),
        context="Failed to parse old session data for cleanup",
    )
    if old_session_data:
        old_access_jti = old_session_data.get("access_jti")

    # 2. 删除旧 Access Token（必须使用 access_jti，而非 refresh_jti）
    if old_access_jti:
        await redis_client.delete(_make_access_token_key(user_id, old_access_jti))
        await redis_client.srem(_make_multi_login_set_key(user_id), old_access_jti)

    # 3. 删除旧的会话信息
    await redis_client.delete(old_session_key)

    # 4. 从多端登录 SET 移除
    refresh_set_key = _make_multi_login_set_key(f"{user_id}:refresh")
    await redis_client.srem(refresh_set_key, old_jti)  # type: ignore[arg-type]

    # 5. 清理旧的 Refresh Token 映射（如果存在）
    if old_access_jti:
        old_mapping_key = _make_refresh_mapping_key(user_id, old_access_jti)
        await redis_client.delete(old_mapping_key)
        logger.debug(f"Cleaned up old mapping: {old_mapping_key}")

    # 创建新令牌
    new_access = await create_access_token(user_id, is_superuser=is_superuser, multi_login=multi_login, **extra_info)
    new_refresh = await create_refresh_token(
        new_access.session_uuid, user_id, multi_login=multi_login, access_jti=new_access.jti
    )

    logger.info(f"刷新 token 成功: user_id={user_id}, old_jti={token_payload.jti}, new_jti={new_access.jti}")

    return NewTokenData(
        new_access_token=new_access.access_token,
        new_access_jti=new_access.jti,
        new_access_token_expire_time=new_access.access_token_expire_time,
        new_refresh_token=new_refresh.refresh_token,
        new_refresh_jti=new_refresh.jti,
        new_refresh_token_expire_time=new_refresh.refresh_token_expire_time,
        session_uuid=new_access.session_uuid,
    )


async def revoke_token(user_id: int, session_uuid: str, jti: str | None = None) -> None:
    """
    撤销令牌（添加到黑名单）

    Args:
        user_id: 用户 ID
        session_uuid: 会话 UUID
        jti: JWT ID（可选，用于精确撤销）
    """

    async def _revoke(redis_client: Any) -> None:
        # 获取会话信息
        session_key = _make_user_session_key(user_id, session_uuid)
        session_data = _load_session_data(
            await redis_client.get(session_key),
            context=f"撤销 token 时解析会话失败: {session_key}",
        )

        if session_data:
            session_jti = session_data.get("jti") or jti

            if session_jti:
                # 添加到黑名单
                blacklist_key = _make_blacklist_key(session_jti)
                # 黑名单保留时间 = token 剩余有效时间 + 缓冲时间
                await redis_client.setex(blacklist_key, settings.JWT_ACCESS_TOKEN_EXPIRE_SECONDS + 300, "1")

                # 删除 access token
                await redis_client.delete(_make_access_token_key(user_id, session_jti))

                # 从多端登录 SET 中移除
                set_key = _make_multi_login_set_key(user_id)
                await redis_client.srem(set_key, session_jti)

            # 删除会话信息
            await redis_client.delete(session_key)

            logger.info(f"撤销 token 成功: user_id={user_id}, session_uuid={session_uuid}, jti={session_jti}")
        else:
            logger.warning(f"会话不存在: user_id={user_id}, session_uuid={session_uuid}")

    await _safe_redis_operation("撤销 token", _revoke)


async def revoke_all_user_tokens(user_id: int) -> int:
    """
    撤销用户所有令牌（强制登出所有设备）

    Args:
        user_id: 用户 ID

    Returns:
        撤销的 token 数量
    """

    async def _revoke_all(redis_client: Any) -> int:
        # 获取所有 access token JTI
        access_set_key = _make_multi_login_set_key(user_id)
        access_jtis = await redis_client.smembers(access_set_key)

        # 获取所有 refresh token JTI
        refresh_set_key = _make_multi_login_set_key(f"{user_id}:refresh")
        refresh_jtis = await redis_client.smembers(refresh_set_key)

        total_count = len(access_jtis) + len(refresh_jtis)

        if total_count > 0:
            pipe = redis_client.pipeline()

            # 添加所有 JTI 到黑名单
            for jti in access_jtis | refresh_jtis:
                blacklist_key = _make_blacklist_key(jti)
                pipe.setex(blacklist_key, settings.JWT_REFRESH_TOKEN_EXPIRE_SECONDS + 300, "1")

            # 删除所有 token
            for jti in access_jtis:
                pipe.delete(_make_access_token_key(user_id, jti))
            for jti in refresh_jtis:
                pipe.delete(_make_refresh_token_key(user_id, jti))

            # 删除所有 Refresh Token 映射（使用用户特定的模式）
            mapping_pattern = f"{REFRESH_TOKEN_PREFIX}:mapping:{user_id}:*"
            mapping_keys = [key async for key in redis_client.scan_iter(match=mapping_pattern)]

            # 直接删除所有用户的映射（无需逐个 GET 验证）
            if mapping_keys:
                pipe.delete(*mapping_keys)
                logger.debug(f"Deleted {len(mapping_keys)} mapping keys for user {user_id}")

            # 删除 SET
            pipe.delete(access_set_key)
            pipe.delete(refresh_set_key)

            # 删除所有会话
            session_pattern = f"{USER_SESSION_PREFIX}:{user_id}:*"
            session_keys = [key async for key in redis_client.scan_iter(match=session_pattern)]
            if session_keys:
                pipe.delete(*session_keys)

            await pipe.execute()
            logger.info(f"撤销用户所有 token: user_id={user_id}, count={total_count}")

        return total_count

    result = await _safe_redis_operation("撤销用户所有 token", _revoke_all, fallback_result=0)
    return result or 0


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
    user_id = _safe_user_id_from_token(token_payload)

    # 验证 token 类型（只接受 access token）
    if token_payload.token_type != TokenType.ACCESS:
        raise InvalidTokenException(f"无效的 token 类型: {token_payload.token_type.value}")

    redis_client = _require_redis_client("访问令牌验证")

    # 验证 Redis 中的 token
    async def _verify_redis(redis_client: Any) -> None:
        # 检查黑名单
        blacklist_key = _make_blacklist_key(token_payload.jti)
        if await redis_client.exists(blacklist_key):
            raise InvalidTokenException("Token 已被撤销")

        # 验证 token 是否存在
        stored_token = _decode_redis_text(await redis_client.get(_make_access_token_key(user_id, token_payload.jti)))
        if not stored_token or stored_token != token:
            raise InvalidTokenException("Token 已失效")

        # 获取用户额外信息
        session_key = _make_user_session_key(user_id, token_payload.session_uuid)
        session_data = _load_session_data(
            await redis_client.get(session_key),
            context=f"无法解析用户会话信息: {session_key}",
        )
        if session_data is None:
            raise InvalidTokenException("Token 会话不存在或已失效")

        extra_info = session_data.get("extra", {})
        if "username" in extra_info:
            request.state.username = extra_info["username"]
        if "email" in extra_info:
            request.state.email = extra_info["email"]

    await _verify_redis(redis_client)

    # 将用户信息附加到 request.state（性能优化：避免重复查询数据库）
    request.state.user_id = user_id
    request.state.session_uuid = token_payload.session_uuid
    request.state.jti = token_payload.jti
    request.state.is_superuser = token_payload.is_superuser

    return token_payload


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),  # pyright: ignore[reportCallInDefaultInitializer]
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
    return int(token_payload.sub) if token_payload else None


async def require_auth(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),  # pyright: ignore[reportCallInDefaultInitializer]
) -> int:
    """
    要求认证（依赖注入）

    Args:
        request: FastAPI 请求对象
        credentials: HTTP 认证凭证

    Returns:
        用户 ID

    Raises:
        TokenMissingException: 未提供认证令牌
        AuthException: Token 无效或已过期
    """
    if credentials is None:
        raise TokenMissingException("缺少访问令牌")

    token_payload = await _verify_token(credentials.credentials, request)
    return int(token_payload.sub)


# 便捷依赖注入别名
DependsAuth = Depends(require_auth)
DependsOptionalAuth = Depends(get_current_user)
