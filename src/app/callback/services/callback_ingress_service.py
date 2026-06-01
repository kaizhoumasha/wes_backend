"""Callback 入站应用服务."""

import time
from collections.abc import Callable
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, cast

from fastapi import Request
from pydantic import ValidationError

from src.app.callback.models import (
    CallbackEventIngressResponse,
    CallbackEventRequest,
    CallbackExternalIngressResponse,
    CallbackRejectedIngressResponse,
    CallbackResultIngressResponse,
    build_callback_event_accepted_response,
    build_callback_external_accepted_response,
    build_callback_rejected_response,
    build_callback_result_accepted_response,
)
from src.app.callback.services.callback_log_service import callback_log_service
from src.app.callback.services.callback_orchestration_service import callback_orchestration_service
from src.app.device.models import parse_device_capabilities
from src.app.device.models.command import CommandCallbackResult
from src.app.device.services import device_command_service, device_context_service, device_service
from src.app.sys.models.audit_log import OperaStatus
from src.app.sys.services import audit_log_service
from src.app.wms_integration.services.callback_normalizer import wms_execution_callback_normalizer
from src.app.workline.services import (
    inbox_service,
    workline_diagnostic_service,
)
from src.app.workline.services.start_admission_service import start_admission_service
from src.core.client_ip import resolve_client_ip
from src.core.logger import logger
from src.core.response import response_builder
from src.core.response.response_code import ClientErrorCode, ResourceErrorCode
from src.database.dependencies import AsyncSessionDep
from src.utils.value_normalization import resolve_entity_id
from src.workline_runtime.diagnostics import (
    ErrorCode,
    build_diagnostic_card,
    build_diagnostic_context,
    build_diagnostic_event,
)
from src.workline_runtime.plugin_sdk import canonicalize_event_type
from src.workline_runtime.runtime_events import is_production_event
from src.workline_runtime.trace_context import TraceContext
from src.workline_runtime.utils import JsonDict, resolve_first_str

# 字段别名常量
_TRACE_ID_ALIASES = ("trace_id",)
_EVENT_ID_ALIASES = ("event_id",)
_CAUSATION_ID_ALIASES = ("causation_id",)
_DEVICE_CODE_ALIASES = ("device_code",)
_COMMAND_CODE_ALIASES = ("command_code",)
_TRACE_TOP_LEVEL_FIELDS = frozenset({"trace_id", "event_id", "causation_id"})
_EVENT_CALLBACK_TOP_LEVEL_FIELDS = (
    frozenset({"device_code", "event_type", "timestamp", "data"}) | _TRACE_TOP_LEVEL_FIELDS
)
_RESULT_CALLBACK_TOP_LEVEL_FIELDS = frozenset(
    {"command_code", "device_code", "result", "finish_time", "data", "error_detail"} | _TRACE_TOP_LEVEL_FIELDS
)

_CALLBACK_AUDIT_TITLES = {
    "result": "设备回调结果",
    "event": "设备事件上报",
    "external": "外部系统回调",
}

_CALLBACK_SUBJECT_ALIASES = {
    "result": _DEVICE_CODE_ALIASES,
    "event": _DEVICE_CODE_ALIASES,
    "external": ("callback_type", "source_system"),
}

# callback 入口结果常量
_INGRESS_OUTCOME_ACCEPTED = "ACCEPTED"
_INGRESS_OUTCOME_REJECTED = "REJECTED"
_INGRESS_OUTCOME_FAILED = "FAILED"
_INGRESS_OUTCOME_DUPLICATE = "DUPLICATE"

# callback 入口失败阶段常量
_FAILURE_STAGE_REQUEST_PARSE = "REQUEST_PARSE"
_FAILURE_STAGE_ENVELOPE_VALIDATE = "ENVELOPE_VALIDATE"
_FAILURE_STAGE_DEVICE_CONTEXT_RESOLVE = "DEVICE_CONTEXT_RESOLVE"
_FAILURE_STAGE_COMMAND_LOOKUP = "COMMAND_LOOKUP"
_FAILURE_STAGE_CAPABILITY_VALIDATE = "CAPABILITY_VALIDATE"
_FAILURE_STAGE_CONFIG_VALIDATE = "CONFIG_VALIDATE"
_FAILURE_STAGE_CONTRACT_VALIDATE = "CONTRACT_VALIDATE"
_FAILURE_STAGE_WORKLINE_GUARD = "WORKLINE_GUARD"
_FAILURE_STAGE_ORCHESTRATION = "ORCHESTRATION"
_WORKLINE_NOT_ACCEPTING_WORK_REASON_CODE = "WORKLINE_NOT_ACCEPTING_WORK"
_WORKLINE_NOT_ACCEPTING_PRODUCTION_STATUSES = frozenset({"STOPPED", "RECONCILING", "ESTOPPED"})


@dataclass(frozen=True)
class CallbackEventIngressDecision:
    """设备事件入站响应与真实 HTTP 状态。"""

    body: CallbackEventIngressResponse
    http_status: int = 200


def _resolve_ctx_error_response_status(ctx_error: JsonDict) -> int:
    code = ctx_error.get("code")
    if isinstance(code, int) and 100 <= code <= 599:
        return code
    return 500


def _resolve_optional_str(payload: JsonDict, aliases: tuple[str, ...]) -> str | None:
    value = resolve_first_str(payload, aliases)
    return value or None


def _require_first_str(payload: JsonDict, aliases: tuple[str, ...], field_name: str) -> str:
    value = resolve_first_str(payload, aliases)
    if value:
        return value
    raise ValueError(f"{field_name} is required")


def _require_payload_value(payload: JsonDict, field_name: str) -> object:
    value = payload.get(field_name)
    if value is None:
        raise ValueError(f"{field_name} is required")
    if isinstance(value, str) and not value.strip():
        raise ValueError(f"{field_name} is required")
    return value


def _validate_top_level_fields(payload: JsonDict, allowed_fields: frozenset[str], callback_type: str) -> None:
    """校验 callback 顶层字段，业务字段必须放入 data。"""

    unexpected_fields = sorted(str(field_name) for field_name in payload if field_name not in allowed_fields)
    if unexpected_fields:
        allowed_text = ", ".join(sorted(allowed_fields))
        unexpected_text = ", ".join(unexpected_fields)
        raise ValueError(
            f"{callback_type} 顶层字段不符合协议: 不允许 {unexpected_text}; "
            f"允许字段: {allowed_text}; 业务字段必须放在 data 中"
        )


