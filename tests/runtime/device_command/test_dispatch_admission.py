"""DeviceCommand 派发前设备状态准入。"""

from __future__ import annotations

from datetime import datetime

import pytest

from src.app.device.contracts import EcsDeviceStatus
from src.app.device.models.command import DeviceCommand
from src.app.device.services.device_dispatch_service import (
    DeviceDispatchAdmissionError,
    DeviceDispatchService,
)
from src.app.workline.models.line_run_epoch import LineRunEpochDeviceBinding


def _binding() -> LineRunEpochDeviceBinding:
    return LineRunEpochDeviceBinding(
        id=21,
        line_run_epoch_id=11,
        device_id=7,
        device_code="ARM-01",
        contract_key="arm.pick",
        contract_version="2.0",
        status_max_age_ms=1_000,
        command_timeout_ms=30_000,
    )


def _command() -> DeviceCommand:
    now = datetime(2026, 8, 13)
    return DeviceCommand(
        command_code="CMD-001",
        device_code="ARM-01",
        line_run_epoch_id=11,
        device_binding_id=21,
        execution_ref_type="MATERIAL_EXECUTION",
        execution_ref_id="EXEC-001",
        contract_key="arm.pick",
        contract_version="2.0",
        task_type="PICK",
        params={},
        payload_digest="a" * 64,
        deadline_at=datetime(2026, 8, 13, 0, 1),
        created_at=now,
        updated_at=now,
    )


def _status(**overrides: object) -> EcsDeviceStatus:
    payload: dict[str, object] = {
        "device_code": "ARM-01",
        "contract_key": "arm.pick",
        "contract_version": "2.0",
        "mode": "AUTO",
        "status": "IDLE",
        "current_command_code": None,
        "error_detail": None,
        "timestamp": 1_786_579_200_000,
    }
    payload.update(overrides)
    return EcsDeviceStatus.model_validate(payload)


def test_fresh_auto_idle_matching_status_is_admissible() -> None:
    DeviceDispatchService.ensure_admissible(
        command=_command(),
        binding=_binding(),
        status=_status(),
        observed_at=datetime(2026, 8, 13, 0, 0, 0, 500_000),
    )


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"mode": "MANUAL"}, "DEVICE_MODE_NOT_AUTO"),
        ({"status": "RUNNING", "current_command_code": "CMD-OTHER"}, "DEVICE_NOT_IDLE"),
        ({"current_command_code": "CMD-OTHER"}, "DEVICE_HAS_ACTIVE_COMMAND"),
        ({"contract_version": "2.1"}, "DEVICE_CONTRACT_MISMATCH"),
        ({"device_code": "ARM-02"}, "DEVICE_IDENTITY_MISMATCH"),
    ],
)
def test_untrusted_status_fails_closed(overrides: dict[str, object], reason: str) -> None:
    with pytest.raises(DeviceDispatchAdmissionError) as exc_info:
        DeviceDispatchService.ensure_admissible(
            command=_command(),
            binding=_binding(),
            status=_status(**overrides),
            observed_at=datetime(2026, 8, 13, 0, 0, 0, 500_000),
        )

    assert exc_info.value.code == reason


def test_stale_status_fails_closed() -> None:
    with pytest.raises(DeviceDispatchAdmissionError) as exc_info:
        DeviceDispatchService.ensure_admissible(
            command=_command(),
            binding=_binding(),
            status=_status(),
            observed_at=datetime(2026, 8, 13, 0, 0, 2),
        )

    assert exc_info.value.code == "DEVICE_STATUS_STALE"
