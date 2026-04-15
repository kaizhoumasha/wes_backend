"""
回调日志 Service
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.callback.models import CallbackLog
from src.app.callback.repositories.callback_log_repository import (
    CallbackLogRepository,
    callback_log_repository,
)
from src.core.base_service import BaseService


class CallbackLogService(BaseService[CallbackLog, CallbackLogRepository]):
    """回调日志服务类"""

    def __init__(self) -> None:
        super().__init__(callback_log_repository)

    async def log_callback(
        self,
        db: AsyncSession,
        *,
        callback_type: str,
        device_id: str,
        request_body: dict[str, Any],
        client_ip: str | None = None,
        user_agent: str | None = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
        response_status: int = 200,
        response_time_ms: int = 0,
        error_message: str | None = None,
    ) -> CallbackLog:
        """
        记录回调日志

        Args:
            db: 数据库会话
            callback_type: 回调类型 (event/result)
            device_id: 设备 ID
            request_body: 原始请求体
            client_ip: 客户端 IP
            user_agent: 客户端 User-Agent
            request_id: 请求 ID（用于链路追踪）
            correlation_id: 关联 ID（串联整个流程）
            response_status: HTTP 响应状态码
            response_time_ms: 响应时间（毫秒）
            error_message: 错误消息

        Returns:
            创建的回调日志对象
        """
        log_data: dict[str, Any] = {
            "callback_type": callback_type,
            "device_id": device_id,
            "request_body": request_body,
            "client_ip": client_ip,
            "user_agent": user_agent,
            "request_id": request_id,
            "correlation_id": correlation_id,
            "response_status": response_status,
            "response_time_ms": response_time_ms,
            "error_message": error_message,
        }

        created = await self.repo.create(db, log_data)  # type: ignore[assignment]
        await db.commit()
        return created  # type: ignore[return-value]


# 创建单例
callback_log_service = CallbackLogService()


__all__ = [
    "CallbackLogService",
    "callback_log_service",
]
