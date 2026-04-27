"""工作线诊断操作 API。"""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field

from src.app.workline.services import workline_operation_service
from src.core.rbac import RequirePermission
from src.core.response import ResponseSchemaModel, response_builder
from src.core.response.response_code import BusinessErrorCode, ResourceErrorCode
from src.database.dependencies import AsyncSessionDep  # noqa: TC001

router = APIRouter(tags=["工作线诊断操作"])


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value) if value is not None else None


class ReplayInboxRequest(BaseModel):
    """Replay 请求。"""

    reason: str = Field(min_length=1, max_length=500)
    operator_id: str | None = Field(default=None, max_length=100)


class ManualOperationRequest(BaseModel):
    """人工操作请求。"""

    operation: str = Field(pattern="^(HOLD|RESUME|CANCEL)$")
    operator_id: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=500)


def _inbox_response(inbox: Any) -> dict[str, Any]:
    return {
        "id": inbox.id,
        "kind": _enum_value(getattr(inbox, "kind", None)),
        "source_message_id": getattr(inbox, "source_message_id", None),
        "trace_id": getattr(inbox, "trace_id", None),
        "session_id": getattr(inbox, "session_id", None),
        "workline_id": getattr(inbox, "workline_id", None),
        "status": _enum_value(getattr(inbox, "status", None)),
    }


def _outbox_response(outbox: Any) -> dict[str, Any]:
    return {
        "id": outbox.id,
        "session_id": getattr(outbox, "session_id", None),
        "workline_id": getattr(outbox, "workline_id", None),
        "dispatch_key": getattr(outbox, "dispatch_key", None),
        "dispatch_type": _enum_value(getattr(outbox, "dispatch_type", None)),
        "target_type": _enum_value(getattr(outbox, "target_type", None)),
        "target_code": getattr(outbox, "target_code", None),
        "status": _enum_value(getattr(outbox, "status", None)),
        "payload_json": getattr(outbox, "payload_json", None),
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


@router.get(
    "/sandbox/pending",
    summary="[biz:workline:list] 查询沙箱待处理 Outbox",
    response_model=ResponseSchemaModel[list[dict[str, Any]]],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:list"))],
)
async def get_sandbox_pending(db: AsyncSessionDep, limit: int = 50) -> ResponseSchemaModel[list[dict[str, Any]]]:
    items = await workline_operation_service.get_sandbox_pending(db, limit=limit)
    return cast(
        "ResponseSchemaModel[list[dict[str, Any]]]", response_builder.success(data=[_outbox_response(i) for i in items])
    )


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


__all__ = ["router"]
