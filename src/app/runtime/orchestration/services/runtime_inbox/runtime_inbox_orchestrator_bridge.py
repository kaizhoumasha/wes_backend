"""RuntimeInboxProcessorService composition (Task 5 三阶段 Processor 拆分).

组合 Validation → Orchestration → Write-back 三阶段.
提供与 InboxBatchProcessor._process_claimed_message 等价的单一入口
`process_claimed(db, claim)`, 由 Task 6 Celery task 调用.

行为对齐:
- claim 阶段: 由 RuntimeInboxClaimRepository.claim_received_with_token 持有
  (调用方负责).
- validation 阶段: RuntimeInboxValidationService.
- orchestration 阶段: RuntimeInboxProcessorService 委托 OrchestratorService.
- write-back 阶段: RuntimeInboxWriteBackService.
- ESTOP / TIMER_TIMEOUT / duplicate entry / late command / missing context
  沿用 InboxBatchProcessor 的判定, 行为等价.
- 写终态: mark_processed / mark_failed / mark_dead_letter 走
  RuntimeInboxService (processor_token 作为 lease_token fencing, 作用于
  RuntimeInbox 表, 不再 fallback 到 legacy WorklineInboxService).
- 失败重试与超时不直接 raise, 全部转换成 ProcessResult 统计.
"""

from __future__ import annotations

import uuid
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypedDict

from loguru import logger

