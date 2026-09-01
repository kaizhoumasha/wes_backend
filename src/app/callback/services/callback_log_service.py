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
from src.app.runtime.orchestration.trace_context import TraceContext
from src.core.base_service import BaseService


def _build_callback_log_data(
    *,
    callback_type: str,
    subject_code: str,
    request_body: dict[str, Any],
    client_ip: str | None,
    user_agent: str | None,
    response_status: int,
    response_time_ms: int,
    error_message: str | None,
    ingress_outcome: str | None,
    failure_stage: str | None,
    trace: TraceContext,
) -> dict[str, Any]:
    return {
        "callback_type": callback_type,
        "subject_code": subject_code,
        "request_body": request_body,
        "client_ip": client_ip,
        "user_agent": user_agent,
        "request_id": trace.request_id,
        "trace_id": trace.trace_id,
        "event_id": trace.event_id,
        "causation_id": trace.causation_id,
        "response_status": response_status,
        "response_time_ms": response_time_ms,
        "error_message": error_message,
        "ingress_outcome": ingress_outcome,
        "failure_stage": failure_stage,
    }


class CallbackLogService(BaseService[CallbackLog, CallbackLogRepository]):
    """回调日志服务类"""

    def __init__(self) -> None:
        super().__init__(callback_log_repository)

    async def log_callback(
        self,
        db: AsyncSession,
        *,
        callback_type: str,
        subject_code: str,
        request_body: dict[str, Any],
        client_ip: str | None = None,
        user_agent: str | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
        response_status: int = 200,
        response_time_ms: int = 0,
        error_message: str | None = None,
        ingress_outcome: str | None = None,
        failure_stage: str | None = None,
        trace: TraceContext | None = None,
    ) -> CallbackLog:
        """
        记录回调日志

        Args:
            db: 数据库会话
            callback_type: 回调类型 (event/result)
            subject_code: 回调主体编码（设备编码或外部回调类型）
            request_body: 原始请求体
            client_ip: 客户端 IP
            user_agent: 客户端 User-Agent
            request_id: 请求 ID（用于链路追踪）
            trace_id: Trace ID（串联整个流程）
            response_status: HTTP 响应状态码
            response_time_ms: 响应时间（毫秒）
            error_message: 错误消息
            ingress_outcome: 入口结果（ACCEPTED/REJECTED/FAILED/DUPLICATE）
            failure_stage: 入口失败阶段
            trace: 统一 trace 上下文（可选）

        Returns:
            创建的回调日志对象
        """
        resolved_trace = trace or TraceContext.from_request(
            request_id=request_id,
            trace_id=trace_id,
        )

        log_data = _build_callback_log_data(
            callback_type=callback_type,
            subject_code=subject_code,
            request_body=request_body,
            client_ip=client_ip,
            user_agent=user_agent,
            response_status=response_status,
            response_time_ms=response_time_ms,
            error_message=error_message,
            ingress_outcome=ingress_outcome,
            failure_stage=failure_stage,
            trace=resolved_trace,
        )

        created = await self.repo.create(db, log_data)  # type: ignore[assignment]
        await db.commit()
        return created  # type: ignore[return-value]

    async def get_by_request_id(self, db: AsyncSession, request_id: str) -> CallbackLog | None:
        """根据 request_id 查询单条回调日志。"""

        return await self.repo.get_by_request_id(db, request_id)

    async def get_by_trace_id(self, db: AsyncSession, trace_id: str) -> list[CallbackLog]:
        """根据 trace_id 查询所有相关的回调日志。"""

        return await self.repo.get_by_trace_id(db, trace_id)

    async def get_by_subject_code(self, db: AsyncSession, subject_code: str, limit: int = 100) -> list[CallbackLog]:
        """根据回调主体编码查询最近的回调日志。"""

        return await self.repo.get_by_subject_code(db, subject_code, limit)


# 创建单例
callback_log_service = CallbackLogService()


__all__ = [
    "CallbackLogService",
    "callback_log_service",
]
