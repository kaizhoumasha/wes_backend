"""Security 运行时逻辑。

承载依赖 Redis / Request 上下文的认证流程：
- access / refresh token 创建
- token 刷新与撤销
- access token 验证
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from fastapi import Request

from src.core.conf import settings
from src.core.exceptions import AuthException, InvalidTokenException
from src.core.logger import logger
from src.core.security_primitives import (
    REFRESH_TOKEN_PREFIX,
    USER_SESSION_PREFIX,
    AccessTokenData,
    NewTokenData,
    RefreshTokenData,
    TokenPayload,
    TokenType,
    _create_token_payload,
    _decode_redis_text,
    _load_session_data,
    _make_access_token_key,
    _make_blacklist_key,
    _make_multi_login_set_key,
    _make_refresh_mapping_key,
    _make_refresh_token_key,
    _make_user_session_key,
    _safe_user_id_from_token,
    jwt_decode,
    jwt_encode,
)
from src.database.redis_client import ensure_redis_connection, get_redis, is_redis_available
from src.utils.timezone import timezone

RedisResultT = TypeVar("RedisResultT")


async def _redis_client_with_reconnect() -> Any | None:
    """认证操作按需触发 Redis single-flight 重连。"""
    if (not is_redis_available() or get_redis() is None) and not await ensure_redis_connection():
        return None
    return get_redis()


async def _safe_redis_operation(
    operation_name: str,
    operation: Callable[[Any], Awaitable[RedisResultT]],
    fallback_result: RedisResultT | None = None,
) -> RedisResultT | None:
    """安全执行 Redis 操作（自动降级）。"""
    redis_client = await _redis_client_with_reconnect()
    if redis_client is None:
        logger.debug(f"Redis 不可用，跳过操作: {operation_name}")
        return fallback_result

    try:
        return await operation(redis_client)
    except AuthException:
        raise
    except Exception as exc:
        logger.error(f"Redis 操作失败 [{operation_name}]: {exc}")
        return fallback_result


async def _require_redis_client(operation_name: str) -> Any:
    """安全关键路径必须要求 Redis 可用，禁止降级放行。"""
    redis_client = await _redis_client_with_reconnect()
    if redis_client is None:
        logger.error(f"Redis 客户端不可用，拒绝执行安全关键操作: {operation_name}")
        raise AuthException("认证服务暂时不可用，请稍后重试")

    return redis_client


async def create_access_token(
    user_id: int,
    *,
    multi_login: bool = True,
    is_superuser: bool = False,
    **extra_info: Any,
) -> AccessTokenData:
    """创建访问令牌。"""
    session_uuid = str(uuid.uuid4())
    payload = _create_token_payload(
        user_id=user_id,
        token_type=TokenType.ACCESS,
        session_uuid=session_uuid,
        expire_seconds=settings.JWT_ACCESS_TOKEN_EXPIRE_SECONDS,
        is_superuser=is_superuser,
    )
    access_token = jwt_encode(payload)

    async def _store_redis(redis_client: Any) -> None:
        if not multi_login:
            set_key = _make_multi_login_set_key(user_id)
            old_jtis = await redis_client.smembers(set_key)
            if old_jtis:
                pipe = redis_client.pipeline()
                for jti in old_jtis:
                    pipe.delete(_make_access_token_key(user_id, jti))
                await pipe.execute()
                logger.info(f"删除用户 {user_id} 的 {len(old_jtis)} 个旧 access token")

            await redis_client.delete(set_key)
            await redis_client.sadd(set_key, payload.jti)
            await redis_client.expire(set_key, settings.JWT_ACCESS_TOKEN_EXPIRE_SECONDS)
        else:
            set_key = _make_multi_login_set_key(user_id)
            await redis_client.sadd(set_key, payload.jti)
            await redis_client.expire(set_key, settings.JWT_ACCESS_TOKEN_EXPIRE_SECONDS * 2)

        await redis_client.setex(
            _make_access_token_key(user_id, payload.jti),
            settings.JWT_ACCESS_TOKEN_EXPIRE_SECONDS,
            access_token,
        )

        session_data = {
            "jti": payload.jti,
            "access_jti": payload.jti,
            "iat": payload.iat,
            "extra": extra_info,
        }
        await redis_client.setex(
            _make_user_session_key(user_id, session_uuid),
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
    session_uuid: str,
    user_id: int,
    *,
    multi_login: bool = True,
    access_jti: str | None = None,
) -> RefreshTokenData:
    """创建刷新令牌。"""
    payload = _create_token_payload(
        user_id=user_id,
        token_type=TokenType.REFRESH,
        session_uuid=session_uuid,
        expire_seconds=settings.JWT_REFRESH_TOKEN_EXPIRE_SECONDS,
    )
    refresh_token = jwt_encode(payload)

    async def _store_redis(redis_client: Any) -> None:
        set_key = _make_multi_login_set_key(f"{user_id}:refresh")
        if not multi_login:
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
            await redis_client.sadd(set_key, payload.jti)
            await redis_client.expire(set_key, settings.JWT_REFRESH_TOKEN_EXPIRE_SECONDS * 2)

        await redis_client.setex(
            _make_refresh_token_key(user_id, payload.jti),
            settings.JWT_REFRESH_TOKEN_EXPIRE_SECONDS,
            refresh_token,
        )

        if access_jti:
            await redis_client.setex(
                _make_refresh_mapping_key(user_id, access_jti),
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
    """创建新令牌（刷新令牌）。"""
    redis_client = await _require_redis_client("刷新令牌")

    token_payload = jwt_decode(refresh_token)
    if token_payload.token_type != TokenType.REFRESH:
        raise InvalidTokenException("Token 类型错误，期望 refresh token")

    token_user_id = _safe_user_id_from_token(token_payload)
    if token_user_id != user_id or token_payload.session_uuid != session_uuid:
        raise InvalidTokenException("Token 与用户信息不匹配")

    if await redis_client.exists(_make_blacklist_key(token_payload.jti)):
        raise InvalidTokenException("Refresh Token 已被撤销")

    stored_refresh_token = _decode_redis_text(
        await redis_client.getdel(_make_refresh_token_key(user_id, token_payload.jti))
    )
    if not stored_refresh_token or stored_refresh_token != refresh_token:
        raise InvalidTokenException("Refresh Token 已过期或不存在")

    old_session_uuid = token_payload.session_uuid
    old_jti = token_payload.jti
    old_session_key = _make_user_session_key(user_id, old_session_uuid)
    old_access_jti = None
    old_session_data = _load_session_data(
        await redis_client.get(old_session_key),
        context="Failed to parse old session data for cleanup",
    )
    if old_session_data:
        old_access_jti = old_session_data.get("access_jti")

    if old_access_jti:
        await redis_client.delete(_make_access_token_key(user_id, old_access_jti))
        await redis_client.srem(_make_multi_login_set_key(user_id), old_access_jti)

    await redis_client.delete(old_session_key)
    await redis_client.srem(_make_multi_login_set_key(f"{user_id}:refresh"), old_jti)  # type: ignore[arg-type]

    if old_access_jti:
        old_mapping_key = _make_refresh_mapping_key(user_id, old_access_jti)
        await redis_client.delete(old_mapping_key)
        logger.debug(f"Cleaned up old mapping: {old_mapping_key}")

    new_access = await create_access_token(user_id, is_superuser=is_superuser, multi_login=multi_login, **extra_info)
    new_refresh = await create_refresh_token(
        new_access.session_uuid,
        user_id,
        multi_login=multi_login,
        access_jti=new_access.jti,
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
    """撤销令牌（添加到黑名单）。"""

    async def _revoke(redis_client: Any) -> None:
        session_key = _make_user_session_key(user_id, session_uuid)
        session_data = _load_session_data(
            await redis_client.get(session_key),
            context=f"撤销 token 时解析会话失败: {session_key}",
        )

        if session_data:
            session_jti = session_data.get("jti") or jti
            if session_jti:
                await redis_client.setex(
                    _make_blacklist_key(session_jti),
                    settings.JWT_ACCESS_TOKEN_EXPIRE_SECONDS + 300,
                    "1",
                )
                await redis_client.delete(_make_access_token_key(user_id, session_jti))
                await redis_client.srem(_make_multi_login_set_key(user_id), session_jti)

            await redis_client.delete(session_key)
            logger.info(f"撤销 token 成功: user_id={user_id}, session_uuid={session_uuid}, jti={session_jti}")
            return

        logger.warning(f"会话不存在: user_id={user_id}, session_uuid={session_uuid}")

    await _safe_redis_operation("撤销 token", _revoke)


async def revoke_all_user_tokens(user_id: int) -> int:
    """撤销用户所有令牌（强制登出所有设备）。"""

    async def _revoke_all(redis_client: Any) -> int:
        access_set_key = _make_multi_login_set_key(user_id)
        access_jtis = await redis_client.smembers(access_set_key)
        refresh_set_key = _make_multi_login_set_key(f"{user_id}:refresh")
        refresh_jtis = await redis_client.smembers(refresh_set_key)
        total_count = len(access_jtis) + len(refresh_jtis)

        if total_count > 0:
            pipe = redis_client.pipeline()

            for jti in access_jtis | refresh_jtis:
                pipe.setex(_make_blacklist_key(jti), settings.JWT_REFRESH_TOKEN_EXPIRE_SECONDS + 300, "1")

            for jti in access_jtis:
                pipe.delete(_make_access_token_key(user_id, jti))
            for jti in refresh_jtis:
                pipe.delete(_make_refresh_token_key(user_id, jti))

            mapping_pattern = f"{REFRESH_TOKEN_PREFIX}:mapping:{user_id}:*"
            mapping_keys = [key async for key in redis_client.scan_iter(match=mapping_pattern)]
            if mapping_keys:
                pipe.delete(*mapping_keys)
                logger.debug(f"Deleted {len(mapping_keys)} mapping keys for user {user_id}")

            pipe.delete(access_set_key)
            pipe.delete(refresh_set_key)

            session_pattern = f"{USER_SESSION_PREFIX}:{user_id}:*"
            session_keys = [key async for key in redis_client.scan_iter(match=session_pattern)]
            if session_keys:
                pipe.delete(*session_keys)

            await pipe.execute()
            logger.info(f"撤销用户所有 token: user_id={user_id}, count={total_count}")

        return total_count

    result = await _safe_redis_operation("撤销用户所有 token", _revoke_all, fallback_result=0)
    return result or 0


async def _verify_token(token: str, request: Request) -> TokenPayload:
    """验证 JWT 令牌（内部辅助函数）。"""
    token_payload = jwt_decode(token)
    user_id = _safe_user_id_from_token(token_payload)

    if token_payload.token_type != TokenType.ACCESS:
        raise InvalidTokenException(f"无效的 token 类型: {token_payload.token_type.value}")

    redis_client = await _require_redis_client("访问令牌验证")

    async def _verify_redis(redis_client: Any) -> None:
        if await redis_client.exists(_make_blacklist_key(token_payload.jti)):
            raise InvalidTokenException("Token 已被撤销")

        stored_token = _decode_redis_text(await redis_client.get(_make_access_token_key(user_id, token_payload.jti)))
        if not stored_token or stored_token != token:
            raise InvalidTokenException("Token 已失效")

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

    request.state.user_id = user_id
    request.state.session_uuid = token_payload.session_uuid
    request.state.jti = token_payload.jti
    request.state.is_superuser = token_payload.is_superuser
    return token_payload


__all__ = [
    "AccessTokenData",
    "NewTokenData",
    "RefreshTokenData",
    "TokenPayload",
    "TokenType",
    "_safe_redis_operation",
    "_verify_token",
    "create_access_token",
    "create_new_token",
    "create_refresh_token",
    "revoke_all_user_tokens",
    "revoke_token",
]
