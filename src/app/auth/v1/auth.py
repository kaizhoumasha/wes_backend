"""
认证 API 路由

提供认证相关的 API 端点：
- POST /api/v1/auth/login - 用户登录
- POST /api/v1/auth/logout - 用户登出（当前会话）
- POST /api/v1/auth/logout-all - 强制登出所有设备
- POST /api/v1/auth/refresh - 刷新访问令牌
- GET /api/v1/auth/sessions - 获取当前用户的所有活跃会话
- DELETE /api/v1/auth/sessions/{session_uuid} - 撤销指定会话
- GET /api/v1/auth/permissions - 获取当前用户的 API 权限列表
"""

from typing import Annotated, cast

from fastapi import APIRouter, Depends, Request, Response, status

from src.app.admin.services.perm_service import permission_service
from src.app.auth.models import (
    ActiveSessionsResponse,
    ApiPermissionInfo,
    LoginRequest,
    LoginResponse,
    LogoutResponse,
    RefreshTokenResponse,
    RevokeSessionResponse,
    UserPermissionsResponse,
)
from src.app.auth.services.auth_service import auth_service
from src.core.response.response_schema import ResponseSchemaModel
from src.core.response.response_util import response_builder
from src.core.security import require_auth
from src.database.dependencies import AsyncSessionDep

router = APIRouter(prefix="/auth", tags=["认证"])


# ==================== 认证端点 ====================


@router.post(
    "/login",
    summary="用户登录",
    status_code=status.HTTP_200_OK,
)
async def login(
    credentials: LoginRequest,
    response: Response,
    db: AsyncSessionDep,
) -> ResponseSchemaModel[LoginResponse]:
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
    result = await auth_service.login(
        db=db,
        username=credentials.username,
        password=credentials.password,
        response=response,
    )
    return cast("ResponseSchemaModel[LoginResponse]", response_builder.success(data=result))


@router.post(
    "/refresh",
    summary="刷新访问令牌",
    status_code=status.HTTP_200_OK,
)
async def refresh_token(
    request: Request,
    response: Response,
    db: AsyncSessionDep,
) -> ResponseSchemaModel[RefreshTokenResponse]:
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
    # Cookie 策略由 AuthService 统一处理，避免路由层重复设置造成策略漂移
    result = await auth_service.refresh_token(db=db, request=request, response=response)
    return cast("ResponseSchemaModel[RefreshTokenResponse]", response_builder.success(data=result))


@router.post(
    "/logout",
    summary="用户登出",
    status_code=status.HTTP_200_OK,
)
async def logout(
    response: Response,
    request: Request,
) -> ResponseSchemaModel[LogoutResponse]:
    """
    用户登出（撤销当前会话）

    撤销当前会话的令牌并删除刷新令牌 Cookie。

    **安全特性**：
    - 优先撤销当前 Access Token（添加到黑名单）
    - 当 Access Token 不可用时，回退使用 Refresh Token Cookie 撤销当前会话
    - 始终删除 Refresh Token Cookie（幂等）
    """
    revoked_count = await auth_service.logout(request=request, response=response)
    result = LogoutResponse(message="登出成功", revoked_count=revoked_count)
    return cast("ResponseSchemaModel[LogoutResponse]", response_builder.success(data=result))


@router.post(
    "/logout-all",
    summary="强制登出所有设备",
    status_code=status.HTTP_200_OK,
)
async def logout_all(
    response: Response,
    current_user: Annotated[int, Depends(require_auth)],
) -> ResponseSchemaModel[LogoutResponse]:
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
    result = LogoutResponse(message=f"已撤销 {revoked_count} 个令牌", revoked_count=revoked_count)
    return cast("ResponseSchemaModel[LogoutResponse]", response_builder.success(data=result))


# ==================== 会话管理端点 ====================


@router.get(
    "/sessions",
    summary="获取当前用户的所有活跃会话",
    status_code=status.HTTP_200_OK,
)
async def get_active_sessions(
    current_user: Annotated[int, Depends(require_auth)],
) -> ResponseSchemaModel[ActiveSessionsResponse]:
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
    result = await auth_service.get_active_sessions(current_user)
    return cast("ResponseSchemaModel[ActiveSessionsResponse]", response_builder.success(data=result))


@router.delete(
    "/sessions/{session_uuid}",
    summary="撤销指定会话",
    status_code=status.HTTP_200_OK,
)
async def revoke_session(
    session_uuid: str,
    current_user: Annotated[int, Depends(require_auth)],
) -> ResponseSchemaModel[RevokeSessionResponse]:
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
    result = RevokeSessionResponse(message="会话已撤销", session_uuid=session_uuid)
    return cast("ResponseSchemaModel[RevokeSessionResponse]", response_builder.success(data=result))


# ==================== 权限端点 ====================


@router.get(
    "/permissions",
    summary="获取当前用户的 API 权限列表",
    description="获取当前用户有权限访问的内部管理 API（用于前端动态路由和权限控制）",
)
async def get_user_permissions(
    db: AsyncSessionDep,
    current_user: Annotated[int, Depends(require_auth)],
) -> ResponseSchemaModel[UserPermissionsResponse]:
    """
    获取当前用户的 API 权限列表（前端动态路由）

    返回用户有权限访问的 user_api 类型权限列表，包含：
    - 权限标识（name）：admin:user:create
    - HTTP 方法（method）：GET、POST、PUT、DELETE 等
    - API 路径（path）：/api/admin/users 等
    - 权限类型（type）：固定为 user_api（内部管理 API）

    **权限规则**：
    - 超级用户返回所有 user_api 权限
    - 普通用户只返回其角色分配的权限
    - 已删除的权限不会返回

    **使用场景**：
    - 前端登录后获取用户可访问的 API 列表
    - 前端根据 API 权限动态显示/隐藏功能按钮
    - 前端根据 API 权限控制路由访问

    **说明**：
        - 此端点只返回 user_api 类型（内部管理 API）
        - app_api 类型（外部应用 API）不返回，前端不需要
    """
    permissions = await permission_service.get_user_api_permissions(db, current_user)

    # 转换为响应模型
    permission_infos = [
        ApiPermissionInfo(
            id=perm.id,
            name=perm.name,
            description=perm.description,
            type=perm.type,
            category=perm.category,
            resource=perm.resource,
            action=perm.action,
            method=perm.method,
            path=perm.path,
        )
        for perm in permissions
    ]

    result = UserPermissionsResponse(total=len(permission_infos), permissions=permission_infos)
    return cast("ResponseSchemaModel[UserPermissionsResponse]", response_builder.success(data=result))
