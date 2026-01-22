"""
认证 API 路由

提供认证相关的 API 端点：
- POST /api/v1/auth/login - 用户登录
- POST /api/v1/auth/logout - 用户登出
- POST /api/v1/auth/refresh - 刷新访问令牌
- GET /api/v1/auth/menu - 获取当前用户菜单
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status

from src.app.admin.services.perm_service import permission_service
from src.app.auth.models import LoginRequest, LoginResponse, RefreshTokenResponse
from src.app.auth.services.auth_service import auth_service
from src.core.response.response_util import response_builder
from src.core.security import DependsAuth, get_current_user
from src.database.dependencies import AsyncSessionDep

router = APIRouter(prefix="/auth", tags=["认证"])


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
):
    """
    用户登录

    返回访问令牌和刷新令牌。刷新令牌存储在 HttpOnly Cookie 中。

    - **username**: 用户名
    - **password**: 密码
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
):
    """
    刷新访问令牌

    使用刷新令牌（从 Cookie 中获取）获取新的访问令牌和刷新令牌。
    新的刷新令牌会自动更新到 HttpOnly Cookie 中。
    """
    from src.core.conf import settings

    result = await auth_service.refresh_token(db=db, request=request)

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


@router.post("/logout", summary="用户登出", status_code=status.HTTP_200_OK, dependencies=[DependsAuth])
async def logout(
    response: Response,
    request: Request,
):
    """
    用户登出

    撤销当前会话的令牌并删除刷新令牌 Cookie。
    """
    await auth_service.logout(request=request, response=response)
    return {"message": "登出成功"}


@router.get("/menu", summary="获取当前用户菜单", description="获取当前用户有权限访问的菜单树（用于前端动态路由）")
async def get_user_menu(
    db: AsyncSessionDep,
    current_user: Annotated[int, Depends(get_current_user)],
    include_hidden: Annotated[bool, Query(description="是否包含隐藏菜单")] = False,
) -> dict:
    """获取当前用户的菜单树（前端动态路由）

    返回用户有权限访问的菜单树，包含：
    - Vue Router 配置（route_config）
    - 面包屑导航（breadcrumb）
    - 子菜单列表（children）

    权限规则：
    - 只返回用户有权限的菜单
    - 如果用户有子菜单权限，自动包含父菜单

    **使用场景**：前端登录后加载动态路由
    """
    menu_tree = await permission_service.get_user_menu_tree(db, current_user, include_hidden=include_hidden)

    return response_builder.success(data=menu_tree)
