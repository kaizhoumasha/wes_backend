"""Callback 入站应用服务."""

import time
from collections.abc import Callable
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Literal, cast

from fastapi import Request
from pydantic import ValidationError

from src.app.callback.contracts import (
    ErrorCode,
    TraceContext,
    build_diagnostic_card,
    build_diagnostic_context,
    build_diagnostic_event,
    canonicalize_event_type,
    is_platform_control_event,
    is_production_event,
)
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
from src.app.callback.utils import JsonDict, resolve_first_str
from src.app.contracts.external_contract_profile import ExternalContractProfile
from src.app.device.models import parse_device_capabilities
from src.app.device.models.command import (
    _FORBIDDEN_PARAM_KEYS,
    CommandCallbackResult,
)
from src.app.device.services import device_command_service, device_context_service, device_service
from src.app.runtime.capabilities.material_flow.start_admission_service import start_admission_service
from src.app.runtime.orchestration.consumers.runtime_inbox_service import RuntimeInboxConflict
from src.app.runtime.orchestration.services.idempotency_guard import IdempotencyConflict
from src.app.runtime.orchestration.services.inbox import inbox_service
from src.app.runtime.orchestration.services.workline_runtime_status_projection_service import (
    WorkLineRuntimeStatusSnapshot,
    workline_runtime_status_projection_service,
)
from src.app.sys.models.audit_log import OperaStatus
from src.app.sys.services import audit_log_service
from src.app.wms_integration.services import callback_normalizer as _wms_callback_normalizer
from src.app.workline.services.diagnostic_service import workline_diagnostic_service
from src.core.client_ip import resolve_client_ip
from src.core.logger import logger
from src.core.response import response_builder
from src.core.response.response_code import ClientErrorCode, ResourceErrorCode, ResponseCode, ServerErrorCode
from src.database.dependencies import AsyncSessionDep
from src.utils.value_normalization import resolve_entity_id

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
        # 兼容 WMS/RCS 协议 (WMS_RCS_RACK_SOURCE_ENVELOPE_FIELDS) 顶层元数据
        "source_system",
        "source_event_id",
        "source_version",
        "occurred_at",
        "request_id",
        "timestamp",
        "signature",
        # WMS/RCS 协议业务追溯与状态字段 (合法顶层, 不视为业务污染):
        # - dispatch_key / status: WMS_RCS_RACK_STATUS_REQUIRED_CALLBACK_TYPES 协议要求
        # - exchange_* / rack_release_id / wms_rcs_task_id:
        #   WMS_RCS_FULL_BOX_EXCHANGE_REQUIRED_FIELDS 协议要求
        # - active_bin_rack: WMS_RACK_ARRIVED 协议要求 (料架到位 payload)
        "dispatch_key",
        "status",
        "exchange_request_code",
        "rack_release_id",
        "wms_rcs_task_id",
        "exchange_status",
        "active_bin_rack",
        # AGV/外部执行协议 (AGV_TASK_RESULT) 顶层追溯字段:
        # - command_code / result / finish_time / device_code:
        #   与 _RESULT_CALLBACK_TOP_LEVEL_FIELDS 一致的执行回执结构
        "command_code",
        "result",
        "finish_time",
        "device_code",
        # WMS/RCS 任务失败 payload (RCS_RACK_TASK_RESULT 等) 顶层字段:
        # - task_status: WMS_RCS_EXECUTION_STATUS_ALIASES 协议别名
        # - reason_code / reason_message: 任务失败结构化原因 (用于诊断与回放)
        "task_status",
        "reason_code",
        "reason_message",
        # WMS 货架操作协议 (rack_operation) 顶层业务字段, 由 wms_mock 真实
        # 集成测试覆盖, H4 边界设计时未枚举全, 现补齐。这些字段是 WMS
        # 协议的合法顶层业务元数据, 不是 H4 关注的安全注入面; H4 子层守卫
        # (_FORBIDDEN_PARAM_KEYS 递归扫描 callback.data) 仍阻断 plc_address /
        # coordinate 等设备控制字段, 顶层白名单扩展不削弱 H4 安全语义。
        # - actions / sequence_no / source / station / target: 操作步骤与角色
        # - bin_mounts / material: 料箱与物料的子结构 (顶层是 schema 入口)
        # - operation_key / operation_type: WMS 操作标识 (派发幂等键的来源)
        # - position_code / source_position_code / target_position_code /
        #   target_position_role: 工位与角色
        # - rack_code / rack_kind: 货架标识与类型
        # - task_type: WMS 任务类型 (与 device_commands.task_type 区分)
        # - workline_code: 工作线标识
        "actions",
        "bin_mounts",
        "material",
        "operation_key",
        "operation_type",
        "position_code",
        "rack_code",
        "rack_kind",
        "sequence_no",
        "source",
        "source_position_code",
        "station",
        "target",
        "target_position_code",
        "target_position_role",
        "task_type",
        "workline_code",
        # WMS 失败 payload 顶层错误字段 (与 reason_code/reason_message 配对):
        # - error_code / error_message: build_rack_operation_failure_payload
        #   返回的诊断结构, 真实 WMS mock 集成测试必需。
        "error_code",
        "error_message",
    }
)
_EXTERNAL_CALLBACK_WMS_RCS_DOCUMENTED_SUFFIXES = (
    "GRN_RECEIVED",
    "PALLET_ARRIVED",
    "RACK_ARRIVED",
    "TRANSPORT_COMPLETED",
    "EXCHANGE_COMPLETED",
    "INVENTORY_UPDATED",
    "TASK_CHANGE",
    "REJECTED",
    "FAILED",
)
_EXTERNAL_CALLBACK_WMS_RCS_DOCUMENTED_TYPES = frozenset(
    f"{provider}_{suffix}" for provider in ("WMS", "RCS") for suffix in _EXTERNAL_CALLBACK_WMS_RCS_DOCUMENTED_SUFFIXES
)
_EXTERNAL_CALLBACK_WMS_RCS_RUNTIME_TYPES = frozenset(
    {
        "WMS_ROUGH_SORTER_INBOUND",
        "WMS_RACK_TASK_RESULT",
        "RCS_RACK_TASK_RESULT",
        "WMS_RACK_TASK_PROGRESS",
        "RCS_RACK_TASK_PROGRESS",
        "WMS_RACK_ARRIVED",
        "RCS_RACK_ARRIVED",
        "WMS_RACK_EXCHANGE_PROGRESS",
        "RCS_RACK_EXCHANGE_PROGRESS",
        "WMS_RACK_EXCHANGE_FAILED",
        "RCS_RACK_EXCHANGE_FAILED",
        "WMS_FULL_BOX_EXCHANGE_RESULT",
        "RCS_FULL_BOX_EXCHANGE_RESULT",
    }
)
_EXTERNAL_CALLBACK_ECS_DEVICE_ALLOWED_TYPES = frozenset(
    {
        "DEVICE_RESULT",
        "DEVICE_EVENT",
        "DEVICE_STATUS_CHANGED",
        "MATERIAL_ARRIVED",
        "SCAN_COMPLETED",
        "ESTOP_PRESSED",
        "DEVICE_ERROR",
        "DEVICE_ONLINE",
        "DEVICE_OFFLINE",
    }
)
_EXTERNAL_CALLBACK_PROVIDER_SPECIFIC_ALLOWED_TYPES = frozenset(
    {
        "AGV_TASK_RESULT",
        "CTU_BIN_MOVE_PROGRESS",
        "CTU_BIN_MOVE_COMPLETED",
        "CTU_BIN_MOVE_FAILED",
    }
)
_EXTERNAL_CALLBACK_ALLOWED_TYPES = (
    _EXTERNAL_CALLBACK_WMS_RCS_DOCUMENTED_TYPES
    | _EXTERNAL_CALLBACK_WMS_RCS_RUNTIME_TYPES
    | _EXTERNAL_CALLBACK_ECS_DEVICE_ALLOWED_TYPES
    | _EXTERNAL_CALLBACK_PROVIDER_SPECIFIC_ALLOWED_TYPES
)
_EXTERNAL_CALLBACK_SOURCE_SYSTEMS_BY_CALLBACK_TYPE = {
    **{callback_type: frozenset({"ECS", "DEVICE"}) for callback_type in _EXTERNAL_CALLBACK_ECS_DEVICE_ALLOWED_TYPES},
    "AGV_TASK_RESULT": frozenset({"AGV"}),
    "CTU_BIN_MOVE_PROGRESS": frozenset({"CTU"}),
    "CTU_BIN_MOVE_COMPLETED": frozenset({"CTU"}),
    "CTU_BIN_MOVE_FAILED": frozenset({"CTU"}),
}
_EXTERNAL_CALLBACK_RESULT_TYPES = frozenset(
    {
        "AGV_TASK_RESULT",
        "DEVICE_RESULT",
        "WMS_RACK_TASK_RESULT",
        "RCS_RACK_TASK_RESULT",
        "WMS_FULL_BOX_EXCHANGE_RESULT",
        "RCS_FULL_BOX_EXCHANGE_RESULT",
    }
)
# H4 拒绝的机器可读原因码: client 可通过 reason_code 字段区分
# 顶层字段违规 vs 其他 schema 校验失败 (用于埋点和告警)。
_CALLBACK_TOP_LEVEL_FIELD_NOT_ALLOWED_REASON_CODE = "CALLBACK_TOP_LEVEL_FIELD_NOT_ALLOWED"
# H4 子层守卫: callback.data 触发 _FORBIDDEN_PARAM_KEYS 时的原因码。
_CALLBACK_DATA_FORBIDDEN_FIELD_REASON_CODE = "CALLBACK_DATA_FORBIDDEN_FIELD"

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


