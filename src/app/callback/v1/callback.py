"""
设备回调 API 路由 (Device Callback API Routes)

提供 WES 回调接口，供设备供应商调用。

接口定义遵循当前 callback ingress 合同：
- POST /api/v1/callback/result - 任务结果回传
- POST /api/v1/callback/event - 设备事件上报

基础能力边界:
- @docs/integration/callback_event_validation_principles.md
- @docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md
"""

import time
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.app.callback.models import (
    CallbackEventIngressResponse,
    CallbackEventRequest,
    CallbackExternalIngressResponse,
    CallbackExternalRequest,
    CallbackHTTPExceptionResponse,
    CallbackResultIngressResponse,
)
from src.app.callback.services import callback_ingress_service
from src.app.callback.services.callback_ingress_service import _read_request_json
from src.app.callback.services.wms_inbound_auth import WmsInboundAuthPolicy
from src.app.device.models import CommandCallbackResult
from src.core.api_security import RequireAPIPermission, require_api_auth, verify_api_auth
from src.core.logger import logger
from src.core.response.response_code import ClientErrorCode, ResourceErrorCode
from src.core.task_queue_gateway import task_queue_gateway
from src.database.dependencies import AsyncSessionDep, CacheDep
from src.utils.audit import get_request_id

router = APIRouter()


def _required_json_request_body(schema_model: type[BaseModel]) -> dict[str, Any]:
    """为自行读取原始请求体的 callback 路由补齐 OpenAPI JSON 合同。"""

    schema = schema_model.model_json_schema()
    definitions = schema.pop("$defs", {})
    for property_schema in schema.get("properties", {}).values():
        reference = property_schema.pop("$ref", None)
        if reference is None:
            continue
        definition_name = reference.removeprefix("#/$defs/")
        property_schema.update({**definitions[definition_name], **property_schema})

    return {
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    # OpenAPI 根文档无法解析嵌套 schema 的 #/$defs 引用，因此在此内联直接字段定义。
                    "schema": schema,
                }
            },
        }
    }


def _enqueue_runtime_inbox_processing() -> None:
    """触发 Runtime Inbox 异步处理。"""

    try:
        task_queue_gateway.enqueue_runtime_inbox(limit=10)
    except Exception as exc:
        logger.warning(f"Callback 已入库，但即时触发 Runtime Inbox 处理失败，将依赖 Beat/重试兜底: {exc}")


async def _require_callback_event_auth(
    request: Request,
    db: AsyncSessionDep,
    cache: CacheDep,
) -> None:
    """仅允许冻结的 WMS NONE event 绕过原有 API Application/HMAC 门禁。"""

    try:
        payload = await _read_request_json(request)
    except HTTPException:
        raise
    except Exception:
        payload = None
    policy = getattr(request.app.state, "wms_inbound_auth_policy", None)
    if isinstance(policy, WmsInboundAuthPolicy) and payload is not None and policy.permits_unsigned_event(payload):
        return
    app_ctx = await require_api_auth(await verify_api_auth(request, db, cache))
    RequireAPIPermission("api:callback:event")(app_ctx)


async def _require_callback_external_auth(
    request: Request,
    db: AsyncSessionDep,
    cache: CacheDep,
) -> None:
    """仅允许冻结的 WMS NONE status hint 绕过原有 API Application/HMAC 门禁。"""

    try:
        payload = await _read_request_json(request)
    except HTTPException:
        raise
    except Exception:
        payload = None
    policy = getattr(request.app.state, "wms_inbound_auth_policy", None)
    if isinstance(policy, WmsInboundAuthPolicy) and payload is not None and policy.permits_unsigned_external(payload):
        return
    app_ctx = await require_api_auth(await verify_api_auth(request, db, cache))
    RequireAPIPermission("api:callback:event")(app_ctx)


for _conditional_callback_auth_dependency in (_require_callback_event_auth, _require_callback_external_auth):
    _conditional_callback_auth_dependency.permission_required = "api:callback:event"  # type: ignore[attr-defined]
    _conditional_callback_auth_dependency.is_api_auth = True  # type: ignore[attr-defined]


