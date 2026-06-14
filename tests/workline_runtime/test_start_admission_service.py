"""WorkLine START 准入服务测试。"""

from __future__ import annotations

import importlib
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.app.device.models import Device, DeviceProtocol
from src.app.resource.models import RackKind
from src.app.sys.models import SystemOutbox, SystemOutboxDispatchType, SystemOutboxStatus, SystemOutboxTargetType
from src.app.workline.models import LineType, WorkLine, WorkLineRunMode
from src.app.workline.models.rack_position import WorklineRackPosition, WorklineRackPositionRole
from src.app.workline.models.safety import WorkLineRuntimeStatus
from src.app.workline.services.start_admission_service import (
    StartAdmissionStatusFetchResult,
    StartAdmissionStatusTarget,
    WorkLineStartAdmissionService,
)
from src.app.workline.services.station_lease_service import WorklineStationLeaseService
from src.core.task_queue_gateway import DISPATCH_SYSTEM_OUTBOX_TASK
from src.workline_plugins.rough_sorter.contract import (
    ROLE_CONVEYOR,
    ROLE_INPUT_ARM,
    ROLE_OUTPUT_ARM,
)
from src.workline_plugins.rough_sorter.plugin import POSITION_WORK_SINGLE_LAYER

start_admission_module = importlib.import_module("src.app.workline.services.start_admission_service")


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
    db_session.add(
        WorklineRackPosition(
            workline_id=workline.id,
            workline_code=workline.line_code,
            position_code=POSITION_WORK_SINGLE_LAYER,
            position_name="START 单层货架工作位",
            position_role=WorklineRackPositionRole.SMT_CLASSIFIER_SINGLE_RACK_WORK,
            allowed_rack_kind=RackKind.SINGLE_LAYER,
            capacity=1,
            logic_location_code=f"{workline.line_code}:{POSITION_WORK_SINGLE_LAYER}",
            external_location_code=POSITION_WORK_SINGLE_LAYER,
            device_role=ROLE_OUTPUT_ARM,
            priority=100,
            metadata_json={"test_fixture": True},
        )
    )
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


