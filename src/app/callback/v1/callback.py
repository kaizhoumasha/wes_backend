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
import uuid
from typing import Any, cast

from fastapi import APIRouter, Depends, Request, status
from pydantic import ValidationError

from src.app.callback.models import CallbackEventRequest
from src.app.callback.services import callback_log_service
from src.app.device.models.command import CommandCallbackResult
from src.app.device.services import device_command_service, device_context_service, device_service
from src.app.sys.models.audit_log import OperaStatus
from src.app.sys.services import audit_log_service
from src.app.workline.models.inbox import SourceSystem
from src.app.workline.services import inbox_service
from src.core.api_security import RequireAPIPermission
from src.core.logger import logger
from src.core.response import response_builder
from src.core.response.response_code import ClientErrorCode, ResourceErrorCode
from src.database.dependencies import AsyncSessionDep
from src.utils.audit import get_request_id
from src.utils.fast_fail import fast_fail_check
from src.utils.timezone import timezone
from src.workline_runtime.utils import JsonDict, ensure_dict, resolve_first_str

router = APIRouter()

_DUPLICATE_ERROR_MARKER = "已存在（幂等键重复）"


# 字段别名常量
_CORRELATION_ID_ALIASES = ("correlation_id",)
_DEVICE_CODE_ALIASES = ("device_code",)
_COMMAND_CODE_ALIASES = ("command_code",)


def _is_duplicate_inbox_error(error: ValueError) -> bool:
    return _DUPLICATE_ERROR_MARKER in str(error)


def _resolve_optional_str(payload: JsonDict, aliases: tuple[str, ...]) -> str | None:
    value = resolve_first_str(payload, aliases)
    return value or None


def _require_first_str(payload: JsonDict, aliases: tuple[str, ...], field_name: str) -> str:
    value = resolve_first_str(payload, aliases)
    if value:
        return value
    raise ValueError(f"{field_name} is required")


def _resolve_command_type(
    callback_result_data: JsonDict,
    command_params: JsonDict,
    existing_command: object,
) -> str | None:
    candidates = [
        callback_result_data.get("command_type"),
        command_params.get("action"),
        command_params.get("task_type"),
    ]
    existing_task_type = getattr(existing_command, "task_type", None)
    candidates.append(getattr(existing_task_type, "value", existing_task_type))

    for candidate in candidates:
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


