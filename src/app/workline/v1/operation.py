"""工作线诊断操作 API。"""

from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Query, Response, status

from src.app.callback.models.ingress_response import (
    CallbackEventAcceptedResponse,
    CallbackRejectedResponse,
    build_callback_event_accepted_response,
    build_callback_rejected_response,
)
from src.app.runtime.capabilities.material_flow.start_admission_service import start_admission_service
from src.app.runtime.orchestration.models.operation import (
    ManualOperationRequest,
    ReplayInboxRequest,
    ResolveRuntimeReconciliationRequest,
    SandboxAckRequest,
    SandboxEventRequest,
    SandboxExternalCallbackRequest,
    SandboxResultRequest,
    SandboxTemplatesResponse,
    SandboxWorklineStartRequest,
)
from src.app.runtime.orchestration.services.intent import operation_service
from src.app.runtime.orchestration.services.runtime_inbox import (
    RuntimeInboxAuditPersistenceFailed,
    RuntimeInboxConflict,
    RuntimeInboxNotFound,
    RuntimeInboxReplayNotAllowed,
)
from src.app.sys.services.event_stream_service import publish_deferred_sse_events
from src.app.workline.models.safety import (  # noqa: TC001 - FastAPI needs runtime annotation
    ClearWorkLineEstopRequest,
    SimulateWorkLineEstopRequest,
)
from src.app.workline.services import WorkLineSafetyBlocked, workline_safety_service
from src.app.workline.unit_of_work import WorklineUnitOfWork
from src.core.rbac import RequirePermission
from src.core.response import ResponseSchemaModel, response_builder
from src.core.response.response_code import BusinessErrorCode, ResourceErrorCode, ServerErrorCode
from src.core.security import require_auth
from src.core.task_queue_gateway import task_queue_gateway
from src.database.dependencies import AsyncSessionDep  # noqa: TC001
from src.utils.value_normalization import enum_value

workline_operation_service = operation_service.workline_operation_service

router = APIRouter(tags=["工作线诊断操作"])


def _inbox_response(inbox: Any) -> dict[str, Any]:
    return {
        "id": inbox.id,
        "kind": enum_value(inbox.kind),
        "source_message_id": getattr(inbox, "source_message_id", None) or getattr(inbox, "source_event_id", None),
        "trace_id": inbox.trace_id,
        "session_id": inbox.workline_session_id,
        "workline_id": inbox.workline_id,
        "status": enum_value(inbox.status),
    }


def _outbox_response(outbox: Any) -> dict[str, Any]:
    raw_payload = outbox.payload_json
    payload = cast("dict[str, Any]", raw_payload) if isinstance(raw_payload, dict) else {}
    return {
        "id": outbox.id,
        "session_id": outbox.session_id,
        "workline_id": outbox.workline_id,
        "dispatch_key": outbox.dispatch_key,
        "dispatch_type": enum_value(outbox.dispatch_type),
        "target_type": enum_value(outbox.target_type),
        "target_code": outbox.target_code,
        "status": enum_value(outbox.status),
        "payload_json": payload,
        "source_device": None,
        "last_error": getattr(outbox, "last_error", None),
        "command_status": getattr(outbox, "command_status", None),
        "is_current_action": getattr(outbox, "is_current_action", None),
        "is_actionable": getattr(outbox, "is_actionable", None),
        "runtime_hold_id": getattr(outbox, "runtime_hold_id", None),
        "failure_summary": getattr(outbox, "failure_summary", None),
        "history_group_key": getattr(outbox, "history_group_key", None),
    }


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


def _sandbox_start_response(
    *,
    workline_id: int,
    device_code: str,
    trace_id: str,
    admission: Any,
) -> CallbackEventAcceptedResponse | CallbackRejectedResponse:
    if bool(getattr(admission, "accepted", False)):
        return build_callback_event_accepted_response(
            status="accepted",
            device_code=device_code,
            request_id=trace_id,
            trace_id=trace_id,
            event_id=trace_id,
            causation_id=None,
            diagnostic=getattr(admission, "diagnostic", None),
        )

    diagnostic = dict(getattr(admission, "diagnostic", None) or {})
    message = getattr(admission, "message", None)
    if isinstance(message, str) and message:
        diagnostic.setdefault("message", message)
    diagnostic.setdefault("workline_id", workline_id)
    return build_callback_rejected_response(
        reason_code=getattr(admission, "reason_code", None) or "START_ADMISSION_FAILED",
        diagnostic=diagnostic,
    )


def _operation_error_response(exc: Exception) -> dict[str, Any]:
    message = str(exc)
    if "不存在" in message or "NOT_FOUND" in message:
        return response_builder.fail(code=ResourceErrorCode.NOT_FOUND, message=message)
    return response_builder.fail(code=BusinessErrorCode.INVALID_STATE, message=message)


