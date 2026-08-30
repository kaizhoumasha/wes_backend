"""WorkLine START 与 Safety target-only API。"""

from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Request, Response, status

from src.app.sys.services.event_stream_service import publish_deferred_sse_events
from src.app.workline.models.safety import ClearWorkLineEstopRequest  # noqa: TC001 - FastAPI runtime annotation
from src.app.workline.models.start import WorkLineStartErrorResponse, WorkLineStartRequest, WorkLineStartResponse
from src.app.workline.services import workline_safety_service
from src.app.workline.services.workline_start_service import (
    WorkLineStartConfigurationError,
    WorkLineStartIdempotencyConflictError,
    WorkLineStartInvalidStateError,
    WorkLineStartNotFoundError,
    WorkLineStartService,
)
from src.app.workline.unit_of_work import WorklineUnitOfWork
from src.core.rbac import RequirePermission
from src.core.response import ResponseCode, ResponseSchemaModel, response_builder
from src.core.response.response_code import BusinessErrorCode, ResourceErrorCode, ServerErrorCode
from src.core.security import require_auth
from src.database.dependencies import AsyncSessionDep  # noqa: TC001
from src.utils.value_normalization import enum_value

router = APIRouter(tags=["工作线诊断操作"])


def _safety_incident_response(incident: Any) -> dict[str, Any]:
    return {
        "id": incident.id,
        "workline_id": incident.workline_id,
        "status": enum_value(incident.status),
        "event_type": incident.event_type,
        "reason": incident.reason,
        "drain_status": incident.drain_status,
        "evidence_json": incident.evidence_json,
        "recovery_check_json": incident.recovery_check_json,
        "cleared_at": incident.cleared_at.isoformat() if incident.cleared_at else None,
        "cleared_by": incident.cleared_by,
    }


def _clear_estop_response(incident: Any) -> dict[str, Any]:
    data = _safety_incident_response(incident)
    release_evidence = getattr(incident, "release_evidence_json", None)
    if isinstance(release_evidence, dict):
        data["workline_runtime_status"] = release_evidence.get("workline_runtime_status")
    data["release_message"] = "已解除冻结，等待现场 START"
    return data


def _operation_error_response(exc: Exception) -> dict[str, Any]:
    message = str(exc)
    if "不存在" in message or "NOT_FOUND" in message:
        return response_builder.fail(code=ResourceErrorCode.NOT_FOUND, message=message)
    return response_builder.fail(code=BusinessErrorCode.INVALID_STATE, message=message)


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
    summary="[biz:workline:start] 启动 WorkLine 并激活运行代际",
    response_model=ResponseSchemaModel[WorkLineStartResponse | WorkLineStartErrorResponse],
    responses={
        200: {"model": ResponseSchemaModel[WorkLineStartResponse], "description": "START 成功或幂等 replay 成功"},
        404: {"model": ResponseSchemaModel[WorkLineStartErrorResponse], "description": "WorkLine 不存在"},
        409: {"model": ResponseSchemaModel[WorkLineStartErrorResponse], "description": "START 状态或幂等身份冲突"},
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
) -> ResponseSchemaModel[WorkLineStartResponse | WorkLineStartErrorResponse]:
    """在一个事务内 replay 或创建完整 LineRunEpoch。"""

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

    try:
        async with WorklineUnitOfWork(db=db) as uow:
            result = await service.start(
                uow.session,
                workline_id=workline_id,
                request_id=payload.request_id,
            )
            await uow.commit()
    except WorkLineStartNotFoundError as exc:
        return _workline_start_error_response(
            response,
            exc,
            code=ResourceErrorCode.NOT_FOUND,
            reason="WORKLINE_NOT_FOUND",
        )
    except WorkLineStartIdempotencyConflictError as exc:
        return _workline_start_error_response(
            response,
            exc,
            code=ResourceErrorCode.CONFLICT,
            reason="IDEMPOTENCY_CONFLICT",
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

    epoch = result.epoch
    data = WorkLineStartResponse(
        line_run_epoch_id=epoch.id,
        epoch_code=epoch.epoch_code,
        workline_id=epoch.workline_id,
        plugin_key=epoch.plugin_key,
        plugin_version=epoch.plugin_version,
        flow_mode=epoch.flow_mode,
        epoch_status=enum_value(epoch.status),
        epoch_started_at=epoch.started_at,
        epoch_closed_at=epoch.closed_at,
        current_workline_runtime_status=result.current_workline_runtime_status,
        created=result.created,
    )
    return cast(
        "ResponseSchemaModel[WorkLineStartResponse]",
        response_builder.success(data=data.model_dump(mode="json")),
    )


@router.post(
    "/safety/worklines/{workline_id}/clear-estop",
    summary="[biz:workline:clear-estop] 人工确认 checklist 后清除工作线急停",
    response_model=ResponseSchemaModel[dict[str, Any]],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:clear-estop"))],
)
async def clear_workline_estop(
    workline_id: int,
    payload: ClearWorkLineEstopRequest,
    db: AsyncSessionDep,
    current_user_id: Annotated[int, Depends(require_auth)],
) -> ResponseSchemaModel[dict[str, Any]]:
    try:
        incident = await workline_safety_service.clear_estop(
            db,
            workline_id=workline_id,
            checks=payload.checks,
            reason=payload.reason,
            operator_id=current_user_id,
        )
        async with WorklineUnitOfWork(db=db) as uow:
            await uow.commit()
        await publish_deferred_sse_events(db)
    except ValueError as exc:
        return cast("ResponseSchemaModel[dict[str, Any]]", _operation_error_response(exc))
    return cast(
        "ResponseSchemaModel[dict[str, Any]]",
        response_builder.success(data=_clear_estop_response(incident)),
    )


__all__ = ["router"]
