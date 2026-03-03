"""
设备回调 API 路由 (Device Callback API Routes)

提供 WES 回调接口，供设备供应商调用。

接口定义遵循白皮书 3.2 节规范：
- POST /api/v1/callback/result - 任务结果回传
- POST /api/v1/callback/event - 设备事件上报

相关文档:
- 白皮书: @docs/third_party_integration_whitepaper.md
"""

from fastapi import APIRouter, status
from loguru import logger

from src.app.device.models.command import CommandCallbackResult
from src.app.device.models.event_log import EventRequest
from src.app.device.services import device_command_service
from src.core.response import response_builder
from src.database.dependencies import AsyncSessionDep

router = APIRouter()


# ==================== 回调接口 ====================


@router.post(
    "/result",
    response_model=None,
    status_code=status.HTTP_200_OK,
    summary="任务结果回传",
    description="设备完成指令后，调用此接口回传执行结果（白皮书 3.2.1）",
)
async def callback_result(
    callback: CommandCallbackResult,
    db: AsyncSessionDep,
) -> dict:
    """
    任务结果回传接口

    设备完成指令后，必须调用此接口通知 WES 更新业务状态。

    请求体格式:
    ```json
    {
      "command_id": "CMD-20251215-1001",
      "device_id": "ARM_01",
      "result": "SUCCESS",
      "finish_time": 1702627250000,
      "data": {
        "actual_qty": 10,
        "scan_result": "PKG-X-99"
      },
      "error_detail": {
        "code": "E-MOTOR-01",
        "msg": "Servo motor timeout"
      }
    }
    ```

    响应格式:
    ```json
    {
      "code": 200,
      "message": "ACK"
    }
    ```
    """
    logger.info(f"收到指令结果回调: {callback.command_id} -> {callback.result}")

    # 处理回调结果
    command = await device_command_service.handle_callback_result(db, callback)
    await db.commit()

    logger.info(
        f"指令结果处理完成: {callback.command_id} -> "
        f"status={command.status.value}, "
        f"duration={command.get_duration_ms()}ms"
    )

    return response_builder.success(data={"ack": True})


@router.post(
    "/event",
    response_model=None,
    status_code=status.HTTP_200_OK,
    summary="设备事件上报",
    description=(
        "设备发生状态变更或传感器触发业务信号时，"
        "调用此接口上报事件（白皮书 3.2.2）"
    ),
)
async def callback_event(
    event_request: EventRequest,
    db: AsyncSessionDep,
) -> dict:
    """
    设备事件上报接口

    设备发生以下情况时调用此接口：
    1. 状态变更：急停、上线、离线、故障
    2. 传感器触发：物料到位、扫码完成

    请求体格式:
    ```json
    {
      "device_id": "CONVEYOR_01",
      "event_type": "MATERIAL_ARRIVED",
      "timestamp": 1702627300000,
      "data": {
        "location": "STATION_04",
        "barcode": "PKG12345678"
      }
    }
    ```

    注意：timestamp 为可选字段，设备无时钟可不传，服务器将使用接收时间。

    响应格式:
    ```json
    {
      "code": 200,
      "message": "Event received",
      "data": {
        "status": "success",
        "event_id": 123,
        "commands_created": ["CMD-20251215-1001"],
        "action_params": {...}
      }
    }
    ```
    """
    logger.info(
        f"收到设备事件上报: {event_request.device_id} -> "
        f"{event_request.event_type.value}"
    )

    # 异步处理事件（通过 Celery）
    from src.celery_app.tasks.device import process_device_event
    from src.utils.timezone import timezone

    # 如果设备未提供时间戳，使用服务器当前时间（毫秒）
    timestamp = event_request.timestamp
    if timestamp is None:
        timestamp = int(timezone.now_utc().timestamp() * 1000)
        logger.debug(f"设备未提供时间戳，使用服务器时间: {timestamp}")

    event_data = {
        "device_id": event_request.device_id,
        "event_type": event_request.event_type.value,
        "timestamp": timestamp,
        "data": event_request.data,
    }

    # 异步处理事件
    process_device_event.delay(event_data)

    # 立即返回响应（不含业务指令，符合白皮书要求）
    logger.info(f"设备事件已提交处理: {event_request.device_id}")
    return response_builder.success(
        message="Event received",
        data={"status": "submitted", "device_id": event_request.device_id},
    )


# ==================== 导出 ====================


__all__ = ["router"]