def _enqueue_runtime_inbox_processing() -> None:
    """触发 Runtime Inbox 异步处理。"""

    task_queue_gateway.enqueue_runtime_inbox(limit=10)


@router.get(
    "/sandbox/pending",
    summary="[biz:workline:list] 查询沙箱待处理 Outbox",
    response_model=ResponseSchemaModel[list[dict[str, Any]]],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:list"))],
)
async def get_sandbox_pending(
    db: AsyncSessionDep,
    limit: int = Query(default=50, ge=1, le=500, description="最多返回条数"),
    workline_id: int | None = Query(default=None, ge=1, description="按工作线过滤"),
    device_id: int | None = Query(default=None, ge=1, description="按设备过滤"),
) -> ResponseSchemaModel[list[dict[str, Any]]]:
    items = await workline_operation_service.get_sandbox_pending(
        db, limit=limit, workline_id=workline_id, device_id=device_id
    )
    return cast(
        "ResponseSchemaModel[list[dict[str, Any]]]", response_builder.success(data=[_outbox_response(i) for i in items])
    )


@router.get(
    "/sandbox/completed",
    summary="[biz:workline:list] 查询沙箱已完成 Outbox",
    response_model=ResponseSchemaModel[list[dict[str, Any]]],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:list"))],
)
async def get_sandbox_completed(
    db: AsyncSessionDep,
    limit: int = Query(default=50, ge=1, le=500, description="最多返回条数"),
    workline_id: int | None = Query(default=None, ge=1, description="按工作线过滤"),
    device_id: int | None = Query(default=None, ge=1, description="按设备过滤"),
) -> ResponseSchemaModel[list[dict[str, Any]]]:
    items = await workline_operation_service.get_sandbox_completed(
        db, limit=limit, workline_id=workline_id, device_id=device_id
    )
    return cast("ResponseSchemaModel[list[dict[str, Any]]]", response_builder.success(data=items))


@router.post(
    "/replay/inboxes/{inbox_id}",
    summary="[biz:workline:update] Replay 历史 Inbox",
    response_model=ResponseSchemaModel[dict[str, Any]],
    status_code=status.HTTP_200_OK,
    responses={
        400: {"model": ResponseSchemaModel[dict[str, Any]], "description": "源 Inbox 当前状态不允许 Replay"},
        404: {"model": ResponseSchemaModel[dict[str, Any]], "description": "源 Inbox 或所属工作线不存在"},
        409: {"model": ResponseSchemaModel[dict[str, Any]], "description": "Replay 幂等身份冲突"},
        503: {"model": ResponseSchemaModel[dict[str, Any]], "description": "Replay 审计证据暂时无法持久化"},
    },
    dependencies=[Depends(RequirePermission("biz:workline:update"))],
)
async def replay_inbox(
    inbox_id: int,
    payload: ReplayInboxRequest,
    response: Response,
    db: AsyncSessionDep,
    current_user_id: Annotated[int, Depends(require_auth)],
) -> ResponseSchemaModel[dict[str, Any]]:
    try:
        replay = await workline_operation_service.replay_inbox(
            db,
            inbox_id=inbox_id,
            request_id=payload.request_id,
            actor=str(current_user_id),
            reason=payload.reason,
        )
    except RuntimeInboxNotFound as exc:
        response.status_code = ResourceErrorCode.NOT_FOUND.http_status
        return cast(
            "ResponseSchemaModel[dict[str, Any]]",
            response_builder.fail(code=ResourceErrorCode.NOT_FOUND, message=str(exc)),
        )
    except RuntimeInboxReplayNotAllowed as exc:
        error_code = (
            ResourceErrorCode.NOT_FOUND
            if exc.reason_code == "SOURCE_WORKLINE_NOT_FOUND"
            else BusinessErrorCode.INVALID_STATE
        )
        response.status_code = error_code.http_status
        return cast(
            "ResponseSchemaModel[dict[str, Any]]",
            response_builder.fail(code=error_code, message=str(exc)),
        )
    except RuntimeInboxAuditPersistenceFailed as exc:
        response.status_code = ServerErrorCode.RUNTIME_INBOX_AUDIT_PERSISTENCE_FAILED.http_status
        return cast(
            "ResponseSchemaModel[dict[str, Any]]",
            response_builder.fail(code=ServerErrorCode.RUNTIME_INBOX_AUDIT_PERSISTENCE_FAILED, message=str(exc)),
        )
    except RuntimeInboxConflict as exc:
        response.status_code = ResourceErrorCode.CONFLICT.http_status
        return cast(
            "ResponseSchemaModel[dict[str, Any]]",
            response_builder.fail(code=ResourceErrorCode.CONFLICT, message=str(exc)),
        )
    except WorkLineSafetyBlocked as exc:
        response.status_code = BusinessErrorCode.INVALID_STATE.http_status
        return cast(
            "ResponseSchemaModel[dict[str, Any]]",
            response_builder.fail(code=BusinessErrorCode.INVALID_STATE, message=str(exc)),
        )
    _enqueue_runtime_inbox_processing()
    return cast("ResponseSchemaModel[dict[str, Any]]", response_builder.success(data=_inbox_response(replay)))


