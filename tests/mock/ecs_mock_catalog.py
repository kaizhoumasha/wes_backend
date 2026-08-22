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
    contract_key: str
    contract_version: str
    supported_commands: tuple[str, ...] = field(default_factory=tuple)
    supported_events: tuple[str, ...] = field(default_factory=tuple)


MOCK_ECS_DEVICES: dict[str, MockEcsDevice] = {
    "CAMERA-CONVEYOR-01": MockEcsDevice(
        device_code="CAMERA-CONVEYOR-01",
        device_name="流水线识别点摄像头",
        device_type="CAMERA",
        role="CAMERA",
        contract_key="camera.scan",
        contract_version="2.0",
        supported_events=("MATERIAL_ARRIVED", "SCAN_COMPLETED"),
    ),
    "ROBOT-ARM-01": MockEcsDevice(
        device_code="ROBOT-ARM-01",
        device_name="搬运机械臂",
        device_type="ROBOTIC_ARM",
        role="ROBOT_ARM",
        contract_key="arm.pick",
        contract_version="2.0",
        supported_commands=("PICK_AND_PLACE", "MOVE"),
    ),
    "RS-MOCK-PLACEMENT-01": MockEcsDevice(
        device_code="RS-MOCK-PLACEMENT-01",
        device_name="粗分拣放置设备",
        device_type="ROUGH_SORTER_PLACEMENT",
        role="PLACEMENT_DEVICE",
        contract_key="rough_sorter.placement_device",
        contract_version="1.0",
        supported_commands=("PICK_AND_PUT",),
    ),
    "STATION_SCAN1": MockEcsDevice(
        device_code="STATION_SCAN1",
        device_name="SMT 流水线扫描工位 1",
        device_type="SCANNER",
        role="SCAN_STATION",
        contract_key="third_party_integration",
        contract_version="1.1",
        supported_commands=("MOVE_FORWARD", "MOVE_BACKWARD", "MOVE_LEFT", "MOVE_RIGHT"),
        supported_events=("SCAN_COMPLETED",),
    ),
}


def default_success_data(device_code: str, task_type: str, params: dict[str, Any]) -> dict[str, Any]:
    """生成命令成功回调的默认业务数据。"""

    return {
        "device_code": device_code,
        "task_type": task_type,
        "accepted_params": params,
    }
