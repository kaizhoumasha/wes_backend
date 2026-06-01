"""WorkLine START 准入服务测试。"""

from __future__ import annotations

from typing import Any

import pytest

from src.app.device.models import Device
from src.app.workline.models import LineType, WorkLine, WorkLineRunMode
from src.app.workline.models.safety import WorkLineRuntimeStatus
from src.app.workline.services.start_admission_service import (
    StartAdmissionStatusFetchResult,
    StartAdmissionStatusTarget,
    WorkLineStartAdmissionService,
)
from src.workline_plugins.rough_sorter.contract import (
    ROLE_CONVEYOR,
    ROLE_INPUT_ARM,
    ROLE_OUTPUT_ARM,
)


class RecordingStatusFetcher:
    """记录 START 准入批量状态探测调用。"""

    def __init__(self, payload: Any, *, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code
        self.calls: list[tuple[StartAdmissionStatusTarget, float]] = []

    async def __call__(
        self,
        target: StartAdmissionStatusTarget,
        timeout_seconds: float,
    ) -> StartAdmissionStatusFetchResult:
        self.calls.append((target, timeout_seconds))
        return StartAdmissionStatusFetchResult(status_code=self.status_code, payload=self.payload)


def _make_workline(**overrides: Any) -> WorkLine:
    data: dict[str, Any] = {
        "line_code": "WL-START-001",
        "line_name": "START 准入线",
        "line_type": LineType.AUTO,
        "plugin_key": "rough_sorter",
        "contract_version": "rough_sorter.v1",
        "run_mode": WorkLineRunMode.AUTO,
        "runtime_status": WorkLineRuntimeStatus.STOPPED,
        "is_active": True,
    }
    data.update(overrides)
    return WorkLine(**data)


def _make_device(
    *,
    device_code: str,
    role: str,
    host: str = "mock-ecs",
    port: int = 8010,
    status_path: str = "/api/v1/device/status",
) -> Device:
    return Device(
        device_code=device_code,
        device_name=device_code,
        device_role=role,
        role_index=1,
        host=host,
        port=port,
        capabilities_json={"status_path": status_path},
    )


async def _persist_workline_with_devices(db_session, workline: WorkLine) -> list[Device]:
    db_session.add(workline)
    await db_session.commit()
    await db_session.refresh(workline)
    devices = [
        _make_device(device_code="RS-IN-01", role=ROLE_INPUT_ARM),
        _make_device(device_code="RS-CONV-01", role=ROLE_CONVEYOR),
        _make_device(device_code="RS-OUT-01", role=ROLE_OUTPUT_ARM),
    ]
    for device in devices:
        device.work_line_id = workline.id
        db_session.add(device)
    await db_session.commit()
    return devices


def _idle_payload(devices: list[Device]) -> dict[str, Any]:
    return {
        "devices": [
            {
                "device_code": device.device_code,
                "state": {"mode": "AUTO", "status": "IDLE", "current_command_id": None},
            }
            for device in devices
        ]
    }


@pytest.mark.asyncio
async def test_start_admission_happy_path_sets_ready_and_records_success(db_session) -> None:
    """STOPPED 且所有 ECS 设备 AUTO/IDLE 时，START 准入写入 READY。"""

    workline = _make_workline(stopped_reason="MANUAL_STOP")
    devices = await _persist_workline_with_devices(db_session, workline)
    fetcher = RecordingStatusFetcher(_idle_payload(devices))
    service = WorkLineStartAdmissionService(status_fetcher=fetcher)

    result = await service.admit_start_for_device(
        db_session,
        "RS-IN-01",
        request_id="req-start-ok",
        trace_id="trace-start-ok",
    )

    await db_session.refresh(workline)
    assert result.accepted is True
    assert result.http_status == 200
    assert workline.runtime_status == WorkLineRuntimeStatus.READY
    assert workline.stopped_reason is None
    assert workline.start_admission_status == "SUCCESS"
    assert workline.start_admission_checked_at is not None
    assert workline.last_start_request_id == "req-start-ok"
    assert workline.last_start_trace_id == "trace-start-ok"
    assert len(fetcher.calls) == 1
    target, timeout_seconds = fetcher.calls[0]
    assert target.url == "http://mock-ecs:8010/api/v1/device/status"
    assert target.device_codes == ("RS-CONV-01", "RS-IN-01", "RS-OUT-01")
    assert timeout_seconds == 2.0


@pytest.mark.asyncio
async def test_start_admission_rejects_non_idle_device_and_keeps_stopped(db_session) -> None:
    """任一必需设备非 IDLE 时，START 返回 409 且不写 READY。"""

    workline = _make_workline()
    devices = await _persist_workline_with_devices(db_session, workline)
    payload = _idle_payload(devices)
    payload["devices"][1]["state"]["status"] = "RUNNING"
    fetcher = RecordingStatusFetcher(payload)
    service = WorkLineStartAdmissionService(status_fetcher=fetcher)

    result = await service.admit_start_for_device(db_session, "RS-IN-01", request_id="req-start-busy")

    await db_session.refresh(workline)
    assert result.accepted is False
    assert result.http_status == 409
    assert result.reason_code == "START_ADMISSION_DEVICE_NOT_IDLE"
    assert result.diagnostic["device_code"] == "RS-CONV-01"
    assert workline.runtime_status == WorkLineRuntimeStatus.STOPPED
    assert workline.start_admission_status == "FAILED"
    assert workline.start_admission_failed_device_code == "RS-CONV-01"


@pytest.mark.parametrize(
    ("payload", "status_code", "reason_code"),
    [
        ({"devices": []}, 503, "START_ADMISSION_ECS_HTTP_ERROR"),
        ("not-json-object", 200, "START_ADMISSION_ECS_BAD_JSON"),
        (
            {"devices": [{"state": {"mode": "AUTO", "status": "IDLE", "current_command_id": None}}]},
            200,
            "START_ADMISSION_ECS_BAD_JSON",
        ),
    ],
)
@pytest.mark.asyncio
async def test_start_admission_rejects_ecs_response_failures_without_ready(
    db_session,
    payload: Any,
    status_code: int,
    reason_code: str,
) -> None:
    """ECS 非 2xx、坏 JSON、缺少 device_code 都拒绝 START。"""

    workline = _make_workline()
    await _persist_workline_with_devices(db_session, workline)
    fetcher = RecordingStatusFetcher(payload, status_code=status_code)
    service = WorkLineStartAdmissionService(status_fetcher=fetcher)

    result = await service.admit_start_for_device(db_session, "RS-IN-01", request_id="req-start-ecs-fail")

    await db_session.refresh(workline)
    assert result.accepted is False
    assert result.http_status == 409
    assert result.reason_code == reason_code
    assert result.diagnostic["device_code"] == "RS-CONV-01"
    assert result.diagnostic["device_codes"] == ("RS-CONV-01", "RS-IN-01", "RS-OUT-01")
    assert workline.runtime_status == WorkLineRuntimeStatus.STOPPED
    assert workline.start_admission_status == "FAILED"
    assert workline.start_admission_failed_device_code == "RS-CONV-01"


@pytest.mark.asyncio
async def test_start_admission_rejects_ecs_timeout_without_ready(db_session) -> None:
    """ECS status timeout 拒绝 START，且不写 READY。"""

    async def timeout_fetcher(
        target: StartAdmissionStatusTarget,
        timeout_seconds: float,
    ) -> StartAdmissionStatusFetchResult:
        raise TimeoutError("status probe timeout")

    workline = _make_workline()
    await _persist_workline_with_devices(db_session, workline)
    service = WorkLineStartAdmissionService(status_fetcher=timeout_fetcher)

    result = await service.admit_start_for_device(db_session, "RS-IN-01", request_id="req-start-timeout")

    await db_session.refresh(workline)
    assert result.accepted is False
    assert result.http_status == 409
    assert result.reason_code == "START_ADMISSION_ECS_TIMEOUT"
    assert result.diagnostic["device_code"] == "RS-CONV-01"
    assert result.diagnostic["device_codes"] == ("RS-CONV-01", "RS-IN-01", "RS-OUT-01")
    assert workline.runtime_status == WorkLineRuntimeStatus.STOPPED
    assert workline.start_admission_failed_device_code == "RS-CONV-01"


def test_start_admission_runtime_config_defaults_and_clamps() -> None:
    """START status timeout 和批量并发使用默认值，并 clamp 到允许区间。"""

    service = WorkLineStartAdmissionService()
    assert service.resolve_status_timeout_seconds({}) == 2.0
    assert service.resolve_status_timeout_seconds({"device_status_timeout_seconds": 0}) == 1.0
    assert service.resolve_status_timeout_seconds({"device_status_timeout_seconds": 9}) == 5.0
    assert service.resolve_batch_concurrency({}) == 4
    assert service.resolve_batch_concurrency({"device_status_batch_concurrency": 0}) == 1
    assert service.resolve_batch_concurrency({"device_status_batch_concurrency": 99}) == 8


@pytest.mark.parametrize("drift_status", [WorkLineRuntimeStatus.ESTOPPED, WorkLineRuntimeStatus.RECONCILING])
@pytest.mark.asyncio
async def test_start_admission_refuses_ready_when_final_cas_drifts(
    db_session,
    drift_status: WorkLineRuntimeStatus,
) -> None:
    """status probe 后 WorkLine 状态漂移时，最终 CAS 复查拒绝写 READY。"""

    workline = _make_workline()
    devices = await _persist_workline_with_devices(db_session, workline)

    async def drifting_fetcher(
        target: StartAdmissionStatusTarget,
        timeout_seconds: float,
    ) -> StartAdmissionStatusFetchResult:
        workline.runtime_status = drift_status
        await db_session.commit()
        return StartAdmissionStatusFetchResult(status_code=200, payload=_idle_payload(devices))

    service = WorkLineStartAdmissionService(status_fetcher=drifting_fetcher)

    result = await service.admit_start_for_device(db_session, "RS-IN-01", request_id="req-start-drift")

    await db_session.refresh(workline)
    assert result.accepted is False
    assert result.http_status == 409
    assert result.reason_code == "START_ADMISSION_STATE_CHANGED"
    assert workline.runtime_status == drift_status
    assert workline.start_admission_status == "FAILED"


@pytest.mark.asyncio
async def test_start_admission_treats_ready_final_drift_as_idempotent_success(db_session) -> None:
    """并发 START 已将 WorkLine 写为 READY 时，后续 final recheck 不应覆盖成功投影。"""

    workline = _make_workline()
    devices = await _persist_workline_with_devices(db_session, workline)

    async def drifting_fetcher(
        target: StartAdmissionStatusTarget,
        timeout_seconds: float,
    ) -> StartAdmissionStatusFetchResult:
        workline.runtime_status = WorkLineRuntimeStatus.READY
        workline.start_admission_status = "SUCCESS"
        workline.start_admission_message = "START 准入通过"
        workline.start_admission_failed_device_code = None
        await db_session.commit()
        return StartAdmissionStatusFetchResult(status_code=200, payload=_idle_payload(devices))

    service = WorkLineStartAdmissionService(status_fetcher=drifting_fetcher)

    result = await service.admit_start_for_device(db_session, "RS-IN-01", request_id="req-start-ready-drift")

    await db_session.refresh(workline)
    assert result.accepted is True
    assert result.http_status == 200
    assert result.reason_code is None
    assert result.diagnostic["runtime_status"] == WorkLineRuntimeStatus.READY.value
    assert workline.runtime_status == WorkLineRuntimeStatus.READY
    assert workline.start_admission_status == "SUCCESS"
    assert workline.start_admission_failed_device_code is None


@pytest.mark.parametrize("drift_status", [WorkLineRuntimeStatus.ESTOPPED, WorkLineRuntimeStatus.RECONCILING])
@pytest.mark.asyncio
async def test_start_admission_probe_failure_rechecks_guard_before_recording_ecs_failure(
    db_session,
    drift_status: WorkLineRuntimeStatus,
) -> None:
    """ECS probe 失败后 final guard 漂移时，应记录 guard failure 而不是覆盖为 ECS failure。"""

    workline = _make_workline()
    await _persist_workline_with_devices(db_session, workline)

    async def drifting_fetcher(
        target: StartAdmissionStatusTarget,
        timeout_seconds: float,
    ) -> StartAdmissionStatusFetchResult:
        workline.runtime_status = drift_status
        await db_session.commit()
        return StartAdmissionStatusFetchResult(status_code=503, payload={"error": "ecs down"})

    service = WorkLineStartAdmissionService(status_fetcher=drifting_fetcher)

    result = await service.admit_start_for_device(db_session, "RS-IN-01", request_id="req-start-probe-drift")

    await db_session.refresh(workline)
    assert result.accepted is False
    assert result.reason_code == "START_ADMISSION_STATE_CHANGED"
    assert result.diagnostic["runtime_status"] == drift_status.value
    assert workline.runtime_status == drift_status
    assert workline.start_admission_status == "FAILED"
    assert "不是 STOPPED" in (workline.start_admission_message or "")
    assert workline.start_admission_failed_device_code is None


@pytest.mark.parametrize("drift_status", [WorkLineRuntimeStatus.ESTOPPED, WorkLineRuntimeStatus.RECONCILING])
@pytest.mark.asyncio
async def test_start_admission_status_failure_rechecks_guard_before_recording_device_failure(
    db_session,
    drift_status: WorkLineRuntimeStatus,
) -> None:
    """设备非 IDLE 后 final guard 漂移时，应记录 guard failure 而不是覆盖为设备状态 failure。"""

    workline = _make_workline()
    devices = await _persist_workline_with_devices(db_session, workline)
    payload = _idle_payload(devices)
    payload["devices"][1]["state"]["status"] = "RUNNING"

    async def drifting_fetcher(
        target: StartAdmissionStatusTarget,
        timeout_seconds: float,
    ) -> StartAdmissionStatusFetchResult:
        workline.runtime_status = drift_status
        await db_session.commit()
        return StartAdmissionStatusFetchResult(status_code=200, payload=payload)

    service = WorkLineStartAdmissionService(status_fetcher=drifting_fetcher)

    result = await service.admit_start_for_device(db_session, "RS-IN-01", request_id="req-start-status-drift")

    await db_session.refresh(workline)
    assert result.accepted is False
    assert result.reason_code == "START_ADMISSION_STATE_CHANGED"
    assert result.diagnostic["runtime_status"] == drift_status.value
    assert workline.runtime_status == drift_status
    assert workline.start_admission_status == "FAILED"
    assert "不是 STOPPED" in (workline.start_admission_message or "")
    assert workline.start_admission_failed_device_code is None