@router.post(
    "/reconciliations/sessions/{session_id}/resolve",
    summary="[biz:workline:resolve-reconciliation] 解除 runtime reconciliation 隔离，不重发设备命令、不调用 timeout 插件处理、释放安全停靠队列",
    response_model=ResponseSchemaModel[dict[str, Any]],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:resolve-reconciliation"))],
)
async def resolve_runtime_reconciliation(
    session_id: int,
    payload: ResolveRuntimeReconciliationRequest,
    db: AsyncSessionDep,
    current_user_id: Annotated[int, Depends(require_auth)],
) -> ResponseSchemaModel[dict[str, Any]]:
    try:
        result = await workline_operation_service.resolve_runtime_reconciliation(
            db,
            session_id=session_id,
            request=payload,
            operator_id=current_user_id,
        )
        await publish_deferred_sse_events(db)
    except ValueError as exc:
        return cast("ResponseSchemaModel[dict[str, Any]]", _operation_error_response(exc))
    return cast("ResponseSchemaModel[dict[str, Any]]", response_builder.success(data=result))


@router.post(
    "/manual/sessions/{session_id}",
    summary="[biz:workline:update] 创建人工操作",
    response_model=ResponseSchemaModel[dict[str, Any]],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:update"))],
)
async def create_manual_operation(
    session_id: int,
    payload: ManualOperationRequest,
    db: AsyncSessionDep,
) -> ResponseSchemaModel[dict[str, Any]]:
    try:
        inbox = await workline_operation_service.create_manual_operation(
            db,
            session_id=session_id,
            operation=payload.operation,
            operator_id=payload.operator_id,
            reason=payload.reason,
        )
    except (ValueError, WorkLineSafetyBlocked) as exc:
        return cast("ResponseSchemaModel[dict[str, Any]]", _operation_error_response(exc))
    _enqueue_runtime_inbox_processing()
    return cast("ResponseSchemaModel[dict[str, Any]]", response_builder.success(data=_inbox_response(inbox)))


@router.post(
    "/sandbox/worklines/{workline_id}/simulate-estop",
    summary="[biz:workline:update] 沙箱模拟 WorkLine 软件急停冻结",
    response_model=ResponseSchemaModel[dict[str, Any]],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:update"))],
)
async def simulate_workline_estop(
    workline_id: int,
    payload: SimulateWorkLineEstopRequest,
    db: AsyncSessionDep,
) -> ResponseSchemaModel[dict[str, Any]]:
    """沙箱专用安全模拟入口；不通过普通 sandbox event 流。"""

    try:
        incident = await workline_safety_service.simulate_estop(
            db,
            workline_id=workline_id,
            reason=payload.reason,
            source_device_id=payload.source_device_id,
            payload=payload.payload,
        )
        async with WorklineUnitOfWork(db=db) as uow:
            await uow.commit()
        await publish_deferred_sse_events(db)
    except (ValueError, WorkLineSafetyBlocked) as exc:
        return cast("ResponseSchemaModel[dict[str, Any]]", _operation_error_response(exc))
    return cast(
        "ResponseSchemaModel[dict[str, Any]]",
        response_builder.success(data=_safety_incident_response(incident)),
    )


