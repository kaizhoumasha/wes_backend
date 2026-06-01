from collections.abc import Mapping
from typing import Any, NoReturn

from loguru import logger

from src.app.device.models.capability import parse_device_capabilities
from src.app.device.models.device import DeviceStatus
from src.utils.timezone import timezone
from src.utils.value_normalization import coerce_optional_int, coerce_string_value, enum_value, resolve_entity_id
from src.workline_runtime.enums import FailureDomain
from src.workline_runtime.utils import payload_dict

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
    ):
        super().__init__(message)
        self.domain = domain
        self.code = code
        self.message = message
        self.device_id = device_id
        self.device_code = device_code


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
    cause: Exception | None = None,
) -> NoReturn:
    error = _DeviceCommandGovernanceError(
        domain=domain,
        code=code,
        message=message,
        device_id=device_id,
        device_code=device_code,
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
    """优先使用 capabilities_json.status_path，未配置时回退标准状态路径。"""

    capabilities = getattr(device, "capabilities_json", None)
    raw = capabilities if isinstance(capabilities, dict) else {}
    status_path = coerce_string_value(raw.get("status_path")) or _DEFAULT_DEVICE_STATUS_PATH
    if not status_path.startswith("/"):
        status_path = f"/{status_path}"
    return status_path


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


def _enforce_device_command_governance(
    device: Any,
    *,
    command_type: str | None,
    stage_label: str,
    allow_busy: bool = False,
) -> None:
    """消费设备治理字段，拒绝不允许的命令创建/派发。"""

    device_id = resolve_entity_id(device)
    device_code = coerce_string_value(getattr(device, "device_code", None), "UNKNOWN_DEVICE")
    resolved_command_type = command_type or "UNKNOWN"

    if bool(getattr(device, "maintenance_mode", False)):
        _raise_device_command_governance_error(
            domain=FailureDomain.MANUAL_INTERVENTION.value,
            code="DEVICE_MAINTENANCE_MODE",
            message=f"设备 {device_code} 处于 maintenance_mode，拒绝{stage_label}: command_type={resolved_command_type}",
        )

    device_status = enum_value(getattr(device, "device_status", DeviceStatus.IDLE)) or DeviceStatus.IDLE.value
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
    if (device_status == DeviceStatus.RUNNING.value or current_command_id is not None) and not allow_busy:
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
    from src.app.sys.models import SystemOutboxDispatchType, SystemOutboxStatus
    from src.app.workline.services.runtime_reconciliation_service import workline_runtime_reconciliation_service

    if getattr(failed_outbox, "status", None) != SystemOutboxStatus.FAILED:
        return
    if getattr(outbox, "dispatch_type", None) != SystemOutboxDispatchType.DEVICE_COMMAND:
        return

    payload = payload_dict(getattr(outbox, "payload_json", None))
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
    """按 WorkLine 运行态阻断 outbox；RECONCILING 进入 parked 队列，ESTOP 保持本地失败。"""

    reason = str(safety_error)
    if "WORKLINE_RECONCILING" in reason:
        from src.app.workline.services.runtime_reconciliation_service import workline_runtime_reconciliation_service

        _ = await workline_runtime_reconciliation_service.park_outbox_for_reconciliation(
            db,
            outbox=outbox,
            reason="CALLBACK_DEADLINE_EXPIRED",
        )
        return "blocked_resource"

    _ = await outbox_repo.mark_as_blocked_by_workline_estop(db, outbox_id)
    return "failed"


def _is_same_session_current_command(*, outbox: Any, command: Any | None, device: Any | None) -> bool:
    command_id = resolve_entity_id(command)
    if command_id is None or device is None:
        return False
    command_session_id = coerce_optional_int(getattr(command, "session_id_int", None))
    outbox_session_id = getattr(outbox, "session_id", None)
    return (
        getattr(device, "current_command_id", None) == command_id
        and outbox_session_id is not None
        and command_session_id == outbox_session_id
    )


def _build_device_status_url(device: Any, *, device_code: str) -> str:
    scheme = _resolve_device_protocol_scheme(device)
    status_path = _resolve_device_status_path(device)
    return f"{scheme}://{device.host}:{device.port}{status_path}?device_code={device_code}"


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


async def _ensure_realtime_device_status_ready(client: Any, device: Any, *, device_code: str) -> bool:
    """真实命令 POST 前查询 ECS 实时状态，避免对非空闲设备产生物理副作用。"""

    status_url = _build_device_status_url(device, device_code=device_code)
    timeout_seconds = _resolve_device_status_timeout_seconds(device)
    try:
        response = await client.get(status_url, timeout=timeout_seconds)
    except Exception as exc:
        logger.warning(f"设备实时状态查询失败: device_code={device_code}, url={status_url}, error={exc}")
        return False

    if response.status_code != 200:
        response_body = getattr(response, "text", "").strip()
        if response_body:
            logger.warning(f"设备实时状态查询失败: HTTP {response.status_code}, body={response_body}")
        else:
            logger.warning(f"设备实时状态查询失败: HTTP {response.status_code}")
        return False

    try:
        state = _extract_device_status_state(response.json())
    except Exception as exc:
        logger.warning(f"设备实时状态响应 JSON 解析失败: device_code={device_code}, error={exc}")
        return False

    mode = state.get("mode")
    status = state.get("status", state.get("device_status"))
    current_command_id = state.get("current_command_id")
    if mode != "AUTO" or status != "IDLE" or current_command_id is not None:
        logger.warning(
            "设备实时状态不允许派发: "
            f"device_code={device_code}, mode={mode}, status={status}, current_command_id={current_command_id}"
        )
        return False
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

        payload = payload_dict(getattr(outbox, "payload_json", None))
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

            payload = payload_dict(getattr(outbox, "payload_json", None))
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
            )
            if is_same_reserved_command:
                logger.warning(
                    "设备命令已占用运行态但尚未 ACK，按通信 ACK 重试/耗尽处理: "
                    f"device_code={outbox.target_code}, command_code={command_code}"
                )
                return False

            # 确保 scheme 是 http 或 https。
            scheme = _resolve_device_protocol_scheme(device)
            callback_path = _resolve_device_command_path(device)
            url = f"{scheme}://{device.host}:{device.port}{callback_path}"
            ack_timeout = _DEFAULT_DEVICE_COMMAND_ACK_TIMEOUT_SECONDS
            async with httpx.AsyncClient() as client:
                if not await _ensure_realtime_device_status_ready(
                    client,
                    device,
                    device_code=payload["device_code"],
                ):
                    outbox._dispatch_failure_reason = "DEVICE_STATUS_PRECHECK_FAILED"
                    outbox._dispatch_failure_error_code = "DEVICE_STATUS_PRECHECK_FAILED"
                    return False

                logger.info(f"发送设备指令参数: {_build_device_command_log_envelope(outbox, payload, endpoint=url)}")
                response = await client.post(url, json=payload, timeout=ack_timeout)
                if response.status_code == 200:
                    from src.app.device.services import device_service
                    from src.app.workline.services.runtime_reconciliation_service import (
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


device_command_gateway = DeviceCommandGateway()
