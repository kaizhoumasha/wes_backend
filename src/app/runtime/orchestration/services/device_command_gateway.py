"""DeviceCommandGateway — 设备命令下发桥接。

DeviceCommandGateway 从 src.app.workline.services 迁入
runtime/orchestration/services/,原位置成为跨域引用违例。

- reserve_sandbox_command / dispatch 是 device 与 workline 域间设备命令流转的
  唯一桥接
- workline 域调用方改为 src.app.runtime.orchestration.services.device_command_gateway
- 内部对 src.app.runtime.orchestration.services.reconciliation
  .runtime_reconciliation_service_impl.workline_runtime_reconciliation_service
  的引用已修正(原 workline shim 已物理删除)
"""

from collections.abc import Mapping
from datetime import timedelta
from typing import Any, NoReturn
from urllib.parse import quote

from loguru import logger

from src.app.device.models.capability import parse_device_capabilities
from src.app.device.models.device import DeviceStatus
from src.app.runtime.orchestration.enums import FailureDomain
from src.app.runtime.orchestration.services.device_dispatch_policy import (
    DeviceDispatchDecision,
    DeviceDispatchDecisionKind,
    DeviceDispatchPolicy,
    DeviceDispatchRequest,
    DeviceRuntimeSnapshot,
    DeviceRuntimeStatus,
    device_dispatch_policy,
)
from src.utils.timezone import timezone
from src.utils.value_normalization import coerce_optional_int, coerce_string_value, enum_value, resolve_entity_id


def _payload_dict(value: Any) -> dict[str, Any]:
    """内联 payload_dict(原 src.app.workline.utils.payload_dict)。"""
    return value if isinstance(value, dict) else {}


_DEFAULT_DEVICE_COMMAND_CALLBACK_PATH = "/api/v1/device/command"
_DEFAULT_DEVICE_STATUS_PATH = "/api/v1/device/status"
_DEFAULT_DEVICE_STATUS_TIMEOUT_SECONDS = 2.0
_DEFAULT_DEVICE_COMMAND_ACK_TIMEOUT_SECONDS = 10.0


