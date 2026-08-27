"""DeviceCommand 共用的设备运行态准入。"""

from datetime import UTC, datetime

from src.app.device.contracts import EcsDeviceMode, EcsDeviceState, EcsDeviceStatus


class DeviceCommandAdmissionError(ValueError):
    """设备运行态不满足可靠派发条件。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def ensure_runtime_admissible(
    *,
    status: EcsDeviceStatus,
    expected_device_code: str,
    task_type: str | None = None,
) -> None:
    """验证业务派发与 MANUAL_DEBUG 共用的实时设备状态。"""

    if status.device.device_code != expected_device_code:
        raise DeviceCommandAdmissionError("DEVICE_IDENTITY_MISMATCH")
    state = status.state
    if not state.is_online:
        raise DeviceCommandAdmissionError("DEVICE_OFFLINE")
    if state.mode is not EcsDeviceMode.AUTO:
        raise DeviceCommandAdmissionError("DEVICE_MODE_NOT_AUTO")
    if state.status is not EcsDeviceState.IDLE:
        raise DeviceCommandAdmissionError("DEVICE_NOT_IDLE")
    if state.current_command_code is not None:
        raise DeviceCommandAdmissionError("DEVICE_HAS_ACTIVE_COMMAND")
    if task_type is not None and task_type not in (status.device.supported_commands or ()):
        raise DeviceCommandAdmissionError("DEVICE_TASK_TYPE_UNSUPPORTED")


def ensure_status_fresh(
    *,
    status: EcsDeviceStatus,
    observed_at: datetime,
    status_max_age_ms: int | None,
) -> None:
    """按冻结的 binding 有效期验证 ECS 状态快照。"""

    observed_at_ms = int(observed_at.replace(tzinfo=UTC).timestamp() * 1000)
    age_ms = observed_at_ms - status.state.updated_at
    if status_max_age_ms is None or age_ms < 0 or age_ms > status_max_age_ms:
        raise DeviceCommandAdmissionError("DEVICE_STATUS_STALE")


__all__ = ["DeviceCommandAdmissionError", "ensure_runtime_admissible", "ensure_status_fresh"]
