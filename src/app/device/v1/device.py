"""Device API 路由"""

from typing import Any, cast

from fastapi import APIRouter, Body, Depends, Path
from pydantic import BaseModel, Field

from src.app.device.models import (
    Device,
    DeviceCreate,
    DeviceResponse,
    DeviceUpdate,
)
from src.app.device.services import device_service
from src.core.base_api import BaseAPI
from src.core.rbac import RequirePermission
from src.core.response.response_schema import ResponseSchemaModel
from src.core.response.response_util import response_builder
from src.database.dependencies import AsyncSessionDep, CacheDep


class DeviceMaintenanceRequest(BaseModel):
    """设备维护操作请求。"""

    reason: str | None = Field(default=None, max_length=50, description="维护原因码")


class DeviceRuntimeActionRequest(BaseModel):
    """设备运行态空操作请求，保留扩展位。"""

    reason: str | None = Field(default=None, max_length=200, description="操作原因")


def register_runtime_routes(router: APIRouter, api: BaseAPI[Any, Any, Any]) -> None:
    """注册设备运行态专用操作入口。"""

    permission = api.get_permission_code("update")

    @router.post(
        "/{id}/runtime/enter-maintenance",
        summary=f"[{permission}] 设备进入维护",
        response_model=ResponseSchemaModel[DeviceResponse],
        dependencies=[Depends(RequirePermission(permission))] if permission else None,
    )
    async def enter_maintenance(
        db: AsyncSessionDep,
        cache: CacheDep,
        id: int = Path(...),
        payload: DeviceMaintenanceRequest = Body(...),
    ) -> ResponseSchemaModel[DeviceResponse]:
        updated = await device_service.enter_maintenance(db, device_id=id, reason=payload.reason)
        response_resource = cast("Device", await device_service.get_by_id(db, cache, id, max_depth=1) or updated)
        return cast(
            "ResponseSchemaModel[DeviceResponse]",
            response_builder.success(data=device_service.to_response(response_resource, DeviceResponse)),
        )

    @router.post(
        "/{id}/runtime/exit-maintenance",
        summary=f"[{permission}] 设备退出维护",
        response_model=ResponseSchemaModel[DeviceResponse],
        dependencies=[Depends(RequirePermission(permission))] if permission else None,
    )
    async def exit_maintenance(
        db: AsyncSessionDep,
        cache: CacheDep,
        id: int = Path(...),
        payload: DeviceRuntimeActionRequest = Body(...),
    ) -> ResponseSchemaModel[DeviceResponse]:
        _ = payload
        updated = await device_service.exit_maintenance(db, device_id=id)
        response_resource = cast("Device", await device_service.get_by_id(db, cache, id, max_depth=1) or updated)
        return cast(
            "ResponseSchemaModel[DeviceResponse]",
            response_builder.success(data=device_service.to_response(response_resource, DeviceResponse)),
        )

    @router.post(
        "/{id}/runtime/clear-fault",
        summary=f"[{permission}] 清除设备故障",
        response_model=ResponseSchemaModel[DeviceResponse],
        dependencies=[Depends(RequirePermission(permission))] if permission else None,
    )
    async def clear_fault(
        db: AsyncSessionDep,
        cache: CacheDep,
        id: int = Path(...),
        payload: DeviceRuntimeActionRequest = Body(...),
    ) -> ResponseSchemaModel[DeviceResponse]:
        _ = payload
        updated = await device_service.clear_fault(db, device_id=id)
        response_resource = cast("Device", await device_service.get_by_id(db, cache, id, max_depth=1) or updated)
        return cast(
            "ResponseSchemaModel[DeviceResponse]",
            response_builder.success(data=device_service.to_response(response_resource, DeviceResponse)),
        )


# 使用 BaseAPI 零代码生成 CRUD 路由
device_api = BaseAPI(
    module_name="biz",
    model=Device,
    service=device_service,
    create_schema=DeviceCreate,
    update_schema=DeviceUpdate,
    response_schema=DeviceResponse,
    prefix="/devices",
    tags=["设备管理"],
    custom_routes=[register_runtime_routes],
)

router = device_api.router
