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

from tests.mock.smt_classifier import agv_mock, allocation_mock, arm_mock, pipeline_mock, rack_exchange_mock
from tests.mock.smt_classifier.run_all import MOCK_SERVICES


def _rack_action(action: str) -> dict[str, object]:
    return {"action": action, "required": True}


def test_run_all_modules_are_importable() -> None:
    for service in MOCK_SERVICES:
        module = importlib.import_module(service["module"])
        assert hasattr(module, service["app_attr"])


def test_run_all_includes_wms_rcs_rack_exchange_mock() -> None:
    services_by_port = {service["port"]: service for service in MOCK_SERVICES}

    assert services_by_port[8010]["module"] == "tests.mock.smt_classifier.rack_exchange_mock"
    assert services_by_port[8010]["device_code"] == "WMS_RCS"


def test_run_all_uses_shared_ports_for_dual_worklines() -> None:
    topology = {
        service["port"]: tuple(service.get("hosted_device_codes", [service["device_code"]]))
        for service in MOCK_SERVICES
    }

    assert topology[8005] == ("PIPELINE01", "PIPELINE02")
    assert topology[8006] == ("ARM01", "ARM03")
    assert topology[8007] == ("ARM02", "ARM04")


def test_rack_exchange_request_accepts_task_action_object() -> None:
    request = rack_exchange_mock.RackExchangeRequest(
        dispatch_key="rack-operation:op-001:2:ALLOCATE_AND_MOVE_RACK",
        actions={"action": "ALLOCATE_AND_MOVE_RACK", "required": True},
    )

    assert len(request.actions) == 1
    assert request.actions[0].action == "ALLOCATE_AND_MOVE_RACK"
    assert request.actions[0].required is True


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


def test_arm_mock_root_endpoint_returns_service_metadata() -> None:
    with TestClient(arm_mock.app) as client:
        response = client.get("/")

    assert response.status_code == 200
    payload = response.json()
    assert payload["service"].startswith("SMT 粗分机机械臂 Mock 服务")
    assert payload["device_code"] == "ARM01"
    assert payload["status"] == "running"


def test_pipeline_mock_root_endpoint_returns_service_metadata() -> None:
    with TestClient(pipeline_mock.app) as client:
        response = client.get("/")

    assert response.status_code == 200
    payload = response.json()
    assert payload["service"] == "SMT 粗分机流水线 Mock 服务"
    assert payload["device_code"] == "PIPELINE01"
    assert payload["status"] == "running"


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
async def test_pipeline_mock_includes_pkg_id_in_result_callback(monkeypatch: pytest.MonkeyPatch) -> None:
    simulator = pipeline_mock.PipelineSimulator()
    captured: dict[str, object] = {}

    async def fake_callback_result_to_wes(
        *,
        command_code: str,
        result: str,
        pkg_id: str | None,
        source: object,
        target: object,
        error_detail: dict[str, object] | None,
    ) -> dict[str, object]:
        captured["command_code"] = command_code
        captured["result"] = result
        captured["pkg_id"] = pkg_id
        captured["source"] = source
        captured["target"] = target
        captured["error_detail"] = error_detail
        return {"code": 1000}

    monkeypatch.setattr(simulator, "_callback_result_to_wes", fake_callback_result_to_wes)

    payload = pipeline_mock.DeviceCommandPayload(
        device_code="PIPELINE01",
        command_code="CMD-PIPE-001",
        task_type="MOVE_FORWARD",
        priority=1,
        timeout=30,
        params={"pkg_id": "PKG001", "execution_time": 0},
        timestamp=1,
    )

    record = await simulator.execute_wes_command(payload)

    assert record.task_type == "MOVE_FORWARD"
    assert record.pkg_id == "PKG001"
    assert captured["result"] == "SUCCESS"
    assert captured["command_code"] == "CMD-PIPE-001"
    assert captured["pkg_id"] == "PKG001"


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
    assert callback_data["PkgID"] == "PKG001"
    assert callback_data["reel_diameter"] == 15.0
    assert callback_data["reel_thickness"] == 20.0


