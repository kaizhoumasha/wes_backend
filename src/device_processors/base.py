"""
设备处理器策略接口 (Device Processor Strategy Interface)

定义设备处理器的抽象接口，实现策略模式。
不同设备类型通过实现此接口来提供特定的业务处理逻辑。

设计模式:
- 策略模式 (Strategy Pattern): 不同设备类型使用不同的处理策略
- 模板方法模式 (Template Method): SDAF 流程定义在基类中，具体步骤由子类实现

SDAF 控制循环:
    Sense (感知) -> Decide (决策) -> Act (执行) -> Feedback (反馈)
"""

from abc import ABC, abstractmethod

from loguru import logger

from src.app.device.models.command import (
    CommandRequest,
)

# ==================== 设备处理器策略接口 ====================


class DeviceProcessor(ABC):
    """
    设备处理器策略接口

    定义设备事件处理的标准流程（SDAF 控制循环）。
    每个设备类型需要实现此接口来提供特定的业务逻辑。

    SDAF 流程:
        1. validate_event(): 感知 (Sense) - 验证事件数据
        2. decide_action(): 决策 (Decide) - 决定执行动作
        3. build_command(): 执行 (Act) - 构建指令请求
        4. handle_result(): 反馈 (Feedback) - 处理执行结果

    使用示例:
        class RoboticArmProcessor(DeviceProcessor):
            async def validate_event(self, event_data: dict) -> tuple[bool, str | None]:
                # 验证事件数据
                barcode = event_data.get("barcode")
                if not barcode:
                    return False, "缺少 barcode 字段"
                return True, None

            async def decide_action(self, event_data: dict) -> dict | None:
                # 决策逻辑
                return {
                    "task_type": "PICK_AND_PLACE",
                    "source_loc": event_data["location"],
                    "target_loc": "SHELF-A-01",
                }

            # ... 其他方法实现
    """

    def __init__(self, device_type: str):
        """
        初始化设备处理器

        Args:
            device_type: 设备类型（如 "ROBOTIC_ARM"）
        """
        self.device_type = device_type

    # ==================== SDAF 控制循环方法 ====================

    @abstractmethod
    async def validate_event(
        self, event_data: dict
    ) -> tuple[bool, str | None]:
        """
        验证事件数据 (Sense - 感知)

        检查事件数据是否完整和有效。

        Args:
            event_data: 事件数据字典

        Returns:
            (是否有效, 错误消息) 元组

        Example:
            >>> async def validate_event(self, event_data: dict) -> tuple[bool, str | None]:
            ...     barcode = event_data.get("barcode")
            ...     if not barcode:
            ...         return False, "缺少 barcode 字段"
            ...     return True, None
        """

    @abstractmethod
    async def decide_action(self, event_data: dict) -> dict | None:
        """
        决策执行动作 (Decide - 决策)

        根据事件数据决定需要执行的动作。
        如果不需要执行动作，返回 None。

        Args:
            event_data: 事件数据字典

        Returns:
            动作参数字典，如果不需要动作返回 None

        Example:
            >>> async def decide_action(self, event_data: dict) -> dict | None:
            ...     if event_data.get("event_type") == "MATERIAL_ARRIVED":
            ...         return {
            ...             "task_type": "PICK_AND_PLACE",
            ...             "source_loc": event_data["location"],
            ...             "target_loc": "SHELF-A-01",
            ...         }
            ...     return None
        """

    @abstractmethod
    async def build_command(
        self, action_params: dict, correlation_id: str | None = None
    ) -> CommandRequest:
        """
        构建指令请求 (Act - 执行)

        根据动作参数构建符合白皮书规范的指令请求。

        Args:
            action_params: 动作参数字典（来自 decide_action）
            correlation_id: 关联 ID（串联整个流程）

        Returns:
            CommandRequest 指令请求对象

        Example:
            >>> async def build_command(
            ...     self,
            ...     action_params: dict,
            ...     correlation_id: str | None = None
            ... ) -> CommandRequest:
            ...     return CommandRequest(
            ...         device_id="ROBOT-ARM-01",
            ...         task_type="PICK_AND_PLACE",
            ...         params=action_params,
            ...         correlation_id=correlation_id,
            ...     )
        """

    @abstractmethod
    async def handle_result(self, result_data: dict) -> dict | None:
        """
        处理执行结果 (Feedback - 反馈)

        处理设备返回的执行结果，决定是否需要后续动作。
        如果有后续动作，返回动作参数；否则返回 None。

        Args:
            result_data: 结果数据字典

        Returns:
            后续动作参数字典，如果不需要后续动作返回 None

        Example:
            >>> async def handle_result(self, result_data: dict) -> dict | None:
            ...     if result_data.get("result") == "SUCCESS":
            ...         # 执行成功，触发下一步流程
            ...         return {
            ...             "task_type": "SCAN",
            ...             "barcode": result_data["data"]["barcode"],
            ...         }
            ...     return None
        """

    @abstractmethod
    async def handle_error(self, error: Exception, context: dict) -> None:
        """
        处理错误情况

        当处理过程中发生错误时调用。

        Args:
            error: 异常对象
            context: 错误上下文（包含 event_data、command_code 等）

        Example:
            >>> async def handle_error(self, error: Exception, context: dict) -> None:
            ...     logger.error(f"处理设备事件失败: {error}")
            ...     # 发送告警通知
            ...     await self.send_alert(error, context)
        """

    # ==================== 辅助方法 ====================

    def get_device_type(self) -> str:
        """获取设备类型"""
        return self.device_type

    def get_processor_name(self) -> str:
        """获取处理器名称"""
        return self.__class__.__name__


