"""
设备回调 API 路由 (Device Callback API Routes)

提供 WES 回调接口，供设备供应商调用。

接口定义遵循白皮书 3.2 节规范：
- POST /api/v1/callback/result - 任务结果回传
- POST /api/v1/callback/event - 设备事件上报

相关文档:
- 白皮书: @docs/third_party_integration_whitepaper.md
"""

import time
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from loguru import logger

from src.app.callback.services import callback_log_service
from src.app.device.models.command import CommandCallbackResult
from src.app.device.models.event_log import EventRequest
from src.app.device.services import device_command_service
from src.app.sys.models.audit_log import OperaStatus
from src.app.sys.services import audit_log_service
from src.app.workline.services import inbox_service
from src.core.api_security import RequireAPIPermission
from src.core.response import response_builder
from src.database.dependencies import AsyncSessionDep
from src.utils.audit import get_request_id

router = APIRouter()

_DUPLICATE_ERROR_MARKER = "已存在（幂等键重复）"


def _is_duplicate_inbox_error(error: ValueError) -> bool:
    return _DUPLICATE_ERROR_MARKER in str(error)


def _build_callback_log_payload(
    request: Request,
    *,
    callback_type: str,
    device_id: str,
    request_body: dict[str, Any],
    request_id: str | None,
    response_status: int,
    response_time_ms: int,
    error_message: str | None = None,
) -> dict[str, Any]:
    return {
        "callback_type": callback_type,
        "device_id": device_id,
        "request_body": request_body,
        "client_ip": request.client.host if request.client else None,
        "user_agent": request.headers.get("User-Agent"),
        "request_id": request_id,
        "response_status": response_status,
        "response_time_ms": response_time_ms,
        "error_message": error_message,
    }


async def _record_callback_audit_log(
    db: AsyncSessionDep,
    request: Request,
    *,
    title: str,
    args: dict[str, Any],
    cost_time: float,
    success: bool,
    message: str | None = None,
) -> None:
    try:
        await audit_log_service.create_audit_log(
            db,
            method=request.method,
            title=title,
            path=str(request.url.path),
            args=args,
            status=OperaStatus.SUCCESS if success else OperaStatus.FAIL,
            code="200" if success else "500",
            msg=message,
            cost_time=cost_time,
        )
    except Exception as audit_error:
        logger.error(f"记录回调审计日志失败: {audit_error}")


# ==================== 回调接口 ====================