@pytest.mark.asyncio
async def test_arm_input_mock_uses_configured_measurement_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARM_MEASUREMENT_REEL_DIAMETER", "7inch")
    monkeypatch.setenv("ARM_MEASUREMENT_REEL_THICKNESS", "12.5")

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
        command_code="CMD-MEASURE-ENV-001",
        task_type="MEASUREMENT_REEL",
        priority=1,
        timeout=30,
        params={"pkg_id": "PKG001", "execution_time": 0},
        timestamp=1,
    )

    await simulator.execute_wes_command(payload)

    callback_data = captured["data"]
    assert isinstance(callback_data, dict)
    assert callback_data["reel_diameter"] == 7.0
    assert callback_data["reel_thickness"] == 12.5


async def test_arm_output_mock_accepts_bin_id_as_target_location(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        arm_mock.DEVICE_STATUS_BY_CODE,
        "ARM02",
        {
            "device_code": "ARM02",
            "status": "IDLE",
            "is_online": True,
            "error_code": "NONE",
            "current_command_code": None,
        },
    )
    simulator = arm_mock.ArmSimulator(arm_mock.DEVICE_CONFIGS["ARM02"])
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
        device_code="ARM02",
        command_code="CMD-OUTPUT-BIN-001",
        task_type="PICK_AND_PUT",
        priority=1,
        timeout=30,
        params={
            "barcode": "PKG-OUTPUT-001",
            "target_type": "BIN",
            "target_loc": "BIN-249",
            "rack_slot_code": "A",
            "rack_slot_location_code": "NHW-1CLJ-0096-1A-0",
            "bin_orientation_code": "BIN-249-A",
            "bin_type": "6格箱",
            "bin_cell_location": "BIN-249-4",
            "bin_cell_index": "4",
            "execution_time": 0,
        },
        timestamp=1,
    )

    record = await simulator.execute_wes_command(payload)

    assert record.task_type == "PICK_AND_PUT"
    assert captured["result"] == "SUCCESS"
    callback_data = captured["data"]
    assert isinstance(callback_data, dict)
    assert callback_data["bin_id"] == "BIN-249"
    assert callback_data["rack_slot_code"] == "A"
    assert callback_data["rack_slot_location_code"] == "NHW-1CLJ-0096-1A-0"
    assert callback_data["bin_orientation_code"] == "BIN-249-A"
    assert callback_data["bin_type"] == "6格箱"
    assert callback_data["bin_cell_location"] == "BIN-249-4"
    assert callback_data["bin_cell_index"] == "4"


@pytest.mark.asyncio
async def test_arm_output_mock_accepts_hyphenated_dynamic_bin_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        arm_mock.DEVICE_STATUS_BY_CODE,
        "ARM02",
        {
            "device_code": "ARM02",
            "status": "IDLE",
            "is_online": True,
            "error_code": "NONE",
            "current_command_code": None,
        },
    )
    simulator = arm_mock.ArmSimulator(arm_mock.DEVICE_CONFIGS["ARM02"])
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
        device_code="ARM02",
        command_code="CMD-OUTPUT-BIN-HYPHEN-001",
        task_type="PICK_AND_PUT",
        priority=1,
        timeout=30,
        params={
            "barcode": "PKG-OUTPUT-002",
            "target_type": "BIN",
            "target_loc": "BIN-MOCK-001",
            "rack_slot_code": "B",
            "rack_slot_location_code": "NHW-1CLJ-0096-1B-0",
            "bin_orientation_code": "BIN-MOCK-001-A",
            "bin_type": "6格箱",
            "bin_cell_location": "BIN-MOCK-001-5",
            "bin_cell_index": "5",
            "execution_time": 0,
        },
        timestamp=1,
    )

    await simulator.execute_wes_command(payload)

    callback_data = captured["data"]
    assert isinstance(callback_data, dict)
    assert callback_data["bin_id"] == "BIN-MOCK-001"
    assert callback_data["rack_slot_code"] == "B"
    assert callback_data["rack_slot_location_code"] == "NHW-1CLJ-0096-1B-0"
    assert callback_data["bin_orientation_code"] == "BIN-MOCK-001-A"
    assert callback_data["bin_cell_location"] == "BIN-MOCK-001-5"
    assert callback_data["bin_cell_index"] == "5"


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
    current_command = arm_mock.CURRENT_COMMANDS["ARM03"]
    assert current_command is not None
    assert current_command["command_code"] == "CMD-ARM03-001"
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
    current_command = pipeline_mock.CURRENT_COMMANDS["PIPELINE02"]
    assert current_command is not None
    assert current_command["command_code"] == "CMD-PIPELINE02-001"
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
    target_bin = second.data["target_bin"]
    assert target_bin["bin_id"].startswith("BIN-")
    assert target_bin["rack_slot_code"] in {"A", "B", "C", "D"}
    assert target_bin["rack_slot_location_code"].startswith(f"{target_bin['rack_id']}-1")
    assert target_bin["bin_orientation_code"] == f"{target_bin['bin_id']}-A"
    assert target_bin["bin_cell_location"].startswith(f"{target_bin['bin_id']}-")
    assert target_bin["bin_cell_index"] == target_bin["bin_cell_location"].rsplit("-", 1)[-1]


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
        trace_id="trace-agv-001",
        callback_type="AGV_TASK_RESULT",
        callback_url="http://localhost:8001/api/v1/callback/external",
    )

    record = await simulator.execute_command(payload)

    assert record.result == "SUCCESS"
    assert captured["url"] == "http://localhost:8001/api/v1/callback/external"
    callback_payload = captured["payload"]
    assert isinstance(callback_payload, dict)
    assert callback_payload["callback_type"] == "AGV_TASK_RESULT"
    assert callback_payload["trace_id"] == "trace-agv-001"
    assert callback_payload["command_code"] == "AGV-REQ-001"


