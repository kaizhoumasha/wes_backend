"""工作线运行监控 API。"""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, Query, status

from src.app.workline.models.runtime import (
    RuntimeDeviceDetailResponse,
    RuntimeDeviceSummary,
    RuntimeOverviewResponse,
    RuntimeTracePathResponse,
    RuntimeWorklineMonitorProjectionResponse,
    RuntimeWorklineSummary,
)
from src.app.workline.services import runtime_query_service
from src.core.rbac import RequirePermission
from src.core.response import DEFAULT_NOT_FOUND, ResponseSchemaModel, response_builder
from src.database.dependencies import AsyncSessionDep  # noqa: TC001

router = APIRouter(tags=["运行监控"])


@router.get(
    "/overview",
    summary="[biz:workline:list] 运行监控总览",
    response_model=ResponseSchemaModel[RuntimeOverviewResponse],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:list"))],
)
async def get_runtime_overview(
    db: AsyncSessionDep,
    include_sim: bool = Query(default=False, alias="includeSim"),
) -> ResponseSchemaModel[RuntimeOverviewResponse]:
    result = await runtime_query_service.get_overview(db, include_sim=include_sim)
    return cast("ResponseSchemaModel[RuntimeOverviewResponse]", response_builder.success(data=result))


@router.get(
    "/worklines",
    summary="[biz:workline:list] 工作线运行态列表",
    response_model=ResponseSchemaModel[list[RuntimeWorklineSummary]],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:list"))],
)
async def get_runtime_worklines(
    db: AsyncSessionDep,
    exclude_simulation: bool = Query(default=False, alias="excludeSimulation"),
) -> ResponseSchemaModel[list[RuntimeWorklineSummary]]:
    result = await runtime_query_service.list_worklines(db, exclude_simulation=exclude_simulation)
    return cast("ResponseSchemaModel[list[RuntimeWorklineSummary]]", response_builder.success(data=result))


@router.get(
    "/worklines/{workline_id}",
    summary="[biz:workline:list] 工作线运行态监控投影",
    response_model=ResponseSchemaModel[RuntimeWorklineMonitorProjectionResponse],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:list"))],
)
async def get_runtime_workline_detail(
    workline_id: int,
    db: AsyncSessionDep,
) -> ResponseSchemaModel[RuntimeWorklineMonitorProjectionResponse]:
    result = await runtime_query_service.get_workline_monitor_projection(db, workline_id)
    if result is None:
        return cast(
            "ResponseSchemaModel[RuntimeWorklineMonitorProjectionResponse]",
            response_builder.fail(code=DEFAULT_NOT_FOUND, message=f"工作线运行态监控投影不存在: {workline_id}"),
        )
    return cast("ResponseSchemaModel[RuntimeWorklineMonitorProjectionResponse]", response_builder.success(data=result))


@router.get(
    "/devices",
    summary="[biz:device:list] 设备运行态列表",
    response_model=ResponseSchemaModel[list[RuntimeDeviceSummary]],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:device:list"))],
)
async def get_runtime_devices(
    db: AsyncSessionDep,
    workline_id: int = Query(alias="worklineId"),
) -> ResponseSchemaModel[list[RuntimeDeviceSummary]]:
    result = await runtime_query_service.list_workline_devices(db, workline_id)
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
    workline_id: int = Query(alias="worklineId"),
) -> ResponseSchemaModel[RuntimeDeviceDetailResponse]:
    result = await runtime_query_service.get_workline_device_detail(db, workline_id, device_id)
    if result is None:
        return cast(
            "ResponseSchemaModel[RuntimeDeviceDetailResponse]",
            response_builder.fail(
                code=DEFAULT_NOT_FOUND,
                message=f"工作线设备运行态不存在: worklineId={workline_id}, deviceId={device_id}",
            ),
        )
    return cast("ResponseSchemaModel[RuntimeDeviceDetailResponse]", response_builder.success(data=result))


@router.get(
    "/sessions/{session_id}/path",
    summary="[biz:workline:list] Session 设备路径视图",
    response_model=ResponseSchemaModel[RuntimeTracePathResponse],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:list"))],
)
async def get_session_path(
    session_id: int,
    db: AsyncSessionDep,
) -> ResponseSchemaModel[RuntimeTracePathResponse]:
    result = await runtime_query_service.get_session_path(db, session_id)
    if result is None:
        return cast(
            "ResponseSchemaModel[RuntimeTracePathResponse]",
            response_builder.fail(code=DEFAULT_NOT_FOUND, message=f"Session 路径不存在: {session_id}"),
        )
    return cast("ResponseSchemaModel[RuntimeTracePathResponse]", response_builder.success(data=result))


@router.get(
    "/traces/{trace_id}/path",
    summary="[biz:workline:list] Trace 设备路径视图",
    response_model=ResponseSchemaModel[RuntimeTracePathResponse],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:list"))],
)
async def get_trace_path(
    trace_id: str,
    db: AsyncSessionDep,
) -> ResponseSchemaModel[RuntimeTracePathResponse]:
    result = await runtime_query_service.get_trace_path(db, trace_id)
    if result is None:
        return cast(
            "ResponseSchemaModel[RuntimeTracePathResponse]",
            response_builder.fail(code=DEFAULT_NOT_FOUND, message=f"Trace 路径不存在: {trace_id}"),
        )
    return cast("ResponseSchemaModel[RuntimeTracePathResponse]", response_builder.success(data=result))


__all__ = ["router"]