@router.post(
    "/result",
    response_model=None,
    status_code=status.HTTP_200_OK,
    summary="任务结果回传",
    dependencies=[Depends(RequireAPIPermission("api:callback:result"))],
    description="设备完成指令后，调用此接口回传执行结果（白皮书 3.2.1）",
)
async def callback_result(
    callback: CommandCallbackResult,
    request: Request,
    db: AsyncSessionDep,
) -> dict:
    """
    任务结果回传接口

    设备完成指令后，必须调用此接口通知 WES 更新业务状态。

    请求体格式:
    ```json
    {
      "command_code": "CMD-20251215-1001",
      "device_code": "ARM_01",
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
    start_time = time.time()
    request_id = get_request_id()
    callback_data = callback.model_dump()
    is_duplicate = False  # 幂等重复标记

    logger.info(f"收到指令结果回调: {callback.command_code} -> {callback.result} (request_id={request_id})")

    try:
        # 写入 WorklineInbox（统一编排入口）
        try:
            await inbox_service.create_command_result_inbox(
                db=db,
                command_code=callback.command_code,
                device_code=callback.device_code,
                result=callback.result.value,
                finish_time=callback.finish_time,
                data=callback.data or {},
                correlation_id=request_id,
            )
            logger.info(f"指令结果已写入 Inbox: {callback.command_code}")
        except ValueError as e:
            # 幂等重复，标记并跳过业务处理
            if _is_duplicate_inbox_error(e):
                is_duplicate = True
                logger.info(f"指令结果幂等重复，将跳过业务处理: {callback.command_code}")
            else:
                raise

        # 只在非重复时执行业务处理
        if not is_duplicate:
            # 处理回调结果（原有逻辑）
            command = await device_command_service.handle_callback_result(db, callback)
            await db.commit()

            logger.info(
                f"指令结果处理完成: {callback.command_code} -> "
                f"status={command.status.value}, "
                f"duration={command.get_duration_ms()}ms"
            )

        cost_time = time.time() - start_time
        response_time_ms = int(cost_time * 1000)

        # 无论是否重复，都记录回调日志
        await callback_log_service.log_callback(
            db,
            **_build_callback_log_payload(
                request,
                callback_type="result",
                device_id=callback.device_code,
                request_body=callback_data,
                request_id=request_id,
                response_status=200,
                response_time_ms=response_time_ms,
                error_message="幂等重复: 已存在相同事件" if is_duplicate else None,
            ),
        )
        if not is_duplicate:
            await _record_callback_audit_log(
                db,
                request,
                title="设备回调结果",
                args=callback_data,
                cost_time=cost_time,
                success=True,
            )

        return response_builder.success(data={"ack": True})

    except Exception as e:
        cost_time = time.time() - start_time
        response_time_ms = int(cost_time * 1000)
        logger.error(f"指令结果回调处理失败: {e}")

        # 记录失败回调日志
        await callback_log_service.log_callback(
            db,
            **_build_callback_log_payload(
                request,
                callback_type="result",
                device_id=callback.device_code,
                request_body=callback_data,
                request_id=request_id,
                response_status=500,
                response_time_ms=response_time_ms,
                error_message=str(e),
            ),
        )
        await _record_callback_audit_log(
            db,
            request,
            title="设备回调结果",
            args=callback_data,
            cost_time=cost_time,
            success=False,
            message=str(e),
        )

        raise


@router.post(
    "/event",
    response_model=None,
    status_code=status.HTTP_200_OK,
    summary="设备事件上报",
    dependencies=[Depends(RequireAPIPermission("api:callback:event"))],
    description=("设备发生状态变更或传感器触发业务信号时，调用此接口上报事件（白皮书 3.2.2）"),
)
async def callback_event(
    event_request: EventRequest,
    request: Request,
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
      "device_code": "CONVEYOR_01",
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
    start_time = time.time()
    request_id = get_request_id()
    event_data = event_request.model_dump()
    is_duplicate = False  # 幂等重复标记

    logger.info(
        f"收到设备事件上报: {event_request.device_code} -> {event_request.event_type.value} (request_id={request_id})"
    )

    try:
        # 异步处理事件（通过 Celery）
        from src.celery_app.app import celery_app
        from src.utils.timezone import timezone

        # 如果设备未提供时间戳，使用服务器当前时间（毫秒）
        timestamp = event_request.timestamp
        if timestamp is None:
            timestamp = int(timezone.now_utc().timestamp() * 1000)
            logger.debug(f"设备未提供时间戳，使用服务器时间: {timestamp}")

        # 写入 WorklineInbox（统一编排入口）
        try:
            await inbox_service.create_device_event_inbox(
                db=db,
                device_code=event_request.device_code,
                event_type=event_request.event_type.value,
                timestamp=timestamp,
                data=event_request.data or {},
                correlation_id=request_id,
            )
            logger.info(f"设备事件已写入 Inbox: {event_request.device_code} -> {event_request.event_type.value}")
        except ValueError as e:
            # 幂等重复，标记并跳过业务处理
            if _is_duplicate_inbox_error(e):
                is_duplicate = True
                logger.info(
                    f"设备事件幂等重复，将跳过业务处理: {event_request.device_code} -> {event_request.event_type.value}"
                )
            else:
                raise

        # 只在非重复时提交 Celery 任务
        if not is_duplicate:
            # 构建事件数据，传递 request_id 用于链路追踪
            event_data = {
                "device_code": event_request.device_code,
                "event_type": event_request.event_type.value,
                "timestamp": timestamp,
                "data": event_request.data,
                "request_id": request_id,  # 传递 request_id 到 Celery
            }

            # 使用 send_task 异步处理事件（更可靠，确保任务被正确调度）
            celery_app.send_task(
                "src.celery_app.tasks.device.process_device_event",
                args=[event_data],
            )

        cost_time = time.time() - start_time
        response_time_ms = int(cost_time * 1000)

        # 无论是否重复，都记录回调日志
        await callback_log_service.log_callback(
            db,
            **_build_callback_log_payload(
                request,
                callback_type="event",
                device_id=event_request.device_code,
                request_body=event_data,
                request_id=request_id,
                response_status=200,
                response_time_ms=response_time_ms,
                error_message="幂等重复: 已存在相同事件" if is_duplicate else None,
            ),
        )
        if not is_duplicate:
            await _record_callback_audit_log(
                db,
                request,
                title="设备事件上报",
                args=event_data,
                cost_time=cost_time,
                success=True,
            )

        # 立即返回响应（不含业务指令，符合白皮书要求）
        logger.info(f"设备事件已提交处理: {event_request.device_code} (request_id={request_id})")
        return response_builder.success(
            message="Event received",
            data={
                "status": "duplicate" if is_duplicate else "submitted",
                "device_code": event_request.device_code,
            },
        )

    except Exception as e:
        cost_time = time.time() - start_time
        response_time_ms = int(cost_time * 1000)
        logger.error(f"设备事件上报处理失败: {e}")

        # 记录失败回调日志
        await callback_log_service.log_callback(
            db,
            **_build_callback_log_payload(
                request,
                callback_type="event",
                device_id=event_request.device_code,
                request_body=event_data,
                request_id=request_id,
                response_status=500,
                response_time_ms=response_time_ms,
                error_message=str(e),
            ),
        )
        await _record_callback_audit_log(
            db,
            request,
            title="设备事件上报",
            args=event_data,
            cost_time=cost_time,
            success=False,
            message=str(e),
        )

        raise


# ==================== 导出 ====================


__all__ = ["router"]