def _response_time_ms(start_time: float) -> int:
    return int((time.time() - start_time) * 1000)


def _summarize_validation_error(exc: ValidationError) -> str:
    """将 Pydantic 校验错误压缩成入口日志/ACK 可读文本。"""

    errors = exc.errors()
    if not errors:
        return str(exc)

    first = errors[0]
    loc = ".".join(str(part) for part in first.get("loc", ())) or "payload"
    msg = str(first.get("msg", "invalid value"))
    return f"{loc}: {msg}"


def _resolve_callback_trace_id(payload: JsonDict) -> str | None:
    return _resolve_optional_str(payload, _TRACE_ID_ALIASES)


def _resolve_callback_event_id(payload: JsonDict) -> str | None:
    return _resolve_optional_str(payload, _EVENT_ID_ALIASES)


def _resolve_callback_causation_id(payload: JsonDict) -> str | None:
    return _resolve_optional_str(payload, _CAUSATION_ID_ALIASES)


def _resolve_payload_command_code(payload: JsonDict) -> str | None:
    command_code = payload.get("command_code")
    return command_code if isinstance(command_code, str) else None


def _resolve_command_device_id(command: object | None) -> int | None:
    value = getattr(command, "device_id", None)
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) else None


def _has_workline_binding(value: object) -> bool:
    return isinstance(value, int) and value > 0


def _optional_enum_str(value: object) -> str | None:
    enum_value = getattr(value, "value", value)
    return enum_value if isinstance(enum_value, str) and enum_value else None


def _is_workline_accepting_production_events(workline: object) -> bool:
    runtime_status = _optional_enum_str(getattr(workline, "runtime_status", None))
    if runtime_status is None:
        return True
    return runtime_status not in _WORKLINE_NOT_ACCEPTING_PRODUCTION_STATUSES


def _build_callback_log_payload(
    request: Request,
    *,
    trace: TraceContext,
    callback_type: str,
    subject_code: str,
    request_body: JsonDict,
    response_status: int,
    response_time_ms: int,
    error_message: str | None = None,
    ingress_outcome: str | None = None,
    failure_stage: str | None = None,
) -> JsonDict:
    return {
        "callback_type": callback_type,
        "subject_code": subject_code,
        "request_body": request_body,
        "client_ip": resolve_client_ip(request),
        "user_agent": request.headers.get("User-Agent"),
        "request_id": trace.request_id,
        "trace_id": trace.trace_id,
        "response_status": response_status,
        "response_time_ms": response_time_ms,
        "error_message": error_message,
        "ingress_outcome": ingress_outcome,
        "failure_stage": failure_stage,
    }


async def _record_callback_audit_log(
    db: AsyncSessionDep,
    request: Request,
    *,
    title: str,
    args: JsonDict,
    cost_time: float,
    success: bool,
    message: str | None = None,
) -> None:
    try:
        _ = await audit_log_service.create_audit_log(
            db,
            method=request.method,
            title=title,
            path=str(request.url.path),
            args=args,
            status=OperaStatus.SUCCESS if success else OperaStatus.FAIL,
            code="200" if success else "500",
            msg=message,
            cost_time=cost_time,
        )
    except Exception as audit_error:
        logger.error(f"记录回调审计日志失败: {audit_error}")


async def _read_request_json(request: Request) -> JsonDict:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise TypeError("request body must be an object")
    return cast("JsonDict", payload)


def _normalize_external_callback_payload(payload: JsonDict) -> JsonDict:
    return wms_execution_callback_normalizer.normalize(payload)


def _validate_wms_rcs_execution_callback_payload(payload: JsonDict, callback_type: str) -> None:
    """校验 WMS/RCS 运行时执行回调第零阶段最小包络。"""

    wms_execution_callback_normalizer.validate(payload, callback_type)


def _build_contract_fail(message: str) -> CallbackRejectedIngressResponse:
    return cast(
        "CallbackRejectedIngressResponse",
        response_builder.fail(
            code=ClientErrorCode.VALIDATION_ERROR,
            message=message,
            data=build_callback_rejected_response(),
        ),
    )


def _build_conflict_fail(message: str, *, reason_code: str) -> CallbackRejectedIngressResponse:
    return cast(
        "CallbackRejectedIngressResponse",
        response_builder.fail(
            code=ResourceErrorCode.CONFLICT,
            message=message,
            data=build_callback_rejected_response(reason_code=reason_code),
        ),
    )


def _build_start_admission_conflict_fail(message: str, *, reason_code: str, diagnostic: JsonDict) -> JsonDict:
    return response_builder.fail(
        code=ResourceErrorCode.CONFLICT,
        message=message,
        data={"ack": False, "reason_code": reason_code, "diagnostic": diagnostic},
    )


def _build_not_found_fail(message: str) -> CallbackRejectedIngressResponse:
    return cast(
        "CallbackRejectedIngressResponse",
        response_builder.fail(
            code=ResourceErrorCode.NOT_FOUND,
            message=message,
            data=build_callback_rejected_response(),
        ),
    )


def _resolve_callback_audit_title(callback_type: str) -> str:
    return _CALLBACK_AUDIT_TITLES.get(callback_type, callback_type)


def _resolve_callback_subject(callback_type: str, request_body: JsonDict) -> str:
    subject_aliases = _CALLBACK_SUBJECT_ALIASES.get(callback_type)
    if subject_aliases is None:
        return "UNKNOWN"
    return resolve_first_str(request_body, subject_aliases) or "UNKNOWN"


