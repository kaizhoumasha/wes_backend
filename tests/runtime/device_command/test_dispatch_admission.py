"""DeviceCommand 派发前冻结身份和合同校验。"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import pytest

from src.app.device.models.command import DeviceCommand
from src.app.device.services.device_command_admission import DeviceCommandAdmissionError
from src.app.device.services.device_dispatch_service import DeviceDispatchService
from src.app.workline.activation import WorkLineDeviceBinding


def _binding() -> WorkLineDeviceBinding:
    return WorkLineDeviceBinding(
        workline_id=11,
        device_id=7,
        device_code="ARM-01",
        device_role="PLACEMENT_DEVICE",
        endpoint_base_url="http://ecs-admission:8080",
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
        workline_id=11,
        execution_ref_type="MATERIAL_EXECUTION",
        execution_ref_id="EXEC-001",
        material_execution_id=21,
        contract_key="arm.pick",
        contract_version="2.0",
        task_type="PICK",
        params={},
        payload_digest="a" * 64,
        deadline_at=datetime(2026, 8, 13, 0, 1),
        created_at=now,
        updated_at=now,
    )


def test_matching_frozen_context_is_admissible_without_status() -> None:
    DeviceDispatchService.ensure_admissible(command=_command(), binding=_binding())


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"device_code": "ARM-02"}, "DEVICE_IDENTITY_MISMATCH"),
        ({"workline_id": 12}, "DEVICE_CONTRACT_MISMATCH"),
        ({"contract_key": "OTHER"}, "DEVICE_CONTRACT_MISMATCH"),
        ({"contract_version": "3.0"}, "DEVICE_CONTRACT_MISMATCH"),
    ],
)
def test_frozen_context_mismatch_fails_closed(overrides: dict[str, object], reason: str) -> None:
    with pytest.raises(DeviceCommandAdmissionError) as exc_info:
        DeviceDispatchService.ensure_admissible(command=_command(), binding=replace(_binding(), **overrides))
    assert exc_info.value.code == reason
