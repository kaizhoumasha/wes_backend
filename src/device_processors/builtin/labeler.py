"""
贴标机设备处理器 (Labeler Processor)

处理贴标机相关的事件和指令。

典型场景:
- 物料到位 -> 打印并粘贴标签
- 贴标完成 -> 通知下游设备
- 标签缺纸 -> 告警并暂停贴标
"""

import uuid
from typing import Any

from loguru import logger

from src.app.device.models.command import (
    CommandRequest,
    TaskType,
)
from src.app.device.models.event_log import EventType
from src.device_processors.base import BaseDeviceProcessor


class LabelerProcessor(BaseDeviceProcessor):
    """
    贴标机设备处理器

    处理贴标机相关的业务逻辑：
    1. MATERIAL_ARRIVED: 物料到位 -> 打印并粘贴标签
    2. PROCESS_COMPLETED: 贴标完成 -> 通知下游
    3. DEVICE_ERROR: 设备故障 -> 告警并暂停
    """

    def __init__(self):
        """初始化贴标机处理器"""
        super().__init__(device_type="LABELER")
        self.default_device_code = "LABELER-01"  # 默认贴标机设备编码

    async def validate_event(self, event_data: dict[str, Any]) -> tuple[bool, str | None]:
        """
        验证贴标机事件数据

        验证规则:
        - MATERIAL_ARRIVED: 必须包含 location 和 barcode
        - PROCESS_COMPLETED: 必须包含 command_code
        - DEVICE_ERROR: 必须包含 error_code

        Args:
            event_data: 事件数据字典

        Returns:
            (是否有效, 错误消息) 元组
        """
        # 先调用基类验证
        is_valid, error_msg = await super().validate_event(event_data)
        if not is_valid:
            return False, error_msg

        event_type = event_data.get("event_type")
        data = event_data.get("data", {})

        # 根据事件类型进行特定验证
        if event_type == EventType.MATERIAL_ARRIVED.value:
            # 物料到位事件：必须有位置和条码
            if "location" not in data:
                return False, "MATERIAL_ARRIVED 事件缺少 location 字段"
            if "barcode" not in data:
                return False, "MATERIAL_ARRIVED 事件缺少 barcode 字段"

        elif event_type == EventType.PROCESS_COMPLETED.value:
            # 贴标完成事件：必须有指令 ID
            if "command_code" not in data:
                return False, "PROCESS_COMPLETED 事件缺少 command_code 字段"

        elif event_type == EventType.DEVICE_ERROR.value:
            # 设备故障事件：必须有错误码
            if "error_code" not in data:
                return False, "DEVICE_ERROR 事件缺少 error_code 字段"

        return True, None

    async def decide_action(self, event_data: dict[str, Any]) -> dict[str, Any] | None:
        """
        决策执行动作

        业务规则:
        1. MATERIAL_ARRIVED -> 打印并粘贴标签
        2. PROCESS_COMPLETED -> 通知下游设备
        3. DEVICE_ERROR -> 暂停贴标，告警

        Args:
            event_data: 事件数据字典

        Returns:
            动作参数字典，如果不需要动作返回 None
        """
        event_type = event_data.get("event_type")
        data = event_data.get("data", {})

        if event_type == EventType.MATERIAL_ARRIVED.value:
            # 物料到位：打印并粘贴标签
            location = data.get("location")
            barcode = data.get("barcode")

            logger.info(f"贴标机决策: 物料到位 {location} ({barcode}) -> 打印并粘贴标签")

            # 决策：执行贴标任务
            return {
                "device_code": self.default_device_code,
                "task_type": TaskType.PROCESS.value,
                "params": {
                    "location": location,
                    "barcode": barcode,
                    "label_type": "STANDARD",  # 标准标签
                    "copies": 1,  # 打印份数
                },
            }

        if event_type == EventType.PROCESS_COMPLETED.value:
            # 贴标完成：通知下游设备（如输送线继续输送）
            command_code = data.get("command_code")

            logger.info(f"贴标机决策: 贴标完成 {command_code} -> 通知下游")

            return {
                "device_code": "CONVEYOR-01",  # 假设下游是输送线
                "task_type": "START",
                "params": {
                    "reason": "贴标完成，继续输送",
                },
            }

        if event_type == EventType.DEVICE_ERROR.value:
            # 设备故障：暂停贴标，告警
            error_code = data.get("error_code")
            logger.error(f"贴标机故障: {error_code} -> 暂停贴标")

            return {
                "device_code": self.default_device_code,
                "task_type": "STOP",
                "params": {
                    "error_code": error_code,
                    "reason": "设备故障，暂停贴标",
                },
            }

        # 其他事件类型：不执行动作
        logger.warning(f"贴标机未处理的事件类型: {event_type}")
        return None

    async def build_command(
        self, action_params: dict[str, Any], correlation_id: str | None = None
    ) -> CommandRequest:
        """
        构建贴标机指令请求

        Args:
            action_params: 动作参数字典
            correlation_id: 关联 ID

        Returns:
            CommandRequest 指令请求对象
        """
        device_id = action_params.get("device_id")
        if not isinstance(device_id, int):
            raise TypeError("内部指令构建失败：缺少已解析的 device_id")
        task_type_str = action_params.get("task_type", "PROCESS")
        params = action_params.get("params", {})

        # 转换任务类型
        try:
            task_type = TaskType(task_type_str)
        except ValueError:
            # 非标准任务类型（如 START, STOP），使用 PROCESS
            task_type = TaskType.PROCESS

        # 如果没有指定 correlation_id，生成一个
        if correlation_id is None:
            correlation_id = str(uuid.uuid4())

        # 贴标机任务中等优先级
        return CommandRequest(
            device_id=device_id,
            task_type=task_type,
            priority=3,  # 贴标机任务中等优先级
            timeout_ms=15000,  # 15 秒超时（贴标较快）
            params=params,
            correlation_id=correlation_id,
        )

    async def handle_result(self, result_data: dict[str, Any]) -> dict[str, Any] | None:
        """
        处理贴标机执行结果

        结果处理:
        1. SUCCESS -> 贴标成功，记录标签信息
        2. FAILED -> 记录错误，告警

        Args:
            result_data: 结果数据字典

        Returns:
            后续动作参数字典，如果不需要后续动作返回 None
        """
        result = result_data.get("result")
        data = result_data.get("data", {})

        if result == "SUCCESS":
            # 贴标成功，记录标签信息
            label_id = data.get("label_id")
            barcode = data.get("barcode")

            logger.info(f"贴标机执行成功: label_id={label_id}, barcode={barcode}")

            # 流程结束，无需后续动作
            return None
        # 贴标失败，记录错误
        error_detail = result_data.get("error_detail", {})
        logger.error(f"贴标机执行失败: {error_detail}")

        # TODO: 判断是否需要重试
        return None

    async def handle_error(self, error: Exception, context: dict[str, Any]) -> None:
        """
        处理贴标机错误

        Args:
            error: 异常对象
            context: 错误上下文
        """
        logger.error(
            f"贴标机处理错误: {error}",
            extra={
                "processor": self.get_processor_name(),
                "context": context,
            },
        )

        # TODO: 发送告警通知
        # await self.send_alert(error, context)


# ==================== 导出 ====================


__all__ = [
    "LabelerProcessor",
]
