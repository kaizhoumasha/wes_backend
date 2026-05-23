"""工作线诊断操作 API。"""

from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, status

from src.app.sys.services.event_stream_service import publish_deferred_sse_events
from src.app.workline.models.operation import (
    ManualOperationRequest,
    ReplayInboxRequest,
    ResolveRuntimeReconciliationRequest,
    SandboxAckRequest,
    SandboxCleanupRequest,
    SandboxCleanupResponse,
    SandboxEventRequest,
    SandboxExternalCallbackRequest,
    SandboxResultRequest,
    SandboxTemplatesResponse,
)
from src.app.workline.models.safety import (  # noqa: TC001 - FastAPI needs runtime annotation
    ClearWorkLineEstopRequest,
    SimulateWorkLineEstopRequest,
)
from src.app.workline.services import (
    WorkLineSafetyBlocked,
    sandbox_cleanup_service,
    workline_operation_service,
    workline_safety_service,
)
from src.core.rbac import RequirePermission
from src.core.response import ResponseSchemaModel, response_builder
from src.core.response.response_code import BusinessErrorCode, ResourceErrorCode
from src.core.security import require_auth
from src.database.dependencies import AsyncSessionDep  # noqa: TC001

router = APIRouter(tags=["工作线诊断操作"])


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value) if value is not None else None


def _inbox_response(inbox: Any) -> dict[str, Any]:
    return {
        "id": inbox.id,
        "kind": _enum_value(inbox.kind),
        "source_message_id": inbox.source_message_id,
        "trace_id": inbox.trace_id,
        "session_id": inbox.session_id,
        "workline_id": inbox.workline_id,
        "status": _enum_value(inbox.status),
    }


def _outbox_response(outbox: Any) -> dict[str, Any]:
    raw_payload = outbox.payload_json
    payload = cast("dict[str, Any]", raw_payload) if isinstance(raw_payload, dict) else {}
    return {
        "id": outbox.id,
        "session_id": outbox.session_id,
        "workline_id": outbox.workline_id,
        "dispatch_key": outbox.dispatch_key,
        "dispatch_type": _enum_value(outbox.dispatch_type),
        "target_type": _enum_value(outbox.target_type),
        "target_code": outbox.target_code,
        "status": _enum_value(outbox.status),
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
        "status": _enum_value(incident.status),
        "event_type": incident.event_type,
        "reason": incident.reason,
        "drain_status": incident.drain_status,
        "evidence_json": incident.evidence_json,
        "recovery_check_json": incident.recovery_check_json,
        "cleared_at": incident.cleared_at.isoformat() if incident.cleared_at else None,
        "cleared_by": incident.cleared_by,
    }


def _operation_error_response(exc: Exception) -> dict[str, Any]:
    message = str(exc)
    if "不存在" in message or "NOT_FOUND" in message:
        return response_builder.fail(code=ResourceErrorCode.NOT_FOUND, message=message)
    return response_builder.fail(code=BusinessErrorCode.INVALID_STATE, message=message)


def _enqueue_workline_processing() -> None:
    """触发 Workline Inbox 异步处理。"""

    from src.celery_app.app import celery_app

    cast("Any", celery_app).send_task(
        "src.celery_app.tasks.workline.process_inbox_batch",
        kwargs={"limit": 10},
    )


@router.get(
    "/sandbox/pending",
    summary="[biz:workline:list] 查询沙箱待处理 Outbox",
    response_model=ResponseSchemaModel[list[dict[str, Any]]],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:list"))],
)
async def get_sandbox_pending(
    db: AsyncSessionDep,
    limit: int = 50,
    workline_id: int | None = None,
    device_id: int | None = None,
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
    limit: int = 50,
    workline_id: int | None = None,
    device_id: int | None = None,
) -> ResponseSchemaModel[list[dict[str, Any]]]:
    items = await workline_operation_service.get_sandbox_completed(
        db, limit=limit, workline_id=workline_id, device_id=device_id
    )
    return cast("ResponseSchemaModel[list[dict[str, Any]]]", response_builder.success(data=items))


@router.post(
    "/sandbox/worklines/{workline_id}/cleanup",
    summary="[biz:workline:cleanup-sandbox] 清理工作线沙箱运行时数据",
    response_model=ResponseSchemaModel[SandboxCleanupResponse],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:cleanup-sandbox"))],
)
async def cleanup_sandbox_workline(
    workline_id: int,
    payload: SandboxCleanupRequest,
    db: AsyncSessionDep,
) -> ResponseSchemaModel[SandboxCleanupResponse]:
    try:
        if payload.dry_run:
            result = await sandbox_cleanup_service.preview_cleanup(db, workline_id=workline_id)
        else:
            result = await sandbox_cleanup_service.cleanup_workline(
                db,
                workline_id=workline_id,
                confirmation=payload.confirmation,
            )
            await db.commit()
            await publish_deferred_sse_events(db)
    except ValueError as exc:
        return cast("ResponseSchemaModel[SandboxCleanupResponse]", _operation_error_response(exc))
    return cast("ResponseSchemaModel[SandboxCleanupResponse]", response_builder.success(data=result))


@router.post(
    "/replay/inboxes/{inbox_id}",
    summary="[biz:workline:update] Replay 历史 Inbox",
    response_model=ResponseSchemaModel[dict[str, Any]],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:update"))],
)
async def replay_inbox(
    inbox_id: int,
    payload: ReplayInboxRequest,
    db: AsyncSessionDep,
) -> ResponseSchemaModel[dict[str, Any]]:
    try:
        replay = await workline_operation_service.replay_inbox(
            db,
            inbox_id=inbox_id,
            reason=payload.reason,
            operator_id=payload.operator_id,
        )
    except (ValueError, WorkLineSafetyBlocked) as exc:
        return cast("ResponseSchemaModel[dict[str, Any]]", _operation_error_response(exc))
    _enqueue_workline_processing()
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
    _enqueue_workline_processing()
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
        await db.commit()
        await publish_deferred_sse_events(db)
    except (ValueError, WorkLineSafetyBlocked) as exc:
        return cast("ResponseSchemaModel[dict[str, Any]]", _operation_error_response(exc))
    return cast(
        "ResponseSchemaModel[dict[str, Any]]",
        response_builder.success(data=_safety_incident_response(incident)),
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
        await db.commit()
        await publish_deferred_sse_events(db)
    except ValueError as exc:
        return cast("ResponseSchemaModel[dict[str, Any]]", _operation_error_response(exc))
    return cast(
        "ResponseSchemaModel[dict[str, Any]]",
        response_builder.success(data=_safety_incident_response(incident)),
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
    _enqueue_workline_processing()
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
    _enqueue_workline_processing()
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
    _enqueue_workline_processing()
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
