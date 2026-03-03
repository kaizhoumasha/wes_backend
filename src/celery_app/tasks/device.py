# ============================================
# 设备事件处理任务 - P9 WES Backend
# ============================================
# 用途: 处理设备事件的异步任务（料盘到达、扫码完成等）
# ============================================

import asyncio

from celery import Task
from loguru import logger

from src.celery_app.app import celery_app

# ============================================
# 设备任务基类 - 自动处理数据库会话
# ============================================


class DeviceTask(Task):
    """设备任务基类

    提供数据库会话管理，避免在每个任务中重复创建会话
    """

    _db = None

    @property
    def db(self):
        """懒加载数据库会话

        使用属性而非方法，确保在任务失败时也能正确清理
        """
        if self._db is None:
            # 动态导入 AsyncSessionLocal，避免使用导入时的全局引用
            from src.database.db import AsyncSessionLocal as AsyncSessionLocalDynamic

            session_local = AsyncSessionLocalDynamic
            if session_local is None:
                raise RuntimeError("数据库未初始化，请先调用 init_db()")
            self._db = session_local()
        return self._db

    def cleanup(self):
        """清理资源"""
        if self._db:
            asyncio.get_event_loop().run_until_complete(self._db.close())
            self._db = None

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        """任务失败时清理资源"""
        self.cleanup()
        logger.error(f"任务 {task_id} 失败: {exc}")
        super().on_failure(exc, task_id, args, kwargs, einfo)

    def on_success(self, retval, task_id, args, kwargs):
        """任务成功时清理资源"""
        self.cleanup()
        logger.info(f"任务 {task_id} 成功完成")
        super().on_success(retval, task_id, args, kwargs)


def _run_async(coro):
    """
    在 Celery 同步任务中运行异步函数

    参考 src/celery_app/tasks/core.py 的实现模式
    """
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    return loop.run_until_complete(coro)


# ============================================
# 料盘到达事件处理任务
# ============================================


@celery_app.task(
    name="src.celery_app.tasks.device.process_material_arrived",
    base=DeviceTask,
    bind=True,
    max_retries=3,
    default_retry_delay=5,  # 首次重试延迟5秒
)
def process_material_arrived(self, event_data: dict):
    """处理料盘到达事件（Celery 异步任务）

    业务流程：
    1. 解析事件数据（barcode, location）
    2. 验证 barcode 非空
    3. 确定目标位置（固定：SHELF-A-01）
    4. 创建搬运指令
    5. 下发指令到机械臂

    Args:
        event_data: 事件数据字典
            - device_id: 设备ID
            - event_type: 事件类型
            - barcode: 条码（必填）
            - location: 源位置

    Returns:
        任务执行结果字典

    Raises:
        ValueError: 当 barcode 为空时
    """
    try:
        from src.app.device.models.device import CommandRequest
        from src.app.device.services.device_service import device_command_service
        from src.core.logger import logger

        # 1. 解析事件数据
        barcode = event_data.get("barcode")
        source_loc = event_data.get("location")
        device_id = event_data.get("device_id")

        logger.info(f"开始处理料盘到达事件: device_id={device_id}, barcode={barcode}, location={source_loc}")

        # 2. 验证 barcode 非空
        if not barcode:
            error_msg = "事件数据缺少 barcode，无法生成搬运指令"
            logger.error(f"{error_msg}: {event_data}")
            return {
                "status": "error",
                "message": error_msg,
                "event_data": event_data,
            }

        # 3. 定义业务参数
        # 硬编码目标位置（实际项目中应该通过业务规则引擎计算）
        target_loc = "SHELF-A-01"
        robot_device_id = "ROBOT-ARM-01"  # 硬编码机械臂设备ID

        # 4. 创建搬运指令（异步操作）
        async def _process():
            async with self.db as db:
                # 4.1 创建指令请求
                command_request = CommandRequest(
                    device_id=robot_device_id,
                    task_type="PICK_AND_PLACE",
                    priority=1,
                    timeout_ms=30000,  # 30秒超时
                    params={
                        "source_loc": source_loc,
                        "target_loc": target_loc,
                        "barcode": barcode,
                    },
                )

                # 4.2 创建指令记录
                command = await device_command_service.create_command(db, command_request)
                logger.info(f"搬运指令已创建: {command.command_id}")

                # 4.3 下发指令到机械臂
                ack = await device_command_service.send_command(db, command.command_id)
                logger.info(f"指令已发送到机械臂: code={ack.code}, message={ack.message}")

                # 4.4 提交数据库事务
                await db.commit()

                return {
                    "command_id": command.command_id,
                    "device_id": robot_device_id,
                    "ack_code": ack.code,
                    "ack_message": ack.message,
                    "trace_id": ack.trace_id,
                }

        # 5. 执行异步处理
        result = _run_async(_process())

        logger.info(f"料盘到达事件处理成功: {result}")
        return {
            "status": "success",
            **result,
        }

    except Exception as e:
        logger.error(f"处理料盘到达事件失败: {e}", exc_info=True)

        # 自动重试（指数退避）
        # retry_count: 0 -> 5秒, 1 -> 10秒, 2 -> 20秒
        countdown = 5 * (2**self.request.retries)

        # Celery self.retry() 已经处理异常链，无需 from
        raise self.retry(exc=e, countdown=countdown) from None


# ============================================
# 扩展任务：扫码完成事件（预留）
# ============================================


@celery_app.task(
    name="src.celery_app.tasks.device.process_scan_completed",
    base=DeviceTask,
    bind=True,
    max_retries=3,
)
def process_scan_completed(self, event_data: dict):
    """处理扫码完成事件（预留任务）

    业务流程：
    1. 解析扫码结果
    2. 验证条码格式
    3. 更新业务单据状态
    4. 触发下一步流程

    Args:
        event_data: 事件数据字典
    """
    try:
        barcode = event_data.get("barcode")
        location = event_data.get("location")

        logger.info(f"处理扫码完成事件: barcode={barcode}, location={location}")

        # TODO: 实现具体的业务逻辑

        return {
            "status": "success",
            "message": "扫码完成事件已处理",
            "barcode": barcode,
        }

    except Exception as e:
        logger.error(f"处理扫码完成事件失败: {e}")
        # Celery self.retry() 已经处理异常链，无需 from
        raise self.retry(exc=e, countdown=5 * (2**self.request.retries)) from None


# ============================================
# 导出
# ============================================

__all__ = [
    "DeviceTask",
    "_run_async",
    "process_material_arrived",
    "process_scan_completed",
]
