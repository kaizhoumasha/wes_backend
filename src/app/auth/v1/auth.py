"""
认证 API 路由

提供认证相关的 API 端点：
- POST /api/v1/auth/login - 用户登录
- POST /api/v1/auth/logout - 用户登出
- POST /api/v1/auth/refresh - 刷新访问令牌
"""

from fastapi import APIRouter, Request, Response, status

from src.app.admin.models import LoginRequest, LoginResponse, RefreshTokenResponse
from src.app.auth.services.auth_service import auth_service
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
    "/logout",
    summary="用户登出",
    status_code=status.HTTP_200_OK,
)
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

    # 更新 Cookie 中的刷新令牌
    response.set_cookie(
        key="refresh_token",
        value=result.refresh_token,
        max_age=settings.JWT_REFRESH_TOKEN_EXPIRE_SECONDS,
        httponly=True,
        secure=not settings.APP_DEBUG,
        samesite="lax",
    )

    return result
