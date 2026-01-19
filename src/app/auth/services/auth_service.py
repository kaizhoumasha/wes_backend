"""
认证服务层

处理用户认证相关的业务逻辑：
- 用户登录
- 用户登出
- 令牌刷新
- 用户验证
"""

from fastapi import Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.app.admin.models import LoginResponse, RefreshTokenResponse, User
from src.core.conf import settings
from src.core.exceptions import AuthException, NotFoundException
from src.core.logger import logger
from src.core.security import (
    create_access_token,
    create_new_token,
    create_refresh_token,
    jwt_decode,
    revoke_token,
    verify_password,
)
from src.database.redis_client import get_redis, is_redis_available


class AuthService:
    """认证服务类"""

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
            NotFoundException: 用户不存在
            AuthException: 密码错误或用户被禁用
        """
        # 查询用户（预加载角色）
        result = await db.execute(select(User).where(User.username == username).options(selectinload(User.roles)))
        user = result.scalar_one_or_none()

        if not user:
            raise NotFoundException("用户名或密码错误")

        # 检查用户状态
        if not user.is_active:
            raise AuthException("用户已被禁用")

        # 验证密码
        if not verify_password(password, user.hashed_password):
            logger.warning(f"用户 {username} 密码错误")
            raise AuthException("用户名或密码错误")

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
            NotFoundException: 用户不存在
            AuthException: 密码错误或用户被禁用
        """
        # 验证用户
        user = await AuthService.verify_user(db, username, password)

        # 提交事务（updated_at 会通过 SQLAlchemy 事件自动更新）
        await db.commit()

        # 创建访问令牌
        access_data = await create_access_token(
            user.id,
            multi_login=user.is_multi_login,
            username=user.username,
            email=user.email,
        )

        # 创建刷新令牌
        refresh_data = await create_refresh_token(
            access_data.session_uuid,
            user.id,
            multi_login=user.is_multi_login,
        )

        # 设置刷新令牌到 HttpOnly Cookie
        # 注意：只使用 max_age，不设置 expires，避免时区问题
        response.set_cookie(
            key="refresh_token",
            value=refresh_data.refresh_token,
            max_age=settings.JWT_REFRESH_TOKEN_EXPIRE_SECONDS,
            httponly=True,
            secure=not settings.APP_DEBUG,  # 生产环境使用 HTTPS
            samesite="lax",
        )

        # 构建响应
        return LoginResponse(
            access_token=access_data.access_token,
            refresh_token=refresh_data.refresh_token,
            access_token_expire_time=access_data.access_token_expire_time,
            refresh_token_expire_time=refresh_data.refresh_token_expire_time,
            session_uuid=access_data.session_uuid,
            user=AuthService._user_to_response(user),
        )

    @staticmethod
    async def refresh_token(
        db: AsyncSession,
        request: Request,
    ) -> RefreshTokenResponse:
        """
        刷新访问令牌

        Args:
            db: 数据库会话
            request: FastAPI 请求对象

        Returns:
            RefreshTokenResponse 对象

        Raises:
            AuthException: 刷新令牌无效或已过期
        """
        # 从 Cookie 获取刷新令牌
        refresh_token = request.cookies.get("refresh_token")
        if not refresh_token:
            raise AuthException("Refresh Token 不存在，请重新登录")

        # 解码刷新令牌
        try:
            token_payload = jwt_decode(refresh_token)
        except AuthException:
            raise AuthException("Refresh Token 无效，请重新登录") from None

        # 查询用户（预加载角色）
        result = await db.execute(select(User).where(User.id == token_payload.id).options(selectinload(User.roles)))
        user = result.scalar_one_or_none()

        if not user:
            raise AuthException("用户不存在")

        if not user.is_active:
            raise AuthException("用户已被禁用")

        # 创建新令牌
        new_token_data = await create_new_token(
            refresh_token=refresh_token,
            session_uuid=token_payload.session_uuid,
            user_id=user.id,
            multi_login=user.is_multi_login,
            username=user.username,
            email=user.email,
        )

        # 更新 Cookie 中的刷新令牌
        # 注意：这里需要从响应对象获取，但在 FastAPI 依赖注入中
        # 我们可能需要通过其他方式处理

        return RefreshTokenResponse(
            access_token=new_token_data.new_access_token,
            refresh_token=new_token_data.new_refresh_token,
            access_token_expire_time=new_token_data.new_access_token_expire_time,
            refresh_token_expire_time=new_token_data.new_refresh_token_expire_time,
            session_uuid=new_token_data.session_uuid,
        )

    @staticmethod
    async def logout(request: Request, response: Response) -> None:
        """
        用户登出

        Args:
            request: FastAPI 请求对象
            response: FastAPI 响应对象
        """
        # 尝试获取并撤销令牌
        try:
            auth_header = request.headers.get("Authorization")
            if auth_header and auth_header.startswith("Bearer "):
                token = auth_header.split(" ")[1]
                token_payload = jwt_decode(token)
                await revoke_token(token_payload.id, token_payload.session_uuid)
        except Exception as e:
            logger.warning(f"登出时撤销令牌失败: {e}")
        finally:
            # 删除刷新令牌 Cookie
            response.delete_cookie("refresh_token")

            # 如果 Redis 可用，也删除刷新令牌
            if is_redis_available():
                refresh_token = request.cookies.get("refresh_token")
                if refresh_token:
                    try:
                        token_payload = jwt_decode(refresh_token)
                        redis_client = get_redis()
                        await redis_client.delete(
                            f"{settings.JWT_REFRESH_TOKEN_REDIS_PREFIX}:{token_payload.id}:{token_payload.session_uuid}"
                        )
                    except Exception as e:
                        logger.warning(f"删除刷新令牌失败: {e}")

    @staticmethod
    def _user_to_response(user: User) -> dict:
        """
        将 User 对象转换为响应字典

        Args:
            user: User 对象

        Returns:
            响应字典
        """
        return {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "full_name": user.full_name,
            "is_active": user.is_active,
            "is_superuser": user.is_superuser,
            "created_at": user.created_at,
            "updated_at": user.updated_at if user.updated_at else None,
        }


# 创建服务实例
auth_service = AuthService()
