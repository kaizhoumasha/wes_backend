"""工作线诊断操作 API。"""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, Depends, status

from src.app.workline.models.operation import (
    ManualOperationRequest,
    ReplayInboxRequest,
    SandboxAckRequest,
    SandboxEventRequest,
    SandboxResultRequest,
    SandboxTemplatesResponse,
)
from src.app.workline.services import workline_operation_service
from src.core.rbac import RequirePermission
from src.core.response import ResponseSchemaModel, response_builder
from src.core.response.response_code import BusinessErrorCode, ResourceErrorCode
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
    }


def _operation_error_response(exc: ValueError) -> dict[str, Any]:
    message = str(exc)
    if "不存在" in message:
        return response_builder.fail(code=ResourceErrorCode.NOT_FOUND, message=message)
    return response_builder.fail(code=BusinessErrorCode.INVALID_STATE, message=message)


def _enqueue_workline_processing() -> None:
    """触发 Workline Inbox 异步处理。"""

    from src.celery_app.app import celery_app

    cast("Any", celery_app).send_task(
        "src.celery_app.tasks.workline.process_inbox_batch",
        kwargs={"limit": 10},
    )


@router.post(
    "/sandbox/process",
    summary="[biz:workline:update] 手动触发编排处理",
    response_model=ResponseSchemaModel[dict[str, Any]],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:update"))],
)
async def trigger_sandbox_process() -> ResponseSchemaModel[dict[str, Any]]:
    """手动触发工作线编排处理（用于沙箱调试，Celery worker 未启动时）"""
    _enqueue_workline_processing()
    return cast("ResponseSchemaModel[dict[str, Any]]", response_builder.success(data={"triggered": True}))


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
    except ValueError as exc:
        return cast("ResponseSchemaModel[dict[str, Any]]", _operation_error_response(exc))
    _enqueue_workline_processing()
    return cast("ResponseSchemaModel[dict[str, Any]]", response_builder.success(data=_inbox_response(replay)))


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
    except ValueError as exc:
        return cast("ResponseSchemaModel[dict[str, Any]]", _operation_error_response(exc))
    _enqueue_workline_processing()
    return cast("ResponseSchemaModel[dict[str, Any]]", response_builder.success(data=_inbox_response(inbox)))


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
