"""
认证服务层

处理用户认证相关的业务逻辑：
- 用户登录
- 用户登出
- 令牌刷新
- 会话管理
- 强制登出所有设备
"""

import json

from fastapi import Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.app.admin.models import User, UserResponse
from src.app.auth.models import (
    ActiveSessionsResponse,
    LoginResponse,
    RefreshTokenResponse,
    SessionInfo,
)
from src.core.conf import settings
from src.core.exceptions import (
    AuthException,
    InvalidCredentialsException,
    InvalidTokenException,
    TokenMissingException,
)
from src.core.logger import logger
from src.core.security import (
    MULTI_LOGIN_SET_PREFIX,
    REFRESH_TOKEN_PREFIX,
    USER_SESSION_PREFIX,
    TokenType,
    create_access_token,
    create_new_token,
    create_refresh_token,
    jwt_decode,
    revoke_all_user_tokens,
    revoke_token,
    verify_password,
)
from src.database.redis_client import get_redis, is_redis_available
from src.utils.timezone import timezone


class AuthService:
    """认证服务类"""

    @staticmethod
    def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
        """
        设置刷新令牌 Cookie（统一策略）

        注意：只使用 max_age，不设置 expires，避免时区兼容问题。
        """
        response.set_cookie(
            key="refresh_token",
            value=refresh_token,
            max_age=settings.JWT_REFRESH_TOKEN_EXPIRE_SECONDS,
            httponly=True,
            secure=settings.COOKIE_SECURE_EFFECTIVE,
            samesite=settings.COOKIE_SAMESITE,
        )

    @staticmethod
    def _clear_refresh_cookie(response: Response) -> None:
        """删除刷新令牌 Cookie（与设置参数保持一致）"""
        response.delete_cookie(
            key="refresh_token",
            secure=settings.COOKIE_SECURE_EFFECTIVE,
            httponly=True,
            samesite=settings.COOKIE_SAMESITE,
        )

    @staticmethod
    async def verify_user(db: AsyncSession, username: str, password: str) -> User:
        """
        验证用户凭证

        Args:
            db: 数据库会话
            username: 用户名
            password: 密码

        Returns:
            User 对象

        Raises:
            InvalidCredentialsException: 用户名或密码错误
            AuthException: 用户被禁用
        """
        # 查询用户（预加载角色）
        result = await db.execute(
            select(User).where(User.username == username).options(selectinload(User.roles))  # type: ignore[arg-type]
        )
        user = result.scalar_one_or_none()

        if not user:
            raise InvalidCredentialsException("用户名或密码错误")

        # 检查用户状态（is_deleted=True 表示已删除/禁用）
        if user.is_deleted:
            raise AuthException("用户已被禁用")

        # 验证密码
        if not verify_password(password, user.hashed_password):
            logger.warning(f"用户 {username} 密码错误")
            raise InvalidCredentialsException("用户名或密码错误")

        logger.info(f"用户 {username} 登录成功")
        return user

    @staticmethod
    async def login(
        db: AsyncSession,
        username: str,
        password: str,
        response: Response,
    ) -> LoginResponse:
        """
        用户登录

        Args:
            db: 数据库会话
            username: 用户名
            password: 密码
            response: FastAPI 响应对象

        Returns:
            LoginResponse 对象

        Raises:
            InvalidCredentialsException: 用户名或密码错误
            AuthException: 用户被禁用
        """
        # 验证用户
        user = await AuthService.verify_user(db, username, password)

        # 提交事务（updated_at 会通过 SQLAlchemy 事件自动更新）
        await db.commit()

        # 验证用户 ID 有效性
        if user.id is None:
            logger.error(f"User {username} has no ID after database query")
            raise AuthException("用户数据异常，请联系管理员")

        # 创建访问令牌（包含 is_superuser 以避免后续数据库查询）
        access_data = await create_access_token(
            user.id,
            is_superuser=user.is_superuser,
            multi_login=user.is_multi_login,
            username=user.username,
            email=user.email,
        )

        # 创建刷新令牌（传入 access token JTI 建立关联）
        refresh_data = await create_refresh_token(
            session_uuid=access_data.session_uuid,
            user_id=user.id,
            multi_login=user.is_multi_login,
            access_jti=access_data.jti,
        )

        # 设置刷新令牌到 HttpOnly Cookie
        AuthService._set_refresh_cookie(response, refresh_data.refresh_token)

        # 构建响应
        return LoginResponse(
            access_token=access_data.access_token,
            refresh_token=refresh_data.refresh_token,
            access_token_jti=access_data.jti,
            refresh_token_jti=refresh_data.jti,
            access_token_expire_time=access_data.access_token_expire_time,
            refresh_token_expire_time=refresh_data.refresh_token_expire_time,
            session_uuid=access_data.session_uuid,
            user=AuthService._user_to_response(user),
        )

    @staticmethod
    async def refresh_token(
        db: AsyncSession,
        request: Request,
        response: Response,
    ) -> RefreshTokenResponse:
        """
        刷新访问令牌

        Args:
            db: 数据库会话
            request: FastAPI 请求对象
            response: FastAPI 响应对象

        Returns:
            RefreshTokenResponse 对象

        Raises:
            TokenMissingException: 缺少 Refresh Token
            AuthException: 刷新令牌无效或已过期
        """
        # 从 Cookie 获取刷新令牌
        refresh_token = request.cookies.get("refresh_token")
        if not refresh_token:
            raise TokenMissingException("Refresh Token 不存在，请重新登录")

        # 解码刷新令牌（保留具体异常语义：无效/过期）
        token_payload = jwt_decode(refresh_token)

        # 验证 token 类型
        if token_payload.token_type != TokenType.REFRESH:
            raise InvalidTokenException("Token 类型错误")

        # 获取用户 ID（安全类型转换）
        from src.core.security import _safe_user_id_from_token

        user_id = _safe_user_id_from_token(token_payload)

        # 查询用户（预加载角色）
        result = await db.execute(
            select(User).where(User.id == user_id).options(selectinload(User.roles))  # type: ignore[arg-type]
        )
        user = result.scalar_one_or_none()

        if not user:
            raise InvalidTokenException("Refresh Token 对应用户不存在")

        # 检查用户状态（is_deleted=True 表示已删除/禁用）
        if user.is_deleted:
            raise AuthException("用户已被禁用")

        # 验证用户 ID 有效性
        if user.id is None:
            logger.error(f"User ID {user_id} has no ID after database query")
            raise AuthException("用户数据异常，请联系管理员")

        # 创建新令牌（包含 is_superuser 以避免后续数据库查询）
        new_token_data = await create_new_token(
            refresh_token=refresh_token,
            session_uuid=token_payload.session_uuid,
            user_id=user.id,
            is_superuser=user.is_superuser,
            multi_login=user.is_multi_login,
            username=user.username,
            email=user.email,
        )

        # 更新 Cookie 中的刷新令牌
        AuthService._set_refresh_cookie(response, new_token_data.new_refresh_token)

        return RefreshTokenResponse(
            access_token=new_token_data.new_access_token,
            refresh_token=new_token_data.new_refresh_token,
            access_token_jti=new_token_data.new_access_jti,
            refresh_token_jti=new_token_data.new_refresh_jti,
            access_token_expire_time=new_token_data.new_access_token_expire_time,
            refresh_token_expire_time=new_token_data.new_refresh_token_expire_time,
            session_uuid=new_token_data.session_uuid,
        )

    @staticmethod
    async def logout(request: Request, response: Response) -> int:
        """
        用户登出（撤销当前会话）

        Args:
            request: FastAPI 请求对象
            response: FastAPI 响应对象

        Returns:
            撤销的令牌数量（0 或 1）
        """
        revoked_count = 0

        if not is_redis_available():
            logger.warning("Redis 不可用，登出时无法清理令牌")
            AuthService._clear_refresh_cookie(response)
            return revoked_count

        redis_client = get_redis()
        if redis_client is None:
            logger.warning("Redis 连接失败，登出时无法清理令牌")
            AuthService._clear_refresh_cookie(response)
            return revoked_count

        # 1) 优先使用 Access Token 撤销
        try:
            auth_header = request.headers.get("Authorization")
            if auth_header and auth_header.startswith("Bearer "):
                token = auth_header.split(" ")[1]
                token_payload = jwt_decode(token)
                if token_payload.token_type == TokenType.ACCESS:
                    from src.core.security import _delete_refresh_token_and_mapping, _safe_user_id_from_token

                    current_user_id = _safe_user_id_from_token(token_payload)

                    # 删除关联的 Refresh Token
                    await _delete_refresh_token_and_mapping(redis_client, current_user_id, token_payload.jti)

                    # 撤销 Access Token（包括黑名单、会话删除等）
                    await revoke_token(
                        user_id=current_user_id,
                        session_uuid=token_payload.session_uuid,
                        jti=token_payload.jti,
                    )
                    revoked_count = 1
                    logger.info(f"用户 {current_user_id} 登出成功，撤销 session: {token_payload.session_uuid}")
                else:
                    logger.warning("登出时收到非 access token 的 Authorization 头，已忽略")
        except Exception as e:
            logger.warning(f"登出时撤销访问令牌失败: {e}")

        # 2) 无可用 Access Token 时，回退使用 Refresh Token Cookie 撤销
        if revoked_count == 0:
            refresh_token = request.cookies.get("refresh_token")
            if refresh_token:
                try:
                    refresh_payload = jwt_decode(refresh_token)
                    if refresh_payload.token_type != TokenType.REFRESH:
                        raise InvalidTokenException("Refresh Token 类型错误")

                    from src.core.security import _delete_refresh_token_and_mapping, _safe_user_id_from_token

                    user_id = _safe_user_id_from_token(refresh_payload)
                    access_jti: str | None = None

                    # 读取会话中的 access_jti（若存在）
                    session_key = f"{USER_SESSION_PREFIX}:{user_id}:{refresh_payload.session_uuid}"
                    session_data_str = await redis_client.get(session_key)
                    if session_data_str:
                        try:
                            session_data = json.loads(session_data_str)
                            access_jti = session_data.get("access_jti") or session_data.get("jti")
                        except json.JSONDecodeError:
                            logger.warning(f"登出时解析会话数据失败: {session_key}")

                    # 如果有 access_jti，则先尝试按映射清理
                    if access_jti:
                        await _delete_refresh_token_and_mapping(redis_client, user_id, access_jti)

                    # 显式删除当前 refresh token（兜底，保持幂等）
                    refresh_key = f"{REFRESH_TOKEN_PREFIX}:{user_id}:{refresh_payload.jti}"
                    refresh_set_key = f"{MULTI_LOGIN_SET_PREFIX}:{user_id}:refresh"
                    await redis_client.delete(refresh_key)
                    await redis_client.srem(refresh_set_key, refresh_payload.jti)

                    await revoke_token(
                        user_id=user_id,
                        session_uuid=refresh_payload.session_uuid,
                        jti=access_jti,
                    )
                    revoked_count = 1
                    logger.info(
                        f"用户 {user_id} 通过 Refresh Token 登出成功，撤销 session: {refresh_payload.session_uuid}"
                    )
                except Exception as e:
                    logger.warning(f"登出时通过 Refresh Token 撤销失败: {e}")

        # 始终删除刷新令牌 Cookie（幂等）
        AuthService._clear_refresh_cookie(response)
        return revoked_count

    @staticmethod
    async def logout_all(response: Response, current_user_id: int) -> int:
        """
        强制登出所有设备（撤销所有会话）

        Args:
            response: FastAPI 响应对象
            current_user_id: 当前用户 ID

        Returns:
            撤销的令牌数量
        """
        # 撤销用户所有令牌
        revoked_count = await revoke_all_user_tokens(current_user_id)

        # 删除刷新令牌 Cookie
        AuthService._clear_refresh_cookie(response)

        logger.info(f"用户 {current_user_id} 强制登出所有设备，撤销 {revoked_count} 个令牌")
        return revoked_count

    @staticmethod
    async def get_active_sessions(current_user_id: int) -> ActiveSessionsResponse:
        """
        获取用户的所有活跃会话

        Args:
            current_user_id: 当前用户 ID

        Returns:
            ActiveSessionsResponse 对象
        """
        sessions: list[SessionInfo] = []

        if not is_redis_available():
            return ActiveSessionsResponse(total=0, sessions=[])

        redis_client = get_redis()
        if redis_client is None:
            return ActiveSessionsResponse(total=0, sessions=[])

        try:
            # 扫描用户的所有会话
            session_pattern = f"{USER_SESSION_PREFIX}:{current_user_id}:*"
            session_keys = []

            # 使用 scan_iter 获取所有匹配的键
            session_keys = [key async for key in redis_client.scan_iter(match=session_pattern)]

            # 批量获取会话数据
            if session_keys:
                session_data_list = await redis_client.mget(session_keys)

                for key, data_str in zip(session_keys, session_data_list, strict=False):
                    if data_str:
                        try:
                            session_data = json.loads(data_str)

                            # 提取 session_uuid（从 key 中解析）
                            # key 格式: auth:user_session:{user_id}:{session_uuid}
                            session_uuid = key.split(":")[-1]

                            # 解析时间戳
                            iat = session_data.get("iat", 0)
                            created_at = timezone.to_utc(iat)

                            # 获取额外信息
                            extra = session_data.get("extra", {})
                            username = extra.get("username", "Unknown")
                            email = extra.get("email", "")

                            # 构建设备信息
                            device_info = {
                                "username": username,
                                "email": email,
                            }

                            sessions.append(
                                SessionInfo(
                                    session_uuid=session_uuid,
                                    jti=session_data.get("jti", ""),
                                    created_at=created_at,
                                    device_info=device_info,
                                    last_active=created_at,  # TODO: 可以添加最后活跃时间追踪
                                )
                            )

                        except (json.JSONDecodeError, ValueError, KeyError) as e:
                            logger.warning(f"解析会话数据失败 [{key}]: {e}")
                            continue

            # 按创建时间倒序排列
            sessions.sort(key=lambda s: s.created_at, reverse=True)

            logger.info(f"用户 {current_user_id} 有 {len(sessions)} 个活跃会话")
            return ActiveSessionsResponse(total=len(sessions), sessions=sessions)

        except Exception as e:
            logger.error(f"获取活跃会话失败: {e}")
            return ActiveSessionsResponse(total=0, sessions=[])

    @staticmethod
    async def revoke_session(current_user_id: int, session_uuid: str) -> bool:
        """
        撤销指定会话

        Args:
            current_user_id: 当前用户 ID
            session_uuid: 会话 UUID

        Returns:
            是否成功撤销
        """
        if not is_redis_available():
            raise AuthException("Redis 不可用，无法撤销会话")

        redis_client = get_redis()
        if redis_client is None:
            raise AuthException("Redis 连接失败，无法撤销会话")

        try:
            # 获取会话信息
            session_key = f"{USER_SESSION_PREFIX}:{current_user_id}:{session_uuid}"
            session_data_str = await redis_client.get(session_key)

            if not session_data_str:
                raise AuthException("会话不存在")

            session_data = json.loads(session_data_str)
            access_jti = session_data.get("jti")

            if not access_jti:
                raise AuthException("会话数据无效")

            # 1. 删除关联的 Refresh Token（使用辅助函数）
            from src.core.security import _delete_refresh_token_and_mapping

            await _delete_refresh_token_and_mapping(
                redis_client,
                current_user_id,
                access_jti,
            )

            # 2. 撤销 Access Token（包括黑名单、会话删除等）
            await revoke_token(user_id=current_user_id, session_uuid=session_uuid, jti=access_jti)

            logger.info(f"用户 {current_user_id} 撤销会话: {session_uuid}, access_jti: {access_jti}")
            return True

        except AuthException:
            raise
        except Exception as e:
            logger.error(f"撤销会话失败: {e}")
            raise AuthException("撤销会话失败") from e

    @staticmethod
    async def get_user_profile(db: AsyncSession, user_id: int) -> UserResponse:
        """
        获取当前用户信息

        Args:
            db: 数据库会话
            user_id: 用户 ID

        Returns:
            UserResponse 对象
        """
        result = await db.execute(
            select(User).where(User.id == user_id).options(selectinload(User.roles))  # type: ignore[arg-type]
        )
        user = result.scalar_one_or_none()

        if not user or user.is_deleted:
            raise AuthException("用户不存在或已被禁用")

        return AuthService._user_to_response(user)

    @staticmethod
    def _user_to_response(user: User) -> UserResponse:
        """
        将 User 对象转换为 UserResponse

        Args:
            user: User 对象

        Returns:
            UserResponse 对象
        """
        return UserResponse(
            id=user.id,  # type: ignore[arg-type]
            version=user.version,  # type: ignore[attr-defined]
            username=user.username,
            email=user.email,
            full_name=user.full_name,
            is_superuser=user.is_superuser,
            is_multi_login=user.is_multi_login,
            created_at=user.created_at,  # type: ignore[attr-defined]
            updated_at=user.updated_at,  # type: ignore[attr-defined]
            roles=[],  # roles 会通过预加载自动填充
        )


# 创建服务实例
auth_service = AuthService()
