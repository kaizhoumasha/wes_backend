from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query, Request
from pydantic import BaseModel

from src.app.admin.services import permission_service
from src.app.api_auth.models import (
    APIApplication,
    APIApplicationCreate,
    APIApplicationResponse,
    APIApplicationUpdate,
)
from src.app.api_auth.models.api_application import ResetValidityPeriodSchema
from src.app.api_auth.services import api_app_service
from src.core.api_security import RequireAPIAuth, RequireAPIPermission
from src.core.base_api import BaseAPI
from src.core.rbac import RequirePermission
from src.core.response import BusinessErrorCode, ResourceErrorCode
from src.core.response.response_util import response_builder
from src.database.dependencies import AsyncSessionDep, CacheDep
from src.utils.permission_scanner import sync_permissions_to_db


def register_custom_route(router: APIRouter, api: "BaseAPI") -> None:
    """注册 APIApplication 自定义路由

    Args:
        router: FastAPI 路由器
        api: BaseAPI 实例（用于访问内部方法）
    """

    @router.get(
        "/available-permissions",
        summary="[api-auth:api_application:list_permissions] 获取系统支持的 API 权限列表",
        dependencies=[Depends(RequirePermission("api-auth:api_application:detail"))],
    )
    async def get_system_api_permissions(
        request: Request,
        db: AsyncSessionDep,
        sync: bool = Query(False, description="是否强制从代码重新扫描并同步到数据库"),
    ):
        """
        返回可供分配给 API 应用的权限列表。
        """
        if sync:
            await sync_permissions_to_db(request.app, db)

        # 通过 Service 层获取 API 权限（符合分层架构）
        permissions = await permission_service.get_api_permissions(db, exclude_deleted=True)

        return response_builder.success(data=permissions)

    @router.post(
        "",
        summary="[api-auth:api_application:create] 创建 API 应用",
        dependencies=[Depends(RequirePermission("api-auth:api_application:create"))],
    )
    async def create_application(
        obj_in: Annotated[APIApplicationCreate, Body(...)],
        db: AsyncSessionDep,
        cache: CacheDep,
    ):
        data = obj_in.model_dump()

        try:
            app, app_secret = await api_app_service.create_app(db, data, cache)

            response_data = {
                **app.model_dump(exclude={"app_secret_encrypted"}),  # type: ignore[arg-type]
                "app_secret": app_secret,
            }
            return response_builder.success(
                data=response_data, message="应用创建成功，请妥善保存 app_secret（仅显示一次）"
            )
        except Exception as e:
            return response_builder.fail(code=BusinessErrorCode.OPERATION_FAILED, message=f"应用创建失败: {e}")

    @router.post(
        "/{id}/revoke",
        summary="[api-auth:api_application:revoke] 撤销 API 应用",
        dependencies=[Depends(RequirePermission("api-auth:api_application:revoke"))],
    )
    async def revoke_application(
        id: Annotated[int, Path(...)],
        db: AsyncSessionDep,
        cache: CacheDep,
    ):
        app = await api_app_service.get_by_id(db, cache, id)
        if not app:
            return response_builder.fail(code=ResourceErrorCode.NOT_FOUND, message=f"应用不存在: {id}")

        success = await api_app_service.revoke_app(db, app.app_id, cache)
        if success:
            return response_builder.success(message="应用已撤销")
        return response_builder.fail(code=BusinessErrorCode.OPERATION_FAILED, message="撤销失败")

    @router.post(
        "/{id}/reset-validity",
        dependencies=[Depends(RequirePermission("api-auth:api_application:reset_validity"))],
        summary="重置应用有效期",
    )
    async def reset_validity_period(
        db: AsyncSessionDep,
        cache: CacheDep,
        id: int,
        data: ResetValidityPeriodSchema,
    ):
        """重置应用有效期

        基于 created_at 重新计算 expires_at，而不是从当前时间计算。
        这样可以保证"延期"是基于原始创建时间，而不是当前时间。

        例如：
        - 应用创建于 2024-01-01，设置有效期 1年，过期时间为 2025-01-01
        - 2024-06-01 重置有效期为 2年，新的过期时间为 2026-01-01（而不是 2026-06-01）

        Args:
            id: 应用 ID
            data: 包含新的有效期时长和修改原因
            db: 数据库会话
        """
        app = await api_app_service.reset_validity_period(
            db=db,
            cache=cache,
            application_id=id,
            validity_period=data.validity_period,
            version=data.version,
        )

        return response_builder.success(data=APIApplicationResponse.model_validate(app), message="有效期重置成功")

    class TryInvokeApplication(BaseModel):
        """测试 API 调用数据模型"""

        command_name: str
        command_description: str
        command_parameters: list[str]
        command_response: str

    class TryInvokeApplicationRequest(BaseModel):
        """测试 API 调用请求模型（包裹格式）"""

        data: TryInvokeApplication

    @router.post(
        "/try/invoke",
        summary="[api:try:invoke] 测试 API 调用",
        dependencies=[Depends(RequireAPIPermission("api:try:invoke"))],  # 只需要 API 认证
    )
    async def try_invoke_application(
        app_ctx: RequireAPIAuth,
        request_data: TryInvokeApplicationRequest,
    ):
        """测试 API 调用

        请求格式：{"data": {...}}
        """
        result_data = {"app_ctx": app_ctx, "data": request_data.data}
        return response_builder.success(data=result_data, message="API 调用成功")

    @router.post(
        "/{id}/permissions",
        summary="[api-auth:api_application:assign_permission] 分配权限",
        dependencies=[Depends(RequirePermission("api-auth:api_application:assign_permission"))],
    )
    async def assign_permissions(
        id: Annotated[int, Path(...)],
        permission_ids: Annotated[list[int], Body(embed=True)],
        db: AsyncSessionDep,
        cache: CacheDep,
    ):
        """为应用分配权限"""
        await api_app_service.assign_permissions(db, cache, id, permission_ids)
        return response_builder.success(message="权限分配成功")

    @router.post(
        "/{id}/reset-secret",
        summary="[api-auth:api_application:reset_secret] 重置应用密钥",
        dependencies=[Depends(RequirePermission("api-auth:api_application:reset_secret"))],
    )
    async def reset_secret(
        id: Annotated[int, Path(...)],
        db: AsyncSessionDep,
        cache: CacheDep,
    ):
        """重置应用密钥

        ⚠️ 注意: 旧密钥将立即失效，新密钥仅返回一次。
        """
        new_secret = await api_app_service.reset_secret(db, cache, id)
        return response_builder.success(data={"app_secret": new_secret}, message="密钥重置成功，请妥善保存新密钥")


# 创建 BaseAPI，通过 custom_routes 参数传入自定义路由
# 自定义路由会在标准 CRUD 路由之前注册，避免被 /{id} 匹配
api_app_api = BaseAPI(
    module_name="api-auth",
    model=APIApplication,
    service=api_app_service,
    create_schema=APIApplicationCreate,
    update_schema=APIApplicationUpdate,
    response_schema=APIApplicationResponse,
    prefix="/api-auth/applications",
    tags=["API 认证管理"],
    enable_permission=True,
    gen_create=False,
    custom_routes=[register_custom_route],
)

# 获取 router，直接在其上添加更多自定义路由
router = api_app_api.router