@router.post(
    "/result",
    response_model=CallbackResultIngressResponse,
    status_code=status.HTTP_200_OK,
    responses={
        409: {"model": CallbackResultIngressResponse, "description": "RuntimeInbox 幂等身份冲突"},
        413: {"model": CallbackHTTPExceptionResponse, "description": "RuntimeInbox payload 超限"},
        503: {"model": CallbackHTTPExceptionResponse, "description": "RuntimeInbox 关联暂不可用"},
    },
    summary="任务结果回传",
    dependencies=[
        Depends(RequireAPIPermission("api:callback:result")),
    ],
    description="设备完成指令后，调用此接口回传执行结果",
    openapi_extra=_required_json_request_body(CommandCallbackResult),
)
async def callback_result(
    request: Request,
    db: AsyncSessionDep,
) -> CallbackResultIngressResponse | Response:
    result = await callback_ingress_service.handle_result(
        request,
        db,
        request_id=get_request_id(),
        start_time=time.time(),
        enqueue_processing=_enqueue_runtime_inbox_processing,
    )
    if cast("dict[str, Any]", result)["code"] == ResourceErrorCode.CONFLICT.code:
        return JSONResponse(status_code=409, content=jsonable_encoder(result))
    return result


@router.post(
    "/event",
    response_model=CallbackEventIngressResponse,
    status_code=status.HTTP_200_OK,
    responses={
        409: {"model": CallbackEventIngressResponse, "description": "RuntimeInbox 幂等身份冲突"},
        413: {"model": CallbackHTTPExceptionResponse, "description": "RuntimeInbox payload 超限"},
        503: {"model": CallbackHTTPExceptionResponse, "description": "RuntimeInbox 关联暂不可用"},
    },
    summary="设备事件上报",
    dependencies=[Depends(_require_callback_event_auth)],
    description=("设备发生状态变更或传感器触发业务信号时，通过当前 callback ingress 合同上报事件"),
    openapi_extra=_required_json_request_body(CallbackEventRequest),
)
async def callback_event(
    request: Request,
    db: AsyncSessionDep,
    response: Response,
) -> CallbackEventIngressResponse:
    decision = await callback_ingress_service.handle_event_decision(
        request,
        db,
        request_id=get_request_id(),
        start_time=time.time(),
        enqueue_processing=_enqueue_runtime_inbox_processing,
    )
    response.status_code = decision.http_status
    return decision.body


@router.post(
    "/external",
    response_model=CallbackExternalIngressResponse,
    status_code=status.HTTP_200_OK,
    responses={
        400: {"model": CallbackExternalIngressResponse, "description": "外部 callback 合同拒绝"},
        409: {"model": CallbackExternalIngressResponse, "description": "RuntimeInbox 幂等身份冲突"},
        413: {"model": CallbackHTTPExceptionResponse, "description": "RuntimeInbox payload 超限"},
    },
    summary="外部系统回调",
    dependencies=[Depends(_require_callback_external_auth)],
    description="WMS 状态查询提示、库位分配、AGV 等外部系统异步回调入口",
    openapi_extra=_required_json_request_body(CallbackExternalRequest),
)
async def callback_external(
    request: Request,
    db: AsyncSessionDep,
) -> CallbackExternalIngressResponse | Response:
    result = await callback_ingress_service.handle_external(
        request,
        db,
        request_id=get_request_id(),
        start_time=time.time(),
        enqueue_processing=_enqueue_runtime_inbox_processing,
    )
    if cast("dict[str, Any]", result)["code"] == ResourceErrorCode.CONFLICT.code:
        return JSONResponse(status_code=409, content=jsonable_encoder(result))
    if cast("dict[str, Any]", result)["code"] == ClientErrorCode.VALIDATION_ERROR.code:
        return JSONResponse(status_code=400, content=jsonable_encoder(result))
    return result


__all__ = ["router"]