@router.post(
    "/sandbox/worklines/{workline_id}/start",
    summary="[biz:workline:update] 沙箱模拟现场硬件 START",
    response_model=ResponseSchemaModel[CallbackEventAcceptedResponse | CallbackRejectedResponse],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:update"))],
)
async def start_sandbox_workline(
    workline_id: int,
    payload: SandboxWorklineStartRequest,
    db: AsyncSessionDep,
) -> ResponseSchemaModel[CallbackEventAcceptedResponse | CallbackRejectedResponse]:
    trace_id = payload.trace_id or f"sandbox:start:{workline_id}"
    admission = await start_admission_service.admit_start(
        db,
        workline_id,
        source_device_code=payload.device_code,
        request_id=trace_id,
        trace_id=trace_id,
    )
    if bool(getattr(admission, "accepted", False)):
        await publish_deferred_sse_events(db)
    return cast(
        "ResponseSchemaModel[CallbackEventAcceptedResponse | CallbackRejectedResponse]",
        response_builder.success(
            message=getattr(admission, "message", "START 准入完成"),
            data=_sandbox_start_response(
                workline_id=workline_id,
                device_code=payload.device_code,
                trace_id=trace_id,
                admission=admission,
            ),
        ),
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


@router.post(
    "/sandbox/events",
    summary="[biz:workline:update] 沙箱发送 Event",
    response_model=ResponseSchemaModel[dict[str, Any]],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:update"))],
)
async def submit_sandbox_event(
    payload: SandboxEventRequest,
    db: AsyncSessionDep,
) -> ResponseSchemaModel[dict[str, Any]]:
    try:
        inbox = await workline_operation_service.submit_sandbox_event(
            db,
            workline_id=payload.workline_id,
            device_id=payload.device_id,
            event_type=payload.event_type,
            trace_id=payload.trace_id,
            session_id=payload.session_id,
            payload=payload.payload,
            timestamp=payload.timestamp,
        )
    except ValueError as exc:
        return cast("ResponseSchemaModel[dict[str, Any]]", _operation_error_response(exc))
    _enqueue_runtime_inbox_processing()
    return cast("ResponseSchemaModel[dict[str, Any]]", response_builder.success(data=_inbox_response(inbox)))


@router.post(
    "/sandbox/ack",
    summary="[biz:workline:update] 沙箱模拟 Command ACK",
    response_model=ResponseSchemaModel[dict[str, Any]],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:update"))],
)
async def submit_sandbox_ack(
    payload: SandboxAckRequest,
    db: AsyncSessionDep,
) -> ResponseSchemaModel[dict[str, Any]]:
    try:
        outbox = await workline_operation_service.submit_sandbox_ack(
            db,
            dispatch_key=payload.dispatch_key,
        )
    except ValueError as exc:
        return cast("ResponseSchemaModel[dict[str, Any]]", _operation_error_response(exc))
    await publish_deferred_sse_events(db)
    return cast("ResponseSchemaModel[dict[str, Any]]", response_builder.success(data=_outbox_response(outbox)))


@router.post(
    "/sandbox/external-callbacks",
    summary="[biz:workline:update] 沙箱模拟 External HTTP 回调",
    response_model=ResponseSchemaModel[dict[str, Any]],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:update"))],
)
async def submit_sandbox_external_callback(
    payload: SandboxExternalCallbackRequest,
    db: AsyncSessionDep,
) -> ResponseSchemaModel[dict[str, Any]]:
    try:
        inbox = await workline_operation_service.submit_sandbox_external_callback(
            db,
            dispatch_key=payload.dispatch_key,
            callback_type=payload.callback_type,
            payload=payload.payload,
            source_system=payload.source_system,
            source_event_id=payload.source_event_id,
            source_version=payload.source_version,
            request_id=payload.request_id,
            occurred_at=payload.occurred_at,
            timestamp=payload.timestamp,
            signature=payload.signature,
        )
    except ValueError as exc:
        return cast("ResponseSchemaModel[dict[str, Any]]", _operation_error_response(exc))
    _enqueue_runtime_inbox_processing()
    return cast("ResponseSchemaModel[dict[str, Any]]", response_builder.success(data=_inbox_response(inbox)))


@router.post(
    "/results",
    summary="[biz:workline:update] 沙箱模拟 Command Result",
    response_model=ResponseSchemaModel[dict[str, Any]],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:update"))],
)
async def submit_sandbox_result(
    payload: SandboxResultRequest,
    db: AsyncSessionDep,
) -> ResponseSchemaModel[dict[str, Any]]:
    try:
        inbox = await workline_operation_service.submit_sandbox_result(
            db,
            command_code=payload.command_code,
            device_code=payload.device_code,
            result=payload.result,
            payload=payload.payload,
            error_detail=payload.error_detail,
            timestamp=payload.timestamp,
        )
    except ValueError as exc:
        return cast("ResponseSchemaModel[dict[str, Any]]", _operation_error_response(exc))
    await publish_deferred_sse_events(db)
    _enqueue_runtime_inbox_processing()
    return cast("ResponseSchemaModel[dict[str, Any]]", response_builder.success(data=_inbox_response(inbox)))


@router.get(
    "/sandbox/templates",
    summary="[biz:workline:list] 获取沙箱模板",
    response_model=ResponseSchemaModel[SandboxTemplatesResponse],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:list"))],
)
async def get_sandbox_templates(
    db: AsyncSessionDep,
    workline_id: int,
    device_id: int | None = None,
) -> ResponseSchemaModel[SandboxTemplatesResponse]:
    try:
        templates = await workline_operation_service.get_sandbox_templates(
            db, workline_id=workline_id, device_id=device_id
        )
    except ValueError as exc:
        return cast(
            "ResponseSchemaModel[SandboxTemplatesResponse]",
            response_builder.fail(code=ResourceErrorCode.NOT_FOUND, message=str(exc)),
        )
    return cast("ResponseSchemaModel[SandboxTemplatesResponse]", response_builder.success(data=templates))


__all__ = ["router"]