def _log_callback_diagnostic(
    *,
    error_code: ErrorCode,
    message: str,
    request_id: str | None,
    callback_type: str,
    payload: JsonDict,
    device: object | None = None,
    workline: object | None = None,
    command_code: str | None = None,
    canonical_event_type: str | None = None,
    trace_id: str | None = None,
    event_id: str | None = None,
    causation_id: str | None = None,
) -> Any:
    trace = TraceContext.from_request(
        request_id=request_id,
        trace_id=trace_id or _resolve_callback_trace_id(payload),
        event_id=event_id or _resolve_callback_event_id(payload),
        causation_id=causation_id or _resolve_callback_causation_id(payload),
        canonical_event_type=canonical_event_type,
    )
    if device is not None:
        trace = trace.with_device(device)
    if workline is not None:
        trace = trace.with_workline(workline)
    if command_code is not None:
        trace = trace.with_command_code(command_code)

    event = build_diagnostic_event(
        error_code=error_code,
        context=build_diagnostic_context(
            trace=trace,
            command=SimpleNamespace(command_code=command_code) if command_code else None,
            device=device,
            workline=workline,
            canonical_event_type=canonical_event_type,
            extra={"callback_type": callback_type},
        ),
        message=message,
        technical_summary=message,
    )
    card = build_diagnostic_card(event)
    logger.warning(f"[CallbackDiagnostic] {card.model_dump_json(exclude_none=True)}")
    return event


async def _record_callback_diagnostic(db: AsyncSessionDep, **kwargs: Any) -> None:
    """记录 callback 诊断日志并尽力持久化诊断卡片。"""

    event = _log_callback_diagnostic(**kwargs)
    try:
        _ = await workline_diagnostic_service.record_event(
            db,
            event=event,
            evidence={"payload": kwargs.get("payload")},
            auto_commit=False,
        )
    except Exception as exc:
        logger.warning(f"Callback 诊断持久化失败: {exc}")


async def _log_callback_outcome(
    db: AsyncSessionDep,
    request: Request,
    *,
    callback_type: str,
    subject_code: str,
    request_body: JsonDict,
    request_id: str | None,
    response_status: int,
    response_time_ms: int,
    success: bool,
    record_audit: bool,
    audit_title: str,
    trace_id: str | None = None,
    event_id: str | None = None,
    causation_id: str | None = None,
    error_message: str | None = None,
    ingress_outcome: str | None = None,
    failure_stage: str | None = None,
) -> None:
    trace = TraceContext.from_request(
        request_id=request_id,
        trace_id=trace_id,
        event_id=event_id,
        causation_id=causation_id,
    )
    _ = await callback_log_service.log_callback(
        db,
        trace=trace,
        **_build_callback_log_payload(
            request,
            trace=trace,
            callback_type=callback_type,
            subject_code=subject_code,
            request_body=request_body,
            response_status=response_status,
            response_time_ms=response_time_ms,
            error_message=error_message,
            ingress_outcome=ingress_outcome,
            failure_stage=failure_stage,
        ),
    )
    if not record_audit:
        return
    if success:
        await _record_callback_audit_log(
            db,
            request,
            title=audit_title,
            args=request_body,
            cost_time=response_time_ms / 1000,
            success=True,
        )
        return
    await _record_callback_audit_log(
        db,
        request,
        title=audit_title,
        args=request_body,
        cost_time=response_time_ms / 1000,
        success=False,
        message=error_message,
    )


async def _handle_validation_failure(
    db: AsyncSessionDep,
    request: Request,
    *,
    callback_type: str,
    request_id: str | None,
    request_body: JsonDict,
    message: str,
    response_time_ms: int = 0,
    failure_stage: str,
    trace_id: str | None = None,
    event_id: str | None = None,
    causation_id: str | None = None,
) -> CallbackRejectedIngressResponse:
    await _log_callback_outcome(
        db,
        request,
        callback_type=callback_type,
        subject_code=_resolve_callback_subject(callback_type, request_body),
        request_body=request_body,
        request_id=request_id,
        trace_id=trace_id or _resolve_callback_trace_id(request_body),
        event_id=event_id or _resolve_callback_event_id(request_body),
        causation_id=causation_id or _resolve_callback_causation_id(request_body),
        response_status=400,
        response_time_ms=response_time_ms,
        success=False,
        record_audit=True,
        audit_title=_resolve_callback_audit_title(callback_type),
        error_message=message,
        ingress_outcome=_INGRESS_OUTCOME_REJECTED,
        failure_stage=failure_stage,
    )
    return _build_contract_fail(message)


async def _handle_result_validation_failure(
    db: AsyncSessionDep,
    request: Request,
    *,
    request_id: str | None,
    callback_data: JsonDict,
    message: str,
    response_time_ms: int = 0,
    failure_stage: str,
    trace_id: str | None = None,
    event_id: str | None = None,
    causation_id: str | None = None,
) -> CallbackResultIngressResponse:
    return cast(
        "CallbackResultIngressResponse",
        await _handle_validation_failure(
            db,
            request,
            callback_type="result",
            request_id=request_id,
            request_body=callback_data,
            message=message,
            response_time_ms=response_time_ms,
            failure_stage=failure_stage,
            trace_id=trace_id,
            event_id=event_id,
            causation_id=causation_id,
        ),
    )


async def _handle_event_validation_failure(
    db: AsyncSessionDep,
    request: Request,
    *,
    request_id: str | None,
    event_data: JsonDict,
    message: str,
    response_time_ms: int = 0,
    failure_stage: str,
) -> CallbackEventIngressResponse:
    return cast(
        "CallbackEventIngressResponse",
        await _handle_validation_failure(
            db,
            request,
            callback_type="event",
            request_id=request_id,
            request_body=event_data,
            message=message,
            response_time_ms=response_time_ms,
            failure_stage=failure_stage,
        ),
    )


async def _handle_event_workline_guard_rejection(
    db: AsyncSessionDep,
    request: Request,
    *,
    request_id: str | None,
    event_data: JsonDict,
    device_code: str,
    runtime_status: str | None,
    response_time_ms: int,
) -> CallbackEventIngressDecision:
    message = "WorkLine 当前运行态不接收生产事件"
    if runtime_status:
        message = f"{message}: {runtime_status}"
    await _log_callback_outcome(
        db,
        request,
        callback_type="event",
        subject_code=device_code,
        request_body=event_data,
        request_id=request_id,
        trace_id=_resolve_callback_trace_id(event_data),
        event_id=_resolve_callback_event_id(event_data),
        causation_id=_resolve_callback_causation_id(event_data),
        response_status=409,
        response_time_ms=response_time_ms,
        success=False,
        record_audit=True,
        audit_title="设备事件上报",
        error_message=message,
        ingress_outcome=_INGRESS_OUTCOME_REJECTED,
        failure_stage=_FAILURE_STAGE_WORKLINE_GUARD,
    )
    return CallbackEventIngressDecision(
        body=cast(
            "CallbackEventIngressResponse",
            _build_conflict_fail(message, reason_code=_WORKLINE_NOT_ACCEPTING_WORK_REASON_CODE),
        ),
        http_status=409,
    )


