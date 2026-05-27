"""Runtime Hold API."""

from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse

from src.app.sys.services.event_stream_service import publish_deferred_sse_events
from src.app.workline.models.runtime_hold_api import (
    NgReasonOption,
    NgReturnItemResponse,
    ResolveRuntimeHoldRequest,
    ResolveRuntimeHoldResponse,
    RuntimeHoldDetailResponse,
    RuntimeHoldSummary,
)
from src.app.workline.services.runtime_hold_query_service import runtime_hold_query_service
from src.app.workline.services.runtime_hold_release_service import RuntimeHoldReleaseError, runtime_hold_release_service
from src.core.rbac import RequirePermission
from src.core.response import ResponseSchemaModel, response_builder
from src.core.response.response_code import BusinessErrorCode, ResourceErrorCode
from src.core.security import require_auth
from src.core.task_queue_gateway import task_queue_gateway
from src.database.dependencies import AsyncSessionDep  # noqa: TC001

router = APIRouter(tags=["Runtime Hold"])


_RUNTIME_HOLD_ERROR_CODES = {
    "RUNTIME_HOLD_MISSING_RELEASE_EVIDENCE": BusinessErrorCode.RUNTIME_HOLD_MISSING_RELEASE_EVIDENCE,
    "RUNTIME_HOLD_VERSION_CONFLICT": BusinessErrorCode.RUNTIME_HOLD_VERSION_CONFLICT,
    "RUNTIME_HOLD_EVIDENCE_CHANGED": BusinessErrorCode.RUNTIME_HOLD_EVIDENCE_CHANGED,
    "RUNTIME_HOLD_ALREADY_RESOLVED": BusinessErrorCode.RUNTIME_HOLD_ALREADY_RESOLVED,
    "RUNTIME_HOLD_SAFETY_ESTOP_REQUIRES_CLEAR_ESTOP": (
        BusinessErrorCode.RUNTIME_HOLD_SAFETY_ESTOP_REQUIRES_CLEAR_ESTOP
    ),
    "RUNTIME_HOLD_REASON_UNMAPPED": BusinessErrorCode.RUNTIME_HOLD_REASON_UNMAPPED,
    "RUNTIME_HOLD_HANDOFF_LOCATION_UNMAPPED": BusinessErrorCode.RUNTIME_HOLD_HANDOFF_LOCATION_UNMAPPED,
    "RUNTIME_HOLD_MATERIAL_CONFLICT": BusinessErrorCode.RUNTIME_HOLD_MATERIAL_CONFLICT,
}


def _json_error(code: Any, *, message: str, data: Any = None) -> JSONResponse:
    return JSONResponse(
        status_code=code.http_status,
        content=response_builder.fail(code=code, message=message, data=data),
    )


def _enqueue_workline_processing() -> None:
    """触发 Workline Inbox 异步处理。"""

    task_queue_gateway.enqueue_workline_inbox(limit=10)


async def _conflict_data(db: AsyncSessionDep, hold_id: int) -> dict[str, Any]:
    detail = await runtime_hold_query_service.get_detail(db, hold_id)
    if detail is None:
        return {"refresh_url": f"/api/v1/workline/runtime-holds/{hold_id}"}
    return {
        "current_hold_version": detail.summary.version,
        "current_status": detail.summary.status,
        "release_eligibility": detail.release_eligibility.model_dump(mode="json"),
        "refresh_url": f"/api/v1/workline/runtime-holds/{hold_id}",
    }


@router.get(
    "/runtime-holds/ng-reasons",
    summary="[biz:workline:view-runtime-hold] 查询 Runtime Hold NG 原因选项",
    response_model=ResponseSchemaModel[list[NgReasonOption]],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:view-runtime-hold"))],
)
async def get_runtime_hold_ng_reasons(
    db: AsyncSessionDep,
    plugin_key: str | None = None,
) -> ResponseSchemaModel[list[NgReasonOption]]:
    _ = db
    return cast(
        "ResponseSchemaModel[list[NgReasonOption]]",
        response_builder.success(data=runtime_hold_query_service.list_ng_reasons(plugin_key=plugin_key)),
    )


