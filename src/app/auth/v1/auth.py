"""
认证 API 路由

提供认证相关的 API 端点：
- POST /api/v1/auth/login - 用户登录
- POST /api/v1/auth/logout - 用户登出（当前会话）
- POST /api/v1/auth/logout-all - 强制登出所有设备
- POST /api/v1/auth/refresh - 刷新访问令牌
- GET /api/v1/auth/sessions - 获取当前用户的所有活跃会话
- DELETE /api/v1/auth/sessions/{session_uuid} - 撤销指定会话
- GET /api/v1/auth/menu - 获取当前用户菜单
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status

from src.app.admin.services.perm_service import permission_service
from src.app.auth.models import (
    ActiveSessionsResponse,
    LoginRequest,
    LoginResponse,
    LogoutResponse,
    RefreshTokenResponse,
    RevokeSessionResponse,
    SessionInfo,
)
from src.app.auth.services.auth_service import auth_service
from src.core.conf import settings
from src.core.response.response_util import response_builder
from src.core.security import DependsAuth, get_current_user, require_auth
from src.database.dependencies import AsyncSessionDep

router = APIRouter(prefix="/auth", tags=["认证"])


# ==================== 认证端点 ====================


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="用户登录",
    status_code=status.HTTP_200_OK,
)
async def login(
    credentials: LoginRequest,
    response: Response,
    db: AsyncSessionDep,
) -> LoginResponse:
    """
    用户登录

    返回访问令牌和刷新令牌。刷新令牌存储在 HttpOnly Cookie 中。

    - **username**: 用户名（3-50字符）
    - **password**: 密码（6-100字符）

    **安全特性**：
    - 使用 Argon2 密码哈希
    - JWT 包含标准声明（iss, sub, jti, iat, nbf, exp）
    - Refresh Token 存储在 HttpOnly Cookie 中
    - 支持 JTI（JWT ID）用于精确撤销
    """
    return await auth_service.login(
        db=db,
        username=credentials.username,
        password=credentials.password,
        response=response,
    )


@router.post(
    "/refresh",
    response_model=RefreshTokenResponse,
    summary="刷新访问令牌",
    status_code=status.HTTP_200_OK,
)
async def refresh_token(
    request: Request,
    response: Response,
    db: AsyncSessionDep,
) -> RefreshTokenResponse:
    """
    刷新访问令牌

    使用刷新令牌（从 Cookie 中获取）获取新的访问令牌和刷新令牌。
    新的刷新令牌会自动更新到 HttpOnly Cookie 中。

    **安全特性**：
    - 验证 Refresh Token 类型和有效性
    - 检查用户状态（是否被禁用）
    - 生成新的 JTI（JWT ID）
    - 自动撤销旧令牌
    """
    result = await auth_service.refresh_token(db=db, request=request, response=response)

    # 确定 Cookie secure 标志：优先使用 COOKIE_SECURE 配置，否则根据 APP_DEBUG 自动判断
    cookie_secure = (
        settings.COOKIE_SECURE
        if settings.COOKIE_SECURE is not None
        else not settings.APP_DEBUG
    )

    # 更新 Cookie 中的刷新令牌
    response.set_cookie(
        key="refresh_token",
        value=result.refresh_token,
        max_age=settings.JWT_REFRESH_TOKEN_EXPIRE_SECONDS,
        httponly=True,
        secure=cookie_secure,
        samesite="lax",
    )

    return result


@router.post(
    "/logout",
    response_model=LogoutResponse,
    summary="用户登出",
    status_code=status.HTTP_200_OK,
)
async def logout(
    response: Response,
    request: Request,
    current_user: Annotated[int, Depends(require_auth)],
) -> LogoutResponse:
    """
    用户登出（撤销当前会话）

    撤销当前会话的令牌并删除刷新令牌 Cookie。

    **安全特性**：
    - 撤销当前 Access Token（添加到黑名单）
    - 删除 Refresh Token Cookie
    - 从 Redis 中删除会话信息
    """
    await auth_service.logout(request=request, response=response, current_user_id=current_user)
    return LogoutResponse(message="登出成功", revoked_count=1)


@router.post(
    "/logout-all",
    response_model=LogoutResponse,
    summary="强制登出所有设备",
    status_code=status.HTTP_200_OK,
)
async def logout_all(
    response: Response,
    current_user: Annotated[int, Depends(require_auth)],
) -> LogoutResponse:
    """
    强制登出所有设备（撤销所有会话）

    撤销用户所有活跃会话的令牌。用于：
    - 用户主动清空所有会话
    - 发现安全问题时强制登出
    - 管理员重置用户会话

    **安全特性**：
    - 撤销所有 Access Token（添加到黑名单）
    - 撤销所有 Refresh Token
    - 删除所有会话信息
    - 返回撤销的令牌数量
    """
    revoked_count = await auth_service.logout_all(response=response, current_user_id=current_user)
    return LogoutResponse(message=f"已撤销 {revoked_count} 个令牌", revoked_count=revoked_count)


# ==================== 会话管理端点 ====================


@router.get(
    "/sessions",
    response_model=ActiveSessionsResponse,
    summary="获取当前用户的所有活跃会话",
    status_code=status.HTTP_200_OK,
)
async def get_active_sessions(
    current_user: Annotated[int, Depends(require_auth)],
) -> ActiveSessionsResponse:
    """
    获取当前用户的所有活跃会话

    返回用户所有活跃的登录会话，包括：
    - 会话 UUID
    - JWT ID (JTI)
    - 创建时间
    - 设备信息
    - 最后活跃时间

    **使用场景**：
    - 用户查看和管理自己的登录设备
    - 安全审计
    - 检测异常登录
    """
    return await auth_service.get_active_sessions(current_user)


@router.delete(
    "/sessions/{session_uuid}",
    response_model=RevokeSessionResponse,
    summary="撤销指定会话",
    status_code=status.HTTP_200_OK,
)
async def revoke_session(
    session_uuid: str,
    current_user: Annotated[int, Depends(require_auth)],
) -> RevokeSessionResponse:
    """
    撤销指定会话

    撤销用户指定会话的令牌（强制登出特定设备）。

    **使用场景**：
    - 用户发现异常登录时撤销该会话
    - 管理员撤销用户特定会话
    - 用户管理自己的多设备登录

    **安全特性**：
    - 验证会话属于当前用户
    - 撤销 Access Token（添加到黑名单）
    - 撤销关联的 Refresh Token
    - 删除会话信息
    """
    await auth_service.revoke_session(current_user, session_uuid)
    return RevokeSessionResponse(message="会话已撤销", session_uuid=session_uuid)


# ==================== 菜单端点 ====================


@router.get(
    "/menu",
    summary="获取当前用户菜单",
    description="获取当前用户有权限访问的菜单树（用于前端动态路由）",
)
async def get_user_menu(
    db: AsyncSessionDep,
    current_user: Annotated[int, Depends(get_current_user)],
    include_hidden: Annotated[bool, Query(description="是否包含隐藏菜单")] = False,
) -> dict:
    """
    获取当前用户的菜单树（前端动态路由）

    返回用户有权限访问的菜单树，包含：
    - Vue Router 配置（route_config）
    - 面包屑导航（breadcrumb）
    - 子菜单列表（children）

    **权限规则**：
    - 只返回用户有权限的菜单
    - 如果用户有子菜单权限，自动包含父菜单

    **使用场景**：前端登录后加载动态路由
    """
    menu_tree = await permission_service.get_user_menu_tree(db, current_user, include_hidden=include_hidden)

    return response_builder.success(data=menu_tree)