class CallbackProviderProfileAdmissionService:
    """Callback 入站 provider profile admission。

    Callback admission gate: callback API 热路径必须拒绝 provider profile 未声明的
    event/result normalizer，不能只依赖 callback_type allow-list。
    """

    def __init__(self, profiles_by_provider: dict[str, ExternalContractProfile] | None = None) -> None:
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


def _build_default_callback_provider_profiles() -> dict[str, ExternalContractProfile]:
    wms_result_types = {
        callback_type for callback_type in _EXTERNAL_CALLBACK_RESULT_TYPES if callback_type.startswith("WMS_")
    }
    rcs_result_types = {
        callback_type for callback_type in _EXTERNAL_CALLBACK_RESULT_TYPES if callback_type.startswith("RCS_")
    }
    return {
        "ECS": _build_callback_provider_profile(
            "ECS",
            event_types=(_EXTERNAL_CALLBACK_ECS_DEVICE_ALLOWED_TYPES - {"DEVICE_RESULT"})
            | {"SCAN_FINISH", "WORKLINE_START_REQUESTED"},
            result_types={"DEVICE_RESULT"},
        ),
        "DEVICE": _build_callback_provider_profile(
            "DEVICE",
            event_types=(_EXTERNAL_CALLBACK_ECS_DEVICE_ALLOWED_TYPES - {"DEVICE_RESULT"})
            | {"SCAN_FINISH", "WORKLINE_START_REQUESTED"},
            result_types={"DEVICE_RESULT"},
        ),
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
        "RCS": _build_callback_provider_profile(
            "RCS",
            event_types=(
                {
                    callback_type
                    for callback_type in _EXTERNAL_CALLBACK_ALLOWED_TYPES
                    if callback_type.startswith("RCS_")
                }
                - rcs_result_types
            ),
            result_types=rcs_result_types,
        ),
        "AGV": _build_callback_provider_profile(
            "AGV",
            event_types=set(),
            result_types={"AGV_TASK_RESULT"},
        ),
        "CTU": _build_callback_provider_profile(
            "CTU",
            event_types={
                callback_type for callback_type in _EXTERNAL_CALLBACK_ALLOWED_TYPES if callback_type.startswith("CTU_")
            },
            result_types=set(),
        ),
    }


