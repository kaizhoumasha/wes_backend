"""
设备回调 API

供第三方设备调用，用于上报任务结果和事件
文档: @docs/third_party_integration_whitepaper.md 第 3.2 节
"""

from fastapi import APIRouter, status

from src.app.device.models.device import AckResponse, EventCallbackRequest, ResultCallbackRequest
from src.app.device.services.device_service import device_callback_service
from src.core.logger import logger
from src.database.dependencies import AsyncSessionDep

router = APIRouter(prefix="/callback", tags=["设备回调"])


@router.post(
    "/result",
    summary="任务结果回调",
    description="第三方设备在任务完成后调用此接口上报结果（白皮书 3.2.1）",
    status_code=status.HTTP_200_OK,
    responses={
        200: {"description": "ACK 确认", "model": AckResponse},
        404: {"description": "指令不存在", "model": AckResponse},
    },
)
async def result_callback(
    request: ResultCallbackRequest,
    db: AsyncSessionDep,
):
    """
    任务结果回调端点

    **请求格式（白皮书 3.2.1）**：
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

    **WES 响应**：`{"code": 200, "message": "ACK"}`
    """
    logger.info(f"收到任务结果回调: command_id={request.command_id}, result={request.result}")

    ack = await device_callback_service.handle_result_callback(db, request)

    return ack.model_dump()


@router.post(
    "/event",
    summary="设备事件上报",
    description="第三方设备在状态变更或传感器触发时调用此接口（白皮书 3.2.2）",
    status_code=status.HTTP_200_OK,
    responses={
        200: {"description": "ACK 确认", "model": AckResponse},
        404: {"description": "设备不存在", "model": AckResponse},
    },
)
async def event_callback(
    request: EventCallbackRequest,
    db: AsyncSessionDep,
):
    """
    设备事件上报端点

    **请求格式（白皮书 3.2.2）**：
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

    **支持的事件类型**：
    - `MATERIAL_ARRIVED`: 物料到达
    - `SCAN_COMPLETED`: 扫码完成
    - `ESTOP_PRESSED`: 急停触发
    - `DEVICE_ONLINE`: 设备上线
    - `DEVICE_OFFLINE`: 设备离线
    - `ERROR_OCCURRED`: 设备故障

    **WES 响应**：`{"code": 200, "message": "ACK"}`
    """
    logger.info(f"收到设备事件上报: device_id={request.device_id}, event_type={request.event_type}")

    ack = await device_callback_service.handle_event_callback(db, request)

    return ack.model_dump()