def _enqueue_workline_processing() -> None:
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
    callback_type: str,
    device_id: str,
    request_body: JsonDict,
    request_id: str | None,
    correlation_id: str | None,
    response_status: int,
    response_time_ms: int,
    error_message: str | None = None,
) -> JsonDict:
    return {
        "callback_type": callback_type,
        "device_id": device_id,
        "request_body": request_body,
        "client_ip": request.client.host if request.client else None,
        "user_agent": request.headers.get("User-Agent"),
        "request_id": request_id,
        "correlation_id": correlation_id,
        "response_status": response_status,
        "response_time_ms": response_time_ms,
        "error_message": error_message,
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


async def _commit_and_enqueue_workline_processing(db: AsyncSessionDep) -> None:
    """先持久化 Inbox，再触发异步编排。"""

    await db.commit()
    _enqueue_workline_processing()


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
) -> None:
    _ = await callback_log_service.log_callback(
        db,
        **_build_callback_log_payload(
            request,
            callback_type=callback_type,
            device_id=device_id,
            request_body=request_body,
            request_id=request_id,
            correlation_id=correlation_id,
            response_status=response_status,
            response_time_ms=response_time_ms,
            error_message=error_message,
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


async def _handle_result_validation_failure(
    db: AsyncSessionDep,
    request: Request,
    *,
    request_id: str | None,
    callback_data: JsonDict,
    message: str,
) -> JsonDict:
    response_time_ms = 0
    await _log_callback_outcome(
        db,
        request,
        callback_type="result",
        device_id=resolve_first_str(callback_data, _DEVICE_CODE_ALIASES) or "UNKNOWN",
        request_body=callback_data,
        request_id=request_id,
        correlation_id=_resolve_optional_str(callback_data, _CORRELATION_ID_ALIASES),
        response_status=400,
        response_time_ms=response_time_ms,
        success=False,
        record_audit=True,
        audit_title="设备回调结果",
        error_message=message,
    )
    return _build_contract_fail(message)


async def _handle_event_validation_failure(
    db: AsyncSessionDep,
    request: Request,
    *,
    request_id: str | None,
    event_data: JsonDict,
    message: str,
) -> JsonDict:
    response_time_ms = 0
    await _log_callback_outcome(
        db,
        request,
        callback_type="event",
        device_id=resolve_first_str(event_data, _DEVICE_CODE_ALIASES) or "UNKNOWN",
        request_body=event_data,
        request_id=request_id,
        correlation_id=_resolve_optional_str(event_data, _CORRELATION_ID_ALIASES),
        response_status=400,
        response_time_ms=response_time_ms,
        success=False,
        record_audit=True,
        audit_title="设备事件上报",
        error_message=message,
    )
    return _build_contract_fail(message)


async def _handle_external_validation_failure(
    db: AsyncSessionDep,
    request: Request,
    *,
    request_id: str | None,
    callback_data: JsonDict,
    message: str,
) -> JsonDict:
    response_time_ms = 0
    await _log_callback_outcome(
        db,
        request,
        callback_type="external",
        device_id=resolve_first_str(callback_data, ("callback_type", "source_system")) or "UNKNOWN",
        request_body=callback_data,
        request_id=request_id,
        correlation_id=_resolve_optional_str(callback_data, _CORRELATION_ID_ALIASES),
        response_status=400,
        response_time_ms=response_time_ms,
        success=False,
        record_audit=True,
        audit_title="外部系统回调",
        error_message=message,
    )
    return _build_contract_fail(message)


async def _is_workline_command_callback(
    db: AsyncSessionDep,
    *,
    existing_command: object | None,
    device_code: str,
) -> bool:
    if _has_workline_binding(getattr(existing_command, "workline_id", None)):
        return True
    device = await device_service.get_device_by_code(db, device_code)
    return _has_workline_binding(getattr(device, "work_line_id", None))


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
async def callback_result(
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
        )

    try:
        device_code = _require_first_str(callback_data, _DEVICE_CODE_ALIASES, "device_code")
        command_code = _require_first_str(callback_data, _COMMAND_CODE_ALIASES, "command_code")
    except ValueError as exc:
        logger.error(f"指令结果回调最小包络校验失败: {exc}")
        return await _handle_result_validation_failure(
            db,
            request,
            request_id=request_id,
            callback_data=callback_data,
            message=f"结果回调最小包络校验失败: {exc}",
        )

    logger.info(f"收到指令结果回调: {command_code} (request_id={request_id})")

    # 使用 DeviceContextService 验证设备和工作线上下文
    ctx_result, ctx_error = await device_context_service.resolve(db, device_code)
    if ctx_error:
        return JsonDict(**ctx_error, request_id=request_id)

    # ctx_error 为 None 时，ctx_result 必有值（类型检查器无法理解 tuple 解包后的关联）
    # 安全保证：上面已检查 ctx_error 并提前返回
    _plugin_key = ctx_result.plugin_key  # type: ignore[union-attr]
    _resolved_contract_version = ctx_result.contract_version  # type: ignore[union-attr]

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
                correlation_id=_resolve_optional_str(callback_data, _CORRELATION_ID_ALIASES),
                response_status=404,
                response_time_ms=int((time.time() - start_time) * 1000),
                success=False,
                record_audit=True,
                audit_title="设备回调结果",
                error_message=message,
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
        inherited_correlation_id = (
            existing_command.correlation_id if isinstance(existing_command.correlation_id, str) else None
        )
        raw_command_params = getattr(existing_command, "params", None)
        command_params = ensure_dict(raw_command_params)
        callback_result_data = ensure_dict(callback.data)
        command_type = _resolve_command_type(callback_result_data, command_params, existing_command)

        is_workline_callback = await _is_workline_command_callback(
            db,
            existing_command=existing_command,
            device_code=callback.device_code,
        )

        if is_workline_callback:
            try:
                _ = await inbox_service.create_command_result_inbox(
                    db=db,
                    command_code=callback.command_code,
                    device_code=callback.device_code,
                    result=callback.result.value,
                    finish_time=callback.finish_time,
                    data=callback.data or {},
                    command_type=command_type,
                    error_detail=callback.error_detail,
                    source_message_id=request_id,
                    correlation_id=inherited_correlation_id,
                )
                logger.info(f"指令结果已写入 Inbox: {callback.command_code}")
            except ValueError as exc:
                if _is_duplicate_inbox_error(exc):
                    is_duplicate = True
                    logger.info(f"指令结果幂等重复，将跳过业务处理: {callback.command_code}")
                else:
                    raise

            if is_duplicate:
                await _commit_and_enqueue_workline_processing(db)
            else:
                command = await device_command_service.handle_callback_result(db, callback)
                if command is None:
                    raise RuntimeError(f"回调指令处理失败: {callback.command_code}")
                inherited_correlation_id = command.correlation_id or inherited_correlation_id
                await _commit_and_enqueue_workline_processing(db)
                logger.info(
                    f"指令结果处理完成: {callback.command_code} -> "
                    f"status={command.status.value}, "
                    f"duration={command.get_duration_ms()}ms, "
                    f"contract_version={_resolved_contract_version}"
                )
        else:
            command = await device_command_service.handle_callback_result(db, callback)
            if command is None:
                raise RuntimeError(f"回调指令处理失败: {callback.command_code}")
            inherited_correlation_id = command.correlation_id or inherited_correlation_id
            await db.commit()
            logger.info(
                f"非 Workline 指令结果已同步处理: {callback.command_code} -> "
                f"status={command.status.value}, "
                f"duration={command.get_duration_ms()}ms"
            )

        response_time_ms = int((time.time() - start_time) * 1000)
        await _log_callback_outcome(
            db,
            request,
            callback_type="result",
            device_id=callback.device_code,
            request_body=callback_data,
            request_id=request_id,
            correlation_id=inherited_correlation_id,
            response_status=200,
            response_time_ms=response_time_ms,
            success=not is_duplicate,
            record_audit=not is_duplicate,
            audit_title="设备回调结果",
            error_message="幂等重复: 已存在相同事件" if is_duplicate else None,
        )
        return response_builder.success(data={"ack": True, "correlation_id": inherited_correlation_id})

    except ValidationError as exc:
        logger.error(f"指令结果回调模型校验失败: {exc}")
        return await _handle_result_validation_failure(
            db,
            request,
            request_id=request_id,
            callback_data=callback_data,
            message="结果回调模型校验失败",
        )
    except ValueError as exc:
        logger.error(f"指令结果回调契约校验失败: {exc}")
        return await _handle_result_validation_failure(
            db,
            request,
            request_id=request_id,
            callback_data=callback_data,
            message=f"结果回调契约校验失败: {exc}",
        )
    except Exception as exc:
        response_time_ms = int((time.time() - start_time) * 1000)
        logger.error(f"指令结果回调处理失败: {exc}")
        _ = await callback_log_service.log_callback(
            db,
            **_build_callback_log_payload(
                request,
                callback_type="result",
                device_id=device_code,
                request_body=callback_data,
                request_id=request_id,
                correlation_id=_resolve_optional_str(callback_data, _CORRELATION_ID_ALIASES),
                response_status=500,
                response_time_ms=response_time_ms,
                error_message=str(exc),
            ),
        )
        await _record_callback_audit_log(
            db,
            request,
            title="设备回调结果",
            args=callback_data,
            cost_time=response_time_ms / 1000,
            success=False,
            message=str(exc),
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
        )

    try:
        device_code = _require_first_str(event_data, _DEVICE_CODE_ALIASES, "device_code")
    except ValueError as exc:
        logger.error(f"设备事件最小包络校验失败: {exc}")
        return await _handle_event_validation_failure(
            db,
            request,
            request_id=request_id,
            event_data=event_data,
            message=f"事件上报最小包络校验失败: {exc}",
        )

    logger.info(f"收到设备事件上报: {device_code} (request_id={request_id})")

    # 使用 DeviceContextService 验证设备和工作线上下文
    ctx_result, ctx_error = await device_context_service.resolve(db, device_code)
    if ctx_error:
        return JsonDict(**ctx_error, request_id=request_id)

    # ctx_error 为 None 时，ctx_result 必有值（类型检查器无法理解 tuple 解包后的关联）
    _plugin_key = ctx_result.plugin_key  # type: ignore[union-attr]
    _resolved_contract_version = ctx_result.contract_version  # type: ignore[union-attr]

    try:
        # 直接用原始 payload 验证（Pydantic 自动处理别名）
        normalized_event_request = CallbackEventRequest.model_validate(event_data)
        event_timestamp = normalized_event_request.timestamp
        if event_timestamp is None:
            event_timestamp = int(timezone.now_utc().timestamp() * 1000)

        # ctx_error 为 None 时，ctx_result 必有值
        is_workline_event = ctx_result.is_workline_bound  # type: ignore[union-attr]
        event_correlation_id = _resolve_optional_str(event_data, _CORRELATION_ID_ALIASES)
        if is_workline_event and event_correlation_id is None:
            event_correlation_id = f"corr_{uuid.uuid4().hex}"

        if is_workline_event:
            try:
                _ = await inbox_service.create_device_event_inbox(
                    db=db,
                    device_code=normalized_event_request.device_code,
                    event_type=normalized_event_request.event_type,
                    timestamp=event_timestamp,
                    data=cast("dict[str, Any]", normalized_event_request.data or {}),
                    source_message_id=request_id,
                    correlation_id=event_correlation_id,
                )
                logger.info(
                    f"设备事件已写入 Inbox: "
                    f"{normalized_event_request.device_code} -> {normalized_event_request.event_type}"
                )
            except ValueError as exc:
                if _is_duplicate_inbox_error(exc):
                    is_duplicate = True
                    logger.info(
                        "设备事件幂等重复，将跳过业务处理: "
                        f"{normalized_event_request.device_code} -> {normalized_event_request.event_type}"
                    )
                else:
                    raise

            await _commit_and_enqueue_workline_processing(db)

        response_time_ms = int((time.time() - start_time) * 1000)
        await _log_callback_outcome(
            db,
            request,
            callback_type="event",
            device_id=normalized_event_request.device_code,
            request_body=event_data,
            request_id=request_id,
            correlation_id=event_correlation_id,
            response_status=200,
            response_time_ms=response_time_ms,
            success=not is_duplicate,
            record_audit=not is_duplicate,
            audit_title="设备事件上报",
            error_message="幂等重复: 已存在相同事件" if is_duplicate else None,
        )
        logger.info(f"设备事件已提交处理: {normalized_event_request.device_code} (request_id={request_id})")
        return response_builder.success(
            message="Event received",
            data={
                "status": "duplicate" if is_duplicate else "submitted",
                "device_code": normalized_event_request.device_code,
                "correlation_id": event_correlation_id,
            },
        )

    except ValidationError as exc:
        logger.error(f"设备事件上报模型校验失败: {exc}")
        return await _handle_event_validation_failure(
            db,
            request,
            request_id=request_id,
            event_data=event_data,
            message="事件上报模型校验失败",
        )
    except ValueError as exc:
        logger.error(f"设备事件上报契约校验失败: {exc}")
        return await _handle_event_validation_failure(
            db,
            request,
            request_id=request_id,
            event_data=event_data,
            message=f"事件上报契约校验失败: {exc}",
        )
    except Exception as exc:
        response_time_ms = int((time.time() - start_time) * 1000)
        logger.error(f"设备事件上报处理失败: {exc}")
        _ = await callback_log_service.log_callback(
            db,
            **_build_callback_log_payload(
                request,
                callback_type="event",
                device_id=device_code,
                request_body=event_data,
                request_id=request_id,
                correlation_id=_resolve_optional_str(event_data, _CORRELATION_ID_ALIASES),
                response_status=500,
                response_time_ms=response_time_ms,
                error_message=str(exc),
            ),
        )
        await _record_callback_audit_log(
            db,
            request,
            title="设备事件上报",
            args=event_data,
            cost_time=response_time_ms / 1000,
            success=False,
            message=str(exc),
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
        )

    logger.info(f"收到外部系统回调: {callback_type} (request_id={request_id})")

    try:
        try:
            _ = await inbox_service.create_external_http_inbox(
                db=db,
                callback_type=callback_type,
                correlation_id=correlation_id,
                payload=callback_data,
                source_system=SourceSystem.SYSTEM,
                source_message_id=request_id,
            )
            logger.info(f"外部回调已写入 Inbox: {callback_type}")
        except ValueError as exc:
            if _is_duplicate_inbox_error(exc):
                is_duplicate = True
                logger.info(f"外部回调幂等重复，将跳过业务处理: {callback_type}")
            else:
                raise

        await _commit_and_enqueue_workline_processing(db)

        response_time_ms = int((time.time() - start_time) * 1000)
        await _log_callback_outcome(
            db,
            request,
            callback_type="external",
            device_id=callback_type,
            request_body=callback_data,
            request_id=request_id,
            correlation_id=correlation_id,
            response_status=200,
            response_time_ms=response_time_ms,
            success=not is_duplicate,
            record_audit=not is_duplicate,
            audit_title="外部系统回调",
            error_message="幂等重复: 已存在相同事件" if is_duplicate else None,
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
        )
    except Exception as exc:
        response_time_ms = int((time.time() - start_time) * 1000)
        logger.error(f"外部回调处理失败: {exc}")
        _ = await callback_log_service.log_callback(
            db,
            **_build_callback_log_payload(
                request,
                callback_type="external",
                device_id=callback_type,
                request_body=callback_data,
                request_id=request_id,
                correlation_id=correlation_id,
                response_status=500,
                response_time_ms=response_time_ms,
                error_message=str(exc),
            ),
        )
        await _record_callback_audit_log(
            db,
            request,
            title="外部系统回调",
            args=callback_data,
            cost_time=response_time_ms / 1000,
            success=False,
            message=str(exc),
        )
        raise


__all__ = ["router"]
