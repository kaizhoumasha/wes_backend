"""
设备处理器注册表 (Device Processor Registry)

实现设备处理器的注册和获取机制，支持插件化架构。
新设备类型通过注册表动态添加，无需修改核心代码。

设计模式:
- 注册表模式 (Registry Pattern): 集中管理设备处理器
- 单例模式 (Singleton): 全局唯一的注册表实例

使用示例:
    # 1. 注册设备处理器
    DeviceProcessorRegistry.register("ROBOTIC_ARM", RoboticArmProcessor())

    # 2. 获取设备处理器
    processor = DeviceProcessorRegistry.get_processor("ROBOTIC_ARM")

    # 3. 列出所有支持的设备类型
    types = DeviceProcessorRegistry.list_supported_types()
"""

from typing import Any, ClassVar

from loguru import logger

from src.app.device.models.device import DeviceType
from src.device_processors.base import DeviceProcessor


class DeviceProcessorRegistry:
    """
    设备处理器注册表

    管理所有设备处理器的注册、获取和列举。
    使用类变量实现单例模式。

    Attributes:
        _processors: 设备处理器字典 {device_type: processor}
    """

    # 类变量 - 单例模式
    _processors: ClassVar[dict[str, DeviceProcessor]] = {}

    @classmethod
    def register(cls, device_type: str, processor: DeviceProcessor, overwrite: bool = False) -> None:
        """
        注册设备处理器

        Args:
            device_type: 设备类型（如 "ROBOTIC_ARM"）
            processor: 设备处理器实例
            overwrite: 是否覆盖已存在的处理器

        Raises:
            ValueError: 当设备类型已存在且 overwrite=False 时

        Example:
            >>> registry = DeviceProcessorRegistry()
            >>> registry.register("ROBOTIC_ARM", RoboticArmProcessor())
            >>> # 或者
            >>> DeviceProcessorRegistry.register("ROBOTIC_ARM", RoboticArmProcessor())
        """
        if device_type in cls._processors and not overwrite:
            raise ValueError(
                f"设备类型 '{device_type}' 的处理器已存在: "
                f"{cls._processors[device_type].__class__.__name__}。"
                f"如需覆盖，请设置 overwrite=True"
            )

        cls._processors[device_type] = processor
        logger.info(f"注册设备处理器: {device_type} -> {processor.__class__.__name__}")

    @classmethod
    def get_processor(cls, device_type: str) -> DeviceProcessor | None:
        """
        获取设备处理器

        Args:
            device_type: 设备类型

        Returns:
            设备处理器实例，如果不存在返回 None

        Example:
            >>> processor = DeviceProcessorRegistry.get_processor("ROBOTIC_ARM")
            >>> if processor:
            ...     await processor.validate_event(event_data)
        """
        processor = cls._processors.get(device_type)
        if processor is None:
            logger.warning(f"未找到设备类型 '{device_type}' 的处理器")
        return processor

    @classmethod
    def get_processor_or_raise(cls, device_type: str) -> DeviceProcessor:
        """
        获取设备处理器（不存在时抛出异常）

        Args:
            device_type: 设备类型

        Returns:
            设备处理器实例

        Raises:
            ValueError: 当设备类型不存在时

        Example:
            >>> try:
            ...     processor = DeviceProcessorRegistry.get_processor_or_raise("ROBOTIC_ARM")
            ...     await processor.validate_event(event_data)
            ... except ValueError as e:
            ...     logger.error(f"处理器未找到: {e}")
        """
        processor = cls.get_processor(device_type)
        if processor is None:
            raise ValueError(f"设备类型 '{device_type}' 的处理器未注册。支持的类型: {cls.list_supported_types()}")
        return processor

    @classmethod
    def is_supported(cls, device_type: str) -> bool:
        """
        检查设备类型是否支持

        Args:
            device_type: 设备类型

        Returns:
            如果支持返回 True，否则返回 False

        Example:
            >>> if DeviceProcessorRegistry.is_supported("ROBOTIC_ARM"):
            ...     print("支持机械臂设备")
        """
        return device_type in cls._processors

    @classmethod
    def list_supported_types(cls) -> list[str]:
        """
        列出所有支持的设备类型

        Returns:
            设备类型列表

        Example:
            >>> types = DeviceProcessorRegistry.list_supported_types()
            >>> print(f"支持的设备类型: {types}")
        """
        return list(cls._processors.keys())

    @classmethod
    def list_processors(cls) -> dict[str, str]:
        """
        列出所有设备处理器及其类型

        Returns:
            {device_type: processor_class_name} 字典

        Example:
            >>> processors = DeviceProcessorRegistry.list_processors()
            >>> for device_type, processor_name in processors.items():
            ...     print(f"{device_type}: {processor_name}")
        """
        return {device_type: processor.__class__.__name__ for device_type, processor in cls._processors.items()}

    @classmethod
    def unregister(cls, device_type: str) -> bool:
        """
        注销设备处理器

        Args:
            device_type: 设备类型

        Returns:
            如果成功注销返回 True，否则返回 False

        Example:
            >>> if DeviceProcessorRegistry.unregister("ROBOTIC_ARM"):
            ...     print("处理器已注销")
        """
        if device_type in cls._processors:
            del cls._processors[device_type]
            logger.info(f"注销设备处理器: {device_type}")
            return True
        return False

    @classmethod
    def clear(cls) -> None:
        """
        清空所有设备处理器

        主要用于测试场景。

        Example:
            >>> DeviceProcessorRegistry.clear()
        """
        cls._processors.clear()
        logger.info("清空所有设备处理器注册")

    @classmethod
    def get_registry_info(cls) -> dict[str, Any]:
        """
        获取注册表信息

        Returns:
            包含注册表统计信息的字典

        Example:
            >>> info = DeviceProcessorRegistry.get_registry_info()
            >>> print(f"已注册处理器数量: {info['total_processors']}")
        """
        return {
            "total_processors": len(cls._processors),
            "supported_types": cls.list_supported_types(),
            "processors": cls.list_processors(),
        }


# ==================== 预定义设备类型注册 ====================


def register_builtin_processors() -> None:
    """
    注册内置设备处理器

    在应用启动时调用，注册所有预定义的设备处理器。
    """
    from src.device_processors.builtin.conveyor import ConveyorProcessor
    from src.device_processors.builtin.labeler import LabelerProcessor
    from src.device_processors.builtin.robotic_arm import RoboticArmProcessor

    # 注册机械臂处理器
    DeviceProcessorRegistry.register(DeviceType.ROBOTIC_ARM.value, RoboticArmProcessor())

    # 注册输送线处理器
    DeviceProcessorRegistry.register(DeviceType.CONVEYOR.value, ConveyorProcessor())

    # 注册贴标机处理器
    DeviceProcessorRegistry.register(DeviceType.LABELER.value, LabelerProcessor())

    logger.info(f"内置设备处理器注册完成: {DeviceProcessorRegistry.list_supported_types()}")


# ==================== 导出 ====================


__all__ = [
    "DeviceProcessorRegistry",
    "register_builtin_processors",
]
