"""非设备外部系统 callback；设备 result/event 已移交 Device evidence owner。"""

from __future__ import annotations

import time
from typing import Any, cast

from fastapi import APIRouter, Depends, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel  # noqa: TC002 - FastAPI route schema generation requires runtime access

from src.app.callback.models import (
    CallbackExternalIngressResponse,
    CallbackExternalRequest,
    CallbackHTTPExceptionResponse,
)
from src.app.callback.services import callback_ingress_service
from src.app.callback.services.callback_ingress_service import _read_request_json
from src.app.wms_adapter import WmsInboundAuthPolicy
from src.core.api_security import RequireAPIPermission, require_api_auth, verify_api_auth
from src.core.logger import logger
from src.core.response.response_code import ClientErrorCode, ResourceErrorCode
from src.core.task_queue_gateway import task_queue_gateway
from src.database.dependencies import AsyncSessionDep, CacheDep  # noqa: TC001 - FastAPI resolves runtime annotations
from src.utils.audit import get_request_id

router = APIRouter()


def _required_json_request_body(schema_model: type[BaseModel]) -> dict[str, Any]:
    schema = schema_model.model_json_schema()
    definitions = schema.pop("$defs", {})
    for property_schema in schema.get("properties", {}).values():
        reference = property_schema.pop("$ref", None)
        if reference is not None:
            property_schema.update(definitions[reference.removeprefix("#/$defs/")])
    return {"requestBody": {"required": True, "content": {"application/json": {"schema": schema}}}}


def _enqueue_runtime_inbox_processing() -> None:
    try:
        task_queue_gateway.enqueue_runtime_inbox(limit=10)
    except Exception as exc:
        logger.warning(f"External callback 已入库，即时触发失败，将由 Beat 兜底: {exc}")


async def _require_callback_external_auth(
    request: Request,
    db: AsyncSessionDep,
    cache: CacheDep,
) -> None:
    try:
        payload = await _read_request_json(request)
    except Exception:
        payload = None
    policy = getattr(request.app.state, "wms_inbound_auth_policy", None)
    if isinstance(policy, WmsInboundAuthPolicy) and payload is not None and policy.permits_unsigned_external(payload):
        return
    app_ctx = await require_api_auth(await verify_api_auth(request, db, cache))
    RequireAPIPermission("api:callback:event")(app_ctx)


_require_callback_external_auth.permission_required = "api:callback:event"  # type: ignore[attr-defined]
_require_callback_external_auth.is_api_auth = True  # type: ignore[attr-defined]


@router.post(
    "/external",
    response_model=CallbackExternalIngressResponse,
    responses={
        400: {"model": CallbackExternalIngressResponse},
        409: {"model": CallbackExternalIngressResponse},
        413: {"model": CallbackHTTPExceptionResponse},
    },
    dependencies=[Depends(_require_callback_external_auth)],
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