async def _handle_event_start_admission(
    db: AsyncSessionDep,
    request: Request,
    *,
    request_id: str | None,
    event_data: JsonDict,
    device_code: str,
    response_time_ms: int,
) -> CallbackEventIngressDecision:
    admission = await start_admission_service.admit_start_for_device(
        db,
        device_code=device_code,
        request_id=request_id,
        trace_id=_resolve_callback_trace_id(event_data),
    )
    await _log_callback_outcome(
        db,
        request,
        callback_type="event",
        subject_code=device_code,
        request_body=event_data,
        request_id=request_id,
        trace_id=_resolve_callback_trace_id(event_data),
        event_id=_resolve_callback_event_id(event_data),
        causation_id=_resolve_callback_causation_id(event_data),
        response_status=admission.http_status,
        response_time_ms=response_time_ms,
        success=admission.accepted,
        record_audit=True,
        audit_title="设备事件上报",
        error_message=None if admission.accepted else admission.message,
        ingress_outcome=_INGRESS_OUTCOME_ACCEPTED if admission.accepted else _INGRESS_OUTCOME_REJECTED,
        failure_stage=None if admission.accepted else _FAILURE_STAGE_WORKLINE_GUARD,
    )
    if admission.accepted:
        return CallbackEventIngressDecision(
            body=cast(
                "CallbackEventIngressResponse",
                response_builder.success(
                    message="START accepted",
                    data={
                        "status": "accepted",
                        "device_code": device_code,
                        "request_id": request_id,
                        "trace_id": _resolve_callback_trace_id(event_data),
                        "event_id": _resolve_callback_event_id(event_data),
                        "causation_id": _resolve_callback_causation_id(event_data),
                        "diagnostic": admission.diagnostic,
                    },
                ),
            ),
            http_status=200,
        )
    return CallbackEventIngressDecision(
        body=cast(
            "CallbackEventIngressResponse",
            _build_start_admission_conflict_fail(
                admission.message,
                reason_code=admission.reason_code or "START_ADMISSION_FAILED",
                diagnostic=admission.diagnostic,
            ),
        ),
        http_status=409,
    )


async def _handle_external_validation_failure(
    db: AsyncSessionDep,
    request: Request,
    *,
    request_id: str | None,
    callback_data: JsonDict,
    message: str,
    response_time_ms: int = 0,
    failure_stage: str,
) -> CallbackExternalIngressResponse:
    return cast(
        "CallbackExternalIngressResponse",
        await _handle_validation_failure(
            db,
            request,
            callback_type="external",
            request_id=request_id,
            request_body=callback_data,
            message=message,
            response_time_ms=response_time_ms,
            failure_stage=failure_stage,
        ),
    )


async def _handle_device_context_failure(
    db: AsyncSessionDep,
    request: Request,
    *,
    callback_type: str,
    subject_code: str,
    request_body: JsonDict,
    request_id: str | None,
    response_time_ms: int,
    error: JsonDict,
    audit_title: str,
    trace_id: str | None = None,
    event_id: str | None = None,
    causation_id: str | None = None,
) -> CallbackResultIngressResponse | CallbackEventIngressResponse:
    message = str(error.get("message") or "设备上下文解析失败")
    await _log_callback_outcome(
        db,
        request,
        callback_type=callback_type,
        subject_code=subject_code,
        request_body=request_body,
        request_id=request_id,
        trace_id=trace_id or _resolve_callback_trace_id(request_body),
        event_id=event_id or _resolve_callback_event_id(request_body),
        causation_id=causation_id or _resolve_callback_causation_id(request_body),
        response_status=_resolve_ctx_error_response_status(error),
        response_time_ms=response_time_ms,
        success=False,
        record_audit=True,
        audit_title=audit_title,
        error_message=message,
        ingress_outcome=_INGRESS_OUTCOME_REJECTED,
        failure_stage=_FAILURE_STAGE_DEVICE_CONTEXT_RESOLVE,
    )
    return cast(
        "CallbackResultIngressResponse | CallbackEventIngressResponse", JsonDict(**error, request_id=request_id)
    )


