"""工作线运行监控 API。"""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, status

from src.app.workline.models.runtime import (
    RuntimeDeviceDetailResponse,
    RuntimeDeviceSummary,
    RuntimeOverviewResponse,
    RuntimeWorklineDetailResponse,
    RuntimeWorklineSummary,
)
from src.app.workline.services import runtime_query_service
from src.core.rbac import RequirePermission
from src.core.response import DEFAULT_NOT_FOUND, ResponseSchemaModel, response_builder
from src.database.dependencies import AsyncSessionDep

router = APIRouter(tags=["运行监控"])


@router.get(
    "/overview",
    summary="[biz:workline:list] 运行监控总览",
    response_model=ResponseSchemaModel[RuntimeOverviewResponse],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:list"))],
)
async def get_runtime_overview(db: AsyncSessionDep) -> ResponseSchemaModel[RuntimeOverviewResponse]:
    result = await runtime_query_service.get_overview(db)
    return cast("ResponseSchemaModel[RuntimeOverviewResponse]", response_builder.success(data=result))


@router.get(
    "/worklines",
    summary="[biz:workline:list] 工作线运行态列表",
    response_model=ResponseSchemaModel[list[RuntimeWorklineSummary]],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:list"))],
)
async def get_runtime_worklines(db: AsyncSessionDep) -> ResponseSchemaModel[list[RuntimeWorklineSummary]]:
    result = await runtime_query_service.list_worklines(db)
    return cast("ResponseSchemaModel[list[RuntimeWorklineSummary]]", response_builder.success(data=result))


@router.get(
    "/worklines/{workline_id}",
    summary="[biz:workline:list] 工作线运行态详情",
    response_model=ResponseSchemaModel[RuntimeWorklineDetailResponse],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:list"))],
)
async def get_runtime_workline_detail(
    workline_id: int,
    db: AsyncSessionDep,
) -> ResponseSchemaModel[RuntimeWorklineDetailResponse]:
    result = await runtime_query_service.get_workline_detail(db, workline_id)
    if result is None:
        return cast(
            "ResponseSchemaModel[RuntimeWorklineDetailResponse]",
            response_builder.fail(code=DEFAULT_NOT_FOUND, message=f"工作线运行态不存在: {workline_id}"),
        )
    return cast("ResponseSchemaModel[RuntimeWorklineDetailResponse]", response_builder.success(data=result))


@router.get(
    "/devices",
    summary="[biz:device:list] 设备运行态列表",
    response_model=ResponseSchemaModel[list[RuntimeDeviceSummary]],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:device:list"))],
)
async def get_runtime_devices(db: AsyncSessionDep) -> ResponseSchemaModel[list[RuntimeDeviceSummary]]:
    result = await runtime_query_service.list_devices(db)
    return cast("ResponseSchemaModel[list[RuntimeDeviceSummary]]", response_builder.success(data=result))


@router.get(
    "/devices/{device_id}",
    summary="[biz:device:list] 设备运行态详情",
    response_model=ResponseSchemaModel[RuntimeDeviceDetailResponse],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:device:list"))],
)
async def get_runtime_device_detail(
    device_id: int,
    db: AsyncSessionDep,
) -> ResponseSchemaModel[RuntimeDeviceDetailResponse]:
    result = await runtime_query_service.get_device_detail(db, device_id)
    if result is None:
        return cast(
            "ResponseSchemaModel[RuntimeDeviceDetailResponse]",
            response_builder.fail(code=DEFAULT_NOT_FOUND, message=f"设备运行态不存在: {device_id}"),
        )
    return cast("ResponseSchemaModel[RuntimeDeviceDetailResponse]", response_builder.success(data=result))


__all__ = ["router"]