def _mock_ecs_idle_payload(devices: list[Device]) -> dict[str, Any]:
    return {
        "devices": [
            {
                "device": {"device_code": device.device_code},
                "state": {
                    "device_code": device.device_code,
                    "mode": "AUTO",
                    "status": "IDLE",
                    "current_command_id": None,
                },
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
async def test_start_admission_status_probe_uses_http_for_non_http_device_protocol(db_session) -> None:
    """TCP/MQTT/MODBUS 设备的 ECS status 探活仍应使用 httpx 支持的 HTTP scheme。"""

    workline = _make_workline(stopped_reason="MANUAL_STOP")
    devices = await _persist_workline_with_devices(db_session, workline)
    for device in devices:
        device.protocol = DeviceProtocol.TCP
    await db_session.commit()
    fetcher = RecordingStatusFetcher(_idle_payload(devices))
    service = WorkLineStartAdmissionService(status_fetcher=fetcher)

    result = await service.admit_start_for_device(
        db_session,
        "RS-IN-01",
        request_id="req-start-tcp-status",
        trace_id="trace-start-tcp-status",
    )

    assert result.accepted is True
    assert len(fetcher.calls) == 1
    target, _ = fetcher.calls[0]
    assert target.url == "http://mock-ecs:8010/api/v1/device/status"


@pytest.mark.asyncio
async def test_start_admission_accepts_mock_ecs_batch_status_shape(db_session) -> None:
    """START 准入接受 mock ECS 批量 status 的 device/state 嵌套契约。"""

    workline = _make_workline(stopped_reason="MANUAL_STOP")
    devices = await _persist_workline_with_devices(db_session, workline)
    fetcher = RecordingStatusFetcher(_mock_ecs_idle_payload(devices))
    service = WorkLineStartAdmissionService(status_fetcher=fetcher)

    result = await service.admit_start_for_device(
        db_session,
        "RS-IN-01",
        request_id="req-start-mock-shape",
        trace_id="trace-start-mock-shape",
    )

    await db_session.refresh(workline)
    assert result.accepted is True
    assert result.http_status == 200
    assert workline.runtime_status == WorkLineRuntimeStatus.READY
    assert workline.start_admission_status == "SUCCESS"


@pytest.mark.asyncio
async def test_start_admission_success_releases_and_enqueues_workline_parked_outbox(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """START 准入通过后，才释放并即时派发 WorkLine 级停放的 outbox。"""

    workline = _make_workline(stopped_reason="RECOVERY_CLEARED_WAITING_START")
    devices = await _persist_workline_with_devices(db_session, workline)
    outbox = SystemOutbox(
        workline_id=workline.id,
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        dispatch_key="device-command:START-RELEASES-PARKED",
        target_type=SystemOutboxTargetType.DEVICE,
        target_code="RS-IN-01",
        status=SystemOutboxStatus.BLOCKED_RESOURCE,
        blocked_workline_id=workline.id,
        blocked_reason="WORKLINE_STOPPED_WAITING_START",
        last_error="WORKLINE_STOPPED_WAITING_START",
    )
    db_session.add(outbox)
    await db_session.commit()
    enqueued: list[tuple[str, dict[str, Any]]] = []

    def fake_send_task(_self: object, task_name: str, *, kwargs: dict[str, Any]) -> None:
        enqueued.append((task_name, kwargs))

    monkeypatch.setattr("src.core.task_queue_gateway.CeleryTaskQueueGateway._send_task", fake_send_task)
    fetcher = RecordingStatusFetcher(_idle_payload(devices))
    service = WorkLineStartAdmissionService(status_fetcher=fetcher)

    result = await service.admit_start_for_device(db_session, "RS-IN-01", request_id="req-start-release")

    await db_session.refresh(workline)
    await db_session.refresh(outbox)
    assert result.accepted is True
    assert workline.runtime_status == WorkLineRuntimeStatus.READY
    assert outbox.status == SystemOutboxStatus.NEW
    assert outbox.blocked_workline_id is None
    assert outbox.blocked_reason is None
    assert outbox.last_error is None
    assert enqueued == [(DISPATCH_SYSTEM_OUTBOX_TASK, {"limit": 50})]
    db_session.add(
        WorklineRackPosition(
            workline_id=workline.id,
            workline_code=workline.line_code,
            position_code="START-STATION-A",
            position_name="START Station A",
            position_role=WorklineRackPositionRole.SMT_CLASSIFIER_SINGLE_RACK_WORK,
            allowed_rack_kind=RackKind.SINGLE_LAYER,
        )
    )
    await db_session.commit()

    station_lease = await WorklineStationLeaseService().get_station_lease_status(
        db_session,
        workline_id=workline.id,
        workline_code=workline.line_code,
        position_code="START-STATION-A",
    )
    assert station_lease.available is True
    assert station_lease.active_session_id is None
    assert station_lease.active_dispatch_key is None


@pytest.mark.asyncio
async def test_start_admission_success_ignores_enqueue_failure_after_releasing_outbox(db_session) -> None:
    """START 已提交成功后，即时派发触发失败不应覆盖 START 接受语义。"""

    class FailingQueueGateway:
        def enqueue_workline_inbox(self, *, limit: int = 10) -> None:
            _ = limit

        def enqueue_outbox(self, outbox_id: int | None = None, *, limit: int = 50) -> None:
            _ = (outbox_id, limit)
            raise RuntimeError("redis unavailable")

        def enqueue_internal_signal(self, target_code: str, payload: dict[str, Any]) -> None:
            _ = (target_code, payload)

    workline = _make_workline(stopped_reason="RECOVERY_CLEARED_WAITING_START")
    devices = await _persist_workline_with_devices(db_session, workline)
    outbox = SystemOutbox(
        workline_id=workline.id,
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        dispatch_key="device-command:START-QUEUE-FAILURE",
        target_type=SystemOutboxTargetType.DEVICE,
        target_code="RS-IN-01",
        status=SystemOutboxStatus.BLOCKED_RESOURCE,
        blocked_workline_id=workline.id,
        blocked_reason="WORKLINE_STOPPED_WAITING_START",
        last_error="WORKLINE_STOPPED_WAITING_START",
    )
    db_session.add(outbox)
    await db_session.commit()
    fetcher = RecordingStatusFetcher(_idle_payload(devices))
    service = WorkLineStartAdmissionService(status_fetcher=fetcher, queue_gateway=FailingQueueGateway())

    result = await service.admit_start_for_device(db_session, "RS-IN-01", request_id="req-start-queue-failure")

    await db_session.refresh(workline)
    await db_session.refresh(outbox)
    assert result.accepted is True
    assert result.http_status == 200
    assert workline.runtime_status == WorkLineRuntimeStatus.READY
    assert outbox.status == SystemOutboxStatus.NEW


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


@pytest.mark.asyncio
async def test_fetch_status_preserves_non_2xx_status_when_body_is_not_json(monkeypatch) -> None:
    """ECS 非 2xx 响应即使不是 JSON，也要保留 status_code 供上层分类为 HTTP_ERROR。"""

    class NonJsonResponse:
        status_code = 503

        def json(self) -> Any:
            raise ValueError("not json")

    class FakeAsyncClient:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self) -> FakeAsyncClient:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, url: str) -> NonJsonResponse:
            assert url == "http://mock-ecs:8010/api/v1/device/status"
            return NonJsonResponse()

    monkeypatch.setattr(start_admission_module.httpx, "AsyncClient", FakeAsyncClient)
    target = StartAdmissionStatusTarget(
        scheme="http",
        host="mock-ecs",
        port=8010,
        status_path="/api/v1/device/status",
        device_codes=("RS-IN-01",),
    )

    result = await WorkLineStartAdmissionService._fetch_status(target, 2.0)

    assert result.status_code == 503
    assert result.payload is None


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
async def test_start_admission_refreshes_final_lock_after_external_session_drift(db_session, db_engine) -> None:
    """status probe 期间其它事务提交安全状态变化时，final guard 必须读取 DB 新值。"""

    workline = _make_workline()
    devices = await _persist_workline_with_devices(db_session, workline)
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async def drifting_fetcher(
        target: StartAdmissionStatusTarget,
        timeout_seconds: float,
    ) -> StartAdmissionStatusFetchResult:
        async with session_factory() as drift_session:
            current = await drift_session.get(WorkLine, workline.id)
            assert current is not None
            current.runtime_status = WorkLineRuntimeStatus.ESTOPPED
            current.stopped_reason = "ESTOP_DURING_START_PROBE"
            await drift_session.commit()
        return StartAdmissionStatusFetchResult(status_code=200, payload=_idle_payload(devices))

    service = WorkLineStartAdmissionService(status_fetcher=drifting_fetcher)

    result = await service.admit_start_for_device(db_session, "RS-IN-01", request_id="req-start-external-drift")

    await db_session.refresh(workline)
    assert result.accepted is False
    assert result.http_status == 409
    assert result.reason_code == "START_ADMISSION_STATE_CHANGED"
    assert result.diagnostic["runtime_status"] == WorkLineRuntimeStatus.ESTOPPED.value
    assert workline.runtime_status == WorkLineRuntimeStatus.ESTOPPED
    assert workline.stopped_reason == "ESTOP_DURING_START_PROBE"
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


@pytest.mark.asyncio
async def test_start_admission_treats_already_ready_snapshot_as_idempotent_success(db_session) -> None:
    """START 已成功写为 READY 后，重复 START 不应覆盖成功投影。"""

    workline = _make_workline(
        runtime_status=WorkLineRuntimeStatus.READY,
        start_admission_status="SUCCESS",
        start_admission_message="START 准入通过",
        start_admission_failed_device_code=None,
    )
    devices = await _persist_workline_with_devices(db_session, workline)
    fetcher = RecordingStatusFetcher(_idle_payload(devices), status_code=500)
    service = WorkLineStartAdmissionService(status_fetcher=fetcher)

    result = await service.admit_start_for_device(db_session, "RS-IN-01", request_id="req-start-repeat-ready")

    await db_session.refresh(workline)
    assert result.accepted is True
    assert result.http_status == 200
    assert result.reason_code is None
    assert result.diagnostic["runtime_status"] == WorkLineRuntimeStatus.READY.value
    assert result.diagnostic["idempotent"] is True
    assert fetcher.calls == []
    assert workline.runtime_status == WorkLineRuntimeStatus.READY
    assert workline.start_admission_status == "SUCCESS"
    assert workline.start_admission_message == "START 准入通过"
    assert workline.start_admission_failed_device_code is None


@pytest.mark.asyncio
async def test_start_admission_success_keeps_device_resource_wait_outbox_blocked(db_session) -> None:
    """START 准入成功只释放 WorkLine hold，不释放 ECS resource wait。"""

    workline = _make_workline()
    devices = await _persist_workline_with_devices(db_session, workline)
    now = start_admission_module.timezone.now_for_db()
    resource_wait = SystemOutbox(
        workline_id=workline.id,
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        dispatch_key="device-command:start-admission-resource-wait",
        target_type=SystemOutboxTargetType.DEVICE,
        target_code=devices[0].device_code,
        status=SystemOutboxStatus.BLOCKED_RESOURCE,
        attempt_count=4,
        last_error="设备实时状态查询返回 HTTP 503，等待下次预检",
        blocked_device_id=devices[0].id,
        blocked_workline_id=workline.id,
        blocked_reason="DEVICE_STATUS_PRECHECK_WAIT",
        blocked_at=now - timedelta(seconds=30),
        last_blocked_check_at=now - timedelta(seconds=5),
        blocked_check_count=5,
        blocked_detail_json={"device_code": devices[0].device_code, "error_kind": "http_status"},
    )
    workline_wait = SystemOutbox(
        workline_id=workline.id,
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        dispatch_key="device-command:start-admission-workline-wait",
        target_type=SystemOutboxTargetType.DEVICE,
        target_code=devices[1].device_code,
        status=SystemOutboxStatus.BLOCKED_RESOURCE,
        attempt_count=2,
        last_error="WORKLINE_STOPPED_WAITING_START",
        blocked_workline_id=workline.id,
        blocked_reason="WORKLINE_STOPPED_WAITING_START",
        blocked_at=now - timedelta(seconds=40),
        last_blocked_check_at=now - timedelta(seconds=20),
        blocked_check_count=3,
        blocked_detail_json={"reason": "workline stopped"},
    )
    db_session.add_all([resource_wait, workline_wait])
    await db_session.flush()

    fetcher = RecordingStatusFetcher(_idle_payload(devices))
    service = WorkLineStartAdmissionService(status_fetcher=fetcher)

    result = await service.admit_start_for_device(db_session, devices[0].device_code, request_id="req-start-resource")

    await db_session.refresh(workline)
    await db_session.refresh(resource_wait)
    await db_session.refresh(workline_wait)
    assert result.accepted is True
    assert workline.runtime_status == WorkLineRuntimeStatus.READY
    assert workline_wait.status == SystemOutboxStatus.NEW
    assert workline_wait.attempt_count == 0
    assert resource_wait.status == SystemOutboxStatus.BLOCKED_RESOURCE
    assert resource_wait.attempt_count == 4
    assert resource_wait.last_error == "设备实时状态查询返回 HTTP 503，等待下次预检"
    assert resource_wait.blocked_reason == "DEVICE_STATUS_PRECHECK_WAIT"
    assert resource_wait.blocked_at == now - timedelta(seconds=30)
    assert resource_wait.last_blocked_check_at == now - timedelta(seconds=5)
    assert resource_wait.blocked_check_count == 5
    assert resource_wait.blocked_detail_json == {"device_code": devices[0].device_code, "error_kind": "http_status"}


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
