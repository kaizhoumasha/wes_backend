"""回调业务编排 Service.

将 callback 路由中的业务编排逻辑下沉到 Service 层，
让路由专注于：协议校验、权限控制、响应构建。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.app.callback.contracts import (
    TraceContext,
    validate_external_callback_type,
)
from src.app.runtime.orchestration.consumers.callback_runtime_inbox_writer import (
    callback_runtime_inbox_writer,
)
from src.app.sys.services.event_stream_service import publish_deferred_sse_events
from src.core.task_queue_gateway import TaskQueueGateway, task_queue_gateway

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.ext.asyncio import AsyncSession

    from src.app.callback.utils import JsonDict

from src.core.logger import logger


@dataclass(frozen=True)
class ExternalCallbackOutcome:
    trace_id: str
    is_duplicate: bool


class CallbackOrchestrationService:
    """处理 callback 的业务编排。"""

    def __init__(
        self,
        *,
        runtime_inbox_writer: Any = callback_runtime_inbox_writer,
        queue_gateway: TaskQueueGateway = task_queue_gateway,
    ) -> None:
        self._runtime_inbox_writer = runtime_inbox_writer
        self._queue_gateway = queue_gateway

    def _enqueue_runtime_inbox_processing(self) -> None:
        try:
            # Plan Task 6: 调 enqueue_runtime_inbox (新 gateway 协议).
            # RuntimeInbox 是唯一入口；broker 故障时由 Beat 兜底，不回退旧队列。
            self._queue_gateway.enqueue_runtime_inbox(limit=10)
        except Exception as exc:
            logger.warning(f"Callback 已入库，但即时触发 Runtime Inbox 处理失败，将依赖 Beat/重试兜底: {exc}")

    def _build_trace_context(
        self,
        *,
        request_id: str | None = None,
        trace_id: str | None = None,
        event_id: str | None = None,
        causation_id: str | None = None,
        canonical_event_type: str | None = None,
    ) -> TraceContext:
        return TraceContext.from_request(
            request_id=request_id,
            trace_id=trace_id,
            event_id=event_id,
            causation_id=causation_id,
            canonical_event_type=canonical_event_type,
        )

    async def _commit_and_enqueue_runtime_inbox_processing(
        self,
        db: AsyncSession,
        *,
        enqueue_processing: Callable[[], None] | None = None,
    ) -> None:
        await db.commit()
        await publish_deferred_sse_events(db)
        try:
            if enqueue_processing is None:
                self._enqueue_runtime_inbox_processing()
                return
            enqueue_processing()
        except Exception as exc:
            logger.warning(f"Callback 已提交，但即时触发 RuntimeInbox 处理失败，将依赖 Beat/重试兜底: {exc}")

    async def process_external(
        self,
        db: AsyncSession,
        *,
        callback_type: str,
        payload: JsonDict,
        request_id: str | None,
        trace_id: str | None = None,
        event_id: str | None = None,
        causation_id: str | None = None,
        enqueue_processing: Callable[[], None] | None = None,
    ) -> ExternalCallbackOutcome:
        callback_type = validate_external_callback_type(
            payload,
            declared_callback_type=callback_type,
        )

        trace = self._build_trace_context(
            request_id=request_id,
            trace_id=trace_id,
            event_id=event_id,
            causation_id=causation_id,
            canonical_event_type=callback_type,
        )

        resolved_trace_id = trace.trace_id or trace.request_id or f"trace_{uuid.uuid4().hex}"
        trace = trace.with_trace_id(resolved_trace_id)

        runtime_inbox_result = await self._runtime_inbox_writer.write_external_callback(
            db,
            payload=payload,
            request_id=request_id,
            trace_id=resolved_trace_id,
            event_id=trace.event_id,
            causation_id=trace.causation_id,
        )
        if not runtime_inbox_result.created:
            return ExternalCallbackOutcome(trace_id=trace.trace_id or "", is_duplicate=True)

        # RuntimeInbox record 是 external callback 唯一 evidence/trace inbox；
        # 业务提示由 worker 消费，API 事务内不得执行 status 路由。
        trace = trace.with_inbox(runtime_inbox_result.record)

        await self._commit_and_enqueue_runtime_inbox_processing(db, enqueue_processing=enqueue_processing)

        return ExternalCallbackOutcome(
            trace_id=trace.trace_id or "",
            is_duplicate=False,
        )


callback_orchestration_service = CallbackOrchestrationService()


__all__ = [
    "CallbackOrchestrationService",
    "ExternalCallbackOutcome",
    "callback_orchestration_service",
]