async def handle_callback_result(  # noqa: PLR0911 - ingress 分支显式早返回，便于 failure_stage 精确归因
    request: Request,
    db: AsyncSessionDep,
    *,
    request_id: str | None,
    start_time: float,
    enqueue_processing: Callable[[], None],
) -> CallbackResultIngressResponse:
    callback_data: JsonDict = {}

    try:
        callback_data = await _read_request_json(request)
    except Exception as exc:
        logger.error(f"指令结果回调解析失败: {exc}")
        return await _handle_result_validation_failure(
            db,
            request,
            request_id=request_id,
            callback_data=callback_data,
            message=f"结果回调报文格式错误: {exc}",
            response_time_ms=_response_time_ms(start_time),
            failure_stage=_FAILURE_STAGE_REQUEST_PARSE,
        )

    try:
        _validate_top_level_fields(callback_data, _RESULT_CALLBACK_TOP_LEVEL_FIELDS, "result")
        device_code = _require_first_str(callback_data, _DEVICE_CODE_ALIASES, "device_code")
        command_code = _require_first_str(callback_data, _COMMAND_CODE_ALIASES, "command_code")
    except ValueError as exc:
        logger.error(f"指令结果回调最小包络校验失败: {exc}")
        await _record_callback_diagnostic(
            db,
            error_code=ErrorCode.CALLBACK_SCHEMA_INVALID,
            message=f"结果回调最小包络校验失败: {exc}",
            request_id=request_id,
            callback_type="result",
            payload=callback_data,
            command_code=_resolve_payload_command_code(callback_data),
        )
        return await _handle_result_validation_failure(
            db,
            request,
            request_id=request_id,
            callback_data=callback_data,
            message=f"结果回调最小包络校验失败: {exc}",
            response_time_ms=_response_time_ms(start_time),
            failure_stage=_FAILURE_STAGE_ENVELOPE_VALIDATE,
        )

    logger.info(f"收到指令结果回调: {command_code} (request_id={request_id})")

    existing_command = await device_command_service.get_command_by_code(db, command_code)
    if existing_command is None:
        message = f"未找到指令: {command_code}"
        await _log_callback_outcome(
            db,
            request,
            callback_type="result",
            subject_code=device_code,
            request_body=callback_data,
            request_id=request_id,
            trace_id=_resolve_callback_trace_id(callback_data),
            event_id=_resolve_callback_event_id(callback_data),
            causation_id=_resolve_callback_causation_id(callback_data),
            response_status=404,
            response_time_ms=_response_time_ms(start_time),
            success=False,
            record_audit=True,
            audit_title="设备回调结果",
            error_message=message,
            ingress_outcome=_INGRESS_OUTCOME_REJECTED,
            failure_stage=_FAILURE_STAGE_COMMAND_LOOKUP,
        )
        return cast("CallbackResultIngressResponse", _build_not_found_fail(message))

    command_trace_id = getattr(existing_command, "trace_id", None)
    resolved_trace_id = _resolve_callback_trace_id(callback_data) or command_trace_id

    # 使用 DeviceContextService 验证设备和工作线上下文
    ctx_result, ctx_error = await device_context_service.resolve(db, device_code)
    if ctx_error:
        return cast(
            "CallbackResultIngressResponse",
            await _handle_device_context_failure(
                db,
                request,
                callback_type="result",
                subject_code=device_code,
                request_body=callback_data,
                request_id=request_id,
                response_time_ms=_response_time_ms(start_time),
                error=ctx_error,
                audit_title="设备回调结果",
                trace_id=resolved_trace_id,
                event_id=_resolve_callback_event_id(callback_data),
                causation_id=_resolve_callback_causation_id(callback_data),
            ),
        )

    # ctx_error 为 None 时，ctx_result 必有值（类型检查器无法理解 tuple 解包后的关联）
    # 安全保证：上面已检查 ctx_error 并提前返回
    device = ctx_result.device  # type: ignore[union-attr]
    workline = ctx_result.workline  # type: ignore[union-attr]
    _resolved_contract_version = ctx_result.contract_version  # type: ignore[union-attr]

    command_device_id = _resolve_command_device_id(existing_command)
    callback_device_id = resolve_entity_id(device)
    if command_device_id is None or callback_device_id is None or command_device_id != callback_device_id:
        message = f"结果回调设备与指令归属不匹配: command_code={command_code}, callback_device_code={device_code}"
        await _record_callback_diagnostic(
            db,
            error_code=ErrorCode.CONTRACT_MISMATCH,
            message=message,
            request_id=request_id,
            callback_type="result",
            payload=callback_data,
            device=device,
            workline=workline,
            command_code=command_code,
            trace_id=resolved_trace_id,
            event_id=_resolve_callback_event_id(callback_data),
            causation_id=_resolve_callback_causation_id(callback_data),
        )
        return await _handle_result_validation_failure(
            db,
            request,
            request_id=request_id,
            callback_data=callback_data,
            message=message,
            response_time_ms=_response_time_ms(start_time),
            failure_stage=_FAILURE_STAGE_CONTRACT_VALIDATE,
            trace_id=resolved_trace_id,
            event_id=_resolve_callback_event_id(callback_data),
            causation_id=_resolve_callback_causation_id(callback_data),
        )

    try:
        capabilities = parse_device_capabilities(getattr(device, "capabilities_json", None))
    except (TypeError, ValidationError, ValueError) as exc:
        message = f"设备能力配置无效: {exc}"
        logger.error(f"指令结果回调能力配置校验失败: {exc}")
        await _record_callback_diagnostic(
            db,
            error_code=ErrorCode.CONFIG_INVALID,
            message=message,
            request_id=request_id,
            callback_type="result",
            payload=callback_data,
            device=device,
            workline=workline,
            command_code=command_code,
            trace_id=resolved_trace_id,
            event_id=_resolve_callback_event_id(callback_data),
            causation_id=_resolve_callback_causation_id(callback_data),
        )
        return await _handle_result_validation_failure(
            db,
            request,
            request_id=request_id,
            callback_data=callback_data,
            message=message,
            response_time_ms=_response_time_ms(start_time),
            failure_stage=_FAILURE_STAGE_CONFIG_VALIDATE,
            trace_id=resolved_trace_id,
            event_id=_resolve_callback_event_id(callback_data),
            causation_id=_resolve_callback_causation_id(callback_data),
        )

    try:
        # 从已有 command 获取 contract_version（覆盖设备上下文）
        command_contract_version = getattr(existing_command, "contract_version", None)
        if isinstance(command_contract_version, str) and command_contract_version:
            _resolved_contract_version = command_contract_version

        # 直接用原始 payload 验证（Pydantic 自动处理别名）
        callback = CommandCallbackResult.model_validate(callback_data)
        if not capabilities.allows_result_callback():
            message = f"设备 {device_code} 未声明支持结果回调"
            await _record_callback_diagnostic(
                db,
                error_code=ErrorCode.CONFIG_INVALID,
                message=message,
                request_id=request_id,
                callback_type="result",
                payload=callback_data,
                device=device,
                workline=workline,
                command_code=callback.command_code,
                trace_id=resolved_trace_id,
                event_id=_resolve_callback_event_id(callback_data),
                causation_id=_resolve_callback_causation_id(callback_data),
            )
            return await _handle_result_validation_failure(
                db,
                request,
                request_id=request_id,
                callback_data=callback_data,
                message=message,
                response_time_ms=_response_time_ms(start_time),
                failure_stage=_FAILURE_STAGE_CAPABILITY_VALIDATE,
                trace_id=resolved_trace_id,
                event_id=_resolve_callback_event_id(callback_data),
                causation_id=_resolve_callback_causation_id(callback_data),
            )
        outcome = await callback_orchestration_service.process_result(
            db,
            callback=callback,
            existing_command=existing_command,
            request_id=request_id,
            resolved_contract_version=_resolved_contract_version,
            trace_id=resolved_trace_id,
            event_id=_resolve_callback_event_id(callback_data),
            causation_id=_resolve_callback_causation_id(callback_data),
            command_service=device_command_service,
            device_service=device_service,
            inbox_service=inbox_service,
            enqueue_processing=enqueue_processing,
        )
        is_duplicate = outcome.is_duplicate

        response_time_ms = _response_time_ms(start_time)
        await _log_callback_outcome(
            db,
            request,
            callback_type="result",
            subject_code=callback.device_code,
            request_body=callback_data,
            request_id=request_id,
            trace_id=outcome.trace_id,
            event_id=_resolve_callback_event_id(callback_data),
            causation_id=_resolve_callback_causation_id(callback_data),
            response_status=200,
            response_time_ms=response_time_ms,
            success=not is_duplicate,
            record_audit=not is_duplicate,
            audit_title="设备回调结果",
            error_message="幂等重复: 已存在相同事件" if is_duplicate else None,
            ingress_outcome=_INGRESS_OUTCOME_DUPLICATE if is_duplicate else _INGRESS_OUTCOME_ACCEPTED,
        )
        return cast(
            "CallbackResultIngressResponse",
            response_builder.success(
                data=build_callback_result_accepted_response(
                    request_id=request_id,
                    trace_id=outcome.trace_id,
                    event_id=_resolve_callback_event_id(callback_data),
                    causation_id=_resolve_callback_causation_id(callback_data),
                )
            ),
        )

    except ValidationError as exc:
        logger.error(f"指令结果回调模型校验失败: {exc}")
        await _record_callback_diagnostic(
            db,
            error_code=ErrorCode.CALLBACK_SCHEMA_INVALID,
            message="结果回调模型校验失败",
            request_id=request_id,
            callback_type="result",
            payload=callback_data,
            command_code=_resolve_payload_command_code(callback_data),
            trace_id=resolved_trace_id,
            event_id=_resolve_callback_event_id(callback_data),
            causation_id=_resolve_callback_causation_id(callback_data),
        )
        return await _handle_result_validation_failure(
            db,
            request,
            request_id=request_id,
            callback_data=callback_data,
            message="结果回调模型校验失败",
            response_time_ms=_response_time_ms(start_time),
            failure_stage=_FAILURE_STAGE_CONTRACT_VALIDATE,
            trace_id=resolved_trace_id,
            event_id=_resolve_callback_event_id(callback_data),
            causation_id=_resolve_callback_causation_id(callback_data),
        )
    except ValueError as exc:
        logger.error(f"指令结果回调契约校验失败: {exc}")
        await _record_callback_diagnostic(
            db,
            error_code=ErrorCode.CONFIG_INVALID,
            message=f"结果回调契约校验失败: {exc}",
            request_id=request_id,
            callback_type="result",
            payload=callback_data,
            command_code=_resolve_payload_command_code(callback_data),
            trace_id=resolved_trace_id,
            event_id=_resolve_callback_event_id(callback_data),
            causation_id=_resolve_callback_causation_id(callback_data),
        )
        return await _handle_result_validation_failure(
            db,
            request,
            request_id=request_id,
            callback_data=callback_data,
            message=f"结果回调契约校验失败: {exc}",
            response_time_ms=_response_time_ms(start_time),
            failure_stage=_FAILURE_STAGE_CONTRACT_VALIDATE,
            trace_id=resolved_trace_id,
            event_id=_resolve_callback_event_id(callback_data),
            causation_id=_resolve_callback_causation_id(callback_data),
        )
    except Exception as exc:
        response_time_ms = _response_time_ms(start_time)
        logger.error(f"指令结果回调处理失败: {exc}")
        await _log_callback_outcome(
            db,
            request,
            callback_type="result",
            subject_code=device_code,
            request_body=callback_data,
            request_id=request_id,
            trace_id=resolved_trace_id,
            event_id=_resolve_callback_event_id(callback_data),
            causation_id=_resolve_callback_causation_id(callback_data),
            response_status=500,
            response_time_ms=response_time_ms,
            success=False,
            record_audit=True,
            audit_title="设备回调结果",
            error_message=str(exc),
            ingress_outcome=_INGRESS_OUTCOME_FAILED,
            failure_stage=_FAILURE_STAGE_ORCHESTRATION,
        )
        raise