@pytest.mark.asyncio
async def test_rack_operation_mock_accepts_smt_rack_operation_request(monkeypatch: pytest.MonkeyPatch) -> None:
    simulator = rack_exchange_mock.RackExchangeSimulator(mode="success")
    captured: dict[str, object] = {}

    async def fake_post_signed_json(
        url: str, payload: dict[str, object], timeout_seconds: float = 10.0
    ) -> dict[str, object]:
        captured["url"] = url
        captured["payload"] = payload
        captured["timeout_seconds"] = timeout_seconds
        return {"code": 1000}

    monkeypatch.setattr(rack_exchange_mock, "post_signed_json", fake_post_signed_json)
    monkeypatch.setattr(rack_exchange_mock, "EXECUTION_TIME", 0)

    request = rack_exchange_mock.RackExchangeRequest(
        dispatch_key="external:smt_classifier:trace-rack-mock:RACK_OPERATION",
        trace_id="trace-rack-mock",
        actions=[_rack_action("SUPPLY_EMPTY_RACK")],
    )

    assert request.request_type == "SMT_RACK_OPERATION"

    record = await simulator.execute_request(request)

    assert record.request_type == "SMT_RACK_OPERATION"
    assert record.callback_type == "WMS_RACK_ARRIVED"
    assert record.result == "SUCCESS"
    callback_payload = captured["payload"]
    assert isinstance(callback_payload, dict)
    assert callback_payload["callback_type"] == "WMS_RACK_ARRIVED"
    assert callback_payload["dispatch_key"] == "external:smt_classifier:trace-rack-mock:RACK_OPERATION"


@pytest.mark.asyncio
async def test_rack_exchange_mock_defaults_to_mixed_single_layer_rack(monkeypatch: pytest.MonkeyPatch) -> None:
    for env_name in (
        "RACK_EXCHANGE_PROFILE",
        "RACK_EXCHANGE_BIN_ID",
        "RACK_EXCHANGE_BIN_TYPE",
        "RACK_EXCHANGE_BIN_CELL_LOCATION",
        "RACK_EXCHANGE_CELL_TYPE",
    ):
        monkeypatch.delenv(env_name, raising=False)

    simulator = rack_exchange_mock.RackExchangeSimulator(mode="success")
    captured: dict[str, object] = {}

    async def fake_post_signed_json(
        url: str, payload: dict[str, object], timeout_seconds: float = 10.0
    ) -> dict[str, object]:
        captured["url"] = url
        captured["payload"] = payload
        captured["timeout_seconds"] = timeout_seconds
        return {"code": 1000}

    monkeypatch.setattr(rack_exchange_mock, "post_signed_json", fake_post_signed_json)
    monkeypatch.setattr(rack_exchange_mock, "EXECUTION_TIME", 0)

    request = rack_exchange_mock.RackExchangeRequest(
        dispatch_key="external:smt_classifier:trace-mixed-rack:RACK_OPERATION",
        trace_id="trace-mixed-rack",
        actions=[_rack_action("SUPPLY_EMPTY_RACK")],
    )

    await simulator.execute_request(request)

    callback_payload = captured["payload"]
    assert isinstance(callback_payload, dict)
    active_bin_rack = callback_payload["active_bin_rack"]
    assert isinstance(active_bin_rack, dict)
    cells = active_bin_rack["cells"]
    assert isinstance(cells, list)
    assert len(cells) == 18

    cells_by_bin: dict[str, list[dict[str, object]]] = {}
    for cell in cells:
        assert isinstance(cell, dict)
        cells_by_bin.setdefault(str(cell["bin_id"]), []).append(cell)
        assert cell["status"] == "EMPTY"

    six_cell_bins = [bin_id for bin_id, bin_cells in cells_by_bin.items() if bin_cells[0]["bin_type"] == "6格箱"]
    three_cell_bins = [bin_id for bin_id, bin_cells in cells_by_bin.items() if bin_cells[0]["bin_type"] == "3格箱"]
    assert len(six_cell_bins) == 2
    assert len(three_cell_bins) == 2
    assert all(
        {str(cell["bin_cell_index"]) for cell in cells_by_bin[bin_id]} == {"1", "2", "3", "4", "5", "6"}
        for bin_id in six_cell_bins
    )
    assert all(
        {str(cell["bin_cell_index"]) for cell in cells_by_bin[bin_id]} == {"1", "2", "7"} for bin_id in three_cell_bins
    )
    assert sum(1 for cell in cells if cell["cell_type"] == "LARGE") == 2


