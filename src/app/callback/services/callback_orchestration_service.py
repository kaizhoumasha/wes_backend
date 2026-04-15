"""回调业务编排 Service.

将 callback 路由中的业务编排逻辑下沉到 Service 层，
让路由专注于：协议校验、权限控制、响应构建。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from src.app.workline.models.inbox import SourceSystem

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.ext.asyncio import AsyncSession

    from src.app.callback.models import CallbackEventRequest
    from src.app.device.models.command import CommandCallbackResult
    from src.app.device.services import DeviceCommandService, DeviceService
    from src.app.workline.services import WorklineInboxService
from src.core.logger import logger
from src.utils.timezone import timezone
from src.workline_runtime.utils import JsonDict, ensure_dict

_DUPLICATE_ERROR_MARKER = "已存在（幂等键重复）"


@dataclass(frozen=True)
class ResultCallbackOutcome:
    correlation_id: str | None
    is_duplicate: bool


@dataclass(frozen=True)
class EventCallbackOutcome:
    correlation_id: str | None
    is_duplicate: bool


@dataclass(frozen=True)
class ExternalCallbackOutcome:
    correlation_id: str
    is_duplicate: bool


class CallbackOrchestrationService:
    """处理 callback 的业务编排。"""

    def _is_duplicate_inbox_error(self, error: ValueError) -> bool:
        return _DUPLICATE_ERROR_MARKER in str(error)

    def _resolve_command_type(
        self,
        callback_result_data: JsonDict,
        command_params: JsonDict,
        existing_command: object,
    ) -> str | None:
        candidates = [
            callback_result_data.get("command_type"),
            command_params.get("action"),
            command_params.get("task_type"),
        ]
        existing_task_type = getattr(existing_command, "task_type", None)
        candidates.append(getattr(existing_task_type, "value", existing_task_type))

        for candidate in candidates:
            if isinstance(candidate, str) and candidate:
                return candidate
        return None

    def _enqueue_workline_processing(self) -> None:
        from src.celery_app.app import celery_app

        cast("Any", celery_app).send_task(
            "src.celery_app.tasks.workline.process_inbox_batch",
            kwargs={"limit": 10},
        )

    def _has_workline_binding(self, value: object) -> bool:
        return isinstance(value, int) and value > 0

    async def _commit_and_enqueue_workline_processing(
        self,
        db: AsyncSession,
        *,
        enqueue_processing: Callable[[], None] | None = None,
    ) -> None:
        await db.commit()
        if enqueue_processing is None:
            self._enqueue_workline_processing()
            return
        enqueue_processing()

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
        inbox_service: WorklineInboxService,
        enqueue_processing: Callable[[], None] | None = None,
    ) -> ResultCallbackOutcome:
        existing_correlation_id = getattr(existing_command, "correlation_id", None)
        inherited_correlation_id = existing_correlation_id if isinstance(existing_correlation_id, str) else None
        raw_command_params = getattr(existing_command, "params", None)
        command_params = ensure_dict(raw_command_params)
        callback_result_data = ensure_dict(callback.data)
        command_type = self._resolve_command_type(callback_result_data, command_params, existing_command)

        is_duplicate = False
        is_workline_callback = await self._is_workline_command_callback(
            db,
            existing_command=existing_command,
            device_code=callback.device_code,
            device_service=device_service,
        )

        if is_workline_callback:
            try:
                _ = await inbox_service.create_command_result_inbox(
                    db=db,
                    command_code=callback.command_code,
                    device_code=callback.device_code,
                    result=callback.result.value,
                    finish_time=callback.finish_time,
                    data=callback.data or {},
                    command_type=command_type,
                    error_detail=callback.error_detail,
                    source_message_id=request_id,
                    correlation_id=inherited_correlation_id,
                    auto_commit=False,
                )
                logger.info(f"指令结果已写入 Inbox: {callback.command_code}")
            except ValueError as exc:
                if self._is_duplicate_inbox_error(exc):
                    is_duplicate = True
                    logger.info(f"指令结果幂等重复，将跳过业务处理: {callback.command_code}")
                else:
                    raise

            if is_duplicate:
                await self._commit_and_enqueue_workline_processing(db, enqueue_processing=enqueue_processing)
            else:
                command = await command_service.handle_callback_result(db, callback)
                if command is None:
                    raise RuntimeError(f"回调指令处理失败: {callback.command_code}")
                inherited_correlation_id = command.correlation_id or inherited_correlation_id
                await self._commit_and_enqueue_workline_processing(db, enqueue_processing=enqueue_processing)
                logger.info(
                    f"指令结果处理完成: {callback.command_code} -> "
                    f"status={command.status.value}, "
                    f"duration={command.get_duration_ms()}ms, "
                    f"contract_version={resolved_contract_version}"
                )
        else:
            command = await command_service.handle_callback_result(db, callback)
            if command is None:
                raise RuntimeError(f"回调指令处理失败: {callback.command_code}")
            inherited_correlation_id = command.correlation_id or inherited_correlation_id
            await db.commit()
            logger.info(
                f"非 Workline 指令结果已同步处理: {callback.command_code} -> "
                f"status={command.status.value}, "
                f"duration={command.get_duration_ms()}ms"
            )

        return ResultCallbackOutcome(
            correlation_id=inherited_correlation_id,
            is_duplicate=is_duplicate,
        )

    async def process_event(
        self,
        db: AsyncSession,
        *,
        event_request: CallbackEventRequest,
        event_data: JsonDict,
        request_id: str | None,
        is_workline_event: bool,
        inbox_service: WorklineInboxService,
        enqueue_processing: Callable[[], None] | None = None,
    ) -> EventCallbackOutcome:
        event_timestamp = event_request.timestamp
        if event_timestamp is None:
            event_timestamp = int(timezone.now_utc().timestamp() * 1000)

        event_correlation_id = cast("str | None", event_data.get("correlation_id"))
        if is_workline_event and event_correlation_id is None:
            event_correlation_id = f"corr_{uuid.uuid4().hex}"

        is_duplicate = False
        if is_workline_event:
            try:
                _ = await inbox_service.create_device_event_inbox(
                    db=db,
                    device_code=event_request.device_code,
                    event_type=event_request.event_type,
                    timestamp=event_timestamp,
                    data=cast("dict[str, Any]", event_request.data or {}),
                    source_message_id=request_id,
                    correlation_id=event_correlation_id,
                    auto_commit=False,
                )
                logger.info(
                    f"设备事件已写入 Inbox: "
                    f"{event_request.device_code} -> {event_request.event_type}"
                )
            except ValueError as exc:
                if self._is_duplicate_inbox_error(exc):
                    is_duplicate = True
                    logger.info(
                        "设备事件幂等重复，将跳过业务处理: "
                        f"{event_request.device_code} -> {event_request.event_type}"
                    )
                else:
                    raise

            await self._commit_and_enqueue_workline_processing(db, enqueue_processing=enqueue_processing)

        return EventCallbackOutcome(
            correlation_id=event_correlation_id,
            is_duplicate=is_duplicate,
        )

    async def process_external(
        self,
        db: AsyncSession,
        *,
        callback_type: str,
        correlation_id: str,
        payload: JsonDict,
        request_id: str | None,
        inbox_service: WorklineInboxService,
        enqueue_processing: Callable[[], None] | None = None,
    ) -> ExternalCallbackOutcome:
        is_duplicate = False

        try:
            _ = await inbox_service.create_external_http_inbox(
                db=db,
                callback_type=callback_type,
                correlation_id=correlation_id,
                payload=payload,
                source_system=SourceSystem.SYSTEM,
                source_message_id=request_id,
                auto_commit=False,
            )
            logger.info(f"外部回调已写入 Inbox: {callback_type}")
        except ValueError as exc:
            if self._is_duplicate_inbox_error(exc):
                is_duplicate = True
                logger.info(f"外部回调幂等重复，将跳过业务处理: {callback_type}")
            else:
                raise

        await self._commit_and_enqueue_workline_processing(db, enqueue_processing=enqueue_processing)

        return ExternalCallbackOutcome(
            correlation_id=correlation_id,
            is_duplicate=is_duplicate,
        )


callback_orchestration_service = CallbackOrchestrationService()


__all__ = [
    "CallbackOrchestrationService",
    "EventCallbackOutcome",
    "ExternalCallbackOutcome",
    "ResultCallbackOutcome",
    "callback_orchestration_service",
]
