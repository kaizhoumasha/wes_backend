"""
设备回调 API 路由 (Device Callback API Routes)

提供 WES 回调接口，供设备供应商调用。

接口定义遵循白皮书 3.2 节规范：
- POST /api/v1/callback/result - 任务结果回传
- POST /api/v1/callback/event - 设备事件上报

相关文档:
- 白皮书: @docs/third_party_integration_whitepaper.md
"""

import time
from typing import Any, cast

from fastapi import APIRouter, Depends, Request, status

from src.app.callback.models import (
    CallbackEventIngressResponse,
    CallbackExternalIngressResponse,
    CallbackResultIngressResponse,
)
from src.app.callback.services import callback_ingress_service
from src.core.api_security import RequireAPIPermission
from src.database.dependencies import AsyncSessionDep
from src.utils.audit import get_request_id
from src.utils.fast_fail import fast_fail_check

router = APIRouter()


def _enqueue_workline_processing() -> None:
    """触发 Workline Inbox 异步处理。"""
    from src.celery_app.app import celery_app

    cast("Any", celery_app).send_task(
        "src.celery_app.tasks.workline.process_inbox_batch",
        kwargs={"limit": 10},
    )


@router.post(
    "/result",
    response_model=CallbackResultIngressResponse,
    status_code=status.HTTP_200_OK,
    summary="任务结果回传",
    dependencies=[
        Depends(RequireAPIPermission("api:callback:result")),
        Depends(fast_fail_check),
    ],
    description="设备完成指令后，调用此接口回传执行结果",
)
async def callback_result(
    request: Request,
    db: AsyncSessionDep,
) -> CallbackResultIngressResponse:
    return await callback_ingress_service.handle_result(
        request,
        db,
        request_id=get_request_id(),
        start_time=time.time(),
        enqueue_processing=_enqueue_workline_processing,
    )


@router.post(
    "/event",
    response_model=CallbackEventIngressResponse,
    status_code=status.HTTP_200_OK,
    summary="设备事件上报",
    dependencies=[
        Depends(RequireAPIPermission("api:callback:event")),
        Depends(fast_fail_check),
    ],
    description=("设备发生状态变更或传感器触发业务信号时，调用此接口上报事件（白皮书 3.2.2）"),
)
async def callback_event(
    request: Request,
    db: AsyncSessionDep,
) -> CallbackEventIngressResponse:
    return await callback_ingress_service.handle_event(
        request,
        db,
        request_id=get_request_id(),
        start_time=time.time(),
        enqueue_processing=_enqueue_workline_processing,
    )


@router.post(
    "/external",
    response_model=CallbackExternalIngressResponse,
    status_code=status.HTTP_200_OK,
    summary="外部系统回调",
    dependencies=[Depends(RequireAPIPermission("api:callback:event"))],
    description="库位分配、AGV 等外部系统异步回调入口",
)
async def callback_external(
    request: Request,
    db: AsyncSessionDep,
) -> CallbackExternalIngressResponse:
    return await callback_ingress_service.handle_external(
        request,
        db,
        request_id=get_request_id(),
        start_time=time.time(),
        enqueue_processing=_enqueue_workline_processing,
    )


__all__ = ["router"]