@pytest.mark.asyncio
async def test_rack_exchange_mock_uses_large_three_cell_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RACK_EXCHANGE_PROFILE", "large_three_cell")
    simulator = rack_exchange_mock.RackExchangeSimulator(mode="success")
    captured: dict[str, object] = {}

    async def fake_post_signed_json(
        url: str, payload: dict[str, object], timeout_seconds: float = 10.0
    ) -> dict[str, object]:
        captured["url"] = url
        captured["payload"] = payload
        captured["timeout_seconds"] = timeout_seconds
        return {"code": 1000}

    monkeypatch.setattr(rack_exchange_mock, "post_signed_json", fake_post_signed_json)
    monkeypatch.setattr(rack_exchange_mock, "EXECUTION_TIME", 0)

    request = rack_exchange_mock.RackExchangeRequest(
        dispatch_key="external:smt_classifier:trace-large-cell:RACK_OPERATION",
        trace_id="trace-large-cell",
        actions=[_rack_action("SUPPLY_EMPTY_RACK")],
    )

    await simulator.execute_request(request)

    callback_payload = captured["payload"]
    assert isinstance(callback_payload, dict)
    active_bin_rack = callback_payload["active_bin_rack"]
    assert isinstance(active_bin_rack, dict)
    cell = active_bin_rack["cells"][0]
    assert cell["bin_type"] == "3格箱"
    assert cell["bin_cell_location"] == "BIN-MOCK-001-7"
    assert cell["bin_cell_index"] == "7"
    assert cell["cell_type"] == "LARGE"


@pytest.mark.asyncio
async def test_rack_exchange_mock_uses_seven_inch_six_cell_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RACK_EXCHANGE_PROFILE", "seven_inch_six_cell")
    simulator = rack_exchange_mock.RackExchangeSimulator(mode="success")
    captured: dict[str, object] = {}

    async def fake_post_signed_json(
        url: str, payload: dict[str, object], timeout_seconds: float = 10.0
    ) -> dict[str, object]:
        captured["url"] = url
        captured["payload"] = payload
        captured["timeout_seconds"] = timeout_seconds
        return {"code": 1000}

    monkeypatch.setattr(rack_exchange_mock, "post_signed_json", fake_post_signed_json)
    monkeypatch.setattr(rack_exchange_mock, "EXECUTION_TIME", 0)

    request = rack_exchange_mock.RackExchangeRequest(
        dispatch_key="external:smt_classifier:trace-seven-inch:RACK_OPERATION",
        trace_id="trace-seven-inch",
        actions=[_rack_action("SUPPLY_EMPTY_RACK")],
    )

    await simulator.execute_request(request)

    callback_payload = captured["payload"]
    assert isinstance(callback_payload, dict)
    active_bin_rack = callback_payload["active_bin_rack"]
    assert isinstance(active_bin_rack, dict)
    cell = active_bin_rack["cells"][0]
    assert cell["bin_type"] == "6格箱"
    assert cell["bin_cell_location"] == "BIN-MOCK-001-4"
    assert cell["bin_cell_index"] == "4"
    assert cell["cell_type"] == "SEVEN_INCH"


