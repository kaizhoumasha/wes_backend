"""回调业务编排 Service.

将 callback 路由中的业务编排逻辑下沉到 Service 层，
让路由专注于：协议校验、权限控制、响应构建。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from src.app.callback.contracts import (
    WMS_ALLOWED_CALLBACK_TYPES,
    TraceContext,
    timeline_generator,
    validate_external_callback_type,
)
from src.app.callback.utils import JsonDict, ensure_dict
from src.app.device.services.device_command_service import DeviceCallbackResultOutcome
from src.app.runtime.orchestration.consumers.callback_runtime_inbox_writer import (
    callback_runtime_inbox_writer,
)
from src.app.runtime.orchestration.models.timeline import (
    TimelineActionType,
    TimelineActorType,
    TimelineStage,
    TimelineStatus,
)
from src.app.runtime.orchestration.services.trace.timeline_sequence_service import add_timeline_with_sequence
from src.app.sys.services.event_stream_service import publish_deferred_sse_events
from src.core.task_queue_gateway import OutboxDispatchTarget, TaskQueueGateway, task_queue_gateway

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.ext.asyncio import AsyncSession

    from src.app.callback.models import CallbackEventRequest
    from src.app.device.models.command import CommandCallbackResult
    from src.app.device.services import DeviceCommandService, DeviceService

from src.core.logger import logger
from src.utils.timezone import timezone

_DUPLICATE_ERROR_MARKER = "已存在（幂等键重复）"
_WMS_EXTERNAL_CALLBACK_TYPES = WMS_ALLOWED_CALLBACK_TYPES


def _current_timestamp_ms() -> int:
    return int(timezone.now_utc().timestamp() * 1000)


@dataclass(frozen=True)
class ResultCallbackOutcome:
    trace_id: str | None
    is_duplicate: bool


@dataclass(frozen=True)
class EventCallbackOutcome:
    trace_id: str | None
    is_duplicate: bool


@dataclass(frozen=True)
class ExternalCallbackOutcome:
    trace_id: str
    is_duplicate: bool


class CallbackOrchestrationService:
    """处理 callback 的业务编排。"""

    def __init__(
        self,
        *,
        typed_effect_callback_router: Any | None = None,
        runtime_inbox_writer: Any = callback_runtime_inbox_writer,
        queue_gateway: TaskQueueGateway = task_queue_gateway,
    ) -> None:
        self._typed_effect_callback_router = typed_effect_callback_router
        self._runtime_inbox_writer = runtime_inbox_writer
        self._queue_gateway = queue_gateway

    def _is_duplicate_inbox_error(self, error: ValueError) -> bool:
        return _DUPLICATE_ERROR_MARKER in str(error)

    def _resolve_duplicate_inbox_error(self, error: ValueError, *, duplicate_message: str) -> object | None:
        if not self._is_duplicate_inbox_error(error):
            raise error
        logger.info(duplicate_message)
        return getattr(error, "existing_inbox", None)

    def _resolve_command_type(
        self,
        _callback_result_data: JsonDict,
        _command_params: JsonDict,
        existing_command: object,
    ) -> str | None:
        existing_task_type = getattr(existing_command, "task_type", None)
        candidate = getattr(existing_task_type, "value", existing_task_type)
        if isinstance(candidate, str) and candidate:
            return candidate
        return None

    def _enqueue_runtime_inbox_processing(self) -> None:
        try:
            # Plan Task 6: 调 enqueue_runtime_inbox (新 gateway 协议).
            # RuntimeInbox 是唯一入口；broker 故障时由 Beat 兜底，不回退旧队列。
            self._queue_gateway.enqueue_runtime_inbox(limit=10)
        except Exception as exc:
            logger.warning(f"Callback 已入库，但即时触发 Runtime Inbox 处理失败，将依赖 Beat/重试兜底: {exc}")

    def _enqueue_outbox_dispatch(self) -> None:
        try:
            self._queue_gateway.enqueue_outbox(targets=(OutboxDispatchTarget.SYSTEM,), limit=50)
        except Exception as exc:
            logger.warning(f"Callback 后续 Outbox 即时派发触发失败，将依赖 Beat/重试兜底: {exc}")

    def _unpack_command_callback_result(self, handled: object) -> tuple[object, bool]:
        if isinstance(handled, DeviceCallbackResultOutcome):
            return handled.command, handled.late_callback_recorded
        return handled, False

    def _has_workline_binding(self, value: object) -> bool:
        return isinstance(value, int) and value > 0

    def _build_trace_context(
        self,
        *,
        request_id: str | None = None,
        trace_id: str | None = None,
        event_id: str | None = None,
        causation_id: str | None = None,
        device_code: str | None = None,
        command_code: str | None = None,
        canonical_event_type: str | None = None,
        existing_command: object | None = None,
    ) -> TraceContext:
        trace = TraceContext.from_request(
            request_id=request_id,
            trace_id=trace_id,
            event_id=event_id,
            causation_id=causation_id,
            device_code=device_code,
            canonical_event_type=canonical_event_type,
        )
        if existing_command is not None:
            trace = trace.with_command(existing_command)
        if command_code is not None:
            trace = trace.with_command_code(command_code)
        return trace

    async def _load_command_session(self, db: AsyncSession, command: object) -> object | None:
        from src.app.runtime.orchestration.repositories.session_repository import WorklineSessionRepository

        command_code = getattr(command, "command_code", None)
        if not isinstance(command_code, str) or not command_code:
            return None

        session = await WorklineSessionRepository().get_open_session_by_awaiting_device_command_code(db, command_code)
        if session is not None:
            return session

        return None

    async def _append_command_acked_timeline(
        self,
        db: AsyncSession,
        *,
        session: object,
        inbox: object,
        command: object,
        trace: TraceContext,
        task_type: str | None,
        device_code: str,
        command_result: str,
    ) -> None:
        resolved_trace = trace.with_session(session).with_inbox(inbox).with_command(command)
        timeline = timeline_generator.generate(
            session=cast("Any", session),
            stage=TimelineStage.CALLBACK,
            action_type=TimelineActionType.COMMAND_ACKED,
            payload=resolved_trace.project_timeline_payload(
                command_code=getattr(command, "command_code", None),
                task_type=task_type,
                result=command_result,
                device_code=device_code,
            ),
            actor_type=TimelineActorType.DEVICE,
            actor_code=device_code,
            related_inbox_id=getattr(inbox, "id", None),
            related_command_id=getattr(command, "id", None),
            status=TimelineStatus.SUCCESS,
            trace=resolved_trace,
        )
        _ = await add_timeline_with_sequence(db, timeline)

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

    async def _is_workline_command_callback(
        self,
        db: AsyncSession,
        *,
        existing_command: object | None,
        device_code: str,
        device_service: DeviceService,
    ) -> bool:
        if self._has_workline_binding(getattr(existing_command, "workline_id", None)):
            return True
        device = await device_service.get_device_by_code(db, device_code)
        return self._has_workline_binding(getattr(device, "work_line_id", None))

    @staticmethod
    def _resolve_callback_error_code(error_detail: JsonDict | None) -> str | None:
        if not error_detail:
            return None
        for key in ("error_code", "code"):
            value = error_detail.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    async def _mark_callback_device_finished(
        self,
        db: AsyncSession,
        *,
        command: object,
        callback: CommandCallbackResult,
        device_service: DeviceService,
    ) -> int:
        command_id = getattr(command, "id", None)
        device_id = getattr(command, "device_id", None)
        if not isinstance(command_id, int) or not isinstance(device_id, int):
            logger.warning(f"回调指令缺少设备状态锚点，跳过设备运行态更新: {callback.command_code}")
            return 0

        updated_device = await device_service.mark_command_finished(
            db,
            device_id=device_id,
            command_id=command_id,
            success=callback.result.value == "SUCCESS",
            error_code=self._resolve_callback_error_code(callback.error_detail),
            auto_commit=False,
        )
        if updated_device is None:
            return 0
        device_status = getattr(getattr(updated_device, "device_status", None), "value", None) or getattr(
            updated_device,
            "device_status",
            None,
        )
        if device_status != "IDLE" or getattr(updated_device, "current_command_id", None) is not None:
            return 0

        # 本地设备投影仅用于诊断；blocked outbox 放行必须由下一轮 ECS admission probe 决定。
        return 0

    async def process_result(
        self,
        db: AsyncSession,
        *,
        callback: CommandCallbackResult,
        existing_command: object,
        request_id: str | None,
        resolved_contract_version: str | None,
        command_service: DeviceCommandService,
        device_service: DeviceService,
        trace_id: str | None = None,
        event_id: str | None = None,
        causation_id: str | None = None,
        enqueue_processing: Callable[[], None] | None = None,
    ) -> ResultCallbackOutcome:
        raw_command_params = getattr(existing_command, "params", None)
        command_params = ensure_dict(raw_command_params)
        callback_result_data = ensure_dict(callback.data)
        command_type = self._resolve_command_type(callback_result_data, command_params, existing_command)
        trace = self._build_trace_context(
            request_id=request_id,
            trace_id=trace_id,
            event_id=event_id,
            causation_id=causation_id,
            device_code=callback.device_code,
            command_code=callback.command_code,
            existing_command=existing_command,
        )
        inherited_trace_id = trace.trace_id or getattr(existing_command, "trace_id", None)
        is_workline_callback = await self._is_workline_command_callback(
            db,
            existing_command=existing_command,
            device_code=callback.device_code,
            device_service=device_service,
        )
        callback_session = await self._load_command_session(db, existing_command) if is_workline_callback else None

        runtime_inbox_result = await self._runtime_inbox_writer.write_result_callback(
            db,
            payload=callback.model_dump(mode="json"),
            request_id=request_id,
            canonical_result_type="DEVICE_RESULT",
            correlation_id=getattr(existing_command, "correlation_id", None),
            trace_id=inherited_trace_id,
            event_id=trace.event_id,
            causation_id=trace.causation_id,
            workline_id=getattr(existing_command, "workline_id", None),
            device_id=getattr(existing_command, "device_id", None),
            command_id=getattr(existing_command, "id", None),
            # 已知 Workline 指令统一进入 processor：当前 awaiting command 正常推进，
            # 已不再 awaiting 的旧指令由 SessionResolver 归属后写入迟到归档证据。
            processing_required=is_workline_callback,
        )
        if not runtime_inbox_result.created:
            return ResultCallbackOutcome(trace_id=inherited_trace_id, is_duplicate=True)

        if is_workline_callback:
            # RuntimeReconciliationFacade 已物理删除。
            # callback 域直接走 workline shim 路径(impl sys.modules alias,行为等价)。
            from src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl import (
                workline_runtime_reconciliation_service,
            )

            if await workline_runtime_reconciliation_service.record_late_callback_if_pending(
                db,
                command=cast("Any", existing_command),
                callback_payload=callback.model_dump(mode="json"),
            ):
                await db.commit()
                await publish_deferred_sse_events(db)
                logger.warning(f"迟到指令结果已记录为 runtime reconciliation evidence: {callback.command_code}")
                return ResultCallbackOutcome(trace_id=inherited_trace_id, is_duplicate=False)

            handled = await command_service.handle_callback_result(db, callback)
            command, late_callback_recorded = self._unpack_command_callback_result(handled)
            if late_callback_recorded:
                await db.commit()
                await publish_deferred_sse_events(db)
                logger.warning(f"迟到指令结果已记录为 runtime reconciliation evidence: {callback.command_code}")
                return ResultCallbackOutcome(trace_id=inherited_trace_id, is_duplicate=False)
            if command is None:
                raise RuntimeError(f"回调指令处理失败: {callback.command_code}")
            released_outboxes = await self._mark_callback_device_finished(
                db,
                command=command,
                callback=callback,
                device_service=device_service,
            )
            session = callback_session
            runtime_inbox_record = getattr(runtime_inbox_result, "record", None)
            if session is not None and runtime_inbox_record is not None:
                await self._append_command_acked_timeline(
                    db,
                    session=session,
                    inbox=runtime_inbox_record,
                    command=command,
                    trace=trace,
                    task_type=command_type,
                    device_code=callback.device_code,
                    command_result=callback.result.value,
                )
            trace = trace.with_command(command)
            inherited_trace_id = trace.trace_id or inherited_trace_id
            await self._commit_and_enqueue_runtime_inbox_processing(db, enqueue_processing=enqueue_processing)
            if released_outboxes:
                self._enqueue_outbox_dispatch()
            logger.info(
                f"指令结果处理完成: {callback.command_code} -> "
                f"status={command.status.value}, "
                f"duration={command.get_duration_ms()}ms, "
                f"contract_version={resolved_contract_version}"
            )
        else:
            handled = await command_service.handle_callback_result(db, callback)
            command, late_callback_recorded = self._unpack_command_callback_result(handled)
            if late_callback_recorded:
                await db.commit()
                await publish_deferred_sse_events(db)
                logger.warning(
                    f"迟到非 Workline 指令结果已记录为 runtime reconciliation evidence: {callback.command_code}"
                )
                return ResultCallbackOutcome(trace_id=inherited_trace_id, is_duplicate=False)
            if command is None:
                raise RuntimeError(f"回调指令处理失败: {callback.command_code}")
            released_outboxes = await self._mark_callback_device_finished(
                db,
                command=command,
                callback=callback,
                device_service=device_service,
            )
            trace = trace.with_command(command)
            inherited_trace_id = trace.trace_id or inherited_trace_id
            await db.commit()
            await publish_deferred_sse_events(db)
            if released_outboxes:
                self._enqueue_outbox_dispatch()
            logger.info(
                f"非 Workline 指令结果已同步处理: {callback.command_code} -> "
                f"status={command.status.value}, "
                f"duration={command.get_duration_ms()}ms"
            )

        return ResultCallbackOutcome(
            trace_id=inherited_trace_id,
            is_duplicate=False,
        )

    async def process_event(
        self,
        db: AsyncSession,
        *,
        event_request: CallbackEventRequest,
        request_id: str | None,
        is_workline_event: bool,
        canonical_event_type: str,
        device_id: int | None = None,
        trace_id: str | None = None,
        event_id: str | None = None,
        causation_id: str | None = None,
        enqueue_processing: Callable[[], None] | None = None,
    ) -> EventCallbackOutcome:
        event_timestamp = event_request.timestamp
        if event_timestamp is None:
            event_timestamp = _current_timestamp_ms()

        trace = self._build_trace_context(
            request_id=request_id,
            trace_id=trace_id,
            event_id=event_id,
            causation_id=causation_id,
            device_code=event_request.device_code,
            canonical_event_type=canonical_event_type,
        )
        event_trace_id = trace.trace_id
        if is_workline_event and event_trace_id is None:
            event_trace_id = f"trace_{uuid.uuid4().hex}"
            trace = trace.with_trace_id(event_trace_id)

        runtime_inbox_result = await self._runtime_inbox_writer.write_event_callback(
            db,
            payload=event_request.model_dump(mode="json"),
            request_id=request_id,
            canonical_event_type=canonical_event_type,
            device_id=device_id,
            trace_id=event_trace_id,
            event_id=trace.event_id,
            causation_id=trace.causation_id,
            # 非工作线事件仍保留 RuntimeInbox 幂等/冲突证据，但接收即终态，
            # 避免只支持工作线上下文的 processor 将其转为 DEAD_LETTER。
            processing_required=is_workline_event,
        )
        if not runtime_inbox_result.created:
            return EventCallbackOutcome(trace_id=event_trace_id, is_duplicate=True)

        if is_workline_event:
            await self._commit_and_enqueue_runtime_inbox_processing(db, enqueue_processing=enqueue_processing)
        else:
            await db.commit()
            await publish_deferred_sse_events(db)

        return EventCallbackOutcome(
            trace_id=event_trace_id,
            is_duplicate=False,
        )

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

        # RuntimeInbox record 是 external callback 唯一 evidence/trace inbox。
        trace = trace.with_inbox(runtime_inbox_result.record)
        _ = await self._resolve_typed_effect_callback_router().route(
            db,
            callback_type=callback_type,
            payload=payload,
        )

        await self._commit_and_enqueue_runtime_inbox_processing(db, enqueue_processing=enqueue_processing)

        return ExternalCallbackOutcome(
            trace_id=trace.trace_id or "",
            is_duplicate=False,
        )

    def _resolve_typed_effect_callback_router(self) -> Any:
        if self._typed_effect_callback_router is None:
            from src.app.runtime.orchestration.services.inbox.wms_typed_effect_callback_router import (
                wms_typed_effect_callback_router,
            )

            self._typed_effect_callback_router = wms_typed_effect_callback_router
        return self._typed_effect_callback_router


callback_orchestration_service = CallbackOrchestrationService()


__all__ = [
    "CallbackOrchestrationService",
    "EventCallbackOutcome",
    "ExternalCallbackOutcome",
    "ResultCallbackOutcome",
    "callback_orchestration_service",
]
