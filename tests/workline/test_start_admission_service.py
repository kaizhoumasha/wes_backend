"""WorkLine START 通用准入回归测试。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.runtime.capabilities.material_flow import start_admission_service as admission_module
from src.app.runtime.capabilities.material_flow.start_admission_service import (
    StartAdmissionStatusFetchResult,
    StartAdmissionStatusTarget,
    WorkLineStartAdmissionService,
)


@pytest.mark.asyncio
async def test_start_snapshot_uses_public_configuration_status_without_plugin_topology_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """删除插件拓扑 helper 后，START 仍能完成通用设备快照。"""

    workline = SimpleNamespace(id=7, is_active=True)
    device = SimpleNamespace(
        id=11,
        is_active=True,
        device_code="ECS-01",
        host="127.0.0.1",
        port=9001,
        protocol="HTTP",
        capabilities_json={},
    )
    second_device = SimpleNamespace(
        id=12,
        is_active=True,
        device_code="ECS-02",
        host="127.0.0.1",
        port=9001,
        protocol="HTTP",
        capabilities_json={},
    )
    monkeypatch.setattr(
        admission_module.workline_repository,
        "get_for_update",
        AsyncMock(return_value=workline),
    )
    monkeypatch.setattr(
        admission_module.device_repository,
        "get_by_work_line_id",
        AsyncMock(return_value=[second_device, device]),
    )
    configuration_status = AsyncMock(
        return_value=SimpleNamespace(
            checks=[SimpleNamespace(status="PASS", severity="INFO")],
        )
    )
    monkeypatch.setattr(admission_module.workline_service, "configuration_status", configuration_status)

    service = WorkLineStartAdmissionService()
    monkeypatch.setattr(service, "_is_ready", AsyncMock(return_value=False))
    monkeypatch.setattr(service, "_guard_startable", AsyncMock(return_value=None))
    db = object()

    snapshot = await service._load_start_snapshot(db, 7, request_id=None, trace_id=None)

    assert isinstance(snapshot, tuple)
    assert snapshot[0] is workline
    assert snapshot[1] == [device, second_device]
    assert [target.url for target in snapshot[2]] == [
        "http://127.0.0.1:9001/api/v1/device/status?device_code=ECS-01",
        "http://127.0.0.1:9001/api/v1/device/status?device_code=ECS-02",
    ]
    configuration_status.assert_awaited_once_with(db, 7)


def test_status_response_accepts_only_the_uniform_single_device_wire() -> None:
    service = WorkLineStartAdmissionService()
    response = {
        "device_code": "ECS-01",
        "contract_key": "scanner.read",
        "contract_version": "1.0",
        "mode": "AUTO",
        "status": "IDLE",
        "current_command_code": None,
        "error_detail": None,
        "timestamp": 1_786_377_600_000,
    }

    assert service._extract_status_records(response) == [response]
    assert service._extract_status_records([response]) is None
    assert service._extract_status_records({"devices": [response]}) is None
    assert service._extract_status_records({"data": [response]}) is None


@pytest.mark.parametrize(
    "missing_field",
    [
        "device_code",
        "contract_key",
        "contract_version",
        "mode",
        "status",
        "current_command_code",
        "error_detail",
        "timestamp",
    ],
)
def test_status_response_rejects_incomplete_uniform_wire(missing_field: str) -> None:
    service = WorkLineStartAdmissionService()
    response = {
        "device_code": "ECS-01",
        "contract_key": "scanner.read",
        "contract_version": "1.0",
        "mode": "AUTO",
        "status": "IDLE",
        "current_command_code": None,
        "error_detail": None,
        "timestamp": 1_786_377_600_000,
    }
    del response[missing_field]

    assert service._extract_status_records(response) is None


def test_status_validation_rejects_device_with_current_command_code() -> None:
    service = WorkLineStartAdmissionService()
    device = SimpleNamespace(device_code="ECS-01", work_line_id=7)
    target = SimpleNamespace(device_code="ECS-01", url="http://ecs/status?device_code=ECS-01")

    result = service._validate_required_device_status(
        [device],
        [target],
        {
            "ECS-01": {
                "device_code": "ECS-01",
                "mode": "AUTO",
                "status": "IDLE",
                "current_command_code": "CMD-001",
            }
        },
    )

    assert result is not None
    assert result.reason_code == "START_ADMISSION_DEVICE_NOT_IDLE"
    assert result.diagnostic["current_command_code"] == "CMD-001"


@pytest.mark.asyncio
async def test_status_probe_rejects_response_for_another_device() -> None:
    async def fetch_status(
        target: StartAdmissionStatusTarget,
        _timeout_seconds: float,
    ) -> StartAdmissionStatusFetchResult:
        other_code = "ECS-02" if target.device_code == "ECS-01" else "ECS-01"
        return StartAdmissionStatusFetchResult(
            status_code=200,
            payload={
                "device_code": other_code,
                "mode": "AUTO",
                "status": "IDLE",
                "current_command_code": None,
            },
        )

    service = WorkLineStartAdmissionService(status_fetcher=fetch_status)
    targets = [
        StartAdmissionStatusTarget("http", "ecs.local", 9001, "/api/v1/device/status", "ECS-01"),
        StartAdmissionStatusTarget("http", "ecs.local", 9001, "/api/v1/device/status", "ECS-02"),
    ]

    failure, _status_by_device_code = await service._probe_targets(
        targets,
        timeout_seconds=1.0,
        batch_concurrency=2,
    )

    assert failure is not None
    assert failure.reason_code == "START_ADMISSION_ECS_BAD_JSON"
