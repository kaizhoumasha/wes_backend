"""内置设备处理器 (Builtin Device Processors)

提供系统预定义的设备处理器实现。
这些处理器处理常见设备类型的业务逻辑。

可用处理器:
- RoboticArmProcessor: 机械臂设备处理器
- ConveyorProcessor: 输送线设备处理器
- LabelerProcessor: 贴标机设备处理器
"""

from src.device_processors.builtin.conveyor import ConveyorProcessor
from src.device_processors.builtin.labeler import LabelerProcessor
from src.device_processors.builtin.robotic_arm import RoboticArmProcessor

__all__ = [
    "ConveyorProcessor",
    "LabelerProcessor",
    "RoboticArmProcessor",
]