def _build_callback_provider_profile(
    provider_code: str,
    *,
    event_types: set[str] | frozenset[str],
    result_types: set[str] | frozenset[str],
) -> ExternalContractProfile:
    fixture_provider = provider_code.lower() if provider_code in {"ECS", "WMS"} else "wms"
    return ExternalContractProfile(
        provider_code=provider_code,
        contract_version="default",
        environment="sandbox",
        runtime_capabilities_query=["WmsMasterDataPort.get_material"],
        inbound_normalizers_event=sorted(event_types),
        inbound_normalizers_result=sorted(result_types),
        timeout_retry_query_timeout_seconds=5,
        timeout_retry_retry_backoff_seconds=[1],
        fixture_set_path=f"tests/fixtures/external_contracts/{fixture_provider}/default",
        fixture_set_required_cases=["success", "timeout", "duplicate", "missing_event_id"],
    )


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


def _collect_forbidden_param_keys(
    data: Any,
    *,
    forbidden: frozenset[str] = frozenset(_FORBIDDEN_PARAM_KEYS),
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


def _is_workline_accepting_production_events(runtime_snapshot: WorkLineRuntimeStatusSnapshot) -> bool:
    runtime_status = _optional_enum_str(runtime_snapshot.runtime_status)
    if runtime_status is None:
        return False
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
        response_status=400,
        response_time_ms=response_time_ms,
        success=False,
        record_audit=True,
        audit_title=_resolve_callback_audit_title(callback_type),
        error_message=message,
        ingress_outcome=_INGRESS_OUTCOME_REJECTED,
        failure_stage=failure_stage,
    )
    return _build_contract_fail(message, reason_code=reason_code, diagnostic=diagnostic)


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
    reason_code: str | None = None,
    diagnostic: JsonDict | None = None,
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
            reason_code=reason_code,
            diagnostic=diagnostic,
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
    reason_code: str | None = None,
    diagnostic: JsonDict | None = None,
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
            reason_code=reason_code,
            diagnostic=diagnostic,
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
    start_time: float,
) -> CallbackEventIngressDecision:
    admission = await start_admission_service.admit_start_for_device(
        db,
        device_code=device_code,
        request_id=request_id,
        trace_id=_resolve_callback_trace_id(event_data),
    )
    response_time_ms = _response_time_ms(start_time)
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
                    data=build_callback_event_accepted_response(
                        status="accepted",
                        device_code=device_code,
                        request_id=request_id,
                        trace_id=_resolve_callback_trace_id(event_data),
                        event_id=_resolve_callback_event_id(event_data),
                        causation_id=_resolve_callback_causation_id(event_data),
                        diagnostic=admission.diagnostic,
                    ),
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
            response_time_ms=response_time_ms,
            failure_stage=failure_stage,
            reason_code=reason_code,
            diagnostic=diagnostic,
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
    response_status = _resolve_ctx_error_response_status(error)
    response_code = _resolve_ctx_error_response_code(response_status)
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
        response_status=response_status,
        response_time_ms=response_time_ms,
        success=False,
        record_audit=True,
        audit_title=audit_title,
        error_message=message,
        ingress_outcome=_INGRESS_OUTCOME_REJECTED,
        failure_stage=_FAILURE_STAGE_DEVICE_CONTEXT_RESOLVE,
    )
    return cast(
        "CallbackResultIngressResponse | CallbackEventIngressResponse",
        response_builder.fail(
            code=response_code,
            message=message,
            data=build_callback_rejected_response(reason_code=response_code.code),
        ),
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
        # 区分 H4 顶层字段违规 (业务追溯字段混入顶层) 与其它 schema 校验失败,
        # client 可通过 reason_code 做埋点和告警。
        unexpected_top_level = sorted(
            str(field_name) for field_name in callback_data if field_name not in _RESULT_CALLBACK_TOP_LEVEL_FIELDS
        )
        is_h4_top_level = bool(unexpected_top_level)
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
            reason_code=_CALLBACK_TOP_LEVEL_FIELD_NOT_ALLOWED_REASON_CODE if is_h4_top_level else None,
            diagnostic={"unexpected_fields": unexpected_top_level} if is_h4_top_level else None,
        )

    try:
        callback_provider_profile_admission_service.admit(
            provider_code="ECS",
            callback_type="DEVICE_RESULT",
            direction="result",
        )
    except PermissionError as exc:
        logger.error(f"指令结果回调 provider profile admission 失败: {exc}")
        return await _handle_result_validation_failure(
            db,
            request,
            request_id=request_id,
            callback_data=callback_data,
            message=f"结果回调契约校验失败: {exc}",
            response_time_ms=_response_time_ms(start_time),
            failure_stage=_FAILURE_STAGE_CONTRACT_VALIDATE,
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
    _emit_callback_normalize_observability(
        callback_data,
        {"callback_type": "result", "trace_id": resolved_trace_id},
        request_id=request_id,
    )

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
        # H4 子层守卫: callback.data 禁止包含 plc_address / coordinate 等
        # 直连设备控制字段, 与 CommandBase.params 入站校验保持一致。
        forbidden_hits = _collect_forbidden_param_keys(callback.data)
        if forbidden_hits:
            forbidden_paths = [path for path, _ in forbidden_hits]
            message = f"指令结果回调 data 包含禁止字段: {', '.join(forbidden_paths)}"
            logger.error(f"{message} (command_code={callback.command_code})")
            await _record_callback_diagnostic(
                db,
                error_code=ErrorCode.CALLBACK_SCHEMA_INVALID,
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
                failure_stage=_FAILURE_STAGE_CONTRACT_VALIDATE,
                reason_code=_CALLBACK_DATA_FORBIDDEN_FIELD_REASON_CODE,
                diagnostic={"forbidden_paths": forbidden_paths},
                trace_id=resolved_trace_id,
                event_id=_resolve_callback_event_id(callback_data),
                causation_id=_resolve_callback_causation_id(callback_data),
            )
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
    except (RuntimeInboxConflict, IdempotencyConflict) as exc:
        logger.error(f"指令结果回调 RuntimeInbox 冲突: {exc}")
        return cast(
            "CallbackResultIngressResponse",
            await _handle_runtime_inbox_conflict(
                db,
                request,
                callback_type="result",
                request_id=request_id,
                request_body=callback_data,
                message=str(exc),
                response_time_ms=_response_time_ms(start_time),
                trace_id=resolved_trace_id,
                event_id=_resolve_callback_event_id(callback_data),
                causation_id=_resolve_callback_causation_id(callback_data),
            ),
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
        # 区分 H4 顶层字段违规 (业务追溯字段混入顶层) 与其它 schema 校验失败,
        # client 可通过 reason_code 做埋点和告警。
        unexpected_top_level = sorted(
            str(field_name) for field_name in event_data if field_name not in _EVENT_CALLBACK_TOP_LEVEL_FIELDS
        )
        is_h4_top_level = bool(unexpected_top_level) and isinstance(exc, ValueError)
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
                reason_code=_CALLBACK_TOP_LEVEL_FIELD_NOT_ALLOWED_REASON_CODE if is_h4_top_level else None,
                diagnostic={"unexpected_fields": unexpected_top_level} if is_h4_top_level else None,
            )
        )

    device_code = normalized_event_request.device_code
    # H4 子层守卫: event.data 同样禁止 plc_address / coordinate 等
    # 直连设备控制字段。
    event_forbidden_hits = _collect_forbidden_param_keys(normalized_event_request.data)
    if event_forbidden_hits:
        forbidden_paths = [path for path, _ in event_forbidden_hits]
        message = f"设备事件上报 data 包含禁止字段: {', '.join(forbidden_paths)}"
        logger.error(f"{message} (device_code={device_code})")
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
                failure_stage=_FAILURE_STAGE_CONTRACT_VALIDATE,
                reason_code=_CALLBACK_DATA_FORBIDDEN_FIELD_REASON_CODE,
                diagnostic={"forbidden_paths": forbidden_paths},
            )
        )
    logger.info(f"收到设备事件上报: {device_code} (request_id={request_id})")

    # 设备/工作线上下文和能力校验属于“是否可路由入站”的入口职责。
    ctx_result, ctx_error = await device_context_service.resolve(db, device_code)
    if ctx_error:
        response_status = _resolve_ctx_error_response_status(ctx_error)
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
            ),
            http_status=response_status,
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
        try:
            callback_provider_profile_admission_service.admit(
                provider_code="ECS",
                callback_type=normalized_event_request.event_type,
                direction="event",
            )
        except PermissionError as exc:
            logger.error(f"设备事件上报 provider profile admission 失败: {exc}")
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

        if normalized_event_request.event_type == "WORKLINE_START_REQUESTED":
            return await _handle_event_start_admission(
                db,
                request,
                request_id=request_id,
                event_data=event_data,
                device_code=device_code,
                start_time=start_time,
            )

        if is_platform_control_event(canonical_event_type):
            message = f"事件上报契约校验失败: {canonical_event_type} 是平台保留控制事件，不能作为事件映射目标"
            logger.error(message)
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
                    failure_stage=_FAILURE_STAGE_CONTRACT_VALIDATE,
                )
            )

        if is_production_event(canonical_event_type):
            workline_id = getattr(workline, "id", None)
            if not isinstance(workline_id, int):
                workline_id = getattr(ctx_result, "work_line_id", None)
            runtime_snapshot = (
                await workline_runtime_status_projection_service.runtime_status_snapshot(db, workline_id=workline_id)
                if isinstance(workline_id, int)
                else WorkLineRuntimeStatusSnapshot(
                    runtime_status=None,
                    source="runtime/orchestration:missing-workline-id",
                    stopped_at=None,
                    stopped_reason=None,
                    resumed_at=None,
                    active_safety_incident_id=None,
                )
            )
            runtime_status = _optional_enum_str(runtime_snapshot.runtime_status)
            if not _is_workline_accepting_production_events(runtime_snapshot):
                return await _handle_event_workline_guard_rejection(
                    db,
                    request,
                    request_id=request_id,
                    event_data=event_data,
                    device_code=device_code,
                    runtime_status=runtime_status or "UNKNOWN",
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
        _emit_callback_normalize_observability(
            event_data,
            {"callback_type": "event", "trace_id": outcome.trace_id},
            request_id=request_id,
        )

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
        if isinstance(exc, (RuntimeInboxConflict, IdempotencyConflict)):
            logger.error(f"设备事件上报 RuntimeInbox 冲突: {exc}")
            return CallbackEventIngressDecision(
                body=cast(
                    "CallbackEventIngressResponse",
                    await _handle_runtime_inbox_conflict(
                        db,
                        request,
                        callback_type="event",
                        request_id=request_id,
                        request_body=event_data,
                        message=str(exc),
                        response_time_ms=_response_time_ms(start_time),
                        trace_id=_resolve_callback_trace_id(event_data),
                        event_id=_resolve_callback_event_id(event_data),
                        causation_id=_resolve_callback_causation_id(event_data),
                    ),
                ),
                http_status=409,
            )
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

    # H4 边界: 业务追溯字段 (provider_code/source_event_id 等) 必须放入 data,
    # 防止外部系统通过顶层字段污染 RuntimeInbox 落库契约。
    try:
        _validate_top_level_fields(callback_data, _EXTERNAL_CALLBACK_TOP_LEVEL_FIELDS, "external")
    except ValueError as exc:
        unexpected_fields = sorted(
            str(field_name) for field_name in callback_data if field_name not in _EXTERNAL_CALLBACK_TOP_LEVEL_FIELDS
        )
        logger.error(f"外部回调 H4 边界违规: {exc}")
        return await _handle_external_validation_failure(
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

    try:
        callback_provider_profile_admission_service.admit(
            provider_code=_callback_normalize_provider_code(callback_type, callback_data),
            callback_type=callback_type,
            direction=_external_callback_normalizer_direction(callback_type),
        )
        _emit_callback_normalize_observability(callback_data, normalized_payload, request_id=request_id)
    except PermissionError as exc:
        logger.error(f"外部回调 provider profile admission 失败: {exc}")
        return await _handle_external_validation_failure(
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
        return await _handle_external_validation_failure(
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
