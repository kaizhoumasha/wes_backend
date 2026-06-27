"""非生产集成调试定位 API。"""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, Query, status

from src.app.workline.models.integration_debug import (
    IntegrationDebugCaseListResponse,
    IntegrationDebugCaseResponse,
)
from src.app.workline.services import integration_debug_service
from src.core.rbac import RequirePermission
from src.core.response import ResponseSchemaModel, response_builder
from src.core.response.response_code import ResourceErrorCode
from src.database.dependencies import AsyncSessionDep  # noqa: TC001

router = APIRouter(tags=["工作线集成调试"])


@router.get(
    "/cases/latest",
    summary="[biz:workline:list] 查询最新集成调试案件",
    response_model=ResponseSchemaModel[IntegrationDebugCaseListResponse],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:list"))],
)
async def get_latest_integration_debug_cases(
    db: AsyncSessionDep,
    limit: int = Query(default=10, ge=1, le=50),
    workline_id: int | None = Query(default=None, ge=1, description="按工作线过滤"),
    device_id: int | None = Query(default=None, ge=1, description="按设备过滤"),
    status: str | None = Query(default=None, description="按 case 状态过滤"),
) -> ResponseSchemaModel[IntegrationDebugCaseListResponse]:
    result = await integration_debug_service.latest_cases(
        db,
        limit=limit,
        workline_id=workline_id,
        device_id=device_id,
        status=status,
    )
    return cast("ResponseSchemaModel[IntegrationDebugCaseListResponse]", response_builder.success(data=result))


@router.get(
    "/cases/lookup",
    summary="[biz:workline:list] 按锚点查询集成调试案件",
    response_model=ResponseSchemaModel[IntegrationDebugCaseResponse],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:list"))],
)
async def lookup_integration_debug_case(
    anchor_type: str,
    anchor: str,
    db: AsyncSessionDep,
    include_raw: bool = False,
) -> ResponseSchemaModel[IntegrationDebugCaseResponse]:
    result = await integration_debug_service.lookup_case(
        db,
        anchor_type=anchor_type,
        anchor=anchor,
        include_raw=include_raw,
    )
    if result is None:
        return cast(
            "ResponseSchemaModel[IntegrationDebugCaseResponse]",
            response_builder.fail(code=ResourceErrorCode.NOT_FOUND, message="未找到匹配的集成调试案件"),
        )
    return cast("ResponseSchemaModel[IntegrationDebugCaseResponse]", response_builder.success(data=result))


__all__ = ["router"]