class _DeviceCommandGovernanceError(RuntimeError):
    """设备治理字段在运行时拒绝命令创建/派发时抛出的显式异常。"""

    def __init__(
        self,
        *,
        domain: str,
        code: str,
        message: str,
        device_id: int | None = None,
        device_code: str | None = None,
        detail: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.domain = domain
        self.code = code
        self.message = message
        self.device_id = device_id
        self.device_code = device_code
        self.detail = detail or {}


# ============================================
# 辅助函数
# ============================================


_DEVICE_COMMAND_SENSITIVE_KEY_PARTS = {"password", "token", "secret", "key", "auth", "authorization", "credential"}
_REDACTED_LOG_VALUE = "***"


def _redact_device_command_payload(value: Any) -> Any:
    """脱敏设备指令日志中的凭据类字段，保留业务参数用于供应商核对。"""
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            key_lower = key_text.lower()
            if any(part in key_lower for part in _DEVICE_COMMAND_SENSITIVE_KEY_PARTS):
                redacted[key_text] = _REDACTED_LOG_VALUE
            else:
                redacted[key_text] = _redact_device_command_payload(item)
        return redacted
    if isinstance(value, list):
        return [_redact_device_command_payload(item) for item in value]
    return value


def _raise_device_command_governance_error(
    *,
    domain: str,
    code: str,
    message: str,
    device_id: int | None = None,
    device_code: str | None = None,
    detail: dict[str, Any] | None = None,
    cause: Exception | None = None,
) -> NoReturn:
    error = _DeviceCommandGovernanceError(
        domain=domain,
        code=code,
        message=message,
        device_id=device_id,
        device_code=device_code,
        detail=detail,
    )
    if cause is not None:
        raise error from cause
    raise error


def _build_device_command_log_envelope(
    outbox: Any,
    payload: dict[str, Any],
    *,
    endpoint: str | None = None,
) -> dict[str, Any]:
    """构造设备指令日志包络，便于硬件供应商按同一份 JSON 核对。"""

    envelope: dict[str, Any] = {
        "outbox_id": resolve_entity_id(outbox),
        "session_id": coerce_optional_int(getattr(outbox, "session_id", None)),
        "dispatch_key": coerce_string_value(getattr(outbox, "dispatch_key", None)),
        "target_type": enum_value(getattr(outbox, "target_type", None)),
        "target_code": coerce_string_value(getattr(outbox, "target_code", None)),
        "payload": _redact_device_command_payload(payload),
    }
    if endpoint:
        envelope["endpoint"] = endpoint
    return envelope


def _device_command_ack_age_ms(command: Any, *, ack_received_at: Any) -> int:
    sent_at = getattr(command, "sent_at", None)
    if sent_at is None:
        return 0
    return max(0, int((ack_received_at - sent_at).total_seconds() * 1000))


def _device_command_provider_code(device: Any, payload: dict[str, Any]) -> str:
    return (
        coerce_string_value(getattr(device, "vendor_type", None))
        or coerce_string_value(getattr(device, "provider_code", None))
        or coerce_string_value(payload.get("provider_code"))
        or "ECS"
    )


def _emit_device_command_ack_observability(
    *,
    command: Any,
    device: Any,
    outbox: Any,
    payload: dict[str, Any],
    ack_received_at: Any,
) -> None:
    """发出 DeviceCommand ACK age 观测事件；观测失败不改变 ACK 业务状态。"""

    from src.app.runtime.orchestration.observability import runtime_observability_registry

    command_code = coerce_string_value(getattr(command, "command_code", None)) or coerce_string_value(
        payload.get("command_code")
    )
    try:
        _ = runtime_observability_registry.emit(
            "device_command.ack",
            {
                "trace_id": coerce_string_value(getattr(command, "trace_id", None))
                or coerce_string_value(getattr(outbox, "trace_id", None)),
                "correlation_id": coerce_string_value(getattr(command, "correlation_id", None))
                or coerce_string_value(getattr(outbox, "correlation_id", None)),
                "command_code": command_code,
                "provider_code": _device_command_provider_code(device, payload),
                "ack_age_ms": _device_command_ack_age_ms(command, ack_received_at=ack_received_at),
            },
        )
    except Exception as exc:  # pragma: no cover - 防止观测链路反向影响设备派发
        logger.warning(f"设备指令 ACK 观测事件发射失败: command_code={command_code or 'UNKNOWN'}, error={exc}")


def _resolve_command_type_for_governance(payload: dict[str, Any]) -> str | None:
    """为设备治理校验提取稳定 command_type。"""

    command_type = coerce_string_value(payload.get("task_type")) or coerce_string_value(payload.get("command_type"))
    return command_type or None


def _resolve_device_command_path(device: Any) -> str:
    """优先使用 device.callback_path，未配置时回退默认命令路径。"""

    callback_path = coerce_string_value(getattr(device, "callback_path", None)) or _DEFAULT_DEVICE_COMMAND_CALLBACK_PATH
    if not callback_path.startswith("/"):
        callback_path = f"/{callback_path}"
    return callback_path


def _resolve_device_status_path(device: Any) -> str:
    """优先使用设备 capabilities 中的配置状态路径，未配置时回退默认状态路径。"""

    capabilities = getattr(device, "capabilities_json", None)
    if isinstance(capabilities, dict):
        for key in ("status_path", "device_status_path"):
            value = capabilities.get(key)
            if isinstance(value, str) and value.strip():
                path = value.strip()
                return path if path.startswith("/") else f"/{path}"

    return _DEFAULT_DEVICE_STATUS_PATH


def _resolve_device_protocol_scheme(device: Any) -> str:
    raw_protocol = getattr(device, "protocol", None)
    scheme = str(raw_protocol).lower() if raw_protocol else "http"
    return scheme if scheme in {"http", "https"} else "http"


def _resolve_device_status_timeout_seconds(device: Any) -> float:
    capabilities = getattr(device, "capabilities_json", None)
    raw = capabilities if isinstance(capabilities, dict) else {}
    value = raw.get("device_status_timeout_seconds", _DEFAULT_DEVICE_STATUS_TIMEOUT_SECONDS)
    if not isinstance(value, int | float):
        value = _DEFAULT_DEVICE_STATUS_TIMEOUT_SECONDS
    return float(min(max(value, 1.0), 5.0))


def _resolve_device_status_snapshot_ttl_ms(device: Any, policy: DeviceDispatchPolicy) -> int:
    capabilities = getattr(device, "capabilities_json", None)
    raw = capabilities if isinstance(capabilities, dict) else {}
    value = raw.get("status_snapshot_ttl_ms", policy.status_snapshot_ttl_ms)
    if not isinstance(value, int | float):
        value = policy.status_snapshot_ttl_ms
    return int(min(max(value, 1), 60_000))


def _normalize_device_runtime_status(value: Any) -> DeviceRuntimeStatus:
    status = str(enum_value(value) or "").upper()
    if status in DeviceRuntimeStatus.__members__:
        return DeviceRuntimeStatus[status]
    return DeviceRuntimeStatus.UNKNOWN


def _resolve_device_snapshot_observed_at(device: Any, *, now: Any) -> Any:
    return (
        timezone.to_db_datetime(getattr(device, "last_heartbeat_at", None))
        or timezone.to_db_datetime(getattr(device, "updated_at", None))
        or now
    )


def _build_device_runtime_snapshot(
    device: Any,
    *,
    now: Any,
    policy: DeviceDispatchPolicy,
) -> DeviceRuntimeSnapshot:
    status = _normalize_device_runtime_status(getattr(device, "device_status", None))
    observed_at = _resolve_device_snapshot_observed_at(device, now=now)
    status_valid_until = observed_at + timedelta(milliseconds=_resolve_device_status_snapshot_ttl_ms(device, policy))
    current_command_id = getattr(device, "current_command_id", None)
    in_flight_count = 1 if current_command_id is not None or status == DeviceRuntimeStatus.RUNNING else 0
    concurrency_limit = coerce_optional_int(getattr(device, "max_concurrent_tasks", None)) or 1
    return DeviceRuntimeSnapshot(
        device_code=coerce_string_value(getattr(device, "device_code", None), "UNKNOWN_DEVICE"),
        status=status,
        observed_at=observed_at,
        status_valid_until=status_valid_until,
        in_flight_count=in_flight_count,
        concurrency_limit=concurrency_limit,
    )


def _resolve_dispatch_deadline_at(outbox: Any, payload: dict[str, Any], command: Any | None, *, now: Any) -> Any:
    for value in (
        payload.get("dispatch_deadline_at"),
        payload.get("deadline_at"),
        getattr(outbox, "dispatch_deadline_at", None),
        getattr(outbox, "deadline_at", None),
        getattr(command, "dispatch_deadline_at", None),
        getattr(command, "deadline_at", None),
    ):
        parsed = timezone.to_db_datetime(value)
        if parsed is not None:
            return parsed
    return now + timedelta(seconds=_DEFAULT_DEVICE_COMMAND_ACK_TIMEOUT_SECONDS)


def _resolve_dispatch_retry_attempt(outbox: Any, payload: dict[str, Any]) -> int:
    return (
        coerce_optional_int(payload.get("dispatch_retry_attempt"))
        or coerce_optional_int(getattr(outbox, "blocked_check_count", None))
        or 0
    )


def _build_device_dispatch_request(
    outbox: Any,
    payload: dict[str, Any],
    device: Any,
    command: Any | None,
    *,
    command_code: str | None,
    now: Any,
) -> DeviceDispatchRequest:
    command_type = _resolve_command_type_for_governance(payload)
    return DeviceDispatchRequest(
        command_code=command_code or "UNKNOWN_COMMAND",
        device_role=coerce_string_value(getattr(device, "device_role", None), "UNKNOWN_ROLE"),
        capability_code=coerce_string_value(payload.get("capability_code") or command_type, "UNKNOWN_CAPABILITY"),
        dispatch_deadline_at=_resolve_dispatch_deadline_at(outbox, payload, command, now=now),
        session_state=coerce_string_value(
            payload.get("session_state") or getattr(outbox, "session_state", None), "RUNNING"
        ).upper(),
        priority=coerce_optional_int(payload.get("priority")) or 5,
        retry_attempt=_resolve_dispatch_retry_attempt(outbox, payload),
    )


def _enforce_device_command_governance(
    device: Any,
    *,
    command_type: str | None,
    stage_label: str,
    allow_busy: bool = False,
    enforce_local_occupancy: bool = True,
    enforce_local_runtime_status: bool = True,
) -> None:
    """消费设备治理字段，拒绝不允许的命令创建/派发。"""

    device_id = resolve_entity_id(device)
    device_code = coerce_string_value(getattr(device, "device_code", None), "UNKNOWN_DEVICE")
    resolved_command_type = command_type or "UNKNOWN"

    device_status = enum_value(getattr(device, "device_status", DeviceStatus.IDLE)) or DeviceStatus.IDLE.value
    if enforce_local_runtime_status:
        if bool(getattr(device, "maintenance_mode", False)):
            _raise_device_command_governance_error(
                domain=FailureDomain.MANUAL_INTERVENTION.value,
                code="DEVICE_MAINTENANCE_MODE",
                message=(
                    f"设备 {device_code} 处于 maintenance_mode，拒绝{stage_label}: command_type={resolved_command_type}"
                ),
            )

        if device_status == DeviceStatus.MAINTENANCE.value:
            _raise_device_command_governance_error(
                domain=FailureDomain.MANUAL_INTERVENTION.value,
                code="DEVICE_MAINTENANCE_MODE",
                message=f"设备 {device_code} 处于 MAINTENANCE，拒绝{stage_label}: command_type={resolved_command_type}",
            )

        if device_status == DeviceStatus.ERROR.value:
            _raise_device_command_governance_error(
                domain=FailureDomain.MANUAL_INTERVENTION.value,
                code="DEVICE_ERROR_STATE",
                message=f"设备 {device_code} 处于 ERROR，拒绝{stage_label}: command_type={resolved_command_type}",
            )

        if device_status == DeviceStatus.OFFLINE.value:
            _raise_device_command_governance_error(
                domain=FailureDomain.HARDWARE.value,
                code="DEVICE_OFFLINE",
                message=f"设备 {device_code} 处于 OFFLINE，拒绝{stage_label}: command_type={resolved_command_type}",
            )

    current_command_id = getattr(device, "current_command_id", None)
    if (
        enforce_local_occupancy
        and (device_status == DeviceStatus.RUNNING.value or current_command_id is not None)
        and not allow_busy
    ):
        _raise_device_command_governance_error(
            domain=FailureDomain.ORCHESTRATION.value,
            code="DEVICE_BUSY",
            message=(
                f"设备 {device_code} 正在执行任务，拒绝{stage_label}: "
                f"current_command_id={current_command_id}, command_type={resolved_command_type}"
            ),
            device_id=device_id,
            device_code=device_code,
        )

    try:
        capabilities = parse_device_capabilities(getattr(device, "capabilities_json", None))
    except (TypeError, ValueError) as exc:
        _raise_device_command_governance_error(
            domain=FailureDomain.CONFIG.value,
            code="DEVICE_CAPABILITY_CONFIG_INVALID",
            message=f"设备 {device_code} capabilities_json 配置非法，拒绝{stage_label}: {exc}",
            cause=exc,
        )

    if not capabilities.supports_command(command_type):
        _raise_device_command_governance_error(
            domain=FailureDomain.CONFIG.value,
            code="UNSUPPORTED_COMMAND_TYPE",
            message=f"设备 {device_code} 不支持 command_type={resolved_command_type}，拒绝{stage_label}",
        )


async def _get_device_for_command_dispatch(db: Any, device_repository: Any, device_code: str) -> Any:
    """派发真实设备命令前锁定设备行，避免多 worker 同时给同一设备下发任务。"""

    from inspect import iscoroutinefunction

    locked_getter = getattr(device_repository, "get_by_device_code_for_update", None)
    if iscoroutinefunction(locked_getter):
        return await locked_getter(db, device_code)
    return await device_repository.get_by_device_code(db, device_code)


async def _release_device_runtime_if_failed_command_was_current(
    db: Any,
    *,
    command: Any,
    command_id: int,
) -> None:
    """派发侧失败只释放设备占用投影，不把设备标记为硬件 ERROR。"""

    device_id = getattr(command, "device_id", None)
    if not isinstance(device_id, int):
        return

    from src.app.device.services import device_service

    device = await device_service.repo.get_by_id(db, device_id)
    if device is None:
        return
    if enum_value(getattr(device, "device_status", None)) != DeviceStatus.RUNNING.value:
        return
    if getattr(device, "current_command_id", None) != command_id:
        return

    # success=True 表示释放派发占用；DeviceCommand 自身已经在调用方标记为 FAILED。
    _ = await device_service.mark_command_finished(
        db,
        device_id=device_id,
        command_id=command_id,
        success=True,
        auto_commit=False,
    )


async def _mark_device_command_failed_if_dispatch_exhausted(
    db: Any,
    *,
    outbox: Any,
    failed_outbox: Any,
    error_message: str,
) -> None:
    """Outbox 已永久失败时，进入通信 ACK runtime reconciliation。"""

    from src.app.device.repositories.command_repository import DeviceCommandRepository
    from src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl import (
        workline_runtime_reconciliation_service,
    )
    from src.app.sys.models import SystemOutboxDispatchType, SystemOutboxStatus

    if getattr(failed_outbox, "status", None) != SystemOutboxStatus.FAILED:
        return
    if getattr(outbox, "dispatch_type", None) != SystemOutboxDispatchType.DEVICE_COMMAND:
        return

    payload = _payload_dict(getattr(outbox, "payload_json", None))
    command_code = coerce_string_value(payload.get("command_code"))
    if not command_code:
        return

    command_repo = DeviceCommandRepository()
    command = await command_repo.get_by_command_code(db, command_code)
    command_id = resolve_entity_id(command)
    if command_id is None:
        return

    _ = await workline_runtime_reconciliation_service.handle_dispatch_ack_exhausted(
        db,
        outbox=failed_outbox,
        command=command,
        error_message=error_message,
    )


async def _mark_outbox_blocked_by_workline_state(
    db: Any,
    *,
    outbox_repo: Any,
    outbox: Any,
    outbox_id: int,
    safety_error: Exception,
) -> str:
    """按运行态阻断 outbox；RECONCILING 和 STOPPED 进入 parked，ESTOP 保持本地失败。"""

    reason = str(safety_error)
    if "WORKLINE_RECONCILING" in reason:
        from src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl import (
            workline_runtime_reconciliation_service,
        )

        _ = await workline_runtime_reconciliation_service.park_outbox_for_reconciliation(
            db,
            outbox=outbox,
            reason="CALLBACK_DEADLINE_EXPIRED",
        )
        return "blocked_resource"

    if "WORKLINE_STOPPED" in reason:
        _ = await outbox_repo.mark_as_blocked_by_workline_stopped(db, outbox_id)
        return "blocked_resource"

    _ = await outbox_repo.mark_as_blocked_by_workline_estop(db, outbox_id)
    return "failed"


def _is_same_session_current_command(*, outbox: Any, command: Any | None, device: Any | None) -> bool:
    command_id = resolve_entity_id(command)
    if command_id is None or device is None:
        return False
    outbox_session_id = getattr(outbox, "session_id", None)
    return (
        getattr(device, "current_command_id", None) == command_id
        and outbox_session_id is not None
        and getattr(outbox, "dispatch_key", None) == f"device-command:{getattr(command, 'command_code', None)}"
    )


def _build_device_status_url(device: Any, *, device_code: str) -> str:
    scheme = _resolve_device_protocol_scheme(device)
    status_path = _resolve_device_status_path(device)
    return f"{scheme}://{device.host}:{device.port}{status_path}?device_code={quote(device_code)}"


def _extract_device_status_state(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    state = payload.get("state")
    if isinstance(state, dict):
        return state
    device = payload.get("device")
    if isinstance(device, dict):
        nested_state = device.get("state")
        if isinstance(nested_state, dict):
            return nested_state
    return payload


def _build_device_status_precheck_detail(
    *,
    device_code: str,
    status_url: str,
    observed_mode: Any = None,
    observed_status: Any = None,
    observed_current_command_id: Any = None,
    http_status: int | None = None,
    error_kind: str | None = None,
    error_message: str | None = None,
    extra_detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造 ECS status precheck 的最小诊断摘要，供 dispatcher 写入资源等待详情。"""

    detail: dict[str, Any] = {
        "device_code": device_code,
        "status_url": status_url,
        "observed_mode": observed_mode,
        "observed_status": observed_status,
        "observed_current_command_id": observed_current_command_id,
    }
    optional_values = {
        "http_status": http_status,
        "error_kind": error_kind,
        "error_message": error_message,
    }
    detail.update({key: value for key, value in optional_values.items() if value is not None})
    if extra_detail:
        detail.update(extra_detail)
    return detail


def _raise_device_status_precheck_wait(
    *,
    device: Any,
    device_code: str,
    status_url: str,
    message: str,
    observed_mode: Any = None,
    observed_status: Any = None,
    observed_current_command_id: Any = None,
    http_status: int | None = None,
    error_kind: str,
    error_message: str | None = None,
    extra_detail: dict[str, Any] | None = None,
    cause: Exception | None = None,
) -> NoReturn:
    _raise_device_command_governance_error(
        domain=FailureDomain.HARDWARE.value,
        code="DEVICE_STATUS_PRECHECK_WAIT",
        message=message,
        device_id=resolve_entity_id(device),
        device_code=device_code,
        detail=_build_device_status_precheck_detail(
            device_code=device_code,
            status_url=status_url,
            observed_mode=observed_mode,
            observed_status=observed_status,
            observed_current_command_id=observed_current_command_id,
            http_status=http_status,
            error_kind=error_kind,
            error_message=error_message,
            extra_detail=extra_detail,
        ),
        cause=cause,
    )


def _build_dispatch_policy_detail(decision: DeviceDispatchDecision) -> dict[str, Any]:
    return {
        "policy_decision": decision.kind.value,
        "reason": decision.reason,
        "retry_after_seconds": decision.retry_after_seconds,
        "runtime_hold_required": decision.runtime_hold_required,
        "cancel_unsubmitted": decision.cancel_unsubmitted,
        "freeze_submitted": decision.freeze_submitted,
    }


def _emit_device_dispatch_policy_observability(
    *,
    command: Any | None,
    device: Any,
    outbox: Any,
    payload: dict[str, Any],
    decision: DeviceDispatchDecision,
) -> None:
    """发出 DeviceDispatchPolicy 决策观测事件；观测失败不改变派发决策。"""

    from src.app.runtime.orchestration.observability import runtime_observability_registry

    command_code = (
        coerce_string_value(getattr(command, "command_code", None))
        or coerce_string_value(payload.get("command_code"))
        or coerce_string_value(getattr(outbox, "dispatch_key", None))
        or "UNKNOWN_COMMAND"
    )
    dispatch_key = coerce_string_value(getattr(outbox, "dispatch_key", None))
    device_code = (
        decision.device_code
        or coerce_string_value(getattr(device, "device_code", None))
        or coerce_string_value(getattr(outbox, "target_code", None))
        or "UNKNOWN_DEVICE"
    )
    try:
        _ = runtime_observability_registry.emit(
            "device_command.dispatch_policy",
            {
                "trace_id": coerce_string_value(getattr(command, "trace_id", None))
                or coerce_string_value(getattr(outbox, "trace_id", None))
                or dispatch_key
                or command_code,
                "correlation_id": coerce_string_value(getattr(command, "correlation_id", None))
                or coerce_string_value(getattr(outbox, "correlation_id", None))
                or dispatch_key
                or command_code,
                "command_code": command_code,
                "device_code": device_code,
                "provider_code": _device_command_provider_code(device, payload),
                "policy_decision": decision.kind.value,
                "reason": decision.reason,
                "dispatch_allowed": decision.dispatch_allowed,
                "runtime_hold_required": decision.runtime_hold_required,
            },
        )
    except Exception as exc:  # pragma: no cover - 防止观测链路反向影响派发策略
        logger.warning(
            f"设备派发策略观测事件发射失败: command_code={command_code}, device_code={device_code}, error={exc}"
        )


def _ensure_dispatch_policy_allows_realtime_probe_or_dispatch(
    *,
    device: Any,
    outbox: Any,
    payload: dict[str, Any],
    command: Any | None,
    command_code: str | None,
    status_url: str,
    allow_same_reserved_command: bool = False,
    policy: DeviceDispatchPolicy = device_dispatch_policy,
) -> None:
    """在真实 ECS status probe 前应用 DeviceDispatchPolicy 的本地快照门禁。"""

    now = timezone.now_for_db()
    snapshot = _build_device_runtime_snapshot(device, now=now, policy=policy)
    request = _build_device_dispatch_request(outbox, payload, device, command, command_code=command_code, now=now)
    if allow_same_reserved_command:
        decision = DeviceDispatchDecision(
            kind=DeviceDispatchDecisionKind.ALLOW_DISPATCH,
            reason="SAME_RESERVED_COMMAND",
            device_code=snapshot.device_code,
            dispatch_allowed=True,
            runtime_hold_required=False,
        )
    else:
        decision = policy.evaluate(request, snapshot=snapshot, now=now)
    _emit_device_dispatch_policy_observability(
        command=command,
        device=device,
        outbox=outbox,
        payload=payload,
        decision=decision,
    )
    if decision.kind in {
        DeviceDispatchDecisionKind.ALLOW_DISPATCH,
        DeviceDispatchDecisionKind.RETRY_STATUS_PROBE,
    }:
        return

    _raise_device_status_precheck_wait(
        device=device,
        device_code=snapshot.device_code,
        status_url=status_url,
        message=f"设备 {snapshot.device_code} 未通过派发策略门禁，等待下次预检: {decision.reason}",
        observed_mode="LOCAL_RUNTIME",
        observed_status=snapshot.status.value,
        observed_current_command_id=getattr(device, "current_command_id", None),
        error_kind="dispatch_policy",
        error_message=decision.reason,
        extra_detail=_build_dispatch_policy_detail(decision),
    )


async def _ensure_realtime_device_status_ready(
    client: Any, device: Any, *, device_code: str, command_code: str | None = None
) -> bool:
    """真实命令 POST 前查询 ECS 实时状态，避免对非空闲设备产生物理副作用。"""

    status_url = _build_device_status_url(device, device_code=device_code)
    timeout_seconds = _resolve_device_status_timeout_seconds(device)
    try:
        response = await client.get(status_url, timeout=timeout_seconds)
    except Exception as exc:
        logger.warning(f"设备实时状态查询失败: device_code={device_code}, url={status_url}, error={exc}")
        _raise_device_status_precheck_wait(
            device=device,
            device_code=device_code,
            status_url=status_url,
            message=f"设备 {device_code} 实时状态查询暂不可用，等待下次预检: error={exc}",
            error_kind=type(exc).__name__,
            error_message=str(exc),
            cause=exc,
        )

    if response.status_code != 200:
        response_body = getattr(response, "text", "").strip()
        if response_body:
            logger.warning(f"设备实时状态查询失败: HTTP {response.status_code}, body={response_body}")
        else:
            logger.warning(f"设备实时状态查询失败: HTTP {response.status_code}")
        _raise_device_status_precheck_wait(
            device=device,
            device_code=device_code,
            status_url=status_url,
            message=f"设备 {device_code} 实时状态查询返回 HTTP {response.status_code}，等待下次预检",
            http_status=response.status_code,
            error_kind="http_status",
            error_message=f"HTTP {response.status_code}",
        )

    try:
        state = _extract_device_status_state(response.json())
    except Exception as exc:
        logger.warning(f"设备实时状态响应 JSON 解析失败: device_code={device_code}, error={exc}")
        _raise_device_status_precheck_wait(
            device=device,
            device_code=device_code,
            status_url=status_url,
            message=f"设备 {device_code} 实时状态响应 JSON 解析失败，等待下次预检: error={exc}",
            http_status=response.status_code,
            error_kind="json_parse_error",
            error_message=str(exc),
            cause=exc,
        )

    mode = state.get("mode")
    status = state.get("status", state.get("device_status"))
    current_command_id = state.get("current_command_id")
    if mode is None or status is None or "current_command_id" not in state:
        _raise_device_status_precheck_wait(
            device=device,
            device_code=device_code,
            status_url=status_url,
            message=(
                f"设备 {device_code} 实时状态响应结构无法提取接纳状态，等待下次预检: "
                f"mode={mode}, status={status}, current_command_id={current_command_id}"
            ),
            observed_mode=mode,
            observed_status=status,
            observed_current_command_id=current_command_id,
            http_status=response.status_code,
            error_kind="invalid_status_shape",
            error_message="missing mode/status/current_command_id",
        )
    if mode != "AUTO" or status != "IDLE" or current_command_id is not None:
        device_id = resolve_entity_id(device)
        detail = _build_device_status_precheck_detail(
            device_code=device_code,
            status_url=status_url,
            observed_mode=mode,
            observed_status=status,
            observed_current_command_id=current_command_id,
            http_status=response.status_code,
        )
        if current_command_id and current_command_id == command_code:
            _raise_device_command_governance_error(
                domain=FailureDomain.ORCHESTRATION.value,
                code="DEVICE_BUSY",
                message=f"设备实时状态已接受该命令但本地未确认: device_code={device_code}, current_command_id={current_command_id}",
                device_id=device_id,
                device_code=device_code,
                detail=detail,
            )

        message = (
            f"设备 {device_code} 实时状态忙，拒绝命令派发: "
            f"mode={mode}, status={status}, current_command_id={current_command_id}"
        )
        logger.warning(
            "设备实时状态不允许派发: "
            f"device_code={device_code}, mode={mode}, status={status}, current_command_id={current_command_id}"
        )
        _raise_device_command_governance_error(
            domain=FailureDomain.ORCHESTRATION.value,
            code="DEVICE_BUSY",
            message=message,
            device_id=device_id,
            device_code=device_code,
            detail=detail,
        )

    return True


class DeviceCommandGateway:
    def __init__(self) -> None:
        pass

    async def reserve_sandbox_command(self, db: Any, outbox: Any) -> bool:
        """沙箱设备命令进入待回传队列时，占用 WES 侧设备运行态。"""

        from src.app.device.models.command import CommandStatus
        from src.app.device.repositories.command_repository import DeviceCommandRepository
        from src.app.device.repositories.device_repository import device_repository
        from src.app.device.services import device_service

        device = await _get_device_for_command_dispatch(db, device_repository, outbox.target_code)
        if device is None:
            logger.error(f"沙箱设备不存在: {outbox.target_code}")
            return False

        payload = _payload_dict(getattr(outbox, "payload_json", None))
        command_code = coerce_string_value(payload.get("command_code"))
        if not command_code:
            logger.error(f"沙箱设备指令缺少 command_code: outbox_id={getattr(outbox, 'id', None)}")
            return False

        command = await DeviceCommandRepository().get_by_command_code(db, command_code)
        command_id = resolve_entity_id(command)
        device_id = resolve_entity_id(device)
        is_same_reserved_command = _is_same_session_current_command(outbox=outbox, command=command, device=device)
        _enforce_device_command_governance(
            device,
            command_type=_resolve_command_type_for_governance(payload),
            stage_label="沙箱命令派发",
            allow_busy=is_same_reserved_command,
        )
        if is_same_reserved_command:
            logger.info(
                f"沙箱设备命令已占用运行态，按已派发处理: device_code={outbox.target_code}, command_code={command_code}"
            )
            return True

        if device_id is None or command_id is None:
            logger.warning(
                "沙箱设备指令已进入待回传，但 WES 侧设备运行态未更新: "
                f"device_code={outbox.target_code}, command_code={command_code}"
            )
            return False
        if command is None:
            return False

        if enum_value(getattr(command, "status", None)) == CommandStatus.PENDING.value:
            command.status = CommandStatus.SENT
            command.sent_at = command.sent_at or timezone.now_for_db()

        _ = await device_service.mark_command_dispatched(
            db,
            device_id=device_id,
            command_id=command_id,
            auto_commit=False,
        )
        return True

    async def dispatch(self, db: Any, outbox: Any) -> bool:
        """派发设备指令。"""
        import httpx

        payload: dict[str, Any] = {}
        try:
            from src.app.device.repositories.device_repository import device_repository

            device = await _get_device_for_command_dispatch(db, device_repository, outbox.target_code)
            if device is None or not device.host or not device.port:
                logger.error(f"设备不存在或通信配置不完整: {outbox.target_code}")
                return False

            payload = _payload_dict(getattr(outbox, "payload_json", None))
            payload["device_code"] = coerce_string_value(getattr(device, "device_code", None), outbox.target_code)
            from src.app.device.models.command import CommandStatus
            from src.app.device.repositories.command_repository import DeviceCommandRepository

            command_code = coerce_string_value(payload.get("command_code"))
            command_repo = DeviceCommandRepository()
            command = await command_repo.get_by_command_code(db, command_code) if command_code else None
            command_id = resolve_entity_id(command)
            is_same_reserved_command = _is_same_session_current_command(outbox=outbox, command=command, device=device)
            _enforce_device_command_governance(
                device,
                command_type=_resolve_command_type_for_governance(payload),
                stage_label="命令派发",
                allow_busy=is_same_reserved_command,
                enforce_local_occupancy=False,
                enforce_local_runtime_status=False,
            )

            # 确保 scheme 是 http 或 https。
            scheme = _resolve_device_protocol_scheme(device)
            callback_path = _resolve_device_command_path(device)
            url = f"{scheme}://{device.host}:{device.port}{callback_path}"
            status_url = _build_device_status_url(device, device_code=payload["device_code"])
            _ensure_dispatch_policy_allows_realtime_probe_or_dispatch(
                device=device,
                outbox=outbox,
                payload=payload,
                command=command,
                command_code=command_code,
                status_url=status_url,
                allow_same_reserved_command=is_same_reserved_command,
            )
            ack_timeout = _DEFAULT_DEVICE_COMMAND_ACK_TIMEOUT_SECONDS
            async with httpx.AsyncClient() as client:
                _ = await _ensure_realtime_device_status_ready(
                    client=client, device=device, device_code=payload["device_code"], command_code=command_code
                )

                logger.info(f"发送设备指令参数: {_build_device_command_log_envelope(outbox, payload, endpoint=url)}")
                response = await client.post(url, json=payload, timeout=ack_timeout)
                if response.status_code == 200:
                    from src.app.device.services import device_service
                    from src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl import (
                        workline_runtime_reconciliation_service,
                    )

                    device_id = resolve_entity_id(device)
                    if command is not None and device_id is not None and command_id is not None:
                        # Mock/设备可能在 HTTP 200 返回前已经完成并回调；刷新后若已是终态，
                        # 不能再把设备占用投影覆盖回 RUNNING。
                        await db.refresh(command)
                        terminal_statuses = {
                            CommandStatus.COMPLETED.value,
                            CommandStatus.FAILED.value,
                            CommandStatus.TIMEOUT.value,
                            CommandStatus.CANCELLED.value,
                        }
                        if enum_value(getattr(command, "status", None)) in terminal_statuses:
                            logger.info(
                                "设备指令 ACK 返回前已收到完成回调，跳过 ACK/RUNNING 投影: "
                                f"device_code={outbox.target_code}, command_code={command_code}"
                            )
                        else:
                            ack_received_at = timezone.now_for_db()
                            command.status = CommandStatus.ACK_RECEIVED
                            command.sent_at = command.sent_at or ack_received_at
                            command.ack_received_at = ack_received_at
                            command.ack_code = response.status_code
                            command.ack_message = "HTTP 200"
                            _ = await device_service.mark_command_dispatched(
                                db,
                                device_id=device_id,
                                command_id=command_id,
                                auto_commit=False,
                            )
                            _ = await workline_runtime_reconciliation_service.activate_execution_deadline_after_ack(
                                db,
                                command_id=command_id,
                                ack_received_at=ack_received_at,
                            )
                            _emit_device_command_ack_observability(
                                command=command,
                                device=device,
                                outbox=outbox,
                                payload=payload,
                                ack_received_at=ack_received_at,
                            )
                            from src.app.sys.services.event_stream_service import (
                                defer_command_status_changed_event,
                            )

                            defer_command_status_changed_event(
                                db,
                                command=command,
                                action="acked",
                                workline_id=getattr(command, "workline_id", None),
                                device_id=device_id,
                                session_id=getattr(outbox, "session_id", None),
                            )
                    else:
                        logger.warning(
                            "设备指令已 ACK，但 WES 侧设备运行态未更新: "
                            f"device_code={outbox.target_code}, command_code={command_code or 'UNKNOWN'}"
                        )
                    logger.info(f"设备指令发送成功: {payload.get('command_code')}")
                    return True
                response_body = response.text.strip()
                if response_body:
                    logger.warning(f"设备指令发送失败: HTTP {response.status_code}, body={response_body}")
                else:
                    logger.warning(f"设备指令发送失败: HTTP {response.status_code}")
                return False
        except httpx.TimeoutException as e:
            logger.warning(f"设备指令 ACK 通信超时: {payload.get('command_code')}")
            raise RuntimeError("OUTBOX_ACK_TIMEOUT") from e
        except _DeviceCommandGovernanceError as e:
            logger.warning(str(e))
            raise
        except Exception as e:
            logger.error(f"设备指令派发失败: {e}")
            return False


async def prepare_runtime_device_command_effect(
    ctx: dict[str, Any], request: object, *, target_device: object, execution: object
) -> tuple[object, object]:
    """通过既有 device/runtime 桥接边界准备命令与 Outbox，不执行外部 I/O。"""

    from src.app.device.services.device_command_service import device_command_service

    return await device_command_service.prepare_runtime_effect(
        ctx["db"],
        request=request,
        target_device=target_device,
        session=ctx["session"],
        workline=ctx["workline"],
        idempotency_key=execution.idempotency_key,  # type: ignore[attr-defined]
        trace_id=ctx.get("trace_id"),
    )


device_command_gateway = DeviceCommandGateway()