from src.app.runtime.capability_catalog import parse_workline_six_in_one
from src.app.runtime.orchestration.consumers.runtime_inbox_repository import (
    RuntimeInboxRepository,
    runtime_inbox_repository,
)
from src.app.runtime.orchestration.consumers.runtime_inbox_service import (
    RuntimeInboxService,
    runtime_inbox_service,
)
from src.app.runtime.orchestration.diagnostics import (
    ErrorCode,
    ErrorDomain,
    ProblemClass,
    map_failure_to_diagnostic,
)
from src.app.runtime.orchestration.effect_result import WriteBackDisposition
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_processor_service import (
    RuntimeInboxOrchestratorDelegate,
)
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_validation_service import (
    RuntimeInboxValidationService,
    _entry_event_types_for_workline,
)
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_writeback_service import (
    RuntimeInboxWriteBackService,
    WriteBackState,
    _is_late_or_duplicate_command_result_for_session,
    _payload_for_inbox,
    _record_duplicate_entry_archive_timeline,
    _record_late_command_result_archive_timeline,
    _require_fenced_update,
    _session_status_value,
    _session_write_snapshot,
)
from src.app.runtime.orchestration.services.session.session_resolver import SessionResolveError
from src.app.workline.constants import (
    INBOX_PROCESS_TIMEOUT_SECONDS,
    WORKLINE_INBOX_PROCESSING_STALE_SECONDS,
)
from src.app.workline.diagnostic_support import _record_diagnostic
from src.app.workline.services.safety_service import WorkLineSafetyBlocked
from src.app.workline.utils import payload_dict
from src.utils.value_normalization import (
    canonical_event_type,
    optional_int,
    resolve_entity_id,
    resolve_required_pk,
    string_value,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.app.runtime.orchestration.orchestrator_bridge import OrchestratorResult


class ProcessResult(TypedDict):
    """处理结果统计 (与 InboxBatchProcessor 等价)."""

    processed: int
    success: int
    failed: int
    skipped: int
    resource_wait: int


@dataclass(frozen=True, slots=True)
class _InboxDiagnosticSnapshot:
    """Inbox 诊断快照, 避免 rollback 后访问已过期 ORM 字段."""

    id: int | None
    kind: Any | None
    trace_id: str | None
    event_id: str | None
    causation_id: str | None
    workline_id: int | None
    session_id: int | None
    device_id: int | None
    command_id: int | None
    attempt_count: int
    payload_json: dict[str, Any]


def _snapshot_inbox_for_diagnostic(inbox: Any) -> _InboxDiagnosticSnapshot:
    """在事务回滚前提取诊断需要的 Inbox 字段."""
    return _InboxDiagnosticSnapshot(
        id=resolve_entity_id(inbox),
        kind=getattr(inbox, "kind", None),
        trace_id=getattr(inbox, "trace_id", None),
        event_id=getattr(inbox, "event_id", None),
        causation_id=getattr(inbox, "causation_id", None),
        workline_id=optional_int(getattr(inbox, "workline_id", None)),
        session_id=optional_int(getattr(inbox, "execution_session_id", None)),
        device_id=optional_int(getattr(inbox, "device_id", None)),
        command_id=optional_int(getattr(inbox, "command_id", None)),
        attempt_count=int(getattr(inbox, "attempt_count", 0)),
        payload_json=dict(_payload_for_inbox(inbox)),
    )


def _empty_result() -> ProcessResult:
    return {
        "processed": 0,
        "success": 0,
        "failed": 0,
        "skipped": 0,
        "resource_wait": 0,
    }


def _merge_result(target: ProcessResult, source: ProcessResult) -> None:
    target["processed"] += source.get("processed", 0)
    target["success"] += source.get("success", 0)
    target["failed"] += source.get("failed", 0)
    target["skipped"] += source.get("skipped", 0)
    target["resource_wait"] += source.get("resource_wait", 0)


def _problem_class_for_error_domain(error_domain: ErrorDomain | None) -> ProblemClass | None:
    """为 UNKNOWN 等兜底码补充更接近现场语义的问题大类."""
    if error_domain in {ErrorDomain.DEVICE, ErrorDomain.NETWORK}:
        return ProblemClass.HARDWARE
    return None


class RuntimeInboxProcessorBridge:
    """RuntimeInbox 三阶段 processor composition.

    与 InboxBatchProcessor._process_claimed_message 等价的单一入口,
    但内部按 validation → orchestration → write-back 三阶段拆分.
    Task 6 的 process_runtime_inbox_batch Celery task 将通过本类调用.
    """

    def __init__(
        self,
        *,
        validation_service: RuntimeInboxValidationService | None = None,
        processor_service: RuntimeInboxOrchestratorDelegate | None = None,
        writeback_service: RuntimeInboxWriteBackService | None = None,
        inbox_service: RuntimeInboxService | None = None,
        inbox_repository: RuntimeInboxRepository | None = None,
    ) -> None:
        self._validation_service = validation_service or RuntimeInboxValidationService()
        self._processor_service = processor_service or RuntimeInboxOrchestratorDelegate()
        self._writeback_service = writeback_service or RuntimeInboxWriteBackService()
        self._inbox_service = inbox_service or runtime_inbox_service
        self._inbox_repository = inbox_repository or runtime_inbox_repository

    @property
    def inbox_service(self) -> RuntimeInboxService:
        """RuntimeInboxService 实例。

        终态写回（mark_processed / mark_failed / mark_dead_letter）一律走
        RuntimeInboxService，作用于 RuntimeInbox 表的 processor_token fencing。
        不再 fallback 到 legacy WorklineInboxService（Task 7c-5 修复）。
        """
        return self._inbox_service

    @property
    def inbox_repository(self) -> RuntimeInboxRepository:
        """RuntimeInboxRepository 实例。

        加载 RuntimeInbox ORM（get_by_id）走 RuntimeInboxRepository，
        不再 fallback 到 legacy WorklineInboxRepository（Task 7c-5 修复）。
        """
        return self._inbox_repository

    async def claim_and_process_batch(
        self,
        db: AsyncSession,
        *,
        limit: int,
        processor_token_prefix: str = "runtime-inbox-worker",  # noqa: S107
    ) -> ProcessResult:
        """顺序 claim 并处理 RuntimeInbox (与 InboxBatchProcessor.process_batch 等价).

        单个 worker 每轮 claim 1 条; 跨 worker 并发由数据库 token claim 和
        claim_bucket_key 队首围栏承载.
        """
        if limit <= 0:
            return _empty_result()

        result: ProcessResult = _empty_result()
        remaining = limit
        while remaining > 0:
            processor_token = f"{processor_token_prefix}-{uuid.uuid4()}"
            claim = await self._claim_one(db, processor_token=processor_token)
            if claim is None:
                break
            message_result = await self.process_claimed(db, claim=claim)
            _merge_result(result, message_result)
            remaining -= 1
            if remaining <= 0:
                break
        return result

    async def _claim_one(
        self,
        db: AsyncSession,
        *,
        processor_token: str,
    ) -> dict[str, Any] | None:
        """claim 1 条 RuntimeInbox 行.

        优先使用 RuntimeInboxClaimRepository.claim_received_with_token.
        缺省时 fallback 到旧 WorklineInboxRepository (保持行为等价).
        """
        try:
            from src.app.runtime.orchestration.repositories.runtime_inbox_claim_repository import (
                runtime_inbox_claim_repository,
            )

            claims = await runtime_inbox_claim_repository.claim_received_with_token(
                db,
                limit=1,
                processor_token=processor_token,
                stale_after_seconds=WORKLINE_INBOX_PROCESSING_STALE_SECONDS,
            )
            if claims:
                return claims[0]
        except Exception as exc:
            logger.debug(f"RuntimeInbox claim repository 不可用, 跳过: {exc}")
        return None

    async def process_claimed(  # noqa: PLR0911, PLR0912
        self,
        db: AsyncSession,
        *,
        claim: dict[str, Any] | Any,
    ) -> ProcessResult:
        """处理已被当前 worker claim 的单条 RuntimeInbox 消息.

        与 InboxBatchProcessor._process_claimed_message 等价, 但内部委托
        Validation / Processor / Write-back 三阶段.
        """
        result: ProcessResult = _empty_result()
        inbox = await self.inbox_repository.get_by_id(db, claim["id"] if isinstance(claim, dict) else claim.id)
        if inbox is None:
            result["skipped"] += 1
            return result

        processor_token = (
            claim.get("processor_token") if isinstance(claim, dict) else getattr(claim, "processor_token", None)
        )
        if not processor_token:
            processor_token = f"runtime-inbox-worker-{uuid.uuid4()}"

        diagnostic_inbox = _snapshot_inbox_for_diagnostic(inbox)
        inbox_pk_text = str(diagnostic_inbox.id or getattr(inbox, "id", "unknown"))
        inbox_pk: int | None = None
        try:
            inbox_pk = resolve_required_pk(inbox, "inbox", "id", "inbox_id")
            if isinstance(claim, dict) and claim.get("id") and claim["id"] != inbox_pk:
                result["skipped"] += 1
                return result

            payload = _payload_for_inbox(inbox)
            resolved_event_type = canonical_event_type(payload)

            # ========== Stage 1: Validation (SCAN gate) ==========
            (
                session,
                workline,
                device,
                command,
                devices_by_role,
                services,
                safety_checked,
            ) = await _load_related_entities(db, inbox, resolved_event_type=resolved_event_type)

            validation_outcome = await self._validation_service.pre_gate(
                db,
                inbox=inbox,
                resolved_event_type=resolved_event_type,
                workline=workline,
            )
            if not validation_outcome.proceed_to_orchestrator:
                # SCAN gate 失败 -> 终态 FAILED.
                _require_fenced_update(
                    await self.inbox_service.mark_failed(
                        db,
                        inbox_id=inbox_pk,
                        lease_token=processor_token,
                        error_message=validation_outcome.error_message or "validation failed",
                        retryable=False,
                    ),
                    action="mark_failed",
                    inbox_id=inbox_pk,
                )
                await db.commit()
                result["failed"] += 1
                result["processed"] += 1
                return result

            # ========== Stage 1b: ESTOP / TIMER 专用路由 ==========
            inbox_kind_value = _kind_value(inbox)
            routing_outcome = self._validation_service.classify_estop_or_timer(
                resolved_event_type=resolved_event_type,
                inbox_kind=inbox_kind_value,
            )
            if routing_outcome.estop_event:
                estop_processed = await _handle_estop(
                    db,
                    inbox=inbox,
                    inbox_pk=inbox_pk,
                    payload=payload,
                    session=session,
                    workline=workline,
                    device=device,
                    command=command,
                    processor_token=processor_token,
                    inbox_service=self.inbox_service,
                )
                await db.commit()
                if estop_processed:
                    result["success"] += 1
                else:
                    result["failed"] += 1
                result["processed"] += 1
                return result
            if routing_outcome.timer_timeout_event:
                _ = await _handle_timer_timeout(
                    db,
                    inbox=inbox,
                    inbox_pk=inbox_pk,
                    payload=payload,
                    processor_token=processor_token,
                    inbox_service=self.inbox_service,
                )
                await db.commit()
                result["success"] += 1
                result["processed"] += 1
                return result

            if not safety_checked:
                from src.app.workline.services.safety_service import workline_safety_service

                _ = await workline_safety_service.assert_accepting_work(db, workline_id=resolve_entity_id(workline))

            if session is None or workline is None:
                error_msg = "Inbox processing missing session/workline context"
                await _record_diagnostic(
                    db,
                    inbox=inbox,
                    error_code=ErrorCode.SESSION_CONTEXT_MISSING,
                    message=error_msg,
                    session=session,
                    workline=workline,
                    device=device,
                    command=command,
                )
                _require_fenced_update(
                    await self.inbox_service.mark_failed(
                        db,
                        inbox_id=inbox_pk,
                        lease_token=processor_token,
                        error_message=error_msg,
                        retryable=False,
                    ),
                    action="mark_failed",
                    inbox_id=inbox_pk,
                )
                await db.commit()
                result["failed"] += 1
                result["processed"] += 1
                return result

            # ========== Stage 1c: duplicate / late detection ==========
            if _is_duplicate_entry_event(
                inbox=inbox,
                payload=payload,
                session=session,
                workline=workline,
            ) and not _is_resource_wait_retry_for_same_inbox(session, inbox_pk):
                material_conflict = _duplicate_entry_material_conflict(
                    session=session,
                    workline=workline,
                    payload=payload,
                )
                if material_conflict is not None:
                    conflict_message, conflict_details = material_conflict
                    await _record_diagnostic(
                        db,
                        inbox=inbox,
                        error_code=ErrorCode.CALLBACK_SCHEMA_INVALID,
                        message=conflict_message,
                        session=session,
                        workline=workline,
                        device=device,
                        command=command,
                        extra=conflict_details,
                    )
                    _require_fenced_update(
                        await self.inbox_service.mark_dead_letter(
                            db,
                            inbox_id=inbox_pk,
                            lease_token=processor_token,
                            error_message=conflict_message,
                        ),
                        action="mark_dead_letter",
                        inbox_id=inbox_pk,
                    )
                    await db.commit()
                    result["failed"] += 1
                    result["processed"] += 1
                    logger.warning(
                        f"Inbox {inbox_pk} rejected conflicting duplicate entry event: "
                        f"session_id={resolve_entity_id(session)}, conflicts={conflict_details['conflicts']}"
                    )
                    return result

                await _record_duplicate_entry_archive_timeline(
                    db,
                    session=session,
                    workline=workline,
                    inbox=inbox,
                    payload=payload,
                    reason="SESSION_ALREADY_IN_PROGRESS_OR_TERMINAL",
                )
                _require_fenced_update(
                    await self.inbox_service.mark_processed(
                        db,
                        inbox_id=inbox_pk,
                        lease_token=processor_token,
                    ),
                    action="mark_processed",
                    inbox_id=inbox_pk,
                )
                await db.commit()
                result["success"] += 1
                result["processed"] += 1
                return result

            if _is_late_or_duplicate_command_result_for_session(
                inbox=inbox,
                payload=payload,
                session=session,
                command=command,
            ):
                await _record_late_command_result_archive_timeline(
                    db,
                    session=session,
                    workline=workline,
                    inbox=inbox,
                    command=command,
                    payload=payload,
                    reason="COMMAND_RESULT_NO_LONGER_MATCHES_SESSION_WAIT",
                )
                _require_fenced_update(
                    await self.inbox_service.mark_processed(
                        db,
                        inbox_id=inbox_pk,
                        lease_token=processor_token,
                    ),
                    action="mark_processed",
                    inbox_id=inbox_pk,
                )
                await db.commit()
                result["success"] += 1
                result["processed"] += 1
                return result

            # ========== Stage 2: Orchestration (delegated) ==========
            write_state = WriteBackState()
            session_snapshot = _session_write_snapshot(session)
            sse_workline_id = resolve_entity_id(workline)
            sse_session_id = resolve_entity_id(session)
            write_callback = self._writeback_service.build_write_callback(
                db,
                session=session,
                workline=workline,
                inbox=inbox,
                devices_by_role=devices_by_role,
                device=device,
                command=command,
                inbox_pk=inbox_pk,
                session_snapshot=session_snapshot,
                sse_workline_id=sse_workline_id,
                sse_session_id=sse_session_id,
                processor_token=processor_token,
                state=write_state,
            )

            orch_result: "OrchestratorResult" = await self._processor_service.process(  # noqa: UP037
                db,
                session=session,
                workline=workline,
                inbox=inbox,
                devices_by_role=devices_by_role,
                services=services,
                trace_id=getattr(inbox, "trace_id", None) or "",
                write_callback=write_callback,
            )

            # ========== Stage 3: Result dispatch ==========
            if orch_result.success:
                if not write_state.write_effects_applied:
                    raise RuntimeError("WRITE lock callback was not executed for successful orchestrator result")
                if write_state.disposition == WriteBackDisposition.RESOURCE_RETRY:
                    result["resource_wait"] += 1
                    logger.info(f"Inbox {inbox_pk} resource wait, parked for retry")
                else:
                    result["success"] += 1
                    logger.info(f"Inbox {inbox_pk} 处理成功")
                if write_state.enqueue_outbox_dispatch:
                    from src.core.task_queue_gateway import task_queue_gateway

                    task_queue_gateway.enqueue_outbox(limit=50)
            else:
                error_msg = orch_result.error or "Unknown error"
                mapped_error_code, mapped_error_domain = map_failure_to_diagnostic(
                    failure=None,
                    error_code=orch_result.error_code,
                )
                await _record_diagnostic(
                    db,
                    inbox=diagnostic_inbox,
                    error_code=mapped_error_code,
                    error_domain=mapped_error_domain,
                    problem_class=_problem_class_for_error_domain(mapped_error_domain),
                    message=error_msg,
                    session=session,
                    workline=workline,
                    device=device,
                    command=command,
                )
                _require_fenced_update(
                    await self.inbox_service.mark_failed(
                        db,
                        inbox_id=inbox_pk,
                        lease_token=processor_token,
                        error_message=error_msg,
                        retryable=False,
                    ),
                    action="mark_failed",
                    inbox_id=inbox_pk,
                )
                await db.commit()
                result["failed"] += 1
                logger.warning(f"Inbox {inbox_pk} 处理失败: {error_msg}")

            result["processed"] += 1

        except SessionResolveError as e:
            logger.warning(f"Inbox {inbox_pk_text} session resolve failed: {e}")
            with suppress(Exception):
                await db.rollback()
            await _record_diagnostic(
                db,
                inbox=diagnostic_inbox,
                error_code=ErrorCode.SESSION_RESOLVE_FAILED,
                message=str(e),
            )
            try:
                if inbox_pk is not None:
                    _require_fenced_update(
                        await self.inbox_service.mark_failed(
                            db,
                            inbox_id=inbox_pk,
                            lease_token=processor_token,
                            error_message=str(e),
                            retryable=False,
                        ),
                        action="mark_failed",
                        inbox_id=inbox_pk,
                    )
                    await db.commit()
            except Exception as mark_error:
                with suppress(Exception):
                    await db.rollback()
                logger.warning(f"Inbox {inbox_pk_text} session resolve 失败补记失败: {mark_error}")
            result["failed"] += 1
            result["processed"] += 1

        except WorkLineSafetyBlocked as e:
            logger.warning(f"Inbox {inbox_pk_text} blocked by WorkLine safety state: {e}")
            with suppress(Exception):
                await db.rollback()
            await _record_diagnostic(
                db,
                inbox=diagnostic_inbox,
                error_code=ErrorCode.UNKNOWN,
                error_domain=ErrorDomain.WORKFLOW,
                message=str(e),
            )
            try:
                if inbox_pk is not None:
                    # RuntimeInboxService.mark_failed(retryable=True) 内部按
                    # attempt_count 计算指数退避 next_retry_at, 等价 park_for_retry 语义。
                    _require_fenced_update(
                        await self.inbox_service.mark_failed(
                            db,
                            inbox_id=inbox_pk,
                            lease_token=processor_token,
                            error_message=str(e),
                            retryable=True,
                        ),
                        action="mark_failed",
                        inbox_id=inbox_pk,
                    )
                    await db.commit()
            except Exception as mark_error:
                with suppress(Exception):
                    await db.rollback()
                logger.warning(f"Inbox {inbox_pk_text} safety blocked 补记失败: {mark_error}")
            result["failed"] += 1
            result["processed"] += 1

        except TimeoutError:
            logger.error(f"Inbox {inbox_pk} 处理超时 (> {INBOX_PROCESS_TIMEOUT_SECONDS}s)")
            with suppress(Exception):
                await db.rollback()
            await _record_diagnostic(
                db,
                inbox=diagnostic_inbox,
                error_code=ErrorCode.INBOX_PROCESSING_TIMEOUT,
                message=f"Inbox processing timeout (> {INBOX_PROCESS_TIMEOUT_SECONDS}s)",
            )
            try:
                pk_to_mark = inbox_pk or diagnostic_inbox.id
                if pk_to_mark is not None:
                    _require_fenced_update(
                        await self.inbox_service.mark_failed(
                            db,
                            inbox_id=pk_to_mark,
                            lease_token=processor_token,
                            error_message=f"处理超时 (> {INBOX_PROCESS_TIMEOUT_SECONDS}s)",
                            retryable=False,
                        ),
                        action="mark_failed",
                        inbox_id=pk_to_mark,
                    )
                    await db.commit()
            except Exception as mark_error:
                with suppress(Exception):
                    await db.rollback()
                logger.warning(f"Inbox 超时标记失败: {mark_error}")
            result["failed"] += 1
            result["processed"] += 1

        except Exception as e:
            logger.exception(f"Inbox {inbox_pk_text} 处理异常")
            with suppress(Exception):
                await db.rollback()
            await _record_diagnostic(
                db,
                inbox=diagnostic_inbox,
                error_code=ErrorCode.UNKNOWN,
                message=str(e),
            )
            try:
                pk_to_mark = inbox_pk or diagnostic_inbox.id
                if pk_to_mark is not None:
                    _require_fenced_update(
                        await self.inbox_service.mark_failed(
                            db,
                            inbox_id=pk_to_mark,
                            lease_token=processor_token,
                            error_message=str(e),
                            retryable=False,
                        ),
                        action="mark_failed",
                        inbox_id=pk_to_mark,
                    )
                    await db.commit()
            except Exception as mark_error:
                with suppress(Exception):
                    await db.rollback()
                logger.warning(f"Inbox {inbox_pk_text} 异常补记失败: {mark_error}")
            result["failed"] += 1
            result["processed"] += 1

        from src.app.sys.services.event_stream_service import publish_deferred_sse_events

        await publish_deferred_sse_events(db)
        return result


def _kind_value(entity: Any) -> str | None:
    value = getattr(getattr(entity, "kind", None), "value", getattr(entity, "kind", None))
    return value if isinstance(value, str) and value else None


def _is_duplicate_entry_event(
    *,
    inbox: Any,
    payload: dict[str, Any],
    session: Any,
    workline: Any,
) -> bool:
    """简化版重复入口识别 (与 InboxBatchProcessor._is_duplicate_entry_event_for_session 等价)."""
    if _kind_value(inbox) != "DEVICE_EVENT":
        return False
    if canonical_event_type(payload) not in _entry_event_types_for_workline(workline):
        return False
    if _is_payload_invalid_entry_replay(payload=payload, session=session):
        return False
    terminal_statuses = {"COMPLETED", "FAILED", "CANCELLED"}
    busy_statuses = {"WAITING_DEVICE_RESULT", "WAITING_EXTERNAL", "MANUAL_HOLD"}
    status = _session_status_value(session)
    if status in terminal_statuses or status in busy_statuses:
        return True
    if getattr(session, "awaiting_device_command_code", None) is not None:
        return True
    current_wait_type = getattr(session, "current_wait_type", None)
    return bool(current_wait_type)


def _is_payload_invalid_entry_replay(*, payload: dict[str, Any], session: Any) -> bool:
    """允许 payload 校验失败后的人工 replay 重新进入编排。"""
    replay_of_event_id = payload.get("replay_of_event_id")
    if not isinstance(replay_of_event_id, str) or not replay_of_event_id:
        return False
    if _session_status_value(session) != "MANUAL_HOLD":
        return False
    if string_value(getattr(session, "failure_code", None)) != "PAYLOAD_INVALID":
        return False
    if getattr(session, "awaiting_device_command_code", None) is not None:
        return False
    return not bool(string_value(getattr(session, "current_wait_type", None)))


def _is_resource_wait_retry_for_same_inbox(session: Any, inbox_id: int) -> bool:
    """识别同一 inbox 从 RESOURCE_WAIT 唤醒后的重试。"""
    if getattr(session, "current_wait_type", None) != "RESOURCE_WAIT":
        return False
    resource_wait = payload_dict(payload_dict(getattr(session, "context_json", None)).get("resource_wait"))
    return optional_int(resource_wait.get("inbox_id")) == inbox_id


def _session_context(session: Any) -> dict[str, Any]:
    raw_context = getattr(session, "context_json", None)
    return dict(raw_context) if isinstance(raw_context, dict) else {}


def _normalized_entry_material_evidence(*, plugin_key: str | None, payload: dict[str, Any]) -> dict[str, str]:
    """提取 capability 拥有的入口物料证据。"""
    try:
        six_in_one = parse_workline_six_in_one(plugin_key, payload)
    except (TypeError, ValueError):
        return {}
    if six_in_one is None:
        return {}
    evidence: dict[str, str] = {}
    for field_name, raw_value in six_in_one.iter_business_fields():
        if not isinstance(field_name, str) or not field_name:
            continue
        if isinstance(raw_value, str):
            value = raw_value.strip()
            if value:
                evidence[field_name] = value
    return evidence


def _duplicate_entry_material_conflict(
    *,
    session: Any,
    workline: Any,
    payload: dict[str, Any],
) -> tuple[str, dict[str, Any]] | None:
    """判断重复入口是否与 session 初始物料证据冲突。"""
    plugin_key = string_value(getattr(session, "plugin_key", None)) or string_value(
        getattr(workline, "plugin_key", None)
    )
    if not plugin_key:
        return None
    session_context = _session_context(session)
    initial_payload = payload_dict(session_context.get("initial_payload") or session_context.get("source_payload"))
    if not initial_payload:
        return None
    expected = _normalized_entry_material_evidence(plugin_key=plugin_key, payload=initial_payload)
    actual = _normalized_entry_material_evidence(plugin_key=plugin_key, payload=payload)
    if not expected or not actual:
        return None
    conflicts = {
        field_name: {"expected": expected[field_name], "actual": actual[field_name]}
        for field_name in sorted(expected.keys() & actual.keys())
        if expected[field_name] != actual[field_name]
    }
    if not conflicts:
        return None
    details = {
        "reason": "ENTRY_MATERIAL_IDENTITY_CONFLICT",
        "conflicts": conflicts,
        "expected": expected,
        "actual": actual,
    }
    message = "ENTRY_MATERIAL_IDENTITY_CONFLICT: duplicate entry event conflicts with session initial material evidence"
    return message, details


async def _load_related_entities(
    db: Any,
    inbox: Any,
    *,
    resolved_event_type: str | None = None,
) -> tuple[
    Any | None,
    Any | None,
    Any | None,
    Any | None,
    dict[str, list[Any]],
    Any,
    bool,
]:
    """加载关联实体 (与 InboxBatchProcessor._load_related_entities 等价的 tuple 返回)."""
    from src.app.runtime.orchestration.services.inbox.inbox_batch_processor import (
        _load_related_entities as _legacy_load_related_entities,
    )

    loaded = await _legacy_load_related_entities(db, inbox, resolved_event_type=resolved_event_type)
    return (
        loaded.get("session"),
        loaded.get("workline"),
        loaded.get("device"),
        loaded.get("command"),
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
    command: Any,
    processor_token: str,
    inbox_service: RuntimeInboxService,
) -> bool:
    """ESTOP_PRESSED 急停处理 (与 InboxBatchProcessor 行为等价)."""
    from src.app.runtime.orchestration.services.inbox.inbox_batch_processor import (  # noqa: F401
        _assert_workline_accepting_runtime_event,
    )
    from src.utils.value_normalization import resolve_entity_id

    workline_pk = resolve_entity_id(workline)
    if workline_pk is None:
        error_msg = "ESTOP_PRESSED missing workline context"
        await _record_diagnostic(
            db,
            inbox=inbox,
            error_code=ErrorCode.SESSION_CONTEXT_MISSING,
            message=error_msg,
            session=session,
            workline=workline,
            device=device,
            command=command,
        )
        _require_fenced_update(
            await inbox_service.mark_failed(
                db,
                inbox_id=inbox_pk,
                lease_token=processor_token,
                error_message=error_msg,
                retryable=False,
            ),
            action="mark_failed",
            inbox_id=inbox_pk,
        )
        return False

    from src.app.workline.services.safety_service import workline_safety_service

    # Fail-safe fencing 特例：handle_estop 内部立即提交安全冻结/排空；
    # RuntimeInbox 终态 fencing 必须后置，绝不能把两者重构为同一可回滚事务。
    _ = await workline_safety_service.handle_estop(
        db,
        workline_id=workline_pk,
        source_inbox_id=inbox_pk,
        source_device_id=resolve_entity_id(device) or getattr(inbox, "device_id", None),
        source_command_id=resolve_entity_id(command) or getattr(inbox, "command_id", None),
        trigger_payload=payload,
    )
    _require_fenced_update(
        await inbox_service.mark_processed(
            db,
            inbox_id=inbox_pk,
            lease_token=processor_token,
        ),
        action="mark_processed",
        inbox_id=inbox_pk,
    )
    return True


async def _handle_timer_timeout(
    db: Any,
    *,
    inbox: Any,
    inbox_pk: int,
    payload: dict[str, Any],
    processor_token: str,
    inbox_service: RuntimeInboxService,
) -> None:
    """TIMER_TIMEOUT 路由到 reconciliation service (与 InboxBatchProcessor 等价)."""
    from src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl import (
        workline_runtime_reconciliation_service,
    )

    payload_data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    execution_session_id = optional_int(getattr(inbox, "execution_session_id", None))
    session_id = execution_session_id or optional_int(payload_data.get("session_id"))
    _ = await workline_runtime_reconciliation_service.handle_timer_timeout(
        db,
        session_id=session_id,
        inbox_id=inbox_pk,
        payload=payload,
        legacy_source_inbox_id=None,
        correlation_id=string_value(getattr(inbox, "correlation_id", None)) or None,
        trace_id=string_value(getattr(inbox, "trace_id", None)) or None,
    )
    _require_fenced_update(
        await inbox_service.mark_processed(
            db,
            inbox_id=inbox_pk,
            lease_token=processor_token,
        ),
        action="mark_processed",
        inbox_id=inbox_pk,
    )


# Public alias used by callers.
RuntimeInboxProcessorService = RuntimeInboxProcessorBridge


__all__ = [
    "ProcessResult",
    "RuntimeInboxProcessorBridge",
    "RuntimeInboxProcessorService",
]
