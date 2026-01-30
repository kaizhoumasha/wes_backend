from typing import Annotated

from fastapi import Body, Depends, Path

from src.app.api_auth.models import (
    APIApplication,
    APIApplicationCreate,
    APIApplicationResponse,
    APIApplicationUpdate,
)
from src.app.api_auth.services import api_app_service
from src.core.api_security import RequireAPIAuth, RequireAPIPermission
from src.core.base_api import BaseAPI
from src.core.rbac import RequirePermission
from src.core.response import BusinessErrorCode, ResourceErrorCode
from src.core.response.response_util import response_builder
from src.database.dependencies import AsyncSessionDep, CacheDep

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
)

router = api_app_api.router


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
        return response_builder.success(data=response_data, message="应用创建成功，请妥善保存 app_secret（仅显示一次）")
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
    "/try/invoke",
    summary="[api:try:invoke] 测试 API 调用",
    dependencies=[Depends(RequireAPIPermission("api:try:invoke"))],  # 只需要 API 认证
)
async def try_invoke_application(app_ctx: RequireAPIAuth):
    return response_builder.success(
        data={
            "app_id": app_ctx.app_id,
            "app_name": app_ctx.app_name,
            "permissions": list(app_ctx.permissions),
        },
        message="API 调用成功",
    )
