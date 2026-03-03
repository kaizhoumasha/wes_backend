"""设备处理器模块 (Device Processors Module)

提供设备事件处理的策略接口和内置处理器实现。
支持插件化架构，第三方供应商可以扩展新的设备处理器。

架构:
- base.py: DeviceProcessor 抽象接口和 BaseDeviceProcessor 基类
- registry.py: DeviceProcessorRegistry 注册表
- builtin/: 内置设备处理器实现

使用示例:
    from src.device_processors import DeviceProcessorRegistry, register_builtin_processors

    # 注册内置处理器
    register_builtin_processors()

    # 获取处理器
    processor = DeviceProcessorRegistry.get_processor("ROBOTIC_ARM")
    if processor:
        await processor.validate_event(event_data)

第三方扩展示例:
    from src.device_processors.base import DeviceProcessor
    from src.device_processors.registry import DeviceProcessorRegistry

    class CustomProcessor(DeviceProcessor):
        async def validate_event(self, event_data: dict) -> tuple[bool, str | None]:
            # 自定义验证逻辑
            return True, None

        # ... 实现其他方法

    # 注册自定义处理器
    DeviceProcessorRegistry.register("CUSTOM_TYPE", CustomProcessor())
"""

from src.device_processors.base import (
    BaseDeviceProcessor,
    DeviceProcessor,
)
from src.device_processors.registry import (
    DeviceProcessorRegistry,
    register_builtin_processors,
)

__all__ = [
    "BaseDeviceProcessor",
    "DeviceProcessor",
    "DeviceProcessorRegistry",
    "register_builtin_processors",
]
