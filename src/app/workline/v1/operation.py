"""WorkLine START target-only API。"""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, Request, Response, status

from src.app.workline.models.start import WorkLineStartErrorResponse, WorkLineStartRequest, WorkLineStartResponse
from src.app.workline.services.workline_start_service import (
    WorkLineStartConfigurationError,
    WorkLineStartInvalidStateError,
    WorkLineStartNotFoundError,
    WorkLineStartService,
    WorkLineStartVersionConflictError,
)
from src.app.workline.unit_of_work import WorklineUnitOfWork
from src.core.rbac import RequirePermission
from src.core.response import ResponseCode, ResponseSchemaModel, response_builder
from src.core.response.response_code import ResourceErrorCode, ServerErrorCode
from src.database.dependencies import AsyncSessionDep, CacheDep  # noqa: TC001

router = APIRouter(tags=["工作线诊断操作"])

type WorkLineStartApiResponse = (
    ResponseSchemaModel[WorkLineStartResponse] | ResponseSchemaModel[WorkLineStartErrorResponse]
)


def _workline_start_error_response(
    response: Response,
    exc: Exception,
    *,
    code: ResponseCode,
    reason: str,
) -> ResponseSchemaModel[WorkLineStartErrorResponse]:
    response.status_code = code.http_status
    return cast(
        "ResponseSchemaModel[WorkLineStartErrorResponse]",
        response_builder.fail(code=code, message=str(exc), data={"reason": reason}),
    )


@router.post(
    "/worklines/{workline_id}/start",
    summary="[biz:workline:start] 启动 WorkLine 当前插件",
    response_model=ResponseSchemaModel[WorkLineStartResponse | WorkLineStartErrorResponse],
    responses={
        200: {"model": ResponseSchemaModel[WorkLineStartResponse], "description": "START 成功"},
        404: {"model": ResponseSchemaModel[WorkLineStartErrorResponse], "description": "WorkLine 不存在"},
        409: {"model": ResponseSchemaModel[WorkLineStartErrorResponse], "description": "START 状态或版本冲突"},
        503: {"model": ResponseSchemaModel[WorkLineStartErrorResponse], "description": "START 服务不可用"},
    },
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:start"))],
)
async def start_workline(
    workline_id: int,
    payload: WorkLineStartRequest,
    request: Request,
    response: Response,
    db: AsyncSessionDep,
    cache: CacheDep,
) -> WorkLineStartApiResponse:
    """在同一事务内校验版本并启动当前插件。"""

    service_candidate = getattr(request.app.state, "workline_start_service", None)
    if service_candidate is None:
        response.status_code = ServerErrorCode.SERVICE_UNAVAILABLE.http_status
        return cast(
            "ResponseSchemaModel[WorkLineStartErrorResponse]",
            response_builder.fail(
                code=ServerErrorCode.SERVICE_UNAVAILABLE,
                data={"reason": "SERVICE_UNAVAILABLE"},
            ),
        )
    service = cast("WorkLineStartService", service_candidate)

    result = None
    try:
        async with WorklineUnitOfWork(db=db) as uow:
            result = await service.start(
                uow.session,
                workline_id=workline_id,
                version=payload.version,
            )
            await uow.commit()
        from src.app.workline.services.workline_service import workline_service

        await workline_service.invalidate_cache(cache, workline_id, invalidate_list=True)
    except WorkLineStartNotFoundError as exc:
        return _workline_start_error_response(
            response,
            exc,
            code=ResourceErrorCode.NOT_FOUND,
            reason="WORKLINE_NOT_FOUND",
        )
    except WorkLineStartVersionConflictError as exc:
        return _workline_start_error_response(
            response,
            exc,
            code=ResourceErrorCode.CONFLICT,
            reason="VERSION_CONFLICT",
        )
    except WorkLineStartInvalidStateError as exc:
        return _workline_start_error_response(
            response,
            exc,
            code=ResourceErrorCode.CONFLICT,
            reason="INVALID_STATE",
        )
    except WorkLineStartConfigurationError as exc:
        return _workline_start_error_response(
            response,
            exc,
            code=ResourceErrorCode.CONFLICT,
            reason="CONFIGURATION_INVALID",
        )

    if result is None or result.id is None:
        raise RuntimeError("START 未返回持久化 WorkLine")
    data = WorkLineStartResponse(
        workline_id=result.id,
        version=result.version,
        plugin_key=cast("str", result.plugin_key),
        plugin_version=cast("str", result.plugin_version),
        flow_mode=result.flow_mode,
        is_active=result.is_active,
    )
    return cast(
        "ResponseSchemaModel[WorkLineStartResponse]", response_builder.success(data=data.model_dump(mode="json"))
    )


__all__ = ["router"]
