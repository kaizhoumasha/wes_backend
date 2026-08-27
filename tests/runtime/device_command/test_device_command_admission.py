"""业务派发、MANUAL_DEBUG 与人工对账共用的设备运行态准入。"""

from datetime import UTC, datetime

import pytest

from src.app.device.contracts import EcsDeviceStatus
from src.app.device.services.device_command_admission import (
    DeviceCommandAdmissionError,
    ensure_runtime_admissible,
    ensure_status_fresh,
)


def _status(*, supported_commands: object = ("PICK",), **state_overrides: object) -> EcsDeviceStatus:
    state: dict[str, object] = {
        "device_code": "ARM-01",
        "mode": "AUTO",
        "status": "IDLE",
        "is_online": True,
        "current_command_code": None,
        "scenario": "success",
        "updated_at": 1_786_579_200_000,
    }
    state.update(state_overrides)
    return EcsDeviceStatus.model_validate(
        {
            "device": {
                "device_code": "ARM-01",
                "device_name": "机械臂 1",
                "device_type": "ROBOTIC_ARM",
                "role": "PLACEMENT_DEVICE",
                "supported_commands": supported_commands,
                "supported_events": [],
            },
            "state": state,
        }
    )


def test_runtime_status_is_admissible_for_supported_task_type() -> None:
    ensure_runtime_admissible(
        status=_status(),
        expected_device_code="ARM-01",
        task_type="PICK",
    )


@pytest.mark.parametrize(
    ("status", "expected_device_code", "task_type", "code"),
    [
        (_status(), "ARM-02", None, "DEVICE_IDENTITY_MISMATCH"),
        (_status(is_online=False), "ARM-01", None, "DEVICE_OFFLINE"),
        (_status(mode="MANUAL"), "ARM-01", None, "DEVICE_MODE_NOT_AUTO"),
        (_status(status="RUNNING"), "ARM-01", None, "DEVICE_NOT_IDLE"),
        (_status(current_command_code="CMD-OTHER"), "ARM-01", None, "DEVICE_HAS_ACTIVE_COMMAND"),
        (_status(supported_commands=None), "ARM-01", "PICK", "DEVICE_TASK_TYPE_UNSUPPORTED"),
        (_status(supported_commands=()), "ARM-01", "PICK", "DEVICE_TASK_TYPE_UNSUPPORTED"),
        (_status(supported_commands=("MOVE",)), "ARM-01", "PICK", "DEVICE_TASK_TYPE_UNSUPPORTED"),
    ],
)
def test_runtime_status_rejection_has_stable_code(
    status: EcsDeviceStatus,
    expected_device_code: str,
    task_type: str | None,
    code: str,
) -> None:
    with pytest.raises(DeviceCommandAdmissionError) as exc_info:
        ensure_runtime_admissible(
            status=status,
            expected_device_code=expected_device_code,
            task_type=task_type,
        )

    assert exc_info.value.code == code


@pytest.mark.parametrize("updated_at", [1_786_579_189_999, 1_786_579_200_001])
def test_status_outside_frozen_freshness_window_is_rejected(updated_at: int) -> None:
    with pytest.raises(DeviceCommandAdmissionError) as exc_info:
        ensure_status_fresh(
            status=_status(updated_at=updated_at),
            observed_at=datetime(2026, 8, 13, tzinfo=UTC),
            status_max_age_ms=10_000,
        )

    assert exc_info.value.code == "DEVICE_STATUS_STALE"


@pytest.mark.parametrize("updated_at", [1_786_579_190_000, 1_786_579_200_000])
def test_status_at_frozen_freshness_boundaries_is_accepted(updated_at: int) -> None:
    ensure_status_fresh(
        status=_status(updated_at=updated_at),
        observed_at=datetime(2026, 8, 13, tzinfo=UTC),
        status_max_age_ms=10_000,
    )