async def handle_callback_event(  # noqa: PLR0911 - ingress 分支显式早返回，便于 failure_stage 精确归因
    request: Request,
    db: AsyncSessionDep,
    *,
    request_id: str | None,
    start_time: float,
    enqueue_processing: Callable[[], None],
) -> CallbackEventIngressDecision:
    event_data: JsonDict = {}

    try:
        event_data = await _read_request_json(request)
    except Exception as exc:
        logger.error(f"设备事件上报解析失败: {exc}")
        return CallbackEventIngressDecision(
            body=await _handle_event_validation_failure(
                db,
                request,
                request_id=request_id,
                event_data=event_data,
                message=f"事件上报报文格式错误: {exc}",
                response_time_ms=_response_time_ms(start_time),
                failure_stage=_FAILURE_STAGE_REQUEST_PARSE,
            )
        )

    try:
        # event 是统一硬件事件入口：这里只做最小包络校验，
        # 不提前判断插件私有 payload 是否“业务成立”。
        _validate_top_level_fields(event_data, _EVENT_CALLBACK_TOP_LEVEL_FIELDS, "event")
        normalized_event_request = CallbackEventRequest.model_validate(event_data)
    except (ValidationError, ValueError) as exc:
        detail = _summarize_validation_error(exc) if isinstance(exc, ValidationError) else str(exc)
        message = f"事件上报最小包络校验失败: {detail}"
        logger.error(message)
        await _record_callback_diagnostic(
            db,
            error_code=ErrorCode.CALLBACK_SCHEMA_INVALID,
            message=message,
            request_id=request_id,
            callback_type="event",
            payload=event_data,
        )
        return CallbackEventIngressDecision(
            body=await _handle_event_validation_failure(
                db,
                request,
                request_id=request_id,
                event_data=event_data,
                message=message,
                response_time_ms=_response_time_ms(start_time),
                failure_stage=_FAILURE_STAGE_ENVELOPE_VALIDATE,
            )
        )

    device_code = normalized_event_request.device_code
    logger.info(f"收到设备事件上报: {device_code} (request_id={request_id})")

    # 设备/工作线上下文和能力校验属于“是否可路由入站”的入口职责。
    ctx_result, ctx_error = await device_context_service.resolve(db, device_code)
    if ctx_error:
        return CallbackEventIngressDecision(
            body=cast(
                "CallbackEventIngressResponse",
                await _handle_device_context_failure(
                    db,
                    request,
                    callback_type="event",
                    subject_code=device_code,
                    request_body=event_data,
                    request_id=request_id,
                    response_time_ms=_response_time_ms(start_time),
                    error=ctx_error,
                    audit_title="设备事件上报",
                ),
            )
        )
    # ctx_error 为 None 时，ctx_result 必有值（类型检查器无法理解 tuple 解包后的关联）
    device = ctx_result.device  # type: ignore[union-attr]
    workline = ctx_result.workline  # type: ignore[union-attr]

    try:
        canonical_event_type = canonicalize_event_type(normalized_event_request.event_type, workline=workline)
    except ValueError as exc:
        logger.error(f"设备事件上报契约校验失败: {exc}")
        await _record_callback_diagnostic(
            db,
            error_code=ErrorCode.CONFIG_INVALID,
            message=f"事件上报契约校验失败: {exc}",
            request_id=request_id,
            callback_type="event",
            payload=event_data,
        )
        return CallbackEventIngressDecision(
            body=await _handle_event_validation_failure(
                db,
                request,
                request_id=request_id,
                event_data=event_data,
                message=f"事件上报契约校验失败: {exc}",
                response_time_ms=_response_time_ms(start_time),
                failure_stage=_FAILURE_STAGE_CONTRACT_VALIDATE,
            )
        )

    try:
        if canonical_event_type == "WORKLINE_START_REQUESTED":
            return await _handle_event_start_admission(
                db,
                request,
                request_id=request_id,
                event_data=event_data,
                device_code=device_code,
                response_time_ms=_response_time_ms(start_time),
            )

        if is_production_event(canonical_event_type):
            if not _is_workline_accepting_production_events(workline):
                return await _handle_event_workline_guard_rejection(
                    db,
                    request,
                    request_id=request_id,
                    event_data=event_data,
                    device_code=device_code,
                    runtime_status=_optional_enum_str(getattr(workline, "runtime_status", None)),
                    response_time_ms=_response_time_ms(start_time),
                )

            try:
                capabilities = parse_device_capabilities(getattr(device, "capabilities_json", None))
            except (TypeError, ValidationError, ValueError) as exc:
                message = f"设备能力配置无效: {exc}"
                logger.error(f"设备事件上报能力配置校验失败: {exc}")
                await _record_callback_diagnostic(
                    db,
                    error_code=ErrorCode.CONFIG_INVALID,
                    message=message,
                    request_id=request_id,
                    callback_type="event",
                    payload=event_data,
                    device=device,
                    workline=workline,
                    canonical_event_type=canonical_event_type,
                )
                return CallbackEventIngressDecision(
                    body=await _handle_event_validation_failure(
                        db,
                        request,
                        request_id=request_id,
                        event_data=event_data,
                        message=message,
                        response_time_ms=_response_time_ms(start_time),
                        failure_stage=_FAILURE_STAGE_CONFIG_VALIDATE,
                    )
                )

            if not capabilities.supports_event(canonical_event_type):
                message = f"设备 {device_code} 未声明支持事件: {canonical_event_type}"
                await _record_callback_diagnostic(
                    db,
                    error_code=ErrorCode.CONFIG_INVALID,
                    message=message,
                    request_id=request_id,
                    callback_type="event",
                    payload=event_data,
                    device=device,
                    workline=workline,
                    canonical_event_type=canonical_event_type,
                )
                return CallbackEventIngressDecision(
                    body=await _handle_event_validation_failure(
                        db,
                        request,
                        request_id=request_id,
                        event_data=event_data,
                        message=message,
                        response_time_ms=_response_time_ms(start_time),
                        failure_stage=_FAILURE_STAGE_CAPABILITY_VALIDATE,
                    )
                )

        # ctx_error 为 None 时，ctx_result 必有值
        is_workline_event = ctx_result.is_workline_bound  # type: ignore[union-attr]
        if not is_workline_event:
            fallback_device = await device_service.get_device_by_code(db, device_code)
            is_workline_event = _has_workline_binding(getattr(fallback_device, "work_line_id", None))

        outcome = await callback_orchestration_service.process_event(
            db,
            event_request=normalized_event_request,
            request_id=request_id,
            is_workline_event=is_workline_event,
            canonical_event_type=canonical_event_type,
            trace_id=_resolve_callback_trace_id(event_data),
            event_id=_resolve_callback_event_id(event_data),
            causation_id=_resolve_callback_causation_id(event_data),
            inbox_service=inbox_service,
            enqueue_processing=enqueue_processing,
        )
        is_duplicate = outcome.is_duplicate

        response_time_ms = _response_time_ms(start_time)
        await _log_callback_outcome(
            db,
            request,
            callback_type="event",
            subject_code=normalized_event_request.device_code,
            request_body=event_data,
            request_id=request_id,
            trace_id=outcome.trace_id,
            event_id=_resolve_callback_event_id(event_data),
            causation_id=_resolve_callback_causation_id(event_data),
            response_status=200,
            response_time_ms=response_time_ms,
            success=not is_duplicate,
            record_audit=not is_duplicate,
            audit_title="设备事件上报",
            error_message="幂等重复: 已存在相同事件" if is_duplicate else None,
            ingress_outcome=_INGRESS_OUTCOME_DUPLICATE if is_duplicate else _INGRESS_OUTCOME_ACCEPTED,
        )
        logger.info(f"设备事件已提交处理: {normalized_event_request.device_code} (request_id={request_id})")
        return CallbackEventIngressDecision(
            body=cast(
                "CallbackEventIngressResponse",
                response_builder.success(
                    message="Event received",
                    data=build_callback_event_accepted_response(
                        status="duplicate" if is_duplicate else "submitted",
                        device_code=normalized_event_request.device_code,
                        request_id=request_id,
                        trace_id=outcome.trace_id,
                        event_id=_resolve_callback_event_id(event_data),
                        causation_id=_resolve_callback_causation_id(event_data),
                    ),
                ),
            ),
        )

    except Exception as exc:
        response_time_ms = _response_time_ms(start_time)
        logger.error(f"设备事件上报处理失败: {exc}")
        await _log_callback_outcome(
            db,
            request,
            callback_type="event",
            subject_code=device_code,
            request_body=event_data,
            request_id=request_id,
            response_status=500,
            response_time_ms=response_time_ms,
            success=False,
            record_audit=True,
            audit_title="设备事件上报",
            error_message=str(exc),
            ingress_outcome=_INGRESS_OUTCOME_FAILED,
            failure_stage=_FAILURE_STAGE_ORCHESTRATION,
        )
        raise