# ==================== 设备处理器基类（提供默认实现） ====================


class BaseDeviceProcessor(DeviceProcessor):
    """
    设备处理器基类

    提供 SDAF 流程的默认实现，子类只需实现业务逻辑部分。
    """

    def __init__(self, device_type: str):
        """
        初始化设备处理器

        Args:
            device_type: 设备类型
        """
        super().__init__(device_type)

    async def validate_event(
        self, event_data: dict
    ) -> tuple[bool, str | None]:
        """
        默认事件验证实现

        检查必需字段: device_code, event_type, timestamp

        Args:
            event_data: 事件数据字典

        Returns:
            (是否有效, 错误消息) 元组
        """
        required_fields = ["device_code", "event_type", "timestamp"]
        for field in required_fields:
            if field not in event_data:
                return False, f"缺少必需字段: {field}"

        return True, None

    async def decide_action(self, _event_data: dict) -> dict | None:
        """
        默认决策实现

        子类应该重写此方法来实现具体的业务逻辑。

        Args:
            _event_data: 事件数据字典（未使用，子类重写）

        Returns:
            动作参数字典，如果不需要动作返回 None
        """
        logger.warning(f"{self.get_processor_name()} 未实现 decide_action 方法")
        return None

    async def build_command(
        self, action_params: dict, correlation_id: str | None = None
    ) -> CommandRequest:
        """
        默认指令构建实现

        Args:
            action_params: 动作参数字典
            correlation_id: 关联 ID

        Returns:
            CommandRequest 指令请求对象
        """
        # 子类应该重写此方法
        # 注意：build_command 仅接收内部已解析完成的数据（必须包含 device_id）
        return CommandRequest(
            device_id=action_params.get("device_id", 0),
            task_type=action_params.get("task_type", "PROCESS"),
            params=action_params.get("params", {}),
            correlation_id=correlation_id,
        )

    async def handle_result(self, result_data: dict) -> dict | None:
        """
        默认结果处理实现

        Args:
            result_data: 结果数据字典（未使用，子类重写）

        Returns:
            后续动作参数字典，如果不需要后续动作返回 None
        """
        # 默认不进行后续动作
        _ = result_data  # 子类重写时使用
        return None

    async def handle_error(self, error: Exception, context: dict) -> None:
        """
        默认错误处理实现

        Args:
            error: 异常对象
            context: 错误上下文
        """
        logger.error(
            f"{self.get_processor_name()} 处理错误: {error}",
            extra={"context": context},
        )


# ==================== 导出 ====================


__all__ = [
    "BaseDeviceProcessor",
    "DeviceProcessor",
]
