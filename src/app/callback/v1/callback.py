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
from types import SimpleNamespace
from typing import Any, cast

from fastapi import APIRouter, Depends, Request, status
from pydantic import ValidationError

from src.app.callback.models import CallbackEventRequest
from src.app.callback.services import (
    callback_log_service,
    callback_orchestration_service,
)
from src.app.device.models import parse_device_capabilities
from src.app.device.models.command import CommandCallbackResult
from src.app.device.services import device_command_service, device_context_service, device_service
from src.app.sys.models.audit_log import OperaStatus
from src.app.sys.services import audit_log_service
from src.app.workline.services import inbox_service, workline_service  # noqa: F401
from src.core.api_security import RequireAPIPermission
from src.core.logger import logger
from src.core.response import response_builder
from src.core.response.response_code import ClientErrorCode, ResourceErrorCode
from src.database.dependencies import AsyncSessionDep
from src.utils.audit import get_request_id
from src.utils.fast_fail import fast_fail_check
from src.workline_runtime.diagnostics import (
    ErrorCode,
    build_diagnostic_card,
    build_diagnostic_context,
    build_diagnostic_event,
)
from src.workline_runtime.plugin_sdk import canonicalize_event_type
from src.workline_runtime.trace_context import TraceContext
from src.workline_runtime.utils import JsonDict, resolve_first_str

router = APIRouter()

# 字段别名常量
_CORRELATION_ID_ALIASES = ("correlation_id",)
_DEVICE_CODE_ALIASES = ("device_code",)
_COMMAND_CODE_ALIASES = ("command_code",)

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
_FAILURE_STAGE_ORCHESTRATION = "ORCHESTRATION"


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


def _resolve_callback_correlation_id(payload: JsonDict) -> str | None:
    return _resolve_optional_str(payload, _CORRELATION_ID_ALIASES)


def _resolve_payload_command_code(payload: JsonDict) -> str | None:
    command_code = payload.get("command_code")
    return command_code if isinstance(command_code, str) else None


def _enqueue_workline_processing() -> None:
    """兼容旧测试 patch 点：触发 Workline Inbox 异步处理。"""
    from src.celery_app.app import celery_app

    cast("Any", celery_app).send_task(
        "src.celery_app.tasks.workline.process_inbox_batch",
        kwargs={"limit": 10},
    )


def _has_workline_binding(value: object) -> bool:
    return isinstance(value, int) and value > 0


