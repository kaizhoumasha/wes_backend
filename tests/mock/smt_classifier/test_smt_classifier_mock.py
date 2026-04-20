from __future__ import annotations

import asyncio
import importlib
import logging
import os
import subprocess
import sys
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from tests.mock.smt_classifier import agv_mock, allocation_mock, arm_mock, pipeline_mock
from tests.mock.smt_classifier.run_all import MOCK_SERVICES


def test_run_all_modules_are_importable() -> None:
    for service in MOCK_SERVICES:
        module = importlib.import_module(service["module"])
        assert hasattr(module, service["app_attr"])


def test_run_all_uses_shared_ports_for_dual_worklines() -> None:
    topology = {
        service["port"]: tuple(service.get("hosted_device_codes", [service["device_code"]]))
        for service in MOCK_SERVICES
    }

    assert topology[8005] == ("PIPELINE01", "PIPELINE02")
    assert topology[8006] == ("ARM01", "ARM03")
    assert topology[8007] == ("ARM02", "ARM04")


def test_pipeline_module_importable_with_pipeline_device_code() -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = "."
    env["DEVICE_CODE"] = "PIPELINE01"

    result = subprocess.run(
        [sys.executable, "-c", "import importlib; importlib.import_module('tests.mock.smt_classifier.pipeline_mock')"],
        cwd=".",
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_pipeline_module_importable_with_pipeline02_device_code() -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = "."
    env["DEVICE_CODE"] = "PIPELINE02"

    result = subprocess.run(
        [sys.executable, "-c", "import importlib; importlib.import_module('tests.mock.smt_classifier.pipeline_mock')"],
        cwd=".",
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_arm_mock_supports_dual_workline_device_codes() -> None:
    assert arm_mock.DEVICE_CONFIGS["ARM03"]["device_role"] == "INPUT_ARM"
    assert arm_mock.DEVICE_CONFIGS["ARM04"]["device_role"] == "OUTPUT_ARM"


def test_arm_mock_logs_request_validation_error(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.ERROR, logger=arm_mock.__name__)

    with TestClient(arm_mock.app) as client:
        response = client.post(
            "/api/v1/device/command",
            json={
                "command_code": "CMD-INVALID-REQ-001",
                "task_type": "PICK_AND_PUT",
                "priority": 1,
                "timeout": 30,
                "params": {},
                "timestamp": 1,
            },
        )

    assert response.status_code == 422
    assert "device_code" in caplog.text
    assert "Field required" in caplog.text


@pytest.mark.asyncio
async def test_cancel_command_cancels_background_task(monkeypatch: pytest.MonkeyPatch) -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def fake_execute_wes_command(payload: arm_mock.DeviceCommandPayload) -> None:
        del payload
        started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(arm_mock.arm_simulator, "execute_wes_command", fake_execute_wes_command)
    monkeypatch.setitem(arm_mock.DEVICE_STATUS, "status", "IDLE")
    arm_mock.current_command = None
    arm_mock.current_command_task = None

    payload = arm_mock.DeviceCommandPayload(
        device_code="ARM01",
        command_code="CMD-CANCEL-001",
        task_type="PICK_AND_PUT",
        priority=1,
        timeout=30,
        params={"source_type": "INPUT_PLATFORM", "target_type": "PIPELINE_PLATFORM"},
        timestamp=1,
    )

    ack = await arm_mock.receive_command(payload)
    assert ack.code == 200

    await asyncio.wait_for(started.wait(), timeout=1)

    result = await arm_mock.cancel_command(arm_mock.CancelRequest(command_code=payload.command_code))

    assert result.message == "Cancelled"
    assert cancelled.is_set()
    assert arm_mock.current_command is None
    assert arm_mock.current_command_task is None
    assert arm_mock.DEVICE_STATUS["status"] == "IDLE"


@pytest.mark.asyncio
async def test_arm_auto_execution_stops_without_self_await(monkeypatch: pytest.MonkeyPatch) -> None:
    simulator = arm_mock.ArmSimulator(arm_mock.DEVICE_CONFIGS["ARM01"])

    async def fake_execute_command(**kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(**kwargs)

    monkeypatch.setattr(simulator, "execute_command", fake_execute_command)

    await simulator.start_auto_execution(
        arm_mock.AutoExecuteConfig(interval_seconds=0, max_executions=1),
    )

    auto_task = simulator._auto_task
    assert auto_task is not None

    await asyncio.wait_for(auto_task, timeout=1)

    assert simulator._is_auto_executing is False
    assert simulator._auto_task is None


@pytest.mark.asyncio
async def test_pipeline_auto_trigger_stops_without_self_await(monkeypatch: pytest.MonkeyPatch) -> None:
    simulator = pipeline_mock.PipelineSimulator()

    async def fake_execute_command(**_: object) -> SimpleNamespace:
        return SimpleNamespace()

    monkeypatch.setattr(simulator, "execute_command", fake_execute_command)

    await simulator.start_auto_execution(
        pipeline_mock.AutoExecuteConfig(interval_seconds=0, max_executions=1),
    )

    auto_task = simulator._auto_task
    assert auto_task is not None

    await asyncio.wait_for(auto_task, timeout=1)

    assert simulator._is_auto_executing is False
    assert simulator._auto_task is None


@pytest.mark.asyncio
async def test_pipeline_cancel_command_cancels_background_task(monkeypatch: pytest.MonkeyPatch) -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def fake_execute_wes_command(payload: pipeline_mock.DeviceCommandPayload) -> None:
        del payload
        started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(pipeline_mock.pipeline_simulator, "execute_wes_command", fake_execute_wes_command)
    monkeypatch.setitem(pipeline_mock.DEVICE_STATUS, "status", "IDLE")
    pipeline_mock.current_command = None
    pipeline_mock.current_command_task = None

    payload = pipeline_mock.DeviceCommandPayload(
        device_code="PIPELINE01",
        command_code="CMD-PIPE-CANCEL-001",
        task_type="MOVE_FORWARD",
        priority=1,
        timeout=30,
        params={"source_type": "PIPELINE_PLATFORM", "target_type": "PIPELINE_PLATFORM"},
        timestamp=1,
    )

    ack = await pipeline_mock.receive_command(payload)
    assert ack.code == 200

    await asyncio.wait_for(started.wait(), timeout=1)

    result = await pipeline_mock.cancel_command(pipeline_mock.CancelRequest(command_code=payload.command_code))

    assert result.message == "Cancelled"
    assert cancelled.is_set()
    assert pipeline_mock.current_command is None
    assert pipeline_mock.current_command_task is None
    assert pipeline_mock.DEVICE_STATUS["status"] == "IDLE"


@pytest.mark.asyncio
async def test_execute_wes_command_rejects_invalid_locations() -> None:
    simulator = arm_mock.ArmSimulator(arm_mock.DEVICE_CONFIGS["ARM01"])
    payload = arm_mock.DeviceCommandPayload(
        device_code="ARM01",
        command_code="CMD-INVALID-001",
        task_type="PICK_AND_PUT",
        priority=1,
        timeout=30,
        params={"source_loc": "INVALID", "target_type": "PIPELINE_PLATFORM"},
        timestamp=1,
    )

    with pytest.raises(HTTPException, match="无效的源位置"):
        await simulator.execute_wes_command(payload)


@pytest.mark.asyncio
async def test_arm_input_mock_supports_measurement_reel(monkeypatch: pytest.MonkeyPatch) -> None:
    simulator = arm_mock.ArmSimulator(arm_mock.DEVICE_CONFIGS["ARM01"])
    captured: dict[str, object] = {}

    async def fake_callback_result_to_wes(
        *,
        command_code: str,
        result: str,
        data: dict[str, object] | None,
        error_detail: dict[str, object] | None,
    ) -> dict[str, object]:
        captured["command_code"] = command_code
        captured["result"] = result
        captured["data"] = data
        captured["error_detail"] = error_detail
        return {"code": 1000}

    monkeypatch.setattr(simulator, "_callback_result_to_wes", fake_callback_result_to_wes)

    payload = arm_mock.DeviceCommandPayload(
        device_code="ARM01",
        command_code="CMD-MEASURE-001",
        task_type="MEASUREMENT_REEL",
        priority=1,
        timeout=30,
        params={"pkg_id": "PKG001", "execution_time": 0},
        timestamp=1,
    )

    record = await simulator.execute_wes_command(payload)

    assert record.task_type == "MEASUREMENT_REEL"
    assert captured["result"] == "SUCCESS"
    callback_data = captured["data"]
    assert isinstance(callback_data, dict)
    assert callback_data["pkg_id"] == "PKG001"
    assert callback_data["reel_diameter"] == 15.0
    assert callback_data["reel_thickness"] == 20.0


@pytest.mark.asyncio
async def test_arm_receive_command_routes_to_matching_device_code(monkeypatch: pytest.MonkeyPatch) -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()
    other_called = False

    async def fake_execute_arm03(payload: arm_mock.DeviceCommandPayload) -> None:
        assert payload.device_code == "ARM03"
        started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def fake_execute_arm01(payload: arm_mock.DeviceCommandPayload) -> None:
        nonlocal other_called
        other_called = True
        del payload

    monkeypatch.setattr(arm_mock.ARM_SIMULATORS["ARM03"], "execute_wes_command", fake_execute_arm03)
    monkeypatch.setattr(arm_mock.ARM_SIMULATORS["ARM01"], "execute_wes_command", fake_execute_arm01)
    monkeypatch.setitem(arm_mock.DEVICE_STATUS_BY_CODE["ARM03"], "status", "IDLE")
    monkeypatch.setitem(arm_mock.DEVICE_STATUS_BY_CODE["ARM01"], "status", "IDLE")
    arm_mock.CURRENT_COMMANDS["ARM03"] = None
    arm_mock.CURRENT_COMMAND_TASKS["ARM03"] = None

    payload = arm_mock.DeviceCommandPayload(
        device_code="ARM03",
        command_code="CMD-ARM03-001",
        task_type="PICK_AND_PUT",
        priority=1,
        timeout=30,
        params={"source_type": "INPUT_PLATFORM", "target_type": "PIPELINE_PLATFORM"},
        timestamp=1,
    )

    ack = await arm_mock.receive_command(payload)

    assert ack.code == 200
    await asyncio.wait_for(started.wait(), timeout=1)
    assert other_called is False
    assert arm_mock.DEVICE_STATUS_BY_CODE["ARM03"]["status"] == "RUNNING"
    assert arm_mock.CURRENT_COMMANDS["ARM03"]["command_code"] == "CMD-ARM03-001"
    assert arm_mock.CURRENT_COMMANDS["ARM01"] is None

    result = await arm_mock.cancel_command(arm_mock.CancelRequest(command_code=payload.command_code))

    assert result.message == "Cancelled"
    assert cancelled.is_set()
    assert arm_mock.CURRENT_COMMANDS["ARM03"] is None
    assert arm_mock.CURRENT_COMMAND_TASKS["ARM03"] is None
    assert arm_mock.DEVICE_STATUS_BY_CODE["ARM03"]["status"] == "IDLE"


@pytest.mark.asyncio
async def test_pipeline_receive_command_routes_to_matching_device_code(monkeypatch: pytest.MonkeyPatch) -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()
    other_called = False

    async def fake_execute_pipeline02(payload: pipeline_mock.DeviceCommandPayload) -> None:
        assert payload.device_code == "PIPELINE02"
        started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def fake_execute_pipeline01(payload: pipeline_mock.DeviceCommandPayload) -> None:
        nonlocal other_called
        other_called = True
        del payload

    monkeypatch.setattr(pipeline_mock.PIPELINE_SIMULATORS["PIPELINE02"], "execute_wes_command", fake_execute_pipeline02)
    monkeypatch.setattr(pipeline_mock.PIPELINE_SIMULATORS["PIPELINE01"], "execute_wes_command", fake_execute_pipeline01)
    monkeypatch.setitem(pipeline_mock.DEVICE_STATUS_BY_CODE["PIPELINE02"], "status", "IDLE")
    monkeypatch.setitem(pipeline_mock.DEVICE_STATUS_BY_CODE["PIPELINE01"], "status", "IDLE")
    pipeline_mock.CURRENT_COMMANDS["PIPELINE02"] = None
    pipeline_mock.CURRENT_COMMAND_TASKS["PIPELINE02"] = None

    payload = pipeline_mock.DeviceCommandPayload(
        device_code="PIPELINE02",
        command_code="CMD-PIPELINE02-001",
        task_type="MOVE_FORWARD",
        priority=1,
        timeout=30,
        params={"source_type": "PIPELINE_PLATFORM", "target_type": "PIPELINE_PLATFORM"},
        timestamp=1,
    )

    ack = await pipeline_mock.receive_command(payload)

    assert ack.code == 200
    await asyncio.wait_for(started.wait(), timeout=1)
    assert other_called is False
    assert pipeline_mock.DEVICE_STATUS_BY_CODE["PIPELINE02"]["status"] == "RUNNING"
    assert pipeline_mock.CURRENT_COMMANDS["PIPELINE02"]["command_code"] == "CMD-PIPELINE02-001"
    assert pipeline_mock.CURRENT_COMMANDS["PIPELINE01"] is None

    result = await pipeline_mock.cancel_command(pipeline_mock.CancelRequest(command_code=payload.command_code))

    assert result.message == "Cancelled"
    assert cancelled.is_set()
    assert pipeline_mock.CURRENT_COMMANDS["PIPELINE02"] is None
    assert pipeline_mock.CURRENT_COMMAND_TASKS["PIPELINE02"] is None
    assert pipeline_mock.DEVICE_STATUS_BY_CODE["PIPELINE02"]["status"] == "IDLE"


def test_get_executions_returns_latest_records_from_deque() -> None:
    simulator = arm_mock.ArmSimulator(arm_mock.DEVICE_CONFIGS["ARM01"])
    now = datetime.now(UTC)

    simulator._executions.extend(
        [
            arm_mock.ExecutionRecord(
                execution_id="exec-1",
                command_code="CMD-001",
                task_type="PICK_AND_PUT",
                source={"location_id": "SRC-1"},
                target={"location_id": "TGT-1"},
                result="SUCCESS",
                message="ok",
                started_at=now,
                finished_at=now,
                duration_ms=1,
            ),
            arm_mock.ExecutionRecord(
                execution_id="exec-2",
                command_code="CMD-002",
                task_type="MEASUREMENT_REEL",
                source={"location_id": "SRC-2"},
                target={"location_id": "TGT-2"},
                result="SUCCESS",
                message="ok",
                started_at=now,
                finished_at=now,
                duration_ms=1,
            ),
        ]
    )

    records = simulator.get_executions(limit=1)

    assert [record.command_code for record in records] == ["CMD-002"]


@pytest.mark.asyncio
async def test_allocation_mock_returns_agv_then_allocated() -> None:
    simulator = allocation_mock.AllocationSimulator(mode="agv_required_then_allocated")
    request = allocation_mock.AllocationRequest(
        request_code="ALLOC-001",
        workline_code="WL-TEST-01",
        business_key="PKG-001",
        barcode="PKG-001",
        reel_diameter="15inch",
        reel_thickness="20",
        inspection_result="OK",
        source_location="STATION_OUTPUT1",
        timestamp=1,
    )

    first = await simulator.allocate(request)
    second = await simulator.allocate(request.model_copy(update={"request_code": "ALLOC-002"}))

    assert first.message == "AGV_REQUIRED"
    assert first.data["allocation_status"] == "AGV_REQUIRED"
    assert second.message == "ALLOCATED"
    assert second.data["target_bin"]["bin_id"].startswith("BIN_")


@pytest.mark.asyncio
async def test_agv_mock_callbacks_external_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    simulator = agv_mock.AgvSimulator(mode="success")
    captured: dict[str, object] = {}

    async def fake_post_signed_json(
        url: str, payload: dict[str, object], timeout_seconds: float = 10.0
    ) -> dict[str, object]:
        captured["url"] = url
        captured["payload"] = payload
        captured["timeout_seconds"] = timeout_seconds
        return {"code": 1000}

    monkeypatch.setattr(agv_mock, "post_signed_json", fake_post_signed_json)

    payload = agv_mock.AgvCommandPayload(
        command_code="AGV-REQ-001",
        task_type="MOVE_RACK",
        params={"from_location": "A", "to_location": "B", "rack_type": "SMT_BIN_RACK", "execution_time": 0},
        timestamp=1,
        correlation_id="corr-agv-001",
        callback_type="AGV_TASK_RESULT",
        callback_url="http://localhost:8001/api/v1/callback/external",
    )

    record = await simulator.execute_command(payload)

    assert record.result == "SUCCESS"
    assert captured["url"] == "http://localhost:8001/api/v1/callback/external"
    callback_payload = captured["payload"]
    assert isinstance(callback_payload, dict)
    assert callback_payload["callback_type"] == "AGV_TASK_RESULT"
    assert callback_payload["correlation_id"] == "corr-agv-001"
    assert callback_payload["command_code"] == "AGV-REQ-001"
