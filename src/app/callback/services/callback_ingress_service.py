"""非设备外部 callback 入站应用服务。"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Literal, cast

from fastapi import HTTPException, Request

from src.app.callback.contracts import (
    ErrorCode,
    TraceContext,
    build_diagnostic_card,
    build_diagnostic_context,
    build_diagnostic_event,
)
from src.app.callback.contracts.external_callbacks import (
    WMS_ALLOWED_CALLBACK_TYPES,
    WMS_ORDINARY_EVENT_TYPES,
    WmsEffectStatusHintAdmissionError,
)
from src.app.callback.models import (
    CallbackExternalIngressResponse,
    CallbackRejectedIngressResponse,
    build_callback_external_accepted_response,
    build_callback_rejected_response,
)
from src.app.callback.services.callback_log_service import callback_log_service
from src.app.callback.services.callback_orchestration_service import callback_orchestration_service
from src.app.callback.utils import resolve_first_str
from src.app.contracts.external_contract_profile import (
    ExternalContractProfileDefinition,
    parse_external_contract_profile,
)
from src.app.runtime.orchestration.services.idempotency_guard import IdempotencyConflict
from src.app.runtime.orchestration.services.runtime_inbox import (
    RuntimeInboxConflict,
    RuntimeInboxPayloadTooLarge,
)
from src.app.sys.models.audit_log import OperaStatus
from src.app.sys.services import audit_log_service
from src.app.wms_integration.services import callback_normalizer as _wms_callback_normalizer
from src.app.workline.services.diagnostic_service import workline_diagnostic_service
from src.core.client_ip import resolve_client_ip
from src.core.conf import settings
from src.core.logger import logger
from src.core.response import response_builder
from src.core.response.response_code import ClientErrorCode, ResourceErrorCode, ResponseCode, ServerErrorCode

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import ValidationError

    from src.app.callback.utils import JsonDict
    from src.database.dependencies import AsyncSessionDep

# 字段别名常量
_TRACE_ID_ALIASES = ("trace_id",)
_EVENT_ID_ALIASES = ("event_id",)
_CAUSATION_ID_ALIASES = ("causation_id",)
_TRACE_TOP_LEVEL_FIELDS = frozenset({"trace_id", "event_id", "causation_id"})
_RUNTIME_INBOX_TRACE_IDENTIFIER_MAX_LENGTH = 120
_REDACTED_SECRET = "***REDACTED***"  # noqa: S105  # nosec B105 -- 固定脱敏占位符，不是凭据。
# 外部回调顶层白名单 (H4 边界一致): 不允许 provider_code/source_event_id
# 等业务追溯字段直接放顶层, 必须放入 data 内。WMS 协议 source_event_id
# 字段已被 wms_execution_callback_normalizer 内化到 callback_type 解析,
# 不需要作为顶层持久化字段。
_EXTERNAL_CALLBACK_TOP_LEVEL_FIELDS = frozenset(
    {
        "callback_type",
        "runtime_capability",
        "attributes",
        "data",
        "trace_id",
        "event_id",
        "causation_id",
        # WMS 普通事件共享包络元数据。
        "source_system",
        "source_event_id",
        "source_version",
        "occurred_at",
        "request_id",
        "timestamp",
        "signature",
        # AGV/外部执行协议 (AGV_TASK_RESULT) 顶层追溯字段。
        "command_code",
        "result",
        "finish_time",
        "device_code",
    }
)
_EXTERNAL_CALLBACK_WMS_ALLOWED_TYPES = WMS_ALLOWED_CALLBACK_TYPES - WMS_ORDINARY_EVENT_TYPES
_EXTERNAL_CALLBACK_PROVIDER_SPECIFIC_ALLOWED_TYPES = frozenset(
    {
        "AGV_TASK_RESULT",
    }
)
_EXTERNAL_CALLBACK_ALLOWED_TYPES = (
    _EXTERNAL_CALLBACK_WMS_ALLOWED_TYPES | _EXTERNAL_CALLBACK_PROVIDER_SPECIFIC_ALLOWED_TYPES
)
_EXTERNAL_CALLBACK_SOURCE_SYSTEMS_BY_CALLBACK_TYPE = {
    "AGV_TASK_RESULT": frozenset({"AGV"}),
}
_EXTERNAL_CALLBACK_RESULT_TYPES = frozenset({"AGV_TASK_RESULT"}) | (
    WMS_ALLOWED_CALLBACK_TYPES - WMS_ORDINARY_EVENT_TYPES
)
# H4 拒绝的机器可读原因码: client 可通过 reason_code 字段区分
# 顶层字段违规 vs 其他 schema 校验失败 (用于埋点和告警)。
_CALLBACK_TOP_LEVEL_FIELD_NOT_ALLOWED_REASON_CODE = "CALLBACK_TOP_LEVEL_FIELD_NOT_ALLOWED"
# H4 子层守卫: callback.data 触发硬件控制字段时的原因码。
_CALLBACK_DATA_FORBIDDEN_FIELD_REASON_CODE = "CALLBACK_DATA_FORBIDDEN_FIELD"
_FORBIDDEN_EXTERNAL_DATA_KEYS = frozenset(
    {
        "axis",
        "coordinate",
        "coordinates",
        "joint",
        "joint_angle",
        "plc",
        "plc_address",
        "plc_point",
        "safety_loop",
        "speed",
        "velocity",
        "x_coord",
        "y_coord",
    }
)

_CALLBACK_AUDIT_TITLES = {
    "external": "外部系统回调",
}

_CALLBACK_SUBJECT_ALIASES = {
    "external": ("callback_type", "source_system"),
}


class CallbackProviderProfileAdmissionService:
    """Callback 入站 provider profile admission。

    Callback admission gate: callback API 热路径必须拒绝 provider profile 未声明的
    event/result normalizer，不能只依赖 callback_type allow-list。
    """

    def __init__(self, profiles_by_provider: dict[str, ExternalContractProfileDefinition] | None = None) -> None:
        self._profiles_by_provider = profiles_by_provider or _build_default_callback_provider_profiles()

    def admit(
        self,
        *,
        provider_code: str,
        callback_type: str,
        direction: Literal["event", "result"],
    ) -> None:
        normalized_provider = provider_code.strip().upper()
        profile = self._profiles_by_provider.get(normalized_provider)
        if profile is None:
            raise PermissionError(f"provider={normalized_provider or 'UNKNOWN'} 未注册 callback profile")
        profile.ensure_inbound_normalizer_declared(callback_type, direction=direction)


def _build_default_callback_provider_profiles() -> dict[str, ExternalContractProfileDefinition]:
    wms_result_types = {
        callback_type for callback_type in _EXTERNAL_CALLBACK_RESULT_TYPES if callback_type.startswith("WMS_")
    }
    return {
        "WMS": _build_callback_provider_profile(
            "WMS",
            event_types=(
                {
                    callback_type
                    for callback_type in _EXTERNAL_CALLBACK_ALLOWED_TYPES
                    if callback_type.startswith("WMS_")
                }
                - wms_result_types
            ),
            result_types=wms_result_types,
        ),
        "AGV": _build_callback_provider_profile(
            "AGV",
            event_types=set(),
            result_types={"AGV_TASK_RESULT"},
        ),
    }


def _build_callback_provider_profile(
    provider_code: str,
    *,
    event_types: set[str] | frozenset[str],
    result_types: set[str] | frozenset[str],
) -> ExternalContractProfileDefinition:
    fixture_provider = provider_code.lower() if provider_code in {"ECS", "WMS"} else "wms"
    profile_fields = {
        "provider_code": provider_code,
        "contract_version": "default",
        "inbound_normalizers_event": sorted(event_types),
        "inbound_normalizers_result": sorted(result_types),
        "timeout_retry_query_timeout_seconds": 5,
        "timeout_retry_retry_backoff_seconds": [1],
        "fixture_set_path": f"tests/fixtures/external_contracts/{fixture_provider}/default",
        "fixture_set_required_cases": ["success", "timeout", "duplicate", "missing_event_id"],
    }
    if provider_code != "WMS":
        profile_fields["environment"] = "sandbox"
    return parse_external_contract_profile(profile_fields)


def _external_callback_normalizer_direction(callback_type: str) -> Literal["event", "result"]:
    if callback_type in _EXTERNAL_CALLBACK_RESULT_TYPES or callback_type.endswith("_TASK_RESULT"):
        return "result"
    return "event"


callback_provider_profile_admission_service = CallbackProviderProfileAdmissionService()

# callback 入口结果常量
_INGRESS_OUTCOME_ACCEPTED = "ACCEPTED"
_INGRESS_OUTCOME_REJECTED = "REJECTED"
_INGRESS_OUTCOME_FAILED = "FAILED"
_INGRESS_OUTCOME_DUPLICATE = "DUPLICATE"

# callback 入口失败阶段常量
_FAILURE_STAGE_REQUEST_PARSE = "REQUEST_PARSE"
_FAILURE_STAGE_ENVELOPE_VALIDATE = "ENVELOPE_VALIDATE"
_FAILURE_STAGE_CONTRACT_VALIDATE = "CONTRACT_VALIDATE"
_FAILURE_STAGE_ORCHESTRATION = "ORCHESTRATION"


def _resolve_ctx_error_response_status(ctx_error: JsonDict) -> int:
    code = ctx_error.get("code")
    if isinstance(code, int) and 100 <= code <= 599:
        return code
    return 500


def _resolve_ctx_error_response_code(response_status: int) -> ResponseCode:
    if response_status == 404:
        return ResourceErrorCode.NOT_FOUND
    if response_status == 403:
        return ClientErrorCode.FORBIDDEN
    return ServerErrorCode.INTERNAL_ERROR


def _resolve_optional_str(payload: JsonDict, aliases: tuple[str, ...]) -> str | None:
    value = resolve_first_str(payload, aliases)
    return value or None


def _require_first_str(payload: JsonDict, aliases: tuple[str, ...], field_name: str) -> str:
    value = resolve_first_str(payload, aliases)
    if value:
        return value
    raise ValueError(f"{field_name} is required")


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

    for field_name in _TRACE_TOP_LEVEL_FIELDS:
        value = payload.get(field_name)
        if isinstance(value, str) and len(value) > _RUNTIME_INBOX_TRACE_IDENTIFIER_MAX_LENGTH:
            raise ValueError(f"{callback_type}.{field_name} 超过最大长度 {_RUNTIME_INBOX_TRACE_IDENTIFIER_MAX_LENGTH}")


def _collect_forbidden_param_keys(
    data: Any,
    *,
    forbidden: frozenset[str] = _FORBIDDEN_EXTERNAL_DATA_KEYS,
    path: str = "data",
) -> list[tuple[str, str]]:
    """递归扫描 ``data`` 字典, 收集所有触犯 ``_FORBIDDEN_PARAM_KEYS`` 的路径。

    H4 子层守卫: 阻断 attacker 通过 callback.data 注入 plc_address / coordinate
    等直连设备的控制字段, 与 CommandBase.params 入站校验保持一致。
    返回 ``(dotted_path, key)`` 列表; 空列表表示通过。
    """

    hits: list[tuple[str, str]] = []
    if isinstance(data, dict):
        for raw_key, raw_value in data.items():
            if not isinstance(raw_key, str):
                hits.append((path, "<non-str-key>"))
                continue
            if raw_key.lower() in forbidden:
                hits.append((f"{path}.{raw_key}", raw_key))
            hits.extend(_collect_forbidden_param_keys(raw_value, forbidden=forbidden, path=f"{path}.{raw_key}"))
        return hits
    if isinstance(data, list):
        for index, item in enumerate(data):
            hits.extend(_collect_forbidden_param_keys(item, forbidden=forbidden, path=f"{path}[{index}]"))
        return hits
    return []


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


def _redact_callback_signatures(value: Any) -> Any:
    """复制 callback 证据并递归移除任何层级、任何大小写的 signature。"""

    if isinstance(value, dict):
        return {
            key: _REDACTED_SECRET
            if isinstance(key, str) and key.casefold() == "signature"
            else _redact_callback_signatures(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_callback_signatures(item) for item in value]
    return value


async def _record_callback_audit_log(
    db: AsyncSessionDep,
    request: Request,
    *,
    title: str,
    args: JsonDict,
    cost_time: float,
    success: bool,
    response_status: int,
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
            code=str(response_status),
            msg=message,
            cost_time=cost_time,
        )
    except Exception as audit_error:
        logger.error(f"记录回调审计日志失败: {audit_error}")


async def _read_request_json(request: Request) -> JsonDict:
    if not isinstance(request, Request):
        payload = await request.json()
        if not isinstance(payload, dict):
            raise TypeError("request body must be an object")
        return cast("JsonDict", payload)

    cached_payload = getattr(request.state, "callback_request_json", None)
    if isinstance(cached_payload, dict):
        return cast("JsonDict", cached_payload)

    max_bytes = settings.callback_request_body_max_bytes
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_bytes = int(content_length)
        except ValueError:
            declared_bytes = 0
        if declared_bytes > max_bytes:
            raise HTTPException(status_code=413, detail=f"callback payload exceeds {max_bytes} bytes")

    chunks: list[bytes] = []
    actual_bytes = 0
    async for chunk in request.stream():
        actual_bytes += len(chunk)
        if actual_bytes > max_bytes:
            raise HTTPException(status_code=413, detail=f"callback payload exceeds {max_bytes} bytes")
        chunks.append(chunk)

    raw_body = b"".join(chunks)
    # 认证依赖已使用同一有界读取语义完成预读；缓存 raw body 供原有 HMAC verifier
    # 与后续 ingress 复用，避免第二次无界读取或耗尽 ASGI stream。
    request._body = raw_body  # pyright: ignore[reportPrivateUsage]
    payload = json.loads(raw_body)
    if not isinstance(payload, dict):
        raise TypeError("request body must be an object")
    request.state.callback_request_json = payload
    return cast("JsonDict", payload)


def _normalize_external_callback_payload(payload: JsonDict) -> JsonDict:
    # 延迟 import: 避免 callback_ingress_service 模块加载时反向 import
    # `src.app.wms_integration.services.callback_normalizer`, 触发与 callback_normalizer.py 顶部
    # `from src.app.callback.utils import ...` 的循环 import
    normalized_payload = _wms_callback_normalizer.wms_execution_callback_normalizer.normalize(payload)
    callback_type = cast("str", normalized_payload["callback_type"])
    _validate_external_callback_allow_list(payload, callback_type)
    return normalized_payload


def _callback_normalize_provider_code(callback_type: str, payload: JsonDict) -> str:
    if callback_type in {"result", "event"}:
        return "ECS"
    return resolve_first_str(payload, ("source_system",)) or callback_type.split("_", 1)[0] or "UNKNOWN"


def _callback_normalize_correlation_id(callback_type: str, payload: JsonDict, request_id: str | None) -> str:
    if callback_type == "event":
        device_code = resolve_first_str(payload, ("device_code",))
        event_type = resolve_first_str(payload, ("event_type",))
        if device_code and event_type:
            return f"event:{device_code}:{event_type}"
    return (
        resolve_first_str(payload, ("dispatch_key", "command_code", "exchange_request_code", "request_id"))
        or request_id
        or f"callback:{callback_type}"
    )


def _callback_normalize_source_event_id(callback_type: str, payload: JsonDict, request_id: str | None) -> str:
    source_event_id = resolve_first_str(payload, ("source_event_id", "event_id", "request_id")) or request_id
    if source_event_id:
        return source_event_id
    return f"callback:{callback_type}:{_callback_normalize_correlation_id(callback_type, payload, request_id)}"


def _callback_normalize_trace_id(
    callback_type: str,
    payload: JsonDict,
    normalized_payload: JsonDict,
    request_id: str | None,
) -> str:
    return (
        resolve_first_str(normalized_payload, ("trace_id",))
        or _resolve_callback_trace_id(payload)
        or request_id
        or _callback_normalize_source_event_id(callback_type, payload, request_id)
    )


def _emit_callback_normalize_observability(
    payload: JsonDict,
    normalized_payload: JsonDict,
    *,
    request_id: str | None,
) -> None:
    """发出 callback normalize 观测事件；观测失败不改变 callback ACK。"""

    from src.app.runtime.orchestration.observability import runtime_observability_registry

    callback_type = cast("str", normalized_payload["callback_type"])
    try:
        _ = runtime_observability_registry.emit(
            "callback.normalize",
            {
                "trace_id": _callback_normalize_trace_id(callback_type, payload, normalized_payload, request_id),
                "correlation_id": _callback_normalize_correlation_id(callback_type, payload, request_id),
                "provider_code": _callback_normalize_provider_code(callback_type, payload),
                "source_event_id": _callback_normalize_source_event_id(callback_type, payload, request_id),
            },
        )
    except Exception as exc:  # pragma: no cover - 防止观测链路反向影响 callback ACK
        logger.warning(f"Callback normalize 观测事件发射失败: callback_type={callback_type}, error={exc}")


def _validate_external_callback_allow_list(payload: JsonDict, callback_type: str) -> None:
    """校验 external callback callback_type 与 source_system 矩阵。"""

    if callback_type not in _EXTERNAL_CALLBACK_ALLOWED_TYPES:
        raise ValueError(f"callback_type is not allow-listed: {callback_type}")

    source_system = resolve_first_str(payload, ("source_system",))
    if not source_system:
        return

    allowed_sources = _EXTERNAL_CALLBACK_SOURCE_SYSTEMS_BY_CALLBACK_TYPE.get(callback_type)
    if allowed_sources is not None and source_system not in allowed_sources:
        raise ValueError("source_system must match callback_type provider")


def _validate_wms_rcs_execution_callback_payload(payload: JsonDict, callback_type: str) -> None:
    """校验 WMS/RCS 运行时执行回调第零阶段最小包络。"""

    # 延迟 import: 与 _normalize_external_callback_payload 同样的循环 import 规避
    _wms_callback_normalizer.wms_execution_callback_normalizer.validate(payload, callback_type)


def _build_contract_fail(
    message: str,
    *,
    reason_code: str | None = None,
    diagnostic: JsonDict | None = None,
) -> CallbackRejectedIngressResponse:
    return cast(
        "CallbackRejectedIngressResponse",
        response_builder.fail(
            code=ClientErrorCode.VALIDATION_ERROR,
            message=message,
            data=build_callback_rejected_response(reason_code=reason_code, diagnostic=diagnostic),
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
        data=build_callback_rejected_response(reason_code=reason_code, diagnostic=diagnostic),
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


async def _handle_runtime_inbox_conflict(
    db: AsyncSessionDep,
    request: Request,
    *,
    callback_type: str,
    request_id: str | None,
    request_body: JsonDict,
    message: str,
    response_time_ms: int,
    trace_id: str | None = None,
    event_id: str | None = None,
    causation_id: str | None = None,
) -> CallbackRejectedIngressResponse:
    try:
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
            response_status=409,
            response_time_ms=response_time_ms,
            success=False,
            record_audit=True,
            audit_title=_resolve_callback_audit_title(callback_type),
            error_message=message,
            ingress_outcome=_INGRESS_OUTCOME_REJECTED,
            failure_stage=_FAILURE_STAGE_ORCHESTRATION,
        )
    except Exception as log_error:
        logger.warning(f"RuntimeInbox 冲突日志写入失败，继续返回 409: {log_error}")
        try:
            await db.rollback()
        except Exception as rollback_error:
            logger.error(f"RuntimeInbox 冲突日志失败后的 rollback 失败: {rollback_error}")
    return _build_conflict_fail(message, reason_code="RUNTIME_INBOX_CONFLICT")


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
    sanitized_payload = _redact_callback_signatures(kwargs.get("payload"))
    try:
        _ = await workline_diagnostic_service.record_event(
            db,
            event=event,
            evidence={"payload": sanitized_payload},
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
    sanitized_request_body = cast("JsonDict", _redact_callback_signatures(request_body))
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
            request_body=sanitized_request_body,
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
            args=sanitized_request_body,
            cost_time=response_time_ms / 1000,
            success=True,
            response_status=response_status,
        )
        return
    await _record_callback_audit_log(
        db,
        request,
        title=audit_title,
        args=sanitized_request_body,
        cost_time=response_time_ms / 1000,
        success=False,
        response_status=response_status,
        message=error_message,
    )


async def _log_payload_too_large_best_effort(
    db: AsyncSessionDep,
    request: Request,
    *,
    callback_type: str,
    subject_code: str,
    evidence: JsonDict,
    request_id: str | None,
    trace_id: str | None,
    event_id: str | None,
    causation_id: str | None,
    response_time_ms: int,
    audit_title: str,
    error_message: str,
) -> None:
    """尽力记录 payload 超限证据；日志故障不得覆盖确定的 HTTP 413。"""

    bounded_evidence = {key: value[:200] if isinstance(value, str) else value for key, value in evidence.items()}
    try:
        await _log_callback_outcome(
            db,
            request,
            callback_type=callback_type,
            subject_code=subject_code[:50],
            request_body=bounded_evidence,
            request_id=request_id[:100] if request_id else None,
            trace_id=trace_id[:100] if trace_id else None,
            event_id=event_id[:200] if event_id else None,
            causation_id=causation_id[:200] if causation_id else None,
            response_status=413,
            response_time_ms=response_time_ms,
            success=False,
            record_audit=True,
            audit_title=audit_title,
            error_message=error_message,
            ingress_outcome=_INGRESS_OUTCOME_REJECTED,
            failure_stage=_FAILURE_STAGE_ORCHESTRATION,
        )
    except Exception as log_error:
        logger.warning(f"RuntimeInbox payload 超限日志写入失败，继续返回 413: {log_error}")
        try:
            await db.rollback()
        except Exception as rollback_error:
            logger.error(f"RuntimeInbox payload 超限日志失败后的 rollback 失败: {rollback_error}")


async def _log_correlation_unavailable_best_effort(
    db: AsyncSessionDep,
    request: Request,
    *,
    subject_code: str,
    evidence: JsonDict,
    request_id: str | None,
    trace_id: str | None,
    event_id: str | None,
    causation_id: str | None,
    response_time_ms: int,
) -> None:
    """尽力记录孤立关联的有限证据；日志故障不得覆盖确定的 HTTP 503。"""

    bounded_evidence = {key: value[:200] if isinstance(value, str) else value for key, value in evidence.items()}
    try:
        await _log_callback_outcome(
            db,
            request,
            callback_type="result",
            subject_code=subject_code[:50],
            request_body=bounded_evidence,
            request_id=request_id[:100] if request_id else None,
            trace_id=trace_id[:100] if trace_id else None,
            event_id=event_id[:200] if event_id else None,
            causation_id=causation_id[:200] if causation_id else None,
            response_status=503,
            response_time_ms=response_time_ms,
            success=False,
            record_audit=True,
            audit_title="设备回调结果",
            error_message="RuntimeInbox correlation unavailable",
            ingress_outcome=_INGRESS_OUTCOME_FAILED,
            failure_stage=_FAILURE_STAGE_ORCHESTRATION,
        )
    except Exception as log_error:
        logger.warning(f"RuntimeInbox 孤立关联日志写入失败，继续返回 503: {log_error}")
        try:
            await db.rollback()
        except Exception as rollback_error:
            logger.error(f"RuntimeInbox 孤立关联日志失败后的 rollback 失败: {rollback_error}")


async def _handle_validation_failure(
    db: AsyncSessionDep,
    request: Request,
    *,
    callback_type: str,
    request_id: str | None,
    request_body: JsonDict,
    message: str,
    response_status: int,
    response_time_ms: int = 0,
    failure_stage: str,
    trace_id: str | None = None,
    event_id: str | None = None,
    causation_id: str | None = None,
    reason_code: str | None = None,
    diagnostic: JsonDict | None = None,
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
        response_status=response_status,
        response_time_ms=response_time_ms,
        success=False,
        record_audit=True,
        audit_title=_resolve_callback_audit_title(callback_type),
        error_message=message,
        ingress_outcome=_INGRESS_OUTCOME_REJECTED,
        failure_stage=failure_stage,
    )
    return _build_contract_fail(message, reason_code=reason_code, diagnostic=diagnostic)


async def _handle_external_validation_failure(
    db: AsyncSessionDep,
    request: Request,
    *,
    request_id: str | None,
    callback_data: JsonDict,
    message: str,
    response_time_ms: int = 0,
    failure_stage: str,
    reason_code: str | None = None,
    diagnostic: JsonDict | None = None,
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
            response_status=400,
            response_time_ms=response_time_ms,
            failure_stage=failure_stage,
            reason_code=reason_code,
            diagnostic=diagnostic,
        ),
    )


@dataclass(frozen=True, slots=True)
class _ExternalCallbackAdmission:
    """已通过 external callback 入口校验的标准化上下文。"""

    callback_data: JsonDict
    callback_type: str
    trace_id: str | None


async def _admit_external_callback(
    request: Request,
    db: AsyncSessionDep,
    *,
    request_id: str | None,
    start_time: float,
) -> tuple[_ExternalCallbackAdmission | None, CallbackExternalIngressResponse | None]:
    """完成读取、标准化、provider 和 H4 边界校验。"""
    callback_data: JsonDict = {}
    try:
        callback_data = await _read_request_json(request)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"外部回调解析失败: {exc}")
        return None, await _handle_external_validation_failure(
            db,
            request,
            request_id=request_id,
            callback_data=callback_data,
            message=f"外部回调报文格式错误: {exc}",
            response_time_ms=_response_time_ms(start_time),
            failure_stage=_FAILURE_STAGE_REQUEST_PARSE,
        )

    # H4 边界: 业务追溯字段 (provider_code/source_event_id 等) 必须放入 data,
    # 防止外部系统通过顶层字段污染 RuntimeInbox 落库契约。
    try:
        _validate_top_level_fields(callback_data, _EXTERNAL_CALLBACK_TOP_LEVEL_FIELDS, "external")
    except ValueError as exc:
        unexpected_fields = sorted(
            str(field_name) for field_name in callback_data if field_name not in _EXTERNAL_CALLBACK_TOP_LEVEL_FIELDS
        )
        logger.error(f"外部回调 H4 边界违规: {exc}")
        return None, await _handle_external_validation_failure(
            db,
            request,
            request_id=request_id,
            callback_data=callback_data,
            message=str(exc),
            response_time_ms=_response_time_ms(start_time),
            failure_stage=_FAILURE_STAGE_ENVELOPE_VALIDATE,
            reason_code=_CALLBACK_TOP_LEVEL_FIELD_NOT_ALLOWED_REASON_CODE,
            diagnostic={"unexpected_fields": unexpected_fields},
        )

    try:
        normalized_payload = _normalize_external_callback_payload(callback_data)
        callback_type = cast("str", normalized_payload["callback_type"])
        external_trace_id = cast("str | None", normalized_payload["trace_id"])
        callback_data = cast("JsonDict", normalized_payload["payload"])
    except WmsEffectStatusHintAdmissionError as exc:
        # 禁止的 status hint 必须在 RuntimeInbox 与业务 session 持久化之前拒绝；
        # 当前边界不复用会提交该 session 的通用拒绝日志路径。
        logger.error(f"WMS EFFECT status hint admission 失败: {exc}")
        return None, cast(
            "CallbackExternalIngressResponse",
            _build_contract_fail(f"外部回调最小包络校验失败: {exc}"),
        )
    except ValueError as exc:
        logger.error(f"外部回调最小包络校验失败: {exc}")
        return None, await _handle_external_validation_failure(
            db,
            request,
            request_id=request_id,
            callback_data=callback_data,
            message=f"外部回调最小包络校验失败: {exc}",
            response_time_ms=_response_time_ms(start_time),
            failure_stage=_FAILURE_STAGE_ENVELOPE_VALIDATE,
        )

    try:
        callback_provider_profile_admission_service.admit(
            provider_code=_callback_normalize_provider_code(callback_type, callback_data),
            callback_type=callback_type,
            direction=_external_callback_normalizer_direction(callback_type),
        )
        _emit_callback_normalize_observability(callback_data, normalized_payload, request_id=request_id)
    except PermissionError as exc:
        logger.error(f"外部回调 provider profile admission 失败: {exc}")
        return None, await _handle_external_validation_failure(
            db,
            request,
            request_id=request_id,
            callback_data=callback_data,
            message=f"外部回调契约校验失败: {exc}",
            response_time_ms=_response_time_ms(start_time),
            failure_stage=_FAILURE_STAGE_CONTRACT_VALIDATE,
        )

    # H4 子层守卫: 外部回调 data 同样禁止 plc_address / coordinate 等
    # 直连设备控制字段, 防止外部系统间接控制设备。
    external_forbidden_hits = _collect_forbidden_param_keys(callback_data.get("data"))
    if external_forbidden_hits:
        forbidden_paths = [path for path, _ in external_forbidden_hits]
        message = f"外部回调 data 包含禁止字段: {', '.join(forbidden_paths)}"
        logger.error(f"{message} (callback_type={callback_type})")
        return None, await _handle_external_validation_failure(
            db,
            request,
            request_id=request_id,
            callback_data=callback_data,
            message=message,
            response_time_ms=_response_time_ms(start_time),
            failure_stage=_FAILURE_STAGE_CONTRACT_VALIDATE,
            reason_code=_CALLBACK_DATA_FORBIDDEN_FIELD_REASON_CODE,
            diagnostic={"forbidden_paths": forbidden_paths},
        )

    return (
        _ExternalCallbackAdmission(
            callback_data=callback_data,
            callback_type=callback_type,
            trace_id=external_trace_id,
        ),
        None,
    )


async def handle_callback_external(
    request: Request,
    db: AsyncSessionDep,
    *,
    request_id: str | None,
    start_time: float,
    enqueue_processing: Callable[[], None],
) -> CallbackExternalIngressResponse:
    admission, rejection = await _admit_external_callback(
        request,
        db,
        request_id=request_id,
        start_time=start_time,
    )
    if rejection is not None:
        return rejection
    admitted = cast("_ExternalCallbackAdmission", admission)
    callback_data = admitted.callback_data
    callback_type = admitted.callback_type
    external_trace_id = admitted.trace_id

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

    except RuntimeInboxPayloadTooLarge as exc:
        logger.error(f"外部回调 RuntimeInbox payload 超限: {exc}")
        oversized_evidence: JsonDict = {
            "callback_type": callback_type,
            "source_system": callback_data.get("source_system"),
            "source_event_id": callback_data.get("source_event_id"),
            "trace_id": external_trace_id,
            "actual_bytes": exc.actual_bytes,
            "max_bytes": exc.max_bytes,
        }
        await _log_payload_too_large_best_effort(
            db,
            request,
            callback_type="external",
            subject_code=callback_type,
            evidence=oversized_evidence,
            request_id=request_id,
            trace_id=external_trace_id,
            event_id=_resolve_callback_event_id(callback_data),
            causation_id=_resolve_callback_causation_id(callback_data),
            response_time_ms=_response_time_ms(start_time),
            audit_title="外部系统回调",
            error_message=str(exc),
        )
        raise HTTPException(status_code=413, detail=str(exc)) from exc
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
    except (RuntimeInboxConflict, IdempotencyConflict) as exc:
        logger.error(f"外部回调 RuntimeInbox 冲突: {exc}")
        return cast(
            "CallbackExternalIngressResponse",
            await _handle_runtime_inbox_conflict(
                db,
                request,
                callback_type="external",
                request_id=request_id,
                request_body=callback_data,
                message=str(exc),
                response_time_ms=_response_time_ms(start_time),
                trace_id=external_trace_id,
                event_id=_resolve_callback_event_id(callback_data),
                causation_id=_resolve_callback_causation_id(callback_data),
            ),
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
