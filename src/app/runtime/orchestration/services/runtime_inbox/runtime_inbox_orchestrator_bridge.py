"""RuntimeInbox 通用处理入口。"""

from __future__ import annotations

import uuid
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypedDict

from loguru import logger

from src.app.runtime.orchestration.diagnostics import ErrorCode
from src.app.runtime.orchestration.repositories.runtime_inbox_repository import (
    RuntimeInboxRepository,
    runtime_inbox_repository,
)
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_service import (
    RuntimeInboxReplayNotAllowed,
    RuntimeInboxReplaySourceValidator,
    RuntimeInboxService,
    runtime_inbox_service,
)
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_validation_service import (
    RuntimeInboxValidationService,
)
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_writeback_service import (
    _payload_for_inbox,
    _require_fenced_update,
)
from src.app.runtime.orchestration.services.session.session_resolver import SessionResolveError
from src.app.workline.constants import INBOX_PROCESS_TIMEOUT_SECONDS, WORKLINE_INBOX_PROCESSING_STALE_SECONDS
from src.app.workline.diagnostic_support import _record_diagnostic
from src.app.workline.services.safety_service import WorkLineSafetyBlocked
from src.core.task_queue_gateway import TaskQueueGateway, task_queue_gateway
from src.utils.value_normalization import (
    optional_int,
    optional_str,
    resolve_entity_id,
    resolve_required_pk,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class ProcessResult(TypedDict):
    processed: int
    success: int
    failed: int
    skipped: int
    resource_wait: int


@dataclass(frozen=True, slots=True)
class _InboxDiagnosticSnapshot:
    id: int | None
    workline_id: int | None
    workline_session_id: int | None
    device_id: int | None
    payload_json: dict[str, Any]


def _empty_result() -> ProcessResult:
    return {"processed": 0, "success": 0, "failed": 0, "skipped": 0, "resource_wait": 0}


def _success_result() -> ProcessResult:
    result = _empty_result()
    result["processed"] = 1
    result["success"] = 1
    return result


def _merge_result(target: ProcessResult, source: ProcessResult) -> None:
    for key in target:
        target[key] += source[key]


def _kind_value(entity: Any) -> str | None:
    value = getattr(getattr(entity, "kind", None), "value", getattr(entity, "kind", None))
    return value if isinstance(value, str) and value else None


def _snapshot_inbox_for_diagnostic(inbox: Any) -> _InboxDiagnosticSnapshot:
    payload = getattr(inbox, "payload_json", None)
    return _InboxDiagnosticSnapshot(
        id=optional_int(getattr(inbox, "id", None)),
        workline_id=optional_int(getattr(inbox, "workline_id", None)),
        workline_session_id=optional_int(getattr(inbox, "workline_session_id", None)),
        device_id=optional_int(getattr(inbox, "device_id", None)),
        payload_json=dict(payload) if isinstance(payload, dict) else {},
    )


def _project_replay_request(inbox: Any, *, validated_source: Any | None) -> Any:
    if validated_source is None:
        return inbox
    envelope = validated_source.envelope
    return type(
        "ReplayInbox",
        (),
        {
            "id": getattr(inbox, "id", None),
            "kind": envelope["original_kind"],
            "payload_json": dict(envelope["original_payload"]),
            "provider_code": envelope["original_provider_code"],
            "event_type": envelope["original_event_type"],
            "source_event_id": envelope["original_source_event_id"],
            "payload_hash": envelope["original_payload_hash"],
            "workline_id": envelope["original_workline_id"],
            "workline_session_id": envelope["original_workline_session_id"],
            "execution_session_id": envelope["original_execution_session_id"],
            "correlation_id": envelope["original_correlation_id"],
            "trace_id": envelope["original_trace_id"],
            "event_id": envelope["original_event_id"],
            "causation_id": envelope["original_causation_id"],
            "is_manual_replay": True,
        },
    )()


async def _load_related_entities(
    db: Any,
    inbox: Any,
    *,
    resolved_event_type: str | None = None,
) -> tuple[Any | None, Any | None, Any | None, dict[str, list[Any]], Any, bool]:
    from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_context_loader import load_related_entities

    loaded = await load_related_entities(db, inbox, resolved_event_type=resolved_event_type)
    return (
        loaded.get("session"),
        loaded.get("workline"),
        loaded.get("device"),
        loaded.get("devices_by_role", {}),
        loaded.get("services"),
        loaded.get("safety_checked", True),
    )


async def _handle_estop(
    db: Any,
    *,
    inbox: Any,
    inbox_pk: int,
    payload: dict[str, Any],
    session: Any,
    workline: Any,
    device: Any,
    processor_token: str,
    inbox_service: RuntimeInboxService,
) -> bool:
    workline_pk = resolve_entity_id(workline)
    if workline_pk is None:
        message = "ESTOP_PRESSED missing workline context"
        await _record_diagnostic(
            db,
            inbox=inbox,
            error_code=ErrorCode.SESSION_CONTEXT_MISSING,
            message=message,
            session=session,
            workline=workline,
            device=device,
        )
        _require_fenced_update(
            await inbox_service.mark_failed(
                db,
                inbox_id=inbox_pk,
                lease_token=processor_token,
                error_code=ErrorCode.SESSION_CONTEXT_MISSING.value,
                error_message=message,
                retryable=False,
            ),
            action="mark_failed",
            inbox_id=inbox_pk,
        )
        return False
    from src.app.workline.services.safety_service import workline_safety_service

    _ = await workline_safety_service.handle_estop(
        db,
        workline_id=workline_pk,
        source_inbox_id=inbox_pk,
        source_device_id=resolve_entity_id(device) or getattr(inbox, "device_id", None),
        source_command_id=None,
        trigger_payload=payload,
    )
    _require_fenced_update(
        await inbox_service.mark_processed(db, inbox_id=inbox_pk, lease_token=processor_token),
        action="mark_processed",
        inbox_id=inbox_pk,
    )
    return True


class RuntimeInboxProcessorBridge:
    """处理 RuntimeInbox 的通用 WMS、控制、诊断与终态路径。"""

    def __init__(
        self,
        *,
        validation_service: RuntimeInboxValidationService | None = None,
        inbox_service: RuntimeInboxService | None = None,
        inbox_repository: RuntimeInboxRepository | None = None,
        replay_source_validator: RuntimeInboxReplaySourceValidator | None = None,
        queue_gateway: TaskQueueGateway = task_queue_gateway,
        wms_inbound_handler: Any | None = None,
    ) -> None:
        self._validation_service = validation_service or RuntimeInboxValidationService()
        self._inbox_service = inbox_service or runtime_inbox_service
        self._inbox_repository = inbox_repository or runtime_inbox_repository
        self._replay_source_validator = replay_source_validator or RuntimeInboxReplaySourceValidator(
            self._inbox_repository
        )
        self._queue_gateway = queue_gateway
        self._wms_inbound_handler = wms_inbound_handler

    @property
    def inbox_service(self) -> RuntimeInboxService:
        return self._inbox_service

    @property
    def inbox_repository(self) -> RuntimeInboxRepository:
        return self._inbox_repository

    def _resolve_wms_inbound_handler(self) -> Any:
        if self._wms_inbound_handler is None:
            from src.app.runtime.orchestration.services.inbox.wms_runtime_inbox_handler import WmsRuntimeInboxHandler

            self._wms_inbound_handler = WmsRuntimeInboxHandler()
        return self._wms_inbound_handler

    async def claim_and_process_batch(
        self,
        db: AsyncSession,
        *,
        limit: int,
        processor_token_prefix: str = "runtime-inbox-worker",  # noqa: S107
    ) -> ProcessResult:
        result = _empty_result()
        for _ in range(max(limit, 0)):
            token = f"{processor_token_prefix}-{uuid.uuid4()}"
            claims = await self.inbox_repository.claim_received_with_token(
                db, limit=1, processor_token=token, stale_after_seconds=WORKLINE_INBOX_PROCESSING_STALE_SECONDS
            )
            if not claims:
                break
            _merge_result(result, await self.process_claimed(db, claim=claims[0]))
        return result

    async def _mark_failure(
        self, db: Any, *, inbox: Any, inbox_id: int | None, token: str, error_code: str, message: str, retryable: bool
    ) -> ProcessResult:
        try:
            diagnostic_error_code = ErrorCode(error_code)
        except ValueError:
            diagnostic_error_code = ErrorCode.UNKNOWN
        await _record_diagnostic(db, inbox=inbox, error_code=diagnostic_error_code, message=message)
        if inbox_id is not None:
            _require_fenced_update(
                await self.inbox_service.mark_failed(
                    db,
                    inbox_id=inbox_id,
                    lease_token=token,
                    error_code=error_code,
                    error_message=message,
                    retryable=retryable,
                ),
                action="mark_failed",
                inbox_id=inbox_id,
            )
            await db.commit()
        result = _empty_result()
        result["failed"] = result["processed"] = 1
        return result

    async def _process_claimed_core(self, db: Any, *, inbox: Any, inbox_id: int, token: str) -> ProcessResult:
        if _kind_value(inbox) == "REPLAY_REQUEST":
            source = await self._replay_source_validator.validate_for_consumption(db, source=inbox)
            inbox = _project_replay_request(inbox, validated_source=source)
        payload = _payload_for_inbox(inbox)
        event_type = optional_str(getattr(inbox, "event_type", None))
        if event_type is None:
            raise ValueError("RuntimeInbox event_type is required")
        if await self._resolve_wms_inbound_handler().handle(
            db,
            provider_code=optional_str(getattr(inbox, "provider_code", None)),
            event_type=event_type,
            payload=payload,
        ):
            _require_fenced_update(
                await self.inbox_service.mark_processed(db, inbox_id=inbox_id, lease_token=token),
                action="mark_processed",
                inbox_id=inbox_id,
            )
            await db.commit()
            return _success_result()
        outcome = await self._validation_service.pre_gate(
            db, inbox=inbox, resolved_event_type=event_type, workline=None
        )
        if not outcome.proceed_to_orchestrator:
            return await self._mark_failure(
                db,
                inbox=inbox,
                inbox_id=inbox_id,
                token=token,
                error_code=(outcome.error_code or ErrorCode.UNKNOWN).value,
                message=outcome.error_message or "validation failed",
                retryable=False,
            )
        routed = self._validation_service.classify_estop(resolved_event_type=event_type)
        session, workline, device, _devices, _services, _safety_checked = await _load_related_entities(
            db, inbox, resolved_event_type=event_type
        )
        if routed.estop_event:
            ok = await _handle_estop(
                db,
                inbox=inbox,
                inbox_pk=inbox_id,
                payload=payload,
                session=session,
                workline=workline,
                device=device,
                processor_token=token,
                inbox_service=self.inbox_service,
            )
            await db.commit()
            result = _empty_result()
            result["processed"] = 1
            result["success" if ok else "failed"] = 1
            return result
        if session is None or workline is None:
            return await self._mark_failure(
                db,
                inbox=inbox,
                inbox_id=inbox_id,
                token=token,
                error_code=ErrorCode.SESSION_CONTEXT_MISSING.value,
                message="Inbox processing missing session/workline context",
                retryable=False,
            )
        return await self._mark_failure(
            db,
            inbox=inbox,
            inbox_id=inbox_id,
            token=token,
            error_code=ErrorCode.CONTRACT_MISMATCH.value,
            message=f"RuntimeInbox event has no active owner: {event_type}",
            retryable=False,
        )

    async def process_claimed(self, db: AsyncSession, *, claim: dict[str, Any] | Any) -> ProcessResult:
        inbox_id = claim["id"] if isinstance(claim, dict) else claim.id
        inbox = await self.inbox_repository.get_by_id(db, inbox_id)
        if inbox is None:
            result = _empty_result()
            result["skipped"] = 1
            return result
        token = claim.get("processor_token") if isinstance(claim, dict) else getattr(claim, "processor_token", None)
        token = token or f"runtime-inbox-worker-{uuid.uuid4()}"
        diagnostic = _snapshot_inbox_for_diagnostic(inbox)
        try:
            return await self._process_claimed_core(
                db, inbox=inbox, inbox_id=resolve_required_pk(inbox, "inbox", "id", "inbox_id"), token=token
            )
        except (SessionResolveError, WorkLineSafetyBlocked) as exc:
            return await self._mark_failure(
                db,
                inbox=inbox,
                inbox_id=diagnostic.id,
                token=token,
                error_code=ErrorCode.SESSION_RESOLVE_FAILED.value,
                message=str(exc),
                retryable=False,
            )
        except TimeoutError:
            return await self._mark_failure(
                db,
                inbox=inbox,
                inbox_id=diagnostic.id,
                token=token,
                error_code=ErrorCode.INBOX_PROCESSING_TIMEOUT.value,
                message=f"处理超时 (> {INBOX_PROCESS_TIMEOUT_SECONDS}s)",
                retryable=False,
            )
        except Exception as exc:
            is_replay_violation = isinstance(exc, RuntimeInboxReplayNotAllowed)
            reason = exc.reason_code if is_replay_violation else ErrorCode.UNKNOWN.value
            retryable = not is_replay_violation
            if is_replay_violation:
                logger.warning(f"Inbox {diagnostic.id} 重放证据校验失败")
            else:
                logger.exception(f"Inbox {diagnostic.id} 处理异常")
            with suppress(Exception):
                await db.rollback()
            return await self._mark_failure(
                db,
                inbox=diagnostic,
                inbox_id=diagnostic.id,
                token=token,
                error_code=reason,
                message=str(exc),
                retryable=retryable,
            )


__all__ = ["ProcessResult", "RuntimeInboxProcessorBridge"]