@router.get(
    "/runtime-holds",
    summary="[biz:workline:view-runtime-hold] 查询 Runtime Hold 列表",
    response_model=ResponseSchemaModel[list[RuntimeHoldSummary]],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:view-runtime-hold"))],
)
async def list_runtime_holds(
    db: AsyncSessionDep,
    workline_id: int | None = None,
    session_id: int | None = None,
    status: str | None = None,
    active_only: bool = True,
    limit: int = 100,
) -> ResponseSchemaModel[list[RuntimeHoldSummary]]:
    items = await runtime_hold_query_service.list_holds(
        db,
        workline_id=workline_id,
        session_id=session_id,
        status=status,
        active_only=active_only,
        limit=limit,
    )
    return cast("ResponseSchemaModel[list[RuntimeHoldSummary]]", response_builder.success(data=items))


@router.get(
    "/runtime-holds/{hold_id}",
    summary="[biz:workline:view-runtime-hold] 查看 Runtime Hold 明细",
    response_model=ResponseSchemaModel[RuntimeHoldDetailResponse],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:view-runtime-hold"))],
)
async def get_runtime_hold_detail(
    hold_id: int,
    db: AsyncSessionDep,
) -> ResponseSchemaModel[RuntimeHoldDetailResponse]:
    detail = await runtime_hold_query_service.get_detail(db, hold_id)
    if detail is None:
        return cast(
            "ResponseSchemaModel[RuntimeHoldDetailResponse]",
            response_builder.fail(code=ResourceErrorCode.NOT_FOUND, message=f"RuntimeHold 不存在: {hold_id}"),
        )
    return cast("ResponseSchemaModel[RuntimeHoldDetailResponse]", response_builder.success(data=detail))


@router.post(
    "/runtime-holds/{hold_id}/resolve",
    summary="[biz:workline:resolve-runtime-hold] 解除 Runtime Hold",
    response_model=ResponseSchemaModel[ResolveRuntimeHoldResponse],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:resolve-runtime-hold"))],
)
async def resolve_runtime_hold(
    hold_id: int,
    payload: ResolveRuntimeHoldRequest,
    db: AsyncSessionDep,
    current_user_id: Annotated[int, Depends(require_auth)],
) -> ResponseSchemaModel[ResolveRuntimeHoldResponse] | JSONResponse:
    try:
        result = await runtime_hold_release_service.resolve_hold(db, hold_id, payload, current_user_id)
        await db.commit()
        await publish_deferred_sse_events(db)
        if result.get("created_inbox_id") is not None:
            _enqueue_workline_processing()
        return cast("ResponseSchemaModel[ResolveRuntimeHoldResponse]", response_builder.success(data=result))
    except RuntimeHoldReleaseError as exc:
        code = _RUNTIME_HOLD_ERROR_CODES.get(exc.error_code, BusinessErrorCode.INVALID_STATE)
        if code.http_status == 409:
            data = await _conflict_data(db, hold_id)
            if exc.data is not None:
                data = {**data, **exc.data}
        else:
            data = exc.data
        return _json_error(code, message=exc.message, data=data)
    except ValueError as exc:
        return _json_error(BusinessErrorCode.INVALID_STATE, message=str(exc))


@router.get(
    "/ng-return-items",
    summary="[biz:workline:list-ng-return-item] 查询 NG Return Items",
    response_model=ResponseSchemaModel[list[NgReturnItemResponse]],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:list-ng-return-item"))],
)
async def list_ng_return_items(
    db: AsyncSessionDep,
    runtime_hold_id: int | None = None,
    status: str | None = None,
    material_identity_key: str | None = None,
    limit: int = 100,
) -> ResponseSchemaModel[list[NgReturnItemResponse]]:
    items = await runtime_hold_query_service.list_ng_return_items(
        db,
        runtime_hold_id=runtime_hold_id,
        status=status,
        material_identity_key=material_identity_key,
        limit=limit,
    )
    return cast("ResponseSchemaModel[list[NgReturnItemResponse]]", response_builder.success(data=items))


__all__ = [
    "get_runtime_hold_detail",
    "get_runtime_hold_ng_reasons",
    "list_ng_return_items",
    "list_runtime_holds",
    "resolve_runtime_hold",
    "router",
]
