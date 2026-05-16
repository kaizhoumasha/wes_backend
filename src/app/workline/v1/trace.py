"""工作线 Trace 查询 API。"""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, status

from src.app.workline.models.runtime import (
    RuntimeTraceListResponse,
    TraceBlockingPointResponse,
    TraceDetailResponse,
    TraceQueryRequest,
)
from src.app.workline.services import runtime_query_service, trace_query_service
from src.app.workline.services.trace_response_builder import build_trace_response
from src.core.rbac import RequirePermission
from src.core.response import ResponseSchemaModel, response_builder
from src.database.dependencies import AsyncSessionDep  # noqa: TC001

router = APIRouter(tags=["工作线 Trace"])


@router.get(
    "/request/{request_id}",
    summary="[biz:workline:list] 根据 request_id 查询 Trace",
    response_model=ResponseSchemaModel[TraceDetailResponse],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:list"))],
)
async def get_trace_by_request_id(request_id: str, db: AsyncSessionDep) -> ResponseSchemaModel[TraceDetailResponse]:
    result = await trace_query_service.by_request_id(db, request_id)
    return cast("ResponseSchemaModel[TraceDetailResponse]", response_builder.success(data=build_trace_response(result)))


@router.get(
    "/trace/{trace_id}",
    summary="[biz:workline:list] 根据 trace_id 查询 Trace",
    response_model=ResponseSchemaModel[TraceDetailResponse],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:list"))],
)
async def get_trace_by_trace_id(
    trace_id: str,
    db: AsyncSessionDep,
) -> ResponseSchemaModel[TraceDetailResponse]:
    result = await trace_query_service.by_trace_id(db, trace_id)
    return cast("ResponseSchemaModel[TraceDetailResponse]", response_builder.success(data=build_trace_response(result)))


@router.get(
    "/{trace_id}/blocking-point",
    summary="[biz:workline:list] 查询 Trace 阻塞点诊断卡",
    response_model=ResponseSchemaModel[TraceBlockingPointResponse],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:list"))],
)
async def get_trace_blocking_point(
    trace_id: str,
    db: AsyncSessionDep,
) -> ResponseSchemaModel[TraceBlockingPointResponse]:
    result = await trace_query_service.get_blocking_point(db, trace_id)
    return cast("ResponseSchemaModel[TraceBlockingPointResponse]", response_builder.success(data=result))


@router.get(
    "/session/{session_id}",
    summary="[biz:workline:list] 根据 session_id 查询 Trace",
    response_model=ResponseSchemaModel[TraceDetailResponse],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:list"))],
)
async def get_trace_by_session_id(session_id: int, db: AsyncSessionDep) -> ResponseSchemaModel[TraceDetailResponse]:
    result = await trace_query_service.by_session_id(db, session_id)
    return cast("ResponseSchemaModel[TraceDetailResponse]", response_builder.success(data=build_trace_response(result)))


@router.get(
    "/command/{command_code}",
    summary="[biz:workline:list] 根据 command_code 查询 Trace",
    response_model=ResponseSchemaModel[TraceDetailResponse],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:list"))],
)
async def get_trace_by_command_code(
    command_code: str,
    db: AsyncSessionDep,
) -> ResponseSchemaModel[TraceDetailResponse]:
    result = await trace_query_service.by_command_code(db, command_code)
    return cast("ResponseSchemaModel[TraceDetailResponse]", response_builder.success(data=build_trace_response(result)))


@router.get(
    "/dispatch/{dispatch_key}",
    summary="[biz:workline:list] 根据 dispatch_key 查询 Trace",
    response_model=ResponseSchemaModel[TraceDetailResponse],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:list"))],
)
async def get_trace_by_dispatch_key(
    dispatch_key: str,
    db: AsyncSessionDep,
) -> ResponseSchemaModel[TraceDetailResponse]:
    result = await trace_query_service.by_dispatch_key(db, dispatch_key)
    return cast("ResponseSchemaModel[TraceDetailResponse]", response_builder.success(data=build_trace_response(result)))


@router.get(
    "/exchange/{exchange_request_code}",
    summary="[biz:workline:list] 根据满箱交换请求编码查询 Trace 与资源证据",
    response_model=ResponseSchemaModel[TraceDetailResponse],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:list"))],
)
async def get_trace_by_exchange_request_code(
    exchange_request_code: str,
    db: AsyncSessionDep,
) -> ResponseSchemaModel[TraceDetailResponse]:
    result = await trace_query_service.by_exchange_request_code(db, exchange_request_code)
    return cast("ResponseSchemaModel[TraceDetailResponse]", response_builder.success(data=build_trace_response(result)))


@router.post(
    "/query",
    summary="[biz:workline:list] Trace 列表查询",
    response_model=ResponseSchemaModel[RuntimeTraceListResponse],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:list"))],
)
async def query_trace_list(
    payload: TraceQueryRequest,
    db: AsyncSessionDep,
) -> ResponseSchemaModel[RuntimeTraceListResponse]:
    result = await runtime_query_service.get_trace_list(db, payload)
    return cast("ResponseSchemaModel[RuntimeTraceListResponse]", response_builder.success(data=result))


__all__ = ["router"]