async def handle_callback_external(
    request: Request,
    db: AsyncSessionDep,
    *,
    request_id: str | None,
    start_time: float,
    enqueue_processing: Callable[[], None],
) -> CallbackExternalIngressResponse:
    callback_data: JsonDict = {}
    callback_type = "UNKNOWN"

    try:
        callback_data = await _read_request_json(request)
    except Exception as exc:
        logger.error(f"外部回调解析失败: {exc}")
        return await _handle_external_validation_failure(
            db,
            request,
            request_id=request_id,
            callback_data=callback_data,
            message=f"外部回调报文格式错误: {exc}",
            response_time_ms=_response_time_ms(start_time),
            failure_stage=_FAILURE_STAGE_REQUEST_PARSE,
        )

    try:
        normalized_payload = _normalize_external_callback_payload(callback_data)
        callback_type = cast("str", normalized_payload["callback_type"])
        external_trace_id = cast("str | None", normalized_payload["trace_id"])
    except ValueError as exc:
        logger.error(f"外部回调最小包络校验失败: {exc}")
        return await _handle_external_validation_failure(
            db,
            request,
            request_id=request_id,
            callback_data=callback_data,
            message=f"外部回调最小包络校验失败: {exc}",
            response_time_ms=_response_time_ms(start_time),
            failure_stage=_FAILURE_STAGE_ENVELOPE_VALIDATE,
        )

    logger.info(f"收到外部系统回调: {callback_type} (request_id={request_id})")

    try:
        outcome = await callback_orchestration_service.process_external(
            db,
            callback_type=callback_type,
            payload=callback_data,
            request_id=request_id,
            trace_id=external_trace_id,
            event_id=_resolve_callback_event_id(callback_data),
            causation_id=_resolve_callback_causation_id(callback_data),
            inbox_service=inbox_service,
            enqueue_processing=enqueue_processing,
        )
        is_duplicate = outcome.is_duplicate

        response_time_ms = _response_time_ms(start_time)
        await _log_callback_outcome(
            db,
            request,
            callback_type="external",
            subject_code=callback_type,
            request_body=callback_data,
            request_id=request_id,
            trace_id=outcome.trace_id,
            event_id=_resolve_callback_event_id(callback_data),
            causation_id=_resolve_callback_causation_id(callback_data),
            response_status=200,
            response_time_ms=response_time_ms,
            success=not is_duplicate,
            record_audit=not is_duplicate,
            audit_title="外部系统回调",
            error_message="幂等重复: 已存在相同事件" if is_duplicate else None,
            ingress_outcome=_INGRESS_OUTCOME_DUPLICATE if is_duplicate else _INGRESS_OUTCOME_ACCEPTED,
        )
        return cast(
            "CallbackExternalIngressResponse",
            response_builder.success(
                message="External callback received",
                data=build_callback_external_accepted_response(
                    status="duplicate" if is_duplicate else "submitted",
                    callback_type=callback_type,
                    request_id=request_id,
                    trace_id=outcome.trace_id,
                    event_id=_resolve_callback_event_id(callback_data),
                    causation_id=_resolve_callback_causation_id(callback_data),
                ),
            ),
        )

    except ValueError as exc:
        logger.error(f"外部回调契约校验失败: {exc}")
        return await _handle_external_validation_failure(
            db,
            request,
            request_id=request_id,
            callback_data=callback_data,
            message=f"外部回调契约校验失败: {exc}",
            response_time_ms=_response_time_ms(start_time),
            failure_stage=_FAILURE_STAGE_CONTRACT_VALIDATE,
        )
    except Exception as exc:
        response_time_ms = _response_time_ms(start_time)
        logger.error(f"外部回调处理失败: {exc}")
        await _log_callback_outcome(
            db,
            request,
            callback_type="external",
            subject_code=callback_type,
            request_body=callback_data,
            request_id=request_id,
            trace_id=external_trace_id,
            response_status=500,
            response_time_ms=response_time_ms,
            success=False,
            record_audit=True,
            audit_title="外部系统回调",
            error_message=str(exc),
            ingress_outcome=_INGRESS_OUTCOME_FAILED,
            failure_stage=_FAILURE_STAGE_ORCHESTRATION,
        )
        raise


