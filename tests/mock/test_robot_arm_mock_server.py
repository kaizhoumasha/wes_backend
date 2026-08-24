from __future__ import annotations

import asyncio

import pytest

from tests.mock import robot_arm_mock_server


@pytest.mark.asyncio
async def test_receive_command_returns_uniform_ack(monkeypatch: pytest.MonkeyPatch) -> None:
    async def finish_without_callback(_payload: robot_arm_mock_server.DeviceCommandPayload) -> None:
        robot_arm_mock_server.DEVICE_INFO["status"] = "IDLE"
        robot_arm_mock_server.current_command = None

    monkeypatch.setattr(robot_arm_mock_server, "_execute_wes_command_with_cleanup", finish_without_callback)
    robot_arm_mock_server.DEVICE_INFO["status"] = "IDLE"
    robot_arm_mock_server.current_command = None

    ack = await robot_arm_mock_server.receive_command(
        robot_arm_mock_server.DeviceCommandPayload(
            command_code="CMD-ACK-001",
            task_type="MOVE_FORWARD",
            priority=1,
            timeout=30_000,
            params={},
            timestamp=1_787_606_391_278,
        )
    )
    await asyncio.sleep(0)

    assert ack.model_dump(exclude_none=True) == {
        "code": 200,
        "message": "ACK",
        "trace_id": "ROBOT-LOG-001",
    }