@pytest.mark.asyncio
async def test_rack_exchange_mock_callbacks_wms_rack_arrived(monkeypatch: pytest.MonkeyPatch) -> None:
    simulator = rack_exchange_mock.RackExchangeSimulator(mode="success")
    captured: dict[str, object] = {}

    async def fake_post_signed_json(
        url: str, payload: dict[str, object], timeout_seconds: float = 10.0
    ) -> dict[str, object]:
        captured["url"] = url
        captured["payload"] = payload
        captured["timeout_seconds"] = timeout_seconds
        return {"code": 1000}

    monkeypatch.setattr(rack_exchange_mock, "post_signed_json", fake_post_signed_json)
    monkeypatch.setattr(rack_exchange_mock, "EXECUTION_TIME", 0)

    request = rack_exchange_mock.RackExchangeRequest(
        request_type="SMT_RACK_OPERATION",
        dispatch_key="external:smt_classifier:trace-rack-mock:RACK_OPERATION",
        trace_id="trace-rack-mock",
        material={
            "PkgID": "PKG-RACK-MOCK-001",
            "HHPN": "620100L00-011-G",
            "MfrPN": "CC0402JRNPO9BN220",
            "DateCode": "122625",
            "LotCode": "8904936031",
        },
        actions=[_rack_action("MOVE_OUT_ACTIVE_RACK"), _rack_action("SUPPLY_EMPTY_RACK")],
        resume_callback_type="WMS_RACK_ARRIVED",
    )

    record = await simulator.execute_request(request)

    assert record.callback_type == "WMS_RACK_ARRIVED"
    assert record.result == "SUCCESS"
    assert captured["url"] == rack_exchange_mock.WES_EXTERNAL_CALLBACK_URL
    callback_payload = captured["payload"]
    assert isinstance(callback_payload, dict)
    assert callback_payload["callback_type"] == "WMS_RACK_ARRIVED"
    assert callback_payload["source_system"] == "WMS"
    assert callback_payload["dispatch_key"] == request.dispatch_key
    assert callback_payload["trace_id"] == "trace-rack-mock"
    active_bin_rack = callback_payload["active_bin_rack"]
    assert isinstance(active_bin_rack, dict)
    cell = active_bin_rack["cells"][0]
    assert cell["bin_id"].startswith("BIN-")
    assert cell["rack_slot_code"] in {"A", "B", "C", "D"}
    assert cell["rack_slot_location_code"].startswith(f"{cell['rack_id']}-1")
    assert cell["bin_orientation_code"] == f"{cell['bin_id']}-A"
    assert cell["bin_cell_location"].startswith(f"{cell['bin_id']}-")
    assert cell["bin_cell_index"] == cell["bin_cell_location"].rsplit("-", 1)[-1]
    assert cell["status"] == "EMPTY"


@pytest.mark.asyncio
async def test_rack_exchange_mock_progress_mode_keeps_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    simulator = rack_exchange_mock.RackExchangeSimulator(mode="progress")
    captured: dict[str, object] = {}

    async def fake_post_signed_json(url: str, payload: dict[str, object], timeout_seconds: float = 10.0) -> dict:
        captured["url"] = url
        captured["payload"] = payload
        captured["timeout_seconds"] = timeout_seconds
        return {"code": 1000}

    monkeypatch.setattr(rack_exchange_mock, "post_signed_json", fake_post_signed_json)
    monkeypatch.setattr(rack_exchange_mock, "EXECUTION_TIME", 0)

    request = rack_exchange_mock.RackExchangeRequest(
        request_type="SMT_RACK_OPERATION",
        dispatch_key="external:smt_classifier:trace-rack-progress:RACK_OPERATION",
        trace_id="trace-rack-progress",
        material={"PkgID": "PKG-RACK-MOCK-002"},
        actions=[_rack_action("MOVE_OUT_ACTIVE_RACK")],
        resume_callback_type="WMS_RACK_ARRIVED",
    )

    record = await simulator.execute_request(request)

    assert record.callback_type == "WMS_RACK_EXCHANGE_PROGRESS"
    assert record.result == "IN_PROGRESS"
    callback_payload = captured["payload"]
    assert isinstance(callback_payload, dict)
    assert callback_payload["callback_type"] == "WMS_RACK_EXCHANGE_PROGRESS"
    assert callback_payload["status"] == "IN_PROGRESS"