class CallbackIngressService:
    """Callback HTTP 入站用例服务。"""

    async def handle_result(
        self,
        request: Request,
        db: AsyncSessionDep,
        *,
        request_id: str | None,
        start_time: float,
        enqueue_processing: Callable[[], None],
    ) -> CallbackResultIngressResponse:
        return await handle_callback_result(
            request,
            db,
            request_id=request_id,
            start_time=start_time,
            enqueue_processing=enqueue_processing,
        )

    async def handle_event(
        self,
        request: Request,
        db: AsyncSessionDep,
        *,
        request_id: str | None,
        start_time: float,
        enqueue_processing: Callable[[], None],
    ) -> CallbackEventIngressResponse:
        decision = await self.handle_event_decision(
            request,
            db,
            request_id=request_id,
            start_time=start_time,
            enqueue_processing=enqueue_processing,
        )
        return decision.body

    async def handle_event_decision(
        self,
        request: Request,
        db: AsyncSessionDep,
        *,
        request_id: str | None,
        start_time: float,
        enqueue_processing: Callable[[], None],
    ) -> CallbackEventIngressDecision:
        return await handle_callback_event(
            request,
            db,
            request_id=request_id,
            start_time=start_time,
            enqueue_processing=enqueue_processing,
        )

    async def handle_external(
        self,
        request: Request,
        db: AsyncSessionDep,
        *,
        request_id: str | None,
        start_time: float,
        enqueue_processing: Callable[[], None],
    ) -> CallbackExternalIngressResponse:
        return await handle_callback_external(
            request,
            db,
            request_id=request_id,
            start_time=start_time,
            enqueue_processing=enqueue_processing,
        )


callback_ingress_service = CallbackIngressService()

__all__ = [
    "CallbackIngressService",
    "callback_ingress_service",
]
