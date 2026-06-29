"""TraceQueryService - 只读 TRACE 聚合查询服务。

只负责把已有事实表/投影表按统一 trace 键拼成可读视图：
- callback_logs
- workline_inbox
- workline_sessions
- device_commands
- system_outbox
- workline_timelines
- workline_diagnostics

注意：不引入任何额外持久化，不做 trace 宽表。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import or_, select

from src.app.callback.repositories.callback_log_repository import (
    CallbackLogRepository,
    callback_log_repository,
)
from src.app.device.models.command import DeviceCommand
from src.app.device.repositories.command_repository import (
    DeviceCommandRepository,
    device_command_repository,
)
from src.app.resource.models import (
    RackBinMount,
    ResourceStateEvent,
)
from src.app.runtime.orchestration.consumers.diagnostics_bridge import (
    DiagnosticCard,
    DiagnosticContext,
    ErrorCode,
    ErrorDomain,
    ProblemClass,
    Recoverability,
    Severity,
    build_diagnostic_card,
    build_diagnostic_context,
    build_diagnostic_event,
    get_diagnostic_code_definition,
)
from src.app.sys.models import SystemOutbox
from src.app.workline.models.dispatch_attempt import WorklineDispatchAttempt
from src.app.workline.models.inbox import WorklineInbox
from src.app.workline.models.runtime import (
    DiagnosisVerdictResponse,
    DiagnosticCardResponse,
    TraceBlockingPointResponse,
)
from src.app.workline.models.runtime_hold import RuntimeHold
from src.app.workline.models.timeline import WorklineTimeline
from src.app.workline.repositories import inbox_repository
from src.app.workline.repositories.diagnostic_repository import workline_diagnostic_repository
from src.app.workline.repositories.session_repository import (
    WorklineSessionRepository,
    workline_session_repository,
)
from src.app.workline.repositories.workline_repository import WorkLineRepository, workline_repository
from src.app.workline.trace_context import TraceContext

# 导入公共工具函数
from src.app.workline.utils import payload_dict
from src.core.base_service import BaseService
from src.utils.value_normalization import coerce_optional_str, optional_enum_str

from .diagnosis_verdict_builder import DiagnosisVerdictBuilder, diagnosis_verdict_builder

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

    from src.app.workline.models.diagnostic import WorklineDiagnostic
    from src.app.workline.models.session import WorklineSession
    from src.app.workline.repositories.diagnostic_repository import WorklineDiagnosticRepository

_SESSION_FAILURE_CODE_MAP: dict[str, ErrorCode] = {
    "DEVICE_TIMEOUT": ErrorCode.DEVICE_TIMEOUT,
    "DEVICE_UNREACHABLE": ErrorCode.DEVICE_UNREACHABLE,
    "PLUGIN_EXECUTION_FAILED": ErrorCode.PLUGIN_EXECUTION_FAILED,
    "PLUGIN_TRANSITION_INVALID": ErrorCode.PLUGIN_TRANSITION_INVALID,
    "CONTRACT_MISMATCH": ErrorCode.CONTRACT_MISMATCH,
    "CONFIG_INVALID": ErrorCode.CONFIG_INVALID,
    "CALLBACK_SCHEMA_INVALID": ErrorCode.CALLBACK_SCHEMA_INVALID,
    "INBOX_RETRY_EXHAUSTED": ErrorCode.INBOX_RETRY_EXHAUSTED,
    "WMS_TIMEOUT": ErrorCode.WMS_TIMEOUT,
}

_RESOURCE_RECONCILIATION_REASON_CODES: tuple[str, ...] = (
    "POST_EXCHANGE_RELATIONS_MISSING_BIN_MOUNTS",
    "RACK_BIN_SLOT_CONFLICT",
    "BIN_ACTIVE_MOUNT_CONFLICT",
    "RACK_PLACEMENT_CONFLICT",
)


@dataclass(frozen=True, slots=True)
class TraceQueryResult:
    """TRACE 查询结果聚合视图。"""

    trace: TraceContext
    callback_logs: list[Any] = field(default_factory=list)
    inboxes: list[WorklineInbox] = field(default_factory=list)
    session: WorklineSession | None = None
    sessions: list[WorklineSession] = field(default_factory=list)
    commands: list[DeviceCommand] = field(default_factory=list)
    outboxes: list[SystemOutbox] = field(default_factory=list)
    dispatch_attempts: list[WorklineDispatchAttempt] = field(default_factory=list)
    timelines: list[WorklineTimeline] = field(default_factory=list)
    diagnostics: list[DiagnosticContext] = field(default_factory=list)
    resource_state_events: list[ResourceStateEvent] = field(default_factory=list)
    rack_bin_mounts: list[RackBinMount] = field(default_factory=list)
    runtime_holds: list[RuntimeHold] = field(default_factory=list)
    workline_runtime_status: str | None = None
    workline_start_admission_status: str | None = None
    workline_start_admission_message: str | None = None
    workline_start_admission_failed_device_code: str | None = None
    workline_start_admission_checked_at: datetime | None = None
    workline_last_start_request_id: str | None = None
    workline_last_start_trace_id: str | None = None

    @property
    def summary(self) -> dict[str, int]:
        return {
            "callback_logs": len(self.callback_logs),
            "inboxes": len(self.inboxes),
            "commands": len(self.commands),
            "outboxes": len(self.outboxes),
            "dispatch_attempts": len(self.dispatch_attempts),
            "timelines": len(self.timelines),
            "diagnostics": len(self.diagnostics),
        }


def _merge_unique_by_id(existing: list[Any], items: list[Any]) -> list[Any]:
    existing_ids = {getattr(item, "id", None) for item in existing if getattr(item, "id", None) is not None}
    return existing + [item for item in items if getattr(item, "id", None) not in existing_ids]


def _callback_diagnostic_extra(callback: Any) -> dict[str, Any]:
    return {
        "callback_type": getattr(callback, "callback_type", None),
        "ingress_outcome": getattr(callback, "ingress_outcome", None),
        "failure_stage": getattr(callback, "failure_stage", None),
        "response_status": getattr(callback, "response_status", None),
        "response_time_ms": getattr(callback, "response_time_ms", None),
    }


def _timeline_trace(trace: TraceContext, timeline: WorklineTimeline) -> TraceContext:
    payload = payload_dict(getattr(timeline, "payload_json", None))
    timeline_trace = TraceContext.from_request(
        request_id=coerce_optional_str(payload.get("request_id")),
        trace_id=coerce_optional_str(payload.get("trace_id")) or trace.trace_id,
        canonical_event_type=coerce_optional_str(payload.get("canonical_event_type")),
        transition=coerce_optional_str(getattr(timeline, "to_status", None))
        or coerce_optional_str(getattr(timeline, "action_type", None)),
    )
    return timeline_trace.with_session(
        SimpleNamespace(
            id=getattr(timeline, "session_id", None),
            workline_id=getattr(timeline, "workline_id", None),
            trace_id=getattr(timeline, "trace_id", None),
        )
    )


class TraceQueryService(BaseService[Any, Any]):
    """只读 TRACE 聚合查询服务。"""

    def __init__(
        self,
        callback_log_repo: CallbackLogRepository | None = None,
        session_repo: WorklineSessionRepository | None = None,
        command_repo: DeviceCommandRepository | None = None,
        inbox_repo: Any | None = None,
        diagnostic_repo: WorklineDiagnosticRepository | None = None,
        workline_repo: WorkLineRepository | None = None,
        verdict_builder: DiagnosisVerdictBuilder | None = None,
    ) -> None:
        super().__init__(inbox_repository, enable_cache=False)
        self.callback_log_repo = callback_log_repo or callback_log_repository
        self.session_repo = session_repo or workline_session_repository
        self.command_repo = command_repo or device_command_repository
        self.inbox_repo = inbox_repo or inbox_repository
        self.diagnostic_repo = diagnostic_repo or workline_diagnostic_repository
        self.workline_repo = workline_repo or workline_repository
        self.verdict_builder = verdict_builder or diagnosis_verdict_builder

    async def by_request_id(self, db: AsyncSession, request_id: str) -> TraceQueryResult:
        return await self.query(db, request_id=request_id)

    async def by_trace_id(self, db: AsyncSession, trace_id: str) -> TraceQueryResult:
        return await self.query(db, trace_id=trace_id)

    async def by_session_id(self, db: AsyncSession, session_id: int) -> TraceQueryResult:
        return await self.query(db, session_id=session_id)

    async def path_by_trace_id(self, db: AsyncSession, trace_id: str) -> TraceQueryResult:
        return await self.query_path(db, trace_id=trace_id)

    async def path_by_session_id(self, db: AsyncSession, session_id: int) -> TraceQueryResult:
        return await self.query_path(db, session_id=session_id)

    async def by_command_code(self, db: AsyncSession, command_code: str) -> TraceQueryResult:
        return await self.query(db, command_code=command_code)

    async def by_dispatch_key(self, db: AsyncSession, dispatch_key: str) -> TraceQueryResult:
        return await self.query(db, dispatch_key=dispatch_key)

    async def by_exchange_request_code(self, db: AsyncSession, exchange_request_code: str) -> TraceQueryResult:
        result = await self.query(db, dispatch_key=exchange_request_code)
        trace_id = result.trace.trace_id

        resource_state_events = await self._load_resource_state_events_for_exchange(
            db,
            exchange_request_code=exchange_request_code,
            trace_id=trace_id,
        )
        rack_bin_mounts = await self._load_rack_bin_mounts_for_resource_events(
            db,
            resource_state_events=resource_state_events,
            trace_id=trace_id,
        )
        runtime_holds = await self._load_runtime_holds_for_resource_events(
            db,
            resource_state_events=resource_state_events,
            trace_id=trace_id,
        )

        return replace(
            result,
            resource_state_events=resource_state_events,
            rack_bin_mounts=rack_bin_mounts,
            runtime_holds=runtime_holds,
        )

    async def query(
        self,
        db: AsyncSession,
        *,
        request_id: str | None = None,
        trace_id: str | None = None,
        session_id: int | None = None,
        command_code: str | None = None,
        dispatch_key: str | None = None,
    ) -> TraceQueryResult:
        trace = TraceContext.from_request(request_id=request_id, trace_id=trace_id)
        request_id = trace.request_id
        trace_id = trace.trace_id
        callback_logs: list[Any] = []
        session: WorklineSession | None = None
        sessions: list[WorklineSession] = []
        commands: list[DeviceCommand] = []
        outboxes: list[SystemOutbox] = []
        dispatch_attempts: list[WorklineDispatchAttempt] = []
        inboxes: list[WorklineInbox] = []
        timelines: list[WorklineTimeline] = []
        diagnostics: list[DiagnosticContext] = []

        if request_id:
            callback_log = await self.callback_log_repo.get_by_request_id(db, request_id)
            if callback_log is not None:
                callback_logs.append(callback_log)
                trace = trace.with_request_id(getattr(callback_log, "request_id", None))
                trace = trace.with_trace_id(getattr(callback_log, "trace_id", None))
                request_id = trace.request_id
                trace_id = trace.trace_id
                diagnostics.append(
                    build_diagnostic_context(
                        trace=trace,
                        extra=_callback_diagnostic_extra(callback_log),
                    )
                )

        if trace_id:
            callback_logs = await self.callback_log_repo.get_by_trace_id(db, trace_id)
            if callback_logs:
                first_callback = callback_logs[0]
                trace = trace.with_request_id(getattr(first_callback, "request_id", None))
                trace = trace.with_trace_id(getattr(first_callback, "trace_id", None))
                request_id = trace.request_id
                trace_id = trace.trace_id
                diagnostics.extend(self._diagnostic_from_callbacks(trace, callback_logs))

        if command_code:
            command = await self.command_repo.get_by_command_code(db, command_code)
            if command is not None:
                commands.append(command)
                trace = trace.with_command(command)
                request_id = trace.request_id
                trace_id = trace.trace_id

        if session_id is not None and session is None:
            session = await self.session_repo.get_by_id(db, session_id)
            if session is not None:
                trace = trace.with_session(session)
                session_id = getattr(session, "id", session_id)
                request_id = trace.request_id
                trace_id = trace.trace_id

        if session is None and trace.trace_id:
            session = await self.session_repo.get_by_trace_id(db, trace.trace_id)
            if session is not None:
                trace = trace.with_session(session)
                sessions = _merge_unique_by_id(sessions, [session])

        if dispatch_key:
            outbox = await self._get_outbox_by_dispatch_key(db, dispatch_key)
            if outbox is not None:
                outboxes.append(outbox)
                trace = trace.with_outbox(outbox)
                session_ref_id = getattr(outbox, "session_id", None)
                if session is None and session_ref_id is not None:
                    session = await self.session_repo.get_by_id(db, session_ref_id)
                    if session is not None:
                        trace = trace.with_session(session)
                        session_id = getattr(session, "id", session_id)
                        sessions = _merge_unique_by_id(sessions, [session])
                request_id = trace.request_id
                trace_id = trace.trace_id

        if session is not None:
            trace = trace.with_session(session)
            if trace.trace_id:
                trace_id = trace.trace_id
                callback_logs = await self._merge_callbacks_by_trace_id(db, trace.trace_id, callback_logs)
            commands = await self._load_commands_for_session(db, session, commands)
            outboxes = await self._load_outboxes_for_session(db, session, outboxes)
            dispatch_attempts = await self._load_dispatch_attempts_for_outboxes(db, outboxes)
            inboxes = await self._load_inboxes_for_session(db, session, inboxes)
            timelines = await self._load_timelines_for_session(db, session)
        elif trace.trace_id:
            trace_id = trace.trace_id
            commands = await self._load_commands_by_trace_id(db, trace.trace_id, commands)
            outboxes = await self._load_outboxes_by_trace_id(db, trace.trace_id, outboxes)
            inboxes = await self._load_inboxes_by_trace_id(db, trace.trace_id)
            timelines = await self._load_timelines_by_trace_id(db, trace.trace_id)
            if not commands and trace.command_code:
                command = await self.command_repo.get_by_command_code(db, trace.command_code)
                if command is not None:
                    commands.append(command)

        diagnostics.extend(self._diagnostic_for_session(trace, session) if session is not None else [])
        diagnostics.extend(self._diagnostic_for_inboxes(trace, inboxes))
        diagnostics.extend(self._diagnostic_for_commands(trace, commands))
        diagnostics.extend(self._diagnostic_for_outboxes(trace, outboxes))
        diagnostics.extend(self._diagnostic_for_timelines(trace, timelines))
        persisted_diagnostics = await self._load_persisted_diagnostics(db, trace_id)
        diagnostics.extend(self._diagnostic_from_persisted(trace, persisted_diagnostics))

        if not diagnostics:
            diagnostics.append(
                build_diagnostic_context(
                    trace=trace,
                    session=session,
                    inbox=inboxes[0] if inboxes else None,
                    command=commands[0] if commands else None,
                    outbox=outboxes[0] if outboxes else None,
                )
            )

        workline_projection = await self._load_workline_projection(
            db,
            session=session,
            outboxes=outboxes,
            commands=commands,
            timelines=timelines,
        )

        return TraceQueryResult(
            trace=trace,
            callback_logs=callback_logs,
            inboxes=inboxes,
            session=session,
            sessions=sessions or ([session] if session is not None else []),
            commands=commands,
            outboxes=outboxes,
            dispatch_attempts=dispatch_attempts,
            timelines=timelines,
            diagnostics=diagnostics,
            **workline_projection,
        )

    async def query_path(
        self,
        db: AsyncSession,
        *,
        trace_id: str | None = None,
        session_id: int | None = None,
    ) -> TraceQueryResult:
        trace = TraceContext.from_request(trace_id=trace_id)
        callback_logs: list[Any] = []
        session: WorklineSession | None = None
        sessions: list[WorklineSession] = []
        commands: list[DeviceCommand] = []
        outboxes: list[SystemOutbox] = []
        inboxes: list[WorklineInbox] = []
        timelines: list[WorklineTimeline] = []
        diagnostics: list[DiagnosticContext] = []

        if session_id is not None:
            session = await self.session_repo.get_by_id(db, session_id)
            if session is not None:
                trace = trace.with_session(session)
                session_id = getattr(session, "id", session_id)
                trace_id = trace.trace_id
                sessions = _merge_unique_by_id(sessions, [session])

        if session is None and trace.trace_id:
            session = await self.session_repo.get_by_trace_id(db, trace.trace_id)
            if session is not None:
                trace = trace.with_session(session)
                session_id = getattr(session, "id", session_id)
                sessions = _merge_unique_by_id(sessions, [session])

        if trace.trace_id:
            trace_id = trace.trace_id
            callback_logs = await self.callback_log_repo.get_summary_by_trace_id(db, trace.trace_id)
            if callback_logs:
                first_callback = callback_logs[0]
                trace = trace.with_request_id(getattr(first_callback, "request_id", None))
                trace = trace.with_trace_id(getattr(first_callback, "trace_id", None))
                diagnostics.extend(self._diagnostic_from_callbacks(trace, callback_logs))

        if session is not None:
            trace = trace.with_session(session)
            if trace.trace_id:
                trace_id = trace.trace_id
            commands = await self._load_commands_for_session(db, session, commands)
            outboxes = await self._load_outboxes_for_session(db, session, outboxes)
            inboxes = await self._load_inboxes_for_session(db, session, inboxes)
            timelines = await self._load_timelines_for_session(db, session)
        elif trace.trace_id:
            trace_id = trace.trace_id
            commands = await self._load_commands_by_trace_id(db, trace.trace_id, commands)
            outboxes = await self._load_outboxes_by_trace_id(db, trace.trace_id, outboxes)
            inboxes = await self._load_inboxes_by_trace_id(db, trace.trace_id)
            timelines = await self._load_timelines_by_trace_id(db, trace.trace_id)

        diagnostics.extend(self._diagnostic_for_session(trace, session) if session is not None else [])
        diagnostics.extend(self._diagnostic_for_inboxes(trace, inboxes))
        diagnostics.extend(self._diagnostic_for_commands(trace, commands))
        diagnostics.extend(self._diagnostic_for_outboxes(trace, outboxes))
        diagnostics.extend(self._diagnostic_for_timelines(trace, timelines))
        persisted_diagnostics = await self._load_persisted_diagnostics(db, trace_id)
        diagnostics.extend(self._diagnostic_from_persisted(trace, persisted_diagnostics))

        if not diagnostics:
            diagnostics.append(
                build_diagnostic_context(
                    trace=trace,
                    session=session,
                    inbox=inboxes[0] if inboxes else None,
                    command=commands[0] if commands else None,
                    outbox=outboxes[0] if outboxes else None,
                )
            )

        workline_projection = await self._load_workline_projection(
            db,
            session=session,
            outboxes=outboxes,
            commands=commands,
            timelines=timelines,
        )

        return TraceQueryResult(
            trace=trace,
            callback_logs=callback_logs,
            inboxes=inboxes,
            session=session,
            sessions=sessions or ([session] if session is not None else []),
            commands=commands,
            outboxes=outboxes,
            dispatch_attempts=[],
            timelines=timelines,
            diagnostics=diagnostics,
            **workline_projection,
        )

    async def get_blocking_point(self, db: AsyncSession, trace_id: str) -> TraceBlockingPointResponse:
        """返回现场可操作的 blocking point 诊断卡。"""

        result = await self.by_trace_id(db, trace_id)
        return self._build_blocking_point(result, trace_id=trace_id)

    def build_diagnosis_verdict(self, result: TraceQueryResult) -> DiagnosisVerdictResponse:
        """返回 trace detail 与 blocking-point 共用的诊断结论。"""

        return self.verdict_builder.build(result)

    async def _get_outbox_by_dispatch_key(self, db: AsyncSession, dispatch_key: str) -> SystemOutbox | None:
        columns = cast("Any", SystemOutbox).__table__.c
        result = await db.execute(select(SystemOutbox).where(columns.dispatch_key == dispatch_key))
        return result.scalar_one_or_none()

    async def _load_workline_projection(
        self,
        db: AsyncSession,
        *,
        session: WorklineSession | None,
        outboxes: list[SystemOutbox],
        commands: list[DeviceCommand],
        timelines: list[WorklineTimeline],
    ) -> dict[str, Any]:
        workline_id = self._resolve_workline_id(
            session=session,
            outboxes=outboxes,
            commands=commands,
            timelines=timelines,
        )
        if workline_id is None:
            return {}
        workline = await self.workline_repo.get_by_id(db, workline_id)
        if workline is None:
            return {}
        return {
            "workline_runtime_status": optional_enum_str(getattr(workline, "runtime_status", None)),
            "workline_start_admission_status": coerce_optional_str(getattr(workline, "start_admission_status", None)),
            "workline_start_admission_message": coerce_optional_str(getattr(workline, "start_admission_message", None)),
            "workline_start_admission_failed_device_code": coerce_optional_str(
                getattr(workline, "start_admission_failed_device_code", None)
            ),
            "workline_start_admission_checked_at": getattr(workline, "start_admission_checked_at", None),
            "workline_last_start_request_id": coerce_optional_str(getattr(workline, "last_start_request_id", None)),
            "workline_last_start_trace_id": coerce_optional_str(getattr(workline, "last_start_trace_id", None)),
        }

    @staticmethod
    def _resolve_workline_id(
        *,
        session: WorklineSession | None,
        outboxes: list[SystemOutbox],
        commands: list[DeviceCommand],
        timelines: list[WorklineTimeline],
    ) -> int | None:
        if session is not None and session.workline_id is not None:
            return cast("int", session.workline_id)
        for source in (outboxes, commands, timelines):
            for item in source:
                workline_id = getattr(item, "workline_id", None)
                if workline_id is not None:
                    return cast("int", workline_id)
        return None

    async def _load_resource_state_events_for_exchange(
        self,
        db: AsyncSession,
        *,
        exchange_request_code: str,
        trace_id: str | None,
    ) -> list[ResourceStateEvent]:
        columns = cast("Any", ResourceStateEvent).__table__.c
        predicates = [columns.resource_code == exchange_request_code]
        if trace_id:
            predicates.append(columns.trace_id == trace_id)
        result = await db.execute(
            select(ResourceStateEvent).where(or_(*predicates)).order_by(columns.occurred_at.asc())
        )
        return list(result.scalars().all())

    async def _load_rack_bin_mounts_for_resource_events(
        self,
        db: AsyncSession,
        *,
        resource_state_events: list[ResourceStateEvent],
        trace_id: str | None,
    ) -> list[RackBinMount]:
        columns = cast("Any", RackBinMount).__table__.c
        source_event_ids = [
            source_event_id
            for event in resource_state_events
            if (source_event_id := coerce_optional_str(getattr(event, "source_event_id", None))) is not None
        ]
        predicates = []
        if source_event_ids:
            predicates.append(columns.source_event_id.in_(source_event_ids))
        if trace_id:
            predicates.append(columns.trace_id == trace_id)
        if not predicates:
            return []
        result = await db.execute(
            select(RackBinMount).where(or_(*predicates)).order_by(columns.rack_code.asc(), columns.rack_slot_code.asc())
        )
        return list(result.scalars().all())

    async def _load_runtime_holds_for_resource_events(
        self,
        db: AsyncSession,
        *,
        resource_state_events: list[ResourceStateEvent],
        trace_id: str | None,
    ) -> list[RuntimeHold]:
        columns = cast("Any", RuntimeHold).__table__.c
        source_event_ids = [
            source_event_id
            for event in resource_state_events
            if (source_event_id := coerce_optional_str(getattr(event, "source_event_id", None))) is not None
        ]
        idempotency_keys = [
            f"resource-reconciliation:{reason_code}:{source_event_id}"
            for source_event_id in source_event_ids
            for reason_code in _RESOURCE_RECONCILIATION_REASON_CODES
        ]
        predicates = []
        if idempotency_keys:
            predicates.append(columns.source_idempotency_key.in_(idempotency_keys))
        if trace_id:
            predicates.append((columns.trace_id == trace_id) & (columns.source_kind == "RESOURCE_RECONCILIATION"))
        if not predicates:
            return []
        result = await db.execute(select(RuntimeHold).where(or_(*predicates)).order_by(columns.created_at.asc()))
        return list(result.scalars().all())

    async def _load_inboxes_by_trace_id(self, db: AsyncSession, trace_id: str) -> list[WorklineInbox]:
        columns = cast("Any", WorklineInbox).__table__.c
        result = await db.execute(
            select(WorklineInbox).where(columns.trace_id == trace_id).order_by(columns.received_at.asc())
        )
        return list(result.scalars().all())

    async def _load_inboxes_for_session(
        self, db: AsyncSession, session: WorklineSession, existing: list[WorklineInbox]
    ) -> list[WorklineInbox]:
        columns = cast("Any", WorklineInbox).__table__.c
        result = await db.execute(
            select(WorklineInbox).where(columns.session_id == session.id).order_by(columns.received_at.asc())
        )
        return _merge_unique_by_id(existing, list(result.scalars().all()))

    async def _load_commands_for_session(
        self,
        db: AsyncSession,
        session: WorklineSession,
        existing: list[DeviceCommand],
    ) -> list[DeviceCommand]:
        columns = cast("Any", DeviceCommand).__table__.c
        if not isinstance(session.trace_id, str) or not session.trace_id:
            return existing
        result = await db.execute(
            select(DeviceCommand).where(columns.trace_id == session.trace_id).order_by(columns.created_at.asc())
        )
        return _merge_unique_by_id(existing, list(result.scalars().all()))

    async def _load_commands_by_trace_id(
        self,
        db: AsyncSession,
        trace_id: str,
        existing: list[DeviceCommand],
    ) -> list[DeviceCommand]:
        columns = cast("Any", DeviceCommand).__table__.c
        result = await db.execute(
            select(DeviceCommand).where(columns.trace_id == trace_id).order_by(columns.created_at.asc())
        )
        return _merge_unique_by_id(existing, list(result.scalars().all()))

    async def _load_outboxes_for_session(
        self,
        db: AsyncSession,
        session: WorklineSession,
        existing: list[SystemOutbox],
    ) -> list[SystemOutbox]:
        columns = cast("Any", SystemOutbox).__table__.c
        result = await db.execute(
            select(SystemOutbox).where(columns.session_id == session.id).order_by(columns.created_at.asc())
        )
        return _merge_unique_by_id(existing, list(result.scalars().all()))

    async def _load_outboxes_by_trace_id(
        self,
        db: AsyncSession,
        trace_id: str,
        existing: list[SystemOutbox],
    ) -> list[SystemOutbox]:
        columns = cast("Any", SystemOutbox).__table__.c
        result = await db.execute(
            select(SystemOutbox).where(columns.trace_id == trace_id).order_by(columns.created_at.asc())
        )
        return _merge_unique_by_id(existing, list(result.scalars().all()))

    async def _load_dispatch_attempts_for_outboxes(
        self,
        db: AsyncSession,
        outboxes: list[SystemOutbox],
    ) -> list[WorklineDispatchAttempt]:
        outbox_ids = [getattr(outbox, "id", None) for outbox in outboxes if getattr(outbox, "id", None) is not None]
        if not outbox_ids:
            return []
        columns = cast("Any", WorklineDispatchAttempt).__table__.c
        result = await db.execute(
            select(WorklineDispatchAttempt)
            .where(columns.outbox_id.in_(outbox_ids))
            .order_by(columns.attempt_no.asc(), columns.created_at.asc())
        )
        return list(result.scalars().all())

    async def _load_timelines_for_session(self, db: AsyncSession, session: WorklineSession) -> list[WorklineTimeline]:
        columns = cast("Any", WorklineTimeline).__table__.c
        result = await db.execute(
            select(WorklineTimeline)
            .where(columns.session_id == session.id)
            .order_by(columns.seq_no.asc(), columns.occurred_at.asc())
        )
        return list(result.scalars().all())

    async def _load_timelines_by_trace_id(self, db: AsyncSession, trace_id: str) -> list[WorklineTimeline]:
        columns = cast("Any", WorklineTimeline).__table__.c
        result = await db.execute(
            select(WorklineTimeline)
            .where(columns.trace_id == trace_id)
            .order_by(columns.seq_no.asc(), columns.occurred_at.asc())
        )
        return list(result.scalars().all())

    async def _merge_callbacks_by_trace_id(
        self,
        db: AsyncSession,
        trace_id: str,
        existing: list[Any],
    ) -> list[Any]:
        items = await self.callback_log_repo.get_by_trace_id(db, trace_id)
        return _merge_unique_by_id(existing, items)

    async def _load_persisted_diagnostics(self, db: AsyncSession, trace_id: str | None) -> list[WorklineDiagnostic]:
        if not trace_id:
            return []
        return await self.diagnostic_repo.get_active_by_trace_id(db, trace_id)

    def _diagnostic_from_callbacks(self, trace: TraceContext, callbacks: list[Any]) -> list[DiagnosticContext]:
        return [
            build_diagnostic_context(
                trace=trace.with_request_id(getattr(callback, "request_id", None)).with_trace_id(
                    getattr(callback, "trace_id", None)
                ),
                extra=_callback_diagnostic_extra(callback),
            )
            for callback in callbacks
        ]

    def _diagnostic_from_persisted(
        self, trace: TraceContext, persisted_diagnostics: list[WorklineDiagnostic]
    ) -> list[DiagnosticContext]:
        return [
            DiagnosticContext(
                request_id=coerce_optional_str(getattr(item, "request_id", None)) or trace.request_id,
                trace_id=coerce_optional_str(getattr(item, "trace_id", None)) or trace.trace_id,
                session_id=getattr(item, "session_id", None),
                inbox_id=getattr(item, "inbox_id", None),
                outbox_id=getattr(item, "outbox_id", None),
                command_code=coerce_optional_str(getattr(item, "command_code", None)),
                device_code=coerce_optional_str(getattr(item, "device_code", None)),
                workline_id=getattr(item, "workline_id", None),
                plugin_key=coerce_optional_str(getattr(item, "plugin_key", None)),
                extra={
                    "source": "workline_diagnostic",
                    "diagnostic_id": getattr(item, "id", None),
                    "diagnostic_code": coerce_optional_str(getattr(item, "diagnostic_code", None)),
                    "error_domain": coerce_optional_str(getattr(item, "error_domain", None)),
                    "severity": coerce_optional_str(getattr(item, "severity", None)),
                    "recoverability": coerce_optional_str(getattr(item, "recoverability", None)),
                    "problem_class": coerce_optional_str(getattr(item, "problem_class", None)),
                    "owner": coerce_optional_str(getattr(item, "owner", None)),
                    "message": coerce_optional_str(getattr(item, "message", None)),
                    "operator_action": coerce_optional_str(getattr(item, "operator_action", None)),
                    "technical_summary": coerce_optional_str(getattr(item, "technical_summary", None)),
                    "next_steps": list(getattr(item, "next_steps_json", None) or []),
                    "evidence": dict(getattr(item, "evidence_json", None) or {}),
                },
            )
            for item in persisted_diagnostics
        ]

    def _diagnostic_for_session(self, trace: TraceContext, session: WorklineSession) -> list[DiagnosticContext]:
        return [
            build_diagnostic_context(
                trace=trace.with_session(session),
                session=session,
                extra={
                    "source": "session_snapshot",
                    "status": getattr(session, "status", None),
                    "current_wait_type": getattr(session, "current_wait_type", None),
                    "awaiting_device_command_code": getattr(session, "awaiting_device_command_code", None),
                },
            )
        ]

    def _diagnostic_for_inboxes(self, trace: TraceContext, inboxes: list[WorklineInbox]) -> list[DiagnosticContext]:
        return [
            build_diagnostic_context(
                trace=trace.with_inbox(inbox),
                inbox=inbox,
                extra={
                    "source": "inbox",
                    "kind": getattr(inbox, "kind", None),
                    "status": getattr(inbox, "status", None),
                    "attempt_count": getattr(inbox, "attempt_count", None),
                },
            )
            for inbox in inboxes
        ]

    def _diagnostic_for_commands(self, trace: TraceContext, commands: list[DeviceCommand]) -> list[DiagnosticContext]:
        return [
            build_diagnostic_context(
                trace=trace.with_command(command),
                command=command,
                extra={
                    "source": "command",
                    "status": getattr(command, "status", None),
                    "result": getattr(command, "result", None),
                    "task_type": getattr(command, "task_type", None),
                },
            )
            for command in commands
        ]

    def _diagnostic_for_outboxes(self, trace: TraceContext, outboxes: list[SystemOutbox]) -> list[DiagnosticContext]:
        return [
            build_diagnostic_context(
                trace=trace.with_outbox(outbox),
                outbox=outbox,
                extra={
                    "source": "outbox",
                    "status": getattr(outbox, "status", None),
                    "dispatch_type": getattr(outbox, "dispatch_type", None),
                    "target_code": getattr(outbox, "target_code", None),
                },
            )
            for outbox in outboxes
        ]

    def _diagnostic_for_timelines(
        self, trace: TraceContext, timelines: list[WorklineTimeline]
    ) -> list[DiagnosticContext]:
        return [
            build_diagnostic_context(
                trace=_timeline_trace(trace, timeline),
                extra={
                    "source": "timeline",
                    "stage": getattr(timeline, "stage", None),
                    "action_type": getattr(timeline, "action_type", None),
                    "status": getattr(timeline, "status", None),
                    "seq_no": getattr(timeline, "seq_no", None),
                },
            )
            for timeline in timelines
        ]

    def _build_blocking_point(self, result: TraceQueryResult, *, trace_id: str) -> TraceBlockingPointResponse:
        trace = result.trace
        verdict = self.build_diagnosis_verdict(result)
        if verdict.state in {"completed_clear", "running", "waiting", "unknown"}:
            context = build_diagnostic_context(trace=trace, session=result.session)
            return self._verdict_blocking_response(
                trace=trace,
                trace_id=trace_id,
                verdict=verdict,
                context=context,
                evidence={"summary": result.summary},
            )

        failed_outbox = next(
            (item for item in result.outboxes if optional_enum_str(getattr(item, "status", None)) == "FAILED"), None
        )
        if failed_outbox is not None:
            context = build_diagnostic_context(
                trace=trace.with_outbox(failed_outbox),
                outbox=failed_outbox,
                extra={"source": "outbox", "last_error": getattr(failed_outbox, "last_error", None)},
            )
            return self._blocking_response(
                trace=trace,
                trace_id=trace_id,
                blocking_point="outbox",
                error_code=ErrorCode.OUTBOX_DISPATCH_FAILED,
                message=getattr(failed_outbox, "last_error", None) or "Outbox 派发失败",
                context=context,
                verdict=verdict,
                evidence={
                    "outbox": {
                        "id": getattr(failed_outbox, "id", None),
                        "dispatch_key": getattr(failed_outbox, "dispatch_key", None),
                        "status": optional_enum_str(getattr(failed_outbox, "status", None)),
                        "last_error": getattr(failed_outbox, "last_error", None),
                    }
                },
            )

        dead_letter_inbox = next(
            (item for item in result.inboxes if optional_enum_str(getattr(item, "status", None)) == "DEAD_LETTER"),
            None,
        )
        if dead_letter_inbox is not None:
            context = build_diagnostic_context(
                trace=trace.with_inbox(dead_letter_inbox),
                inbox=dead_letter_inbox,
                extra={"source": "inbox", "error_message": getattr(dead_letter_inbox, "error_message", None)},
            )
            return self._blocking_response(
                trace=trace,
                trace_id=trace_id,
                blocking_point="inbox",
                error_code=ErrorCode.INBOX_RETRY_EXHAUSTED,
                message=getattr(dead_letter_inbox, "error_message", None) or "Inbox 重试耗尽",
                context=context,
                verdict=verdict,
                evidence={
                    "inbox": {
                        "id": getattr(dead_letter_inbox, "id", None),
                        "status": optional_enum_str(getattr(dead_letter_inbox, "status", None)),
                        "error_message": getattr(dead_letter_inbox, "error_message", None),
                    }
                },
            )

        failed_inbox = next(
            (item for item in result.inboxes if optional_enum_str(getattr(item, "status", None)) == "FAILED"),
            None,
        )
        if failed_inbox is not None:
            matched_diagnostic = self._diagnostic_for_entity(
                result.diagnostics, inbox_id=getattr(failed_inbox, "id", None)
            )
            error_code = self._diagnostic_error_code(matched_diagnostic) or ErrorCode.SESSION_RESOLVE_FAILED
            message = (
                (matched_diagnostic.extra.get("message") if matched_diagnostic is not None else None)
                or getattr(failed_inbox, "error_message", None)
                or "Inbox 处理失败"
            )
            context = build_diagnostic_context(
                trace=trace.with_inbox(failed_inbox),
                inbox=failed_inbox,
                extra={
                    "source": "inbox",
                    "error_message": getattr(failed_inbox, "error_message", None),
                    "diagnostic": matched_diagnostic.extra if matched_diagnostic is not None else None,
                },
            )
            return self._blocking_response(
                trace=trace,
                trace_id=trace_id,
                blocking_point="inbox",
                error_code=error_code,
                message=str(message),
                context=context,
                verdict=verdict,
                evidence={
                    "inbox": {
                        "id": getattr(failed_inbox, "id", None),
                        "status": optional_enum_str(getattr(failed_inbox, "status", None)),
                        "error_message": getattr(failed_inbox, "error_message", None),
                    },
                    "diagnostic": matched_diagnostic.extra if matched_diagnostic is not None else None,
                },
            )

        failed_command = next(
            (
                item
                for item in result.commands
                if optional_enum_str(getattr(item, "status", None)) in {"FAILED", "TIMEOUT"}
            ),
            None,
        )
        if failed_command is not None:
            context = build_diagnostic_context(trace=trace.with_command(failed_command), command=failed_command)
            return self._blocking_response(
                trace=trace,
                trace_id=trace_id,
                blocking_point="command",
                error_code=ErrorCode.DEVICE_TIMEOUT,
                message="设备指令失败或超时",
                context=context,
                verdict=verdict,
                evidence={
                    "command": {
                        "command_code": getattr(failed_command, "command_code", None),
                        "status": optional_enum_str(getattr(failed_command, "status", None)),
                        "error_detail": getattr(failed_command, "error_detail", None),
                    }
                },
            )

        session = result.session
        manual_hold_block = self._manual_hold_block(result)
        if session is not None and manual_hold_block is not None:
            timeline, block_payload = manual_hold_block
            context = build_diagnostic_context(
                trace=trace.with_session(session),
                session=session,
                extra={
                    "source": "manual_hold",
                    "failure_domain": getattr(session, "failure_domain", None),
                    "failure_code": getattr(session, "failure_code", None),
                    "timeline": {
                        "id": getattr(timeline, "id", None),
                        "seq_no": getattr(timeline, "seq_no", None),
                        "action_type": getattr(timeline, "action_type", None),
                    },
                },
            )
            completed_commands = [
                getattr(command, "command_code", None)
                for command in result.commands
                if optional_enum_str(getattr(command, "status", None)) == "COMPLETED"
            ]
            return self._blocking_response(
                trace=trace,
                trace_id=trace_id,
                blocking_point=verdict.blocking_point,
                error_code=ErrorCode.WMS_TIMEOUT
                if verdict.blocking_point == "external_wms"
                else ErrorCode.SESSION_CONTEXT_MISSING,
                message=getattr(session, "failure_message", None)
                or getattr(timeline, "message", None)
                or "流程进入人工保持",
                context=context,
                verdict=verdict,
                evidence={
                    "session": {
                        "id": getattr(session, "id", None),
                        "status": optional_enum_str(getattr(session, "status", None)),
                        "failure_domain": getattr(session, "failure_domain", None),
                        "failure_code": getattr(session, "failure_code", None),
                        "failure_message": getattr(session, "failure_message", None),
                    },
                    "timeline": {
                        "id": getattr(timeline, "id", None),
                        "seq_no": getattr(timeline, "seq_no", None),
                        "action_type": optional_enum_str(getattr(timeline, "action_type", None)),
                        "status": optional_enum_str(getattr(timeline, "status", None)),
                        "reason_code": block_payload.get("reason_code"),
                        "target_code": block_payload.get("target_code"),
                        "block_scope": block_payload.get("block_scope"),
                        "suggested_action": block_payload.get("suggested_action"),
                    },
                    "command_chain": {
                        "completed_commands": [code for code in completed_commands if code],
                    },
                },
            )

        if session is not None and optional_enum_str(getattr(session, "status", None)) == "FAILED":
            context = build_diagnostic_context(trace=trace.with_session(session), session=session)
            failure_code = optional_enum_str(getattr(session, "failure_code", None)) or ""
            session_error_code = _SESSION_FAILURE_CODE_MAP.get(failure_code, ErrorCode.SESSION_RESOLVE_FAILED)
            return self._blocking_response(
                trace=trace,
                trace_id=trace_id,
                blocking_point="session",
                error_code=session_error_code,
                message=getattr(session, "failure_message", None) or "会话失败",
                context=context,
                verdict=verdict,
                evidence={
                    "session": {
                        "id": getattr(session, "id", None),
                        "status": optional_enum_str(getattr(session, "status", None)),
                        "failure_code": getattr(session, "failure_code", None),
                        "failure_message": getattr(session, "failure_message", None),
                    }
                },
            )

        context = build_diagnostic_context(trace=trace, session=session)
        return self._blocking_response(
            trace=trace,
            trace_id=trace_id,
            blocking_point="none",
            error_code=ErrorCode.UNKNOWN,
            message="当前 trace 未发现明确阻塞点",
            context=context,
            verdict=verdict,
            evidence={"summary": result.summary},
        )

    def _blocking_response(
        self,
        *,
        trace: TraceContext,
        trace_id: str,
        blocking_point: str,
        error_code: ErrorCode,
        message: str,
        context: DiagnosticContext,
        verdict: DiagnosisVerdictResponse,
        evidence: dict[str, Any],
    ) -> TraceBlockingPointResponse:
        definition = get_diagnostic_code_definition(error_code)
        event = build_diagnostic_event(
            error_code=error_code,
            context=context,
            message=message,
            operator_action=definition.operator_action,
        )
        card = build_diagnostic_card(event)
        return TraceBlockingPointResponse(
            trace_id=trace.trace_id or trace_id,
            request_id=trace.request_id,
            blocking_point=blocking_point,
            diagnosis_verdict=verdict,
            owner=definition.owner,
            recoverability=card.recoverability.value,
            operator_action=card.operator_action or definition.operator_action,
            diagnostic_card=DiagnosticCardResponse.model_validate(card.model_dump(mode="json")),
            evidence=evidence,
            next_steps=[],
        )

    def _verdict_blocking_response(
        self,
        *,
        trace: TraceContext,
        trace_id: str,
        verdict: DiagnosisVerdictResponse,
        context: DiagnosticContext,
        evidence: dict[str, Any],
    ) -> TraceBlockingPointResponse:
        card = self._diagnostic_card_from_verdict(verdict, context)
        return TraceBlockingPointResponse(
            trace_id=trace.trace_id or trace_id,
            request_id=trace.request_id,
            blocking_point=verdict.blocking_point,
            diagnosis_verdict=verdict,
            owner=verdict.owner or "workflow",
            recoverability=card.recoverability.value,
            operator_action=verdict.primary_action or "查看诊断证据",
            diagnostic_card=DiagnosticCardResponse.model_validate(card.model_dump(mode="json")),
            evidence=evidence,
            next_steps=[verdict.primary_action] if verdict.primary_action else [],
        )

    @staticmethod
    def _diagnostic_card_from_verdict(
        verdict: DiagnosisVerdictResponse,
        context: DiagnosticContext,
    ) -> DiagnosticCard:
        if verdict.state == "completed_clear":
            return DiagnosticCard(
                title="无阻塞点",
                summary=verdict.summary,
                error_code=ErrorCode.SESSION_CONTEXT_MISSING,
                error_domain=ErrorDomain.WORKFLOW,
                severity=Severity.INFO,
                recoverability=Recoverability.AUTO_RETRYABLE,
                problem_class=ProblemClass.SOFTWARE,
                user_message=verdict.summary,
                operator_action=verdict.primary_action,
                technical_summary="流程完成且未发现失败、阻塞或人工保持证据。",
                next_steps=[verdict.primary_action] if verdict.primary_action else [],
                context=context,
            )
        if verdict.state == "unknown":
            return DiagnosticCard(
                title="诊断不足",
                summary=verdict.summary,
                error_code=ErrorCode.UNKNOWN,
                error_domain=ErrorDomain.SYSTEM,
                severity=Severity.WARNING,
                recoverability=Recoverability.MANUAL_RETRYABLE,
                problem_class=ProblemClass.SOFTWARE,
                user_message="当前证据不足，无法可靠判断运行状态。",
                operator_action=verdict.primary_action,
                technical_summary=verdict.evidence_health.summary,
                next_steps=[verdict.primary_action] if verdict.primary_action else [],
                context=context,
            )
        return DiagnosticCard(
            title=verdict.title,
            summary=verdict.summary,
            error_code=ErrorCode.SESSION_CONTEXT_MISSING,
            error_domain=ErrorDomain.WORKFLOW,
            severity=Severity.WARNING if verdict.state == "waiting" else Severity.INFO,
            recoverability=Recoverability.AUTO_RETRYABLE,
            problem_class=ProblemClass.SOFTWARE,
            user_message=verdict.summary,
            operator_action=verdict.primary_action,
            technical_summary=verdict.evidence_health.summary,
            next_steps=[verdict.primary_action] if verdict.primary_action else [],
            context=context,
        )

    @staticmethod
    def _manual_hold_block(result: TraceQueryResult) -> tuple[WorklineTimeline, dict[str, Any]] | None:
        session = result.session
        if session is None or optional_enum_str(getattr(session, "status", None)) != "MANUAL_HOLD":
            return None
        session_failure_code = optional_enum_str(getattr(session, "failure_code", None))
        matched_timelines: list[tuple[WorklineTimeline, dict[str, Any]]] = []
        for timeline in result.timelines:
            payload = payload_dict(getattr(timeline, "payload_json", None))
            reason_code = coerce_optional_str(payload.get("reason_code"))
            if reason_code:
                matched_timelines.append((timeline, payload))
        if matched_timelines:
            return matched_timelines[-1]
        if session_failure_code:
            latest = result.timelines[-1] if result.timelines else SimpleNamespace(payload_json={})
            return cast("WorklineTimeline", latest), {
                "reason_code": session_failure_code,
                "target_code": "WMS_INVENTORY" if session_failure_code == "WMS_TIMEOUT" else None,
            }
        return None

    @staticmethod
    def _diagnostic_for_entity(
        diagnostics: list[DiagnosticContext],
        *,
        inbox_id: int | None = None,
        outbox_id: int | None = None,
        command_code: str | None = None,
    ) -> DiagnosticContext | None:
        matches: list[DiagnosticContext] = []
        for diagnostic in diagnostics:
            if inbox_id is not None and diagnostic.inbox_id == inbox_id:
                matches.append(diagnostic)
            if outbox_id is not None and diagnostic.outbox_id == outbox_id:
                matches.append(diagnostic)
            if command_code is not None and diagnostic.command_code == command_code:
                matches.append(diagnostic)
        return next(
            (diagnostic for diagnostic in matches if diagnostic.extra.get("source") == "workline_diagnostic"),
            matches[0] if matches else None,
        )

    @staticmethod
    def _diagnostic_error_code(diagnostic: DiagnosticContext | None) -> ErrorCode | None:
        if diagnostic is None:
            return None
        value = diagnostic.extra.get("diagnostic_code")
        if not isinstance(value, str):
            return None
        try:
            return ErrorCode(value)
        except ValueError:
            return None


trace_query_service = TraceQueryService()


__all__ = ["TraceQueryResult", "TraceQueryService", "trace_query_service"]
