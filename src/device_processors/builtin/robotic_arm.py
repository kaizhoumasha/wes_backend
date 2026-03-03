"""
机械臂设备处理器 (Robotic Arm Processor)

处理机械臂相关的事件和指令。

典型场景:
- 料盘到达 -> 抓取并放置到货架
- 扫码完成 -> 根据结果决定下一步动作
- 抓取完成 -> 触发扫码或放置流程
"""

import uuid

from loguru import logger

from src.app.device.models.command import (
    CommandRequest,
    TaskType,
)
from src.app.device.models.event_log import EventType
from src.device_processors.base import BaseDeviceProcessor


class RoboticArmProcessor(BaseDeviceProcessor):
    """
    机械臂设备处理器

    处理机械臂相关的业务逻辑：
    1. MATERIAL_ARRIVED: 料盘到达 -> 抓取并放置
    2. PICK_COMPLETED: 抓取完成 -> 触发放置或扫码
    3. PUT_COMPLETED: 放置完成 -> 结束流程
    """

    def __init__(self):
        """初始化机械臂处理器"""
        super().__init__(device_type="ROBOTIC_ARM")
        self.default_device_id = "ROBOT-ARM-01"  # 默认机械臂设备 ID

    async def validate_event(
        self, event_data: dict
    ) -> tuple[bool, str | None]:
        """
        验证机械臂事件数据

        验证规则:
        - MATERIAL_ARRIVED: 必须包含 location 和可选的 barcode
        - PICK_COMPLETED: 必须包含 command_id
        - PUT_COMPLETED: 必须包含 command_id

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
            # 料盘到达事件：必须有位置信息
            if "location" not in data:
                return False, "MATERIAL_ARRIVED 事件缺少 location 字段"
        elif event_type in [
            EventType.PICK_COMPLETED.value,
            EventType.PUT_COMPLETED.value,
        ] and "command_id" not in data:
            # 任务完成事件：必须有 command_id
            return False, f"{event_type} 事件缺少 command_id 字段"

        return True, None

    async def decide_action(self, event_data: dict) -> dict | None:
        """
        决策执行动作

        业务规则:
        1. MATERIAL_ARRIVED -> 抓取并放置到固定货架
        2. PICK_COMPLETED -> 放置到目标位置
        3. PUT_COMPLETED -> 流程结束，无需后续动作

        Args:
            event_data: 事件数据字典

        Returns:
            动作参数字典，如果不需要动作返回 None
        """
        event_type = event_data.get("event_type")
        data = event_data.get("data", {})

        if event_type == EventType.MATERIAL_ARRIVED.value:
            # 料盘到达：执行抓取并放置
            source_loc = data.get("location")
            barcode = data.get("barcode")

            # 决策：固定目标位置（实际项目中应该通过业务规则引擎计算）
            target_loc = "SHELF-A-01"

            logger.info(
                f"机械臂决策: 料盘到达 {source_loc} -> 抓取并放置到 {target_loc}"
            )

            return {
                "device_id": self.default_device_id,
                "task_type": TaskType.PICK_AND_PLACE.value,
                "params": {
                    "source_loc": source_loc,
                    "target_loc": target_loc,
                    "barcode": barcode,
                },
            }

        if event_type == EventType.PICK_COMPLETED.value:
            # 抓取完成：执行放置
            command_id = data.get("command_id")

            logger.info(f"机械臂决策: 抓取完成 {command_id} -> 执行放置")

            # 这里应该从原始指令中获取目标位置
            # 简化处理：使用固定的目标位置
            return {
                "device_id": self.default_device_id,
                "task_type": TaskType.PUT.value,
                "params": {
                    "target_loc": "SHELF-A-01",
                    "previous_command_id": command_id,
                },
            }

        if event_type == EventType.PUT_COMPLETED.value:
            # 放置完成：流程结束
            logger.info("机械臂决策: 放置完成 -> 流程结束")
            return None

        # 其他事件类型：不执行动作
        logger.warning(f"机械臂未处理的事件类型: {event_type}")
        return None

    async def build_command(
        self, action_params: dict, correlation_id: str | None = None
    ) -> CommandRequest:
        """
        构建机械臂指令请求

        Args:
            action_params: 动作参数字典
            correlation_id: 关联 ID

        Returns:
            CommandRequest 指令请求对象
        """
        device_id = action_params.get("device_id", self.default_device_id)
        task_type = action_params.get("task_type", TaskType.PROCESS.value)
        params = action_params.get("params", {})

        # 如果没有指定 correlation_id，生成一个
        if correlation_id is None:
            correlation_id = str(uuid.uuid4())

        return CommandRequest(
            device_id=device_id,
            task_type=TaskType(task_type),
            priority=1,  # 机械臂任务高优先级
            timeout_ms=30000,  # 30 秒超时
            params=params,
            correlation_id=correlation_id,
        )

    async def handle_result(self, result_data: dict) -> dict | None:
        """
        处理机械臂执行结果

        结果处理:
        1. SUCCESS -> 检查是否有后续动作
        2. FAILED -> 记录错误，告警

        Args:
            result_data: 结果数据字典

        Returns:
            后续动作参数字典，如果不需要后续动作返回 None
        """
        result = result_data.get("result")
        data = result_data.get("data", {})

        if result == "SUCCESS":
            # 执行成功，检查是否需要扫码
            barcode = data.get("barcode")
            if barcode:
                logger.info(f"机械臂执行成功，触发扫码: {barcode}")
                return {
                    "device_id": "SCANNER-01",  # 假设有扫码设备
                    "task_type": TaskType.SCAN.value,
                    "params": {"barcode": barcode},
                }
            return None
        # 执行失败，记录错误
        error_detail = result_data.get("error_detail", {})
        logger.error(f"机械臂执行失败: {error_detail}")
        return None

    async def handle_error(self, error: Exception, context: dict) -> None:
        """
        处理机械臂错误

        Args:
            error: 异常对象
            context: 错误上下文
        """
        logger.error(
            f"机械臂处理错误: {error}",
            extra={
                "processor": self.get_processor_name(),
                "context": context,
            },
        )

        # TODO: 发送告警通知
        # await self.send_alert(error, context)


# ==================== 导出 ====================


__all__ = [
    "RoboticArmProcessor",
]
