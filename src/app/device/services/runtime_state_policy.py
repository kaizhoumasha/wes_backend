"""设备运行态投影策略。"""

from dataclasses import dataclass
from typing import Any

from src.app.device.models.device import DeviceStatus
from src.core.exceptions import BusinessException


@dataclass(frozen=True)
class DeviceRuntimeProjection:
    """运行态字段投影结果。"""

    data: dict[str, Any]


class DeviceRuntimeStatePolicy:
    """集中定义设备运行态合法组合。"""

    @staticmethod
    def _status_value(value: Any) -> str:
        resolved = getattr(value, "value", value)
        return str(resolved or DeviceStatus.IDLE.value)

    @staticmethod
    def normalize_error_code(error_code: str | None, fallback: str) -> str:
        if isinstance(error_code, str) and error_code.strip():
            return error_code.strip()
        return fallback

    @staticmethod
    def _projection(
        *,
        status: DeviceStatus,
        maintenance_mode: bool = False,
        current_command_id: int | None = None,
        error_code: str | None = None,
    ) -> DeviceRuntimeProjection:
        return DeviceRuntimeProjection(
            {
                "device_status": status,
                "maintenance_mode": maintenance_mode,
                "current_command_id": current_command_id,
                "error_code": error_code,
            }
        )

    @classmethod
    def idle(cls) -> DeviceRuntimeProjection:
        return cls._projection(status=DeviceStatus.IDLE)

    @classmethod
    def running(cls, command_id: int) -> DeviceRuntimeProjection:
        return cls._projection(status=DeviceStatus.RUNNING, current_command_id=command_id)

    @classmethod
    def error(cls, error_code: str | None, fallback: str = "UNKNOWN_DEVICE_ERROR") -> DeviceRuntimeProjection:
        return cls._projection(status=DeviceStatus.ERROR, error_code=cls.normalize_error_code(error_code, fallback))

    @classmethod
    def callback_deadline_expired(cls) -> DeviceRuntimeProjection:
        """执行 Callback 超时后的设备隔离投影。"""

        return cls.error("CALLBACK_DEADLINE_EXPIRED")

    @classmethod
    def dispatch_ack_exhausted(cls) -> DeviceRuntimeProjection:
        """派发 ACK 重试耗尽后的设备隔离投影。"""

        return cls.error("OUTBOX_DISPATCH_FAILED")

    @classmethod
    def offline(cls, error_code: str | None = None) -> DeviceRuntimeProjection:
        return cls._projection(
            status=DeviceStatus.OFFLINE,
            error_code=cls.normalize_error_code(error_code, "HEARTBEAT_TIMEOUT"),
        )

    @classmethod
    def maintenance(cls, reason: str | None = None) -> DeviceRuntimeProjection:
        return cls._projection(
            status=DeviceStatus.MAINTENANCE,
            maintenance_mode=True,
            error_code=cls.normalize_error_code(reason, "MAINTENANCE"),
        )

    @classmethod
    def validate(cls, state: dict[str, Any], *, reason: str) -> None:
        """校验完整运行态组合。"""

        status = cls._status_value(state.get("device_status"))
        current_command_id = state.get("current_command_id")
        error_code = state.get("error_code")
        maintenance_mode = bool(state.get("maintenance_mode", False))

        invalid_reason: str | None = None
        if status == DeviceStatus.IDLE.value:
            if current_command_id is not None or error_code or maintenance_mode:
                invalid_reason = "IDLE 必须无 current_command_id、无 error_code、maintenance_mode=false"
        elif status == DeviceStatus.RUNNING.value:
            if not isinstance(current_command_id, int) or current_command_id <= 0 or error_code or maintenance_mode:
                invalid_reason = "RUNNING 必须有正整数 current_command_id、无 error_code、maintenance_mode=false"
        elif status == DeviceStatus.ERROR.value:
            if not error_code or current_command_id is not None or maintenance_mode:
                invalid_reason = "ERROR 必须有 error_code、无 current_command_id、maintenance_mode=false"
        elif status == DeviceStatus.OFFLINE.value:
            if not error_code or current_command_id is not None or maintenance_mode:
                invalid_reason = "OFFLINE 必须有 error_code、无 current_command_id、maintenance_mode=false"
        elif status == DeviceStatus.MAINTENANCE.value:
            if not maintenance_mode or current_command_id is not None or not error_code:
                invalid_reason = "MAINTENANCE 必须 maintenance_mode=true、有 error_code、无 current_command_id"
        else:
            invalid_reason = f"未知设备状态: {status}"

        if invalid_reason:
            raise BusinessException(
                message=f"非法设备运行态组合: status={status}, reason={reason}, {invalid_reason}",
                code="4000",
                detail={
                    "status": status,
                    "current_command_id": current_command_id,
                    "error_code": error_code,
                    "maintenance_mode": maintenance_mode,
                    "reason": reason,
                },
            )
