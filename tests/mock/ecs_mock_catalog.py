"""ECS Mock 设备目录。

该目录只描述 Mock 支持的外部 ECS 协议能力，不依赖插件运行时代码。
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class MockEcsDevice:
    """ECS Mock 中的一台受控设备。"""

    device_code: str
    device_name: str
    device_type: str
    role: str
    supported_commands: tuple[str, ...] = field(default_factory=tuple)
    supported_events: tuple[str, ...] = field(default_factory=tuple)


MOCK_ECS_DEVICES: dict[str, MockEcsDevice] = {
    "CAMERA-CONVEYOR-01": MockEcsDevice(
        device_code="CAMERA-CONVEYOR-01",
        device_name="流水线识别点摄像头",
        device_type="CAMERA",
        role="CAMERA",
        supported_events=("MATERIAL_ARRIVED", "SCAN_COMPLETED"),
    ),
    "ROBOT-ARM-01": MockEcsDevice(
        device_code="ROBOT-ARM-01",
        device_name="搬运机械臂",
        device_type="ROBOTIC_ARM",
        role="ROBOT_ARM",
        supported_commands=("PICK_AND_PLACE", "PICK_AND_PUT", "PICK_NG"),
    ),
    "RS-INPUT-ARM-01": MockEcsDevice(
        device_code="RS-INPUT-ARM-01",
        device_name="测试粗分机入料机械臂",
        device_type="ROBOTIC_ARM",
        role="ROUGH_SORTER_INPUT_ARM",
        supported_commands=("PICK_AND_PUT", "MOVE_TO_NG", "PICK_NG"),
        supported_events=("SCAN_COMPLETED", "ROUGH_SORTER_STORAGE_RETRY"),
    ),
    "RS-CONVEYOR-01": MockEcsDevice(
        device_code="RS-CONVEYOR-01",
        device_name="测试粗分机输送线",
        device_type="CONVEYOR",
        role="ROUGH_SORTER_CONVEYOR",
        supported_commands=("MOVE_FORWARD",),
    ),
    "RS-OUTPUT-ARM-01": MockEcsDevice(
        device_code="RS-OUTPUT-ARM-01",
        device_name="测试粗分机出料机械臂",
        device_type="ROBOTIC_ARM",
        role="ROUGH_SORTER_OUTPUT_ARM",
        supported_commands=("PUT_TO_BIN", "OUTPUT"),
    ),
}


def default_success_data(device_code: str, task_type: str, params: dict[str, Any]) -> dict[str, Any]:
    """生成命令成功回调的默认业务数据。"""

    data = {
        "device_code": device_code,
        "task_type": task_type,
        "accepted_params": params,
    }
    if device_code == "RS-INPUT-ARM-01" and task_type == "PICK_AND_PUT":
        data.update(
            {
                "reel_diameter": "178.0",
                "reel_thickness": "15.0",
                "measurement_result": "OK",
            }
        )
    return data
