from __future__ import annotations

import asyncio
import importlib
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from tests.mock.smt_classifier import arm_mock, pipeline_mock
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
        task_type="PICK_FROM_POLE",
        priority=1,
        timeout=30,
        params={"source_loc": "POLE_A", "target_loc": "CONVEYOR_IN"},
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

    async def fake_trigger_full_flow(barcode: str) -> list[str]:
        return [barcode]

    monkeypatch.setattr(simulator, "trigger_full_flow", fake_trigger_full_flow)

    await simulator.start_auto_trigger(
        pipeline_mock.AutoTriggerConfig(interval_seconds=0, max_triggers=1),
    )

    auto_task = simulator._auto_trigger_task
    assert auto_task is not None

    await asyncio.wait_for(auto_task, timeout=1)

    assert simulator._is_auto_triggering is False
    assert simulator._auto_trigger_task is None


@pytest.mark.asyncio
async def test_execute_wes_command_rejects_invalid_locations() -> None:
    simulator = arm_mock.ArmSimulator(arm_mock.DEVICE_CONFIGS["ARM01"])
    payload = arm_mock.DeviceCommandPayload(
        command_code="CMD-INVALID-001",
        task_type="PICK_FROM_POLE",
        priority=1,
        timeout=30,
        params={"source_loc": "INVALID", "target_loc": "CONVEYOR_IN"},
        timestamp=1,
    )

    with pytest.raises(HTTPException, match="无效的源位置"):
        await simulator.execute_wes_command(payload)
