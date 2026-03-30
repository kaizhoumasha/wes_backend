from __future__ import annotations

import asyncio
import importlib
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from tests.mock.smt_classifier import agv_mock, allocation_mock, arm_mock, pipeline_mock
from tests.mock.smt_classifier.run_all import MOCK_SERVICES


def test_run_all_modules_are_importable() -> None:
    for service in MOCK_SERVICES:
        module = importlib.import_module(service["module"])
        assert hasattr(module, service["app_attr"])


def test_pipeline_module_importable_with_pipeline_device_code() -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = "."
    env["DEVICE_CODE"] = "PIPELINE01"

    result = subprocess.run(  # noqa: S603 - controlled test subprocess for import isolation
        [sys.executable, "-c", "import importlib; importlib.import_module('tests.mock.smt_classifier.pipeline_mock')"],
        cwd=".",
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


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

    async def fake_post_signed_json(url: str, payload: dict[str, object], timeout_seconds: float = 10.0) -> dict[str, object]:
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
