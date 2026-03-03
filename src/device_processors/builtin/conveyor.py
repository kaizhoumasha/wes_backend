"""
输送线设备处理器 (Conveyor Processor)

处理输送线相关的事件和指令。

典型场景:
- 物料到位 -> 触发扫码或抓取
- 输送完成 -> 通知下游设备
- 设备故障 -> 告警并停止输送
"""

import uuid

from loguru import logger

from src.app.device.models.command import (
    CommandRequest,
    TaskType,
)
from src.app.device.models.event_log import EventType
from src.device_processors.base import BaseDeviceProcessor


class ConveyorProcessor(BaseDeviceProcessor):
    """
    输送线设备处理器

    处理输送线相关的业务逻辑：
    1. MATERIAL_ARRIVED: 物料到位 -> 触发扫码或等待抓取
    2. DEVICE_ERROR: 设备故障 -> 告警并停止
    3. DEVICE_ONLINE: 设备上线 -> 恢复输送
    """

    def __init__(self):
        """初始化输送线处理器"""
        super().__init__(device_type="CONVEYOR")
        self.default_device_id = "CONVEYOR-01"  # 默认输送线设备 ID

    async def validate_event(
        self, event_data: dict
    ) -> tuple[bool, str | None]:
        """
        验证输送线事件数据

        验证规则:
        - MATERIAL_ARRIVED: 必须包含 location
        - DEVICE_ERROR: 必须包含 error_code
        - DEVICE_ONLINE: 无额外要求

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
            # 物料到位事件：必须有位置信息
            if "location" not in data:
                return False, "MATERIAL_ARRIVED 事件缺少 location 字段"
        elif event_type == EventType.DEVICE_ERROR.value and "error_code" not in data:
            # 设备故障事件：必须有错误码
            return False, "DEVICE_ERROR 事件缺少 error_code 字段"

        return True, None

    async def decide_action(self, event_data: dict) -> dict | None:
        """
        决策执行动作

        业务规则:
        1. MATERIAL_ARRIVED -> 通知机械臂抓取或触发扫码
        2. DEVICE_ERROR -> 停止输送，告警
        3. DEVICE_ONLINE -> 恢复正常运行

        Args:
            event_data: 事件数据字典

        Returns:
            动作参数字典，如果不需要动作返回 None
        """
        event_type = event_data.get("event_type")
        data = event_data.get("data", {})

        if event_type == EventType.MATERIAL_ARRIVED.value:
            # 物料到位：通知机械臂抓取
            location = data.get("location")
            barcode = data.get("barcode")

            logger.info(f"输送线决策: 物料到位 {location} -> 通知机械臂抓取")

            # 决策：通知机械臂抓取
            return {
                "device_id": "ROBOT-ARM-01",  # 假设机械臂在输送线旁边
                "task_type": TaskType.PICK.value,
                "params": {
                    "source_loc": location,
                    "barcode": barcode,
                },
            }

        if event_type == EventType.DEVICE_ERROR.value:
            # 设备故障：停止输送，告警
            error_code = data.get("error_code")
            logger.error(f"输送线故障: {error_code} -> 停止输送")

            # 返回停止动作
            return {
                "device_id": self.default_device_id,
                "task_type": "STOP",
                "params": {
                    "error_code": error_code,
                    "reason": "设备故障，紧急停止",
                },
            }

        if event_type == EventType.DEVICE_ONLINE.value:
            # 设备上线：恢复输送
            logger.info("输送线上线 -> 恢复运行")

            return {
                "device_id": self.default_device_id,
                "task_type": "START",
                "params": {
                    "reason": "设备上线，恢复运行",
                },
            }

        # 其他事件类型：不执行动作
        logger.warning(f"输送线未处理的事件类型: {event_type}")
        return None

    async def build_command(
        self, action_params: dict, correlation_id: str | None = None
    ) -> CommandRequest:
        """
        构建输送线指令请求

        Args:
            action_params: 动作参数字典
            correlation_id: 关联 ID

        Returns:
            CommandRequest 指令请求对象
        """
        device_id = action_params.get("device_id", self.default_device_id)
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

        # 输送线任务优先级较低
        return CommandRequest(
            device_id=device_id,
            task_type=task_type,
            priority=3,  # 输送线任务中等优先级
            timeout_ms=60000,  # 60 秒超时
            params=params,
            correlation_id=correlation_id,
        )

    async def handle_result(self, result_data: dict) -> dict | None:
        """
        处理输送线执行结果

        结果处理:
        1. SUCCESS -> 输送完成，通知下游
        2. FAILED -> 记录错误，告警

        Args:
            result_data: 结果数据字典

        Returns:
            后续动作参数字典，如果不需要后续动作返回 None
        """
        result = result_data.get("result")

        if result == "SUCCESS":
            # 输送成功，流程结束
            logger.info("输送线执行成功")
            return None
        # 输送失败，记录错误
        error_detail = result_data.get("error_detail", {})
        logger.error(f"输送线执行失败: {error_detail}")
        return None

    async def handle_error(self, error: Exception, context: dict) -> None:
        """
        处理输送线错误

        Args:
            error: 异常对象
            context: 错误上下文
        """
        logger.error(
            f"输送线处理错误: {error}",
            extra={
                "processor": self.get_processor_name(),
                "context": context,
            },
        )

        # TODO: 发送告警通知
        # await self.send_alert(error, context)


# ==================== 导出 ====================


__all__ = [
    "ConveyorProcessor",
]
