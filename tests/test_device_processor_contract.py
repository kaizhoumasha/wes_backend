import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.device_processors.builtin.conveyor import ConveyorProcessor


@pytest.mark.asyncio
async def test_conveyor_event_validation_uses_device_code() -> None:
    processor = ConveyorProcessor()

    is_valid, error_msg = await processor.validate_event(
        {
            "device_code": "CAMERA-CONVEYOR-01",
            "event_type": "MATERIAL_ARRIVED",
            "timestamp": 1700000000000,
            "data": {"location": "CONVEYOR-STATION-01"},
        }
    )

    assert is_valid is True
    assert error_msg is None


@pytest.mark.asyncio
async def test_conveyor_decide_action_returns_device_code() -> None:
    processor = ConveyorProcessor()

    action = await processor.decide_action(
        {
            "device_code": "CAMERA-CONVEYOR-01",
            "event_type": "MATERIAL_ARRIVED",
            "timestamp": 1700000000000,
            "data": {"location": "CONVEYOR-STATION-01", "barcode": "PKG-TEST-001"},
        }
    )

    assert isinstance(action, dict)
    assert action["device_code"] == "ROBOT-ARM-01"
    assert "device_id" not in action


@pytest.mark.asyncio
async def test_build_command_requires_internal_device_id() -> None:
    processor = ConveyorProcessor()

    with pytest.raises(ValueError, match="device_id"):
        await processor.build_command(
            {
                "device_code": "ROBOT-ARM-01",
                "task_type": "PICK",
                "params": {"source_loc": "CONVEYOR-STATION-01"},
            }
        )