def _build_callback_log_payload(
    request: Request,
    *,
    trace: TraceContext,
    callback_type: str,
    device_id: str,
    request_body: JsonDict,
    response_status: int,
    response_time_ms: int,
    error_message: str | None = None,
    ingress_outcome: str | None = None,
    failure_stage: str | None = None,
) -> JsonDict:
    return {
        "callback_type": callback_type,
        "device_id": device_id,
        "request_body": request_body,
        "client_ip": request.client.host if request.client else None,
        "user_agent": request.headers.get("User-Agent"),
        "request_id": trace.request_id,
        "correlation_id": trace.correlation_id,
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
    callback_type = _require_first_str(payload, ("callback_type",), "callback_type")
    correlation_id = _require_first_str(payload, _CORRELATION_ID_ALIASES, "correlation_id")

    return {
        "callback_type": callback_type,
        "correlation_id": correlation_id,
        "payload": payload,
    }


def _build_contract_fail(message: str) -> JsonDict:
    return response_builder.fail(
        code=ClientErrorCode.VALIDATION_ERROR,
        message=message,
        data={"ack": False},
    )


def _build_not_found_fail(message: str) -> JsonDict:
    return response_builder.fail(
        code=ResourceErrorCode.NOT_FOUND,
        message=message,
        data={"ack": False},
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
) -> None:
    trace = TraceContext.from_request(
        request_id=request_id,
        correlation_id=_resolve_callback_correlation_id(payload),
        canonical_event_type=canonical_event_type,
    )
    if device is not None:
        trace = trace.with_device(device)
    if workline is not None:
        trace = trace.with_workline(workline)
    if command_code is not None:
        trace = trace.with_command_code(command_code)

    card = build_diagnostic_card(
        build_diagnostic_event(
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
    )
    logger.warning(f"[CallbackDiagnostic] {card.model_dump_json(exclude_none=True)}")


async def _log_callback_outcome(
    db: AsyncSessionDep,
    request: Request,
    *,
    callback_type: str,
    device_id: str,
    request_body: JsonDict,
    request_id: str | None,
    correlation_id: str | None,
    response_status: int,
    response_time_ms: int,
    success: bool,
    record_audit: bool,
    audit_title: str,
    error_message: str | None = None,
    ingress_outcome: str | None = None,
    failure_stage: str | None = None,
) -> None:
    trace = TraceContext.from_request(
        request_id=request_id,
        correlation_id=correlation_id,
    )
    _ = await callback_log_service.log_callback(
        db,
        trace=trace,
        **_build_callback_log_payload(
            request,
            trace=trace,
            callback_type=callback_type,
            device_id=device_id,
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
) -> JsonDict:
    await _log_callback_outcome(
        db,
        request,
        callback_type=callback_type,
        device_id=_resolve_callback_subject(callback_type, request_body),
        request_body=request_body,
        request_id=request_id,
        correlation_id=_resolve_callback_correlation_id(request_body),
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
) -> JsonDict:
    return await _handle_validation_failure(
        db,
        request,
        callback_type="result",
        request_id=request_id,
        request_body=callback_data,
        message=message,
        response_time_ms=response_time_ms,
        failure_stage=failure_stage,
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
) -> JsonDict:
    return await _handle_validation_failure(
        db,
        request,
        callback_type="event",
        request_id=request_id,
        request_body=event_data,
        message=message,
        response_time_ms=response_time_ms,
        failure_stage=failure_stage,
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
) -> JsonDict:
    return await _handle_validation_failure(
        db,
        request,
        callback_type="external",
        request_id=request_id,
        request_body=callback_data,
        message=message,
        response_time_ms=response_time_ms,
        failure_stage=failure_stage,
    )


async def _handle_device_context_failure(
    db: AsyncSessionDep,
    request: Request,
    *,
    callback_type: str,
    device_id: str,
    request_body: JsonDict,
    request_id: str | None,
    correlation_id: str | None,
    response_time_ms: int,
    error: JsonDict,
    audit_title: str,
) -> JsonDict:
    message = str(error.get("message") or "设备上下文解析失败")
    await _log_callback_outcome(
        db,
        request,
        callback_type=callback_type,
        device_id=device_id,
        request_body=request_body,
        request_id=request_id,
        correlation_id=correlation_id,
        response_status=_resolve_ctx_error_response_status(error),
        response_time_ms=response_time_ms,
        success=False,
        record_audit=True,
        audit_title=audit_title,
        error_message=message,
        ingress_outcome=_INGRESS_OUTCOME_REJECTED,
        failure_stage=_FAILURE_STAGE_DEVICE_CONTEXT_RESOLVE,
    )
    return JsonDict(**error, request_id=request_id)


@router.post(
    "/result",
    response_model=None,
    status_code=status.HTTP_200_OK,
    summary="任务结果回传",
    dependencies=[
        Depends(RequireAPIPermission("api:callback:result")),
        Depends(fast_fail_check),
    ],
    description="设备完成指令后，调用此接口回传执行结果",
)
async def callback_result(  # noqa: PLR0911 - ingress 分支显式早返回，便于 failure_stage 精确归因
    request: Request,
    db: AsyncSessionDep,
) -> JsonDict:
    start_time = time.time()
    request_id = get_request_id()
    callback_data: JsonDict = {}
    is_duplicate = False

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
        device_code = _require_first_str(callback_data, _DEVICE_CODE_ALIASES, "device_code")
        command_code = _require_first_str(callback_data, _COMMAND_CODE_ALIASES, "command_code")
    except ValueError as exc:
        logger.error(f"指令结果回调最小包络校验失败: {exc}")
        _log_callback_diagnostic(
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

    # 使用 DeviceContextService 验证设备和工作线上下文
    ctx_result, ctx_error = await device_context_service.resolve(db, device_code)
    if ctx_error:
        return await _handle_device_context_failure(
            db,
            request,
            callback_type="result",
            device_id=device_code,
            request_body=callback_data,
            request_id=request_id,
            correlation_id=_resolve_callback_correlation_id(callback_data),
            response_time_ms=_response_time_ms(start_time),
            error=ctx_error,
            audit_title="设备回调结果",
        )

    # ctx_error 为 None 时，ctx_result 必有值（类型检查器无法理解 tuple 解包后的关联）
    # 安全保证：上面已检查 ctx_error 并提前返回
    device = ctx_result.device  # type: ignore[union-attr]
    workline = ctx_result.workline  # type: ignore[union-attr]
    _plugin_key = ctx_result.plugin_key  # type: ignore[union-attr]
    _resolved_contract_version = ctx_result.contract_version  # type: ignore[union-attr]

    try:
        capabilities = parse_device_capabilities(getattr(device, "capabilities_json", None))
    except (TypeError, ValidationError, ValueError) as exc:
        message = f"设备能力配置无效: {exc}"
        logger.error(f"指令结果回调能力配置校验失败: {exc}")
        _log_callback_diagnostic(
            error_code=ErrorCode.CONFIG_INVALID,
            message=message,
            request_id=request_id,
            callback_type="result",
            payload=callback_data,
            device=device,
            workline=workline,
            command_code=command_code,
        )
        return await _handle_result_validation_failure(
            db,
            request,
            request_id=request_id,
            callback_data=callback_data,
            message=message,
            response_time_ms=_response_time_ms(start_time),
            failure_stage=_FAILURE_STAGE_CONFIG_VALIDATE,
        )

    try:
        existing_command = await device_command_service.get_command_by_code(db, command_code)
        if existing_command is None:
            message = f"未找到指令: {command_code}"
            await _log_callback_outcome(
                db,
                request,
                callback_type="result",
                device_id=device_code,
                request_body=callback_data,
                request_id=request_id,
                correlation_id=_resolve_callback_correlation_id(callback_data),
                response_status=404,
                response_time_ms=_response_time_ms(start_time),
                success=False,
                record_audit=True,
                audit_title="设备回调结果",
                error_message=message,
                ingress_outcome=_INGRESS_OUTCOME_REJECTED,
                failure_stage=_FAILURE_STAGE_COMMAND_LOOKUP,
            )
            return _build_not_found_fail(message)

        # 从已有 command 获取 plugin_key 和 contract_version（覆盖设备上下文）
        command_plugin_key = getattr(existing_command, "plugin_key", None)
        if isinstance(command_plugin_key, str) and command_plugin_key:
            _plugin_key = command_plugin_key
        command_contract_version = getattr(existing_command, "contract_version", None)
        if isinstance(command_contract_version, str) and command_contract_version:
            _resolved_contract_version = command_contract_version

        # 直接用原始 payload 验证（Pydantic 自动处理别名）
        callback = CommandCallbackResult.model_validate(callback_data)
        if not capabilities.allows_result_callback():
            message = f"设备 {device_code} 未声明支持结果回调"
            _log_callback_diagnostic(
                error_code=ErrorCode.CONFIG_INVALID,
                message=message,
                request_id=request_id,
                callback_type="result",
                payload=callback_data,
                device=device,
                workline=workline,
                command_code=callback.command_code,
            )
            return await _handle_result_validation_failure(
                db,
                request,
                request_id=request_id,
                callback_data=callback_data,
                message=message,
                response_time_ms=_response_time_ms(start_time),
                failure_stage=_FAILURE_STAGE_CAPABILITY_VALIDATE,
            )
        outcome = await callback_orchestration_service.process_result(
            db,
            callback=callback,
            existing_command=existing_command,
            request_id=request_id,
            resolved_contract_version=_resolved_contract_version,
            command_service=device_command_service,
            device_service=device_service,
            inbox_service=inbox_service,
            enqueue_processing=_enqueue_workline_processing,
        )
        is_duplicate = outcome.is_duplicate

        response_time_ms = _response_time_ms(start_time)
        await _log_callback_outcome(
            db,
            request,
            callback_type="result",
            device_id=callback.device_code,
            request_body=callback_data,
            request_id=request_id,
            correlation_id=outcome.correlation_id,
            response_status=200,
            response_time_ms=response_time_ms,
            success=not is_duplicate,
            record_audit=not is_duplicate,
            audit_title="设备回调结果",
            error_message="幂等重复: 已存在相同事件" if is_duplicate else None,
            ingress_outcome=_INGRESS_OUTCOME_DUPLICATE if is_duplicate else _INGRESS_OUTCOME_ACCEPTED,
        )
        return response_builder.success(data={"ack": True, "correlation_id": outcome.correlation_id})

    except ValidationError as exc:
        logger.error(f"指令结果回调模型校验失败: {exc}")
        _log_callback_diagnostic(
            error_code=ErrorCode.CALLBACK_SCHEMA_INVALID,
            message="结果回调模型校验失败",
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
            message="结果回调模型校验失败",
            response_time_ms=_response_time_ms(start_time),
            failure_stage=_FAILURE_STAGE_CONTRACT_VALIDATE,
        )
    except ValueError as exc:
        logger.error(f"指令结果回调契约校验失败: {exc}")
        _log_callback_diagnostic(
            error_code=ErrorCode.CONFIG_INVALID,
            message=f"结果回调契约校验失败: {exc}",
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
            message=f"结果回调契约校验失败: {exc}",
            response_time_ms=_response_time_ms(start_time),
            failure_stage=_FAILURE_STAGE_CONTRACT_VALIDATE,
        )
    except Exception as exc:
        response_time_ms = _response_time_ms(start_time)
        logger.error(f"指令结果回调处理失败: {exc}")
        await _log_callback_outcome(
            db,
            request,
            callback_type="result",
            device_id=device_code,
            request_body=callback_data,
            request_id=request_id,
            correlation_id=_resolve_callback_correlation_id(callback_data),
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


@router.post(
    "/event",
    response_model=None,
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
) -> JsonDict:
    start_time = time.time()
    request_id = get_request_id()
    event_data: JsonDict = {}
    is_duplicate = False

    try:
        event_data = await _read_request_json(request)
    except Exception as exc:
        logger.error(f"设备事件上报解析失败: {exc}")
        return await _handle_event_validation_failure(
            db,
            request,
            request_id=request_id,
            event_data=event_data,
            message=f"事件上报报文格式错误: {exc}",
            response_time_ms=_response_time_ms(start_time),
            failure_stage=_FAILURE_STAGE_REQUEST_PARSE,
        )

    try:
        # event 是统一硬件事件入口：这里只做最小包络校验，
        # 不提前判断插件私有 payload 是否“业务成立”。
        normalized_event_request = CallbackEventRequest.model_validate(event_data)
    except ValidationError as exc:
        message = f"事件上报最小包络校验失败: {_summarize_validation_error(exc)}"
        logger.error(message)
        _log_callback_diagnostic(
            error_code=ErrorCode.CALLBACK_SCHEMA_INVALID,
            message=message,
            request_id=request_id,
            callback_type="event",
            payload=event_data,
        )
        return await _handle_event_validation_failure(
            db,
            request,
            request_id=request_id,
            event_data=event_data,
            message=message,
            response_time_ms=_response_time_ms(start_time),
            failure_stage=_FAILURE_STAGE_ENVELOPE_VALIDATE,
        )

    device_code = normalized_event_request.device_code
    logger.info(f"收到设备事件上报: {device_code} (request_id={request_id})")

    # 设备/工作线上下文和能力校验属于“是否可路由入站”的入口职责。
    ctx_result, ctx_error = await device_context_service.resolve(db, device_code)
    if ctx_error:
        return await _handle_device_context_failure(
            db,
            request,
            callback_type="event",
            device_id=device_code,
            request_body=event_data,
            request_id=request_id,
            correlation_id=_resolve_callback_correlation_id(event_data),
            response_time_ms=_response_time_ms(start_time),
            error=ctx_error,
            audit_title="设备事件上报",
        )

    # ctx_error 为 None 时，ctx_result 必有值（类型检查器无法理解 tuple 解包后的关联）
    device = ctx_result.device  # type: ignore[union-attr]
    workline = ctx_result.workline  # type: ignore[union-attr]

    try:
        canonical_event_type = canonicalize_event_type(normalized_event_request.event_type, workline=workline)
    except ValueError as exc:
        logger.error(f"设备事件上报契约校验失败: {exc}")
        _log_callback_diagnostic(
            error_code=ErrorCode.CONFIG_INVALID,
            message=f"事件上报契约校验失败: {exc}",
            request_id=request_id,
            callback_type="event",
            payload=event_data,
        )
        return await _handle_event_validation_failure(
            db,
            request,
            request_id=request_id,
            event_data=event_data,
            message=f"事件上报契约校验失败: {exc}",
            response_time_ms=_response_time_ms(start_time),
            failure_stage=_FAILURE_STAGE_CONTRACT_VALIDATE,
        )

    try:
        capabilities = parse_device_capabilities(getattr(device, "capabilities_json", None))
    except (TypeError, ValidationError, ValueError) as exc:
        message = f"设备能力配置无效: {exc}"
        logger.error(f"设备事件上报能力配置校验失败: {exc}")
        _log_callback_diagnostic(
            error_code=ErrorCode.CONFIG_INVALID,
            message=message,
            request_id=request_id,
            callback_type="event",
            payload=event_data,
            device=device,
            workline=workline,
            canonical_event_type=canonical_event_type,
        )
        return await _handle_event_validation_failure(
            db,
            request,
            request_id=request_id,
            event_data=event_data,
            message=message,
            response_time_ms=_response_time_ms(start_time),
            failure_stage=_FAILURE_STAGE_CONFIG_VALIDATE,
        )

    try:
        if not capabilities.supports_event(canonical_event_type):
            message = f"设备 {device_code} 未声明支持事件: {canonical_event_type}"
            _log_callback_diagnostic(
                error_code=ErrorCode.CONFIG_INVALID,
                message=message,
                request_id=request_id,
                callback_type="event",
                payload=event_data,
                device=device,
                workline=workline,
                canonical_event_type=canonical_event_type,
            )
            return await _handle_event_validation_failure(
                db,
                request,
                request_id=request_id,
                event_data=event_data,
                message=message,
                response_time_ms=_response_time_ms(start_time),
                failure_stage=_FAILURE_STAGE_CAPABILITY_VALIDATE,
            )

        # ctx_error 为 None 时，ctx_result 必有值
        is_workline_event = ctx_result.is_workline_bound  # type: ignore[union-attr]
        if not is_workline_event:
            fallback_device = await device_service.get_device_by_code(db, device_code)
            is_workline_event = _has_workline_binding(getattr(fallback_device, "work_line_id", None))

        outcome = await callback_orchestration_service.process_event(
            db,
            event_request=normalized_event_request,
            event_data=event_data,
            request_id=request_id,
            is_workline_event=is_workline_event,
            canonical_event_type=canonical_event_type,
            inbox_service=inbox_service,
            enqueue_processing=_enqueue_workline_processing,
        )
        is_duplicate = outcome.is_duplicate

        response_time_ms = _response_time_ms(start_time)
        await _log_callback_outcome(
            db,
            request,
            callback_type="event",
            device_id=normalized_event_request.device_code,
            request_body=event_data,
            request_id=request_id,
            correlation_id=outcome.correlation_id,
            response_status=200,
            response_time_ms=response_time_ms,
            success=not is_duplicate,
            record_audit=not is_duplicate,
            audit_title="设备事件上报",
            error_message="幂等重复: 已存在相同事件" if is_duplicate else None,
            ingress_outcome=_INGRESS_OUTCOME_DUPLICATE if is_duplicate else _INGRESS_OUTCOME_ACCEPTED,
        )
        logger.info(f"设备事件已提交处理: {normalized_event_request.device_code} (request_id={request_id})")
        return response_builder.success(
            message="Event received",
            data={
                "status": "duplicate" if is_duplicate else "submitted",
                "device_code": normalized_event_request.device_code,
                "correlation_id": outcome.correlation_id,
            },
        )

    except Exception as exc:
        response_time_ms = _response_time_ms(start_time)
        logger.error(f"设备事件上报处理失败: {exc}")
        await _log_callback_outcome(
            db,
            request,
            callback_type="event",
            device_id=device_code,
            request_body=event_data,
            request_id=request_id,
            correlation_id=_resolve_callback_correlation_id(event_data),
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


@router.post(
    "/external",
    response_model=None,
    status_code=status.HTTP_200_OK,
    summary="外部系统回调",
    dependencies=[Depends(RequireAPIPermission("api:callback:event"))],
    description="库位分配、AGV 等外部系统异步回调入口",
)
async def callback_external(
    request: Request,
    db: AsyncSessionDep,
) -> JsonDict:
    start_time = time.time()
    request_id = get_request_id()
    callback_data: JsonDict = {}
    is_duplicate = False
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
        correlation_id = cast("str", normalized_payload["correlation_id"])
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
            correlation_id=correlation_id,
            payload=callback_data,
            request_id=request_id,
            inbox_service=inbox_service,
            enqueue_processing=_enqueue_workline_processing,
        )
        is_duplicate = outcome.is_duplicate

        response_time_ms = _response_time_ms(start_time)
        await _log_callback_outcome(
            db,
            request,
            callback_type="external",
            device_id=callback_type,
            request_body=callback_data,
            request_id=request_id,
            correlation_id=outcome.correlation_id,
            response_status=200,
            response_time_ms=response_time_ms,
            success=not is_duplicate,
            record_audit=not is_duplicate,
            audit_title="外部系统回调",
            error_message="幂等重复: 已存在相同事件" if is_duplicate else None,
            ingress_outcome=_INGRESS_OUTCOME_DUPLICATE if is_duplicate else _INGRESS_OUTCOME_ACCEPTED,
        )
        return response_builder.success(
            message="External callback received",
            data={
                "status": "duplicate" if is_duplicate else "submitted",
                "callback_type": callback_type,
            },
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
            device_id=callback_type,
            request_body=callback_data,
            request_id=request_id,
            correlation_id=correlation_id,
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


__all__ = ["router"]
