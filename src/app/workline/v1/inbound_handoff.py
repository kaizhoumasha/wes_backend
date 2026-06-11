"""SMT 入库 handoff 查询与人工动作 API。"""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, Path, Query, status

from src.app.workline.models.smt_inbound_handoff import (
    SmtInboundHandoffActionResponse,
    SmtInboundHandoffDemandDetailResponse,
    SmtInboundHandoffDemandListResponse,
)
from src.app.workline.services import smt_inbound_handoff_service
from src.core.rbac import RequirePermission
from src.core.response import BusinessErrorCode, ResourceErrorCode, ResponseSchemaModel, response_builder
from src.database.dependencies import AsyncSessionDep  # noqa: TC001

router = APIRouter(tags=["SMT 入库 handoff"])


def _handoff_value_error_response(exc: ValueError) -> dict[str, object]:
    message = str(exc)
    if "不存在" in message or "未找到" in message or "not found" in message.lower():
        return response_builder.fail(code=ResourceErrorCode.NOT_FOUND, message=message)
    return response_builder.fail(code=BusinessErrorCode.INVALID_STATE, message=message)


@router.get(
    "/demands",
    summary="[biz:workline:list] 查询 SMT 入库 handoff demand 列表",
    response_model=ResponseSchemaModel[SmtInboundHandoffDemandListResponse],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:list"))],
)
async def list_inbound_handoff_demands(
    db: AsyncSessionDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    status: str | None = Query(default=None),
) -> ResponseSchemaModel[SmtInboundHandoffDemandListResponse]:
    result = await smt_inbound_handoff_service.list_handoff_demand_summaries(
        db,
        limit=limit,
        offset=offset,
        status=status,
    )
    return cast(
        "ResponseSchemaModel[SmtInboundHandoffDemandListResponse]",
        response_builder.success(data=result),
    )


@router.get(
    "/demands/{demand_id}",
    summary="[biz:workline:list] 查询 SMT 入库 handoff demand 详情",
    response_model=ResponseSchemaModel[SmtInboundHandoffDemandDetailResponse],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:list"))],
)
async def get_inbound_handoff_demand_detail(
    db: AsyncSessionDep,
    demand_id: int = Path(..., ge=1),
) -> ResponseSchemaModel[SmtInboundHandoffDemandDetailResponse]:
    result = await smt_inbound_handoff_service.get_handoff_demand_detail(db, demand_id)
    if result is None:
        return cast(
            "ResponseSchemaModel[SmtInboundHandoffDemandDetailResponse]",
            response_builder.fail(
                code=ResourceErrorCode.NOT_FOUND, message=f"SMT 入库 handoff demand 不存在: {demand_id}"
            ),
        )
    return cast(
        "ResponseSchemaModel[SmtInboundHandoffDemandDetailResponse]",
        response_builder.success(data=result),
    )


@router.post(
    "/source-items/{source_item_id}/actions/retry-source-pick",
    summary="[biz:workline:update] 重试 SMT 入库 handoff source-pick",
    response_model=ResponseSchemaModel[SmtInboundHandoffActionResponse],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:update"))],
)
async def retry_source_pick_action(
    db: AsyncSessionDep,
    source_item_id: int = Path(..., ge=1),
) -> ResponseSchemaModel[SmtInboundHandoffActionResponse]:
    try:
        result = await smt_inbound_handoff_service.retry_source_pick_action(db, source_item_id=source_item_id)
    except ValueError as exc:
        return cast("ResponseSchemaModel[SmtInboundHandoffActionResponse]", _handoff_value_error_response(exc))
    return cast(
        "ResponseSchemaModel[SmtInboundHandoffActionResponse]",
        response_builder.success(data=result),
    )


__all__ = ["router"]
