"""TraceQueryService - 只读 TRACE 聚合查询服务。

只负责把已有事实表/投影表按统一 trace 键拼成可读视图：
- callback_logs
- workline_inbox
- workline_sessions
- device_commands
- workline_outbox
- workline_timelines

注意：不引入任何额外持久化，不做 trace 宽表。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import select

from src.app.callback.repositories.callback_log_repository import (
    CallbackLogRepository,
    callback_log_repository,
)
from src.app.device.models.command import DeviceCommand
from src.app.device.repositories.command_repository import (
    DeviceCommandRepository,
    device_command_repository,
)
from src.app.workline.models.inbox import WorklineInbox
from src.app.workline.models.outbox import WorklineOutbox
from src.app.workline.models.timeline import WorklineTimeline
from src.app.workline.repositories import inbox_repository
from src.app.workline.repositories.session_repository import (
    WorklineSessionRepository,
    workline_session_repository,
)
from src.core.base_service import BaseService
from src.workline_runtime.diagnostics import DiagnosticContext, build_diagnostic_context
from src.workline_runtime.trace_context import TraceContext

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.app.workline.models.session import WorklineSession


@dataclass(frozen=True, slots=True)
class TraceQueryResult:
    """TRACE 查询结果聚合视图。"""

    trace: TraceContext
    callback_logs: list[Any] = field(default_factory=list)
    inboxes: list[WorklineInbox] = field(default_factory=list)
    session: WorklineSession | None = None
    commands: list[DeviceCommand] = field(default_factory=list)
    outboxes: list[WorklineOutbox] = field(default_factory=list)
    timelines: list[WorklineTimeline] = field(default_factory=list)
    diagnostics: list[DiagnosticContext] = field(default_factory=list)

    @property
    def summary(self) -> dict[str, int]:
        return {
            "callback_logs": len(self.callback_logs),
            "inboxes": len(self.inboxes),
            "commands": len(self.commands),
            "outboxes": len(self.outboxes),
            "timelines": len(self.timelines),
            "diagnostics": len(self.diagnostics),
        }


def _safe_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _payload_dict(value: Any) -> dict[str, Any]:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


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
    payload_dict = _payload_dict(getattr(timeline, "payload_json", None))
    timeline_trace = TraceContext.from_request(
        request_id=_safe_str(payload_dict.get("request_id")),
        correlation_id=_safe_str(payload_dict.get("correlation_id")) or trace.correlation_id,
        canonical_event_type=_safe_str(payload_dict.get("canonical_event_type")),
        transition=_safe_str(getattr(timeline, "to_status", None)) or _safe_str(getattr(timeline, "action_type", None)),
    )
    return timeline_trace.with_session(
        SimpleNamespace(
            id=getattr(timeline, "session_id", None),
            workline_id=getattr(timeline, "workline_id", None),
            correlation_id=getattr(timeline, "correlation_id", None),
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
    ) -> None:
        super().__init__(inbox_repository, enable_cache=False)
        self.callback_log_repo = callback_log_repo or callback_log_repository
        self.session_repo = session_repo or workline_session_repository
        self.command_repo = command_repo or device_command_repository
        self.inbox_repo = inbox_repo or inbox_repository

    async def by_request_id(self, db: AsyncSession, request_id: str) -> TraceQueryResult:
        return await self.query(db, request_id=request_id)

    async def by_correlation_id(self, db: AsyncSession, correlation_id: str) -> TraceQueryResult:
        return await self.query(db, correlation_id=correlation_id)

    async def by_session_id(self, db: AsyncSession, session_id: int) -> TraceQueryResult:
        return await self.query(db, session_id=session_id)

    async def by_command_code(self, db: AsyncSession, command_code: str) -> TraceQueryResult:
        return await self.query(db, command_code=command_code)

    async def by_dispatch_key(self, db: AsyncSession, dispatch_key: str) -> TraceQueryResult:
        return await self.query(db, dispatch_key=dispatch_key)

    async def query(  # noqa: PLR0912
        self,
        db: AsyncSession,
        *,
        request_id: str | None = None,
        correlation_id: str | None = None,
        session_id: int | None = None,
        command_code: str | None = None,
        dispatch_key: str | None = None,
    ) -> TraceQueryResult:
        trace = TraceContext.from_request(request_id=request_id, correlation_id=correlation_id)
        request_id = trace.request_id
        correlation_id = trace.correlation_id
        callback_logs: list[Any] = []
        session: WorklineSession | None = None
        commands: list[DeviceCommand] = []
        outboxes: list[WorklineOutbox] = []
        inboxes: list[WorklineInbox] = []
        timelines: list[WorklineTimeline] = []
        diagnostics: list[DiagnosticContext] = []

        if request_id:
            callback_log = await self.callback_log_repo.get_by_request_id(db, request_id)
            if callback_log is not None:
                callback_logs.append(callback_log)
                trace = trace.with_request_id(getattr(callback_log, "request_id", None))
                trace = trace.with_correlation_id(getattr(callback_log, "correlation_id", None))
                request_id = trace.request_id
                correlation_id = trace.correlation_id
                diagnostics.append(
                    build_diagnostic_context(
                        trace=trace,
                        extra=_callback_diagnostic_extra(callback_log),
                    )
                )

        if correlation_id:
            callback_logs = await self.callback_log_repo.get_by_correlation_id(db, correlation_id)
            if callback_logs:
                first_callback = callback_logs[0]
                trace = trace.with_request_id(getattr(first_callback, "request_id", None))
                trace = trace.with_correlation_id(getattr(first_callback, "correlation_id", None))
                request_id = trace.request_id
                correlation_id = trace.correlation_id
                diagnostics.extend(self._diagnostic_from_callbacks(trace, callback_logs))

        if command_code:
            command = await self.command_repo.get_by_command_code(db, command_code)
            if command is not None:
                commands.append(command)
                trace = trace.with_command(command)
                if getattr(command, "correlation_id", None):
                    trace = trace.with_correlation_id(getattr(command, "correlation_id", None))
                request_id = trace.request_id
                correlation_id = trace.correlation_id

        if session_id is not None and session is None:
            session = await self.session_repo.get_by_id(db, session_id)
            if session is not None:
                trace = trace.with_session(session)
                session_id = getattr(session, "id", session_id)
                request_id = trace.request_id
                correlation_id = trace.correlation_id

        if session is None and trace.correlation_id:
            session = await self.session_repo.get_by_correlation_id(db, trace.correlation_id)
            if session is not None:
                trace = trace.with_session(session)

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
                request_id = trace.request_id
                correlation_id = trace.correlation_id

        if session is not None:
            trace = trace.with_session(session)
            if trace.correlation_id:
                correlation_id = trace.correlation_id
                callback_logs = await self._merge_callbacks_by_correlation_id(db, trace.correlation_id, callback_logs)
            commands = await self._load_commands_for_session(db, session, commands)
            outboxes = await self._load_outboxes_for_session(db, session, outboxes)
            inboxes = await self._load_inboxes_for_session(db, session, inboxes)
            timelines = await self._load_timelines_for_session(db, session)
        elif trace.correlation_id:
            correlation_id = trace.correlation_id
            inboxes = await self._load_inboxes_by_correlation_id(db, trace.correlation_id)
            timelines = await self._load_timelines_by_correlation_id(db, trace.correlation_id)
            if not commands and trace.command_code:
                command = await self.command_repo.get_by_command_code(db, trace.command_code)
                if command is not None:
                    commands.append(command)

        diagnostics.extend(self._diagnostic_for_session(trace, session) if session is not None else [])
        diagnostics.extend(self._diagnostic_for_inboxes(trace, inboxes))
        diagnostics.extend(self._diagnostic_for_commands(trace, commands))
        diagnostics.extend(self._diagnostic_for_outboxes(trace, outboxes))
        diagnostics.extend(self._diagnostic_for_timelines(trace, timelines))

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

        return TraceQueryResult(
            trace=trace,
            callback_logs=callback_logs,
            inboxes=inboxes,
            session=session,
            commands=commands,
            outboxes=outboxes,
            timelines=timelines,
            diagnostics=diagnostics,
        )

    async def _get_outbox_by_dispatch_key(self, db: AsyncSession, dispatch_key: str) -> WorklineOutbox | None:
        columns = cast("Any", WorklineOutbox).__table__.c
        result = await db.execute(select(WorklineOutbox).where(columns.dispatch_key == dispatch_key))
        return result.scalar_one_or_none()

    async def _load_inboxes_by_correlation_id(self, db: AsyncSession, correlation_id: str) -> list[WorklineInbox]:
        columns = cast("Any", WorklineInbox).__table__.c
        result = await db.execute(
            select(WorklineInbox).where(columns.correlation_id == correlation_id).order_by(columns.received_at.asc())
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
        result = await db.execute(
            select(DeviceCommand).where(columns.session_id == str(session.id)).order_by(columns.created_at.asc())
        )
        return _merge_unique_by_id(existing, list(result.scalars().all()))

    async def _load_outboxes_for_session(
        self,
        db: AsyncSession,
        session: WorklineSession,
        existing: list[WorklineOutbox],
    ) -> list[WorklineOutbox]:
        columns = cast("Any", WorklineOutbox).__table__.c
        result = await db.execute(
            select(WorklineOutbox).where(columns.session_id == session.id).order_by(columns.created_at.asc())
        )
        return _merge_unique_by_id(existing, list(result.scalars().all()))

    async def _load_timelines_for_session(self, db: AsyncSession, session: WorklineSession) -> list[WorklineTimeline]:
        columns = cast("Any", WorklineTimeline).__table__.c
        result = await db.execute(
            select(WorklineTimeline)
            .where(columns.session_id == session.id)
            .order_by(columns.seq_no.asc(), columns.occurred_at.asc())
        )
        return list(result.scalars().all())

    async def _load_timelines_by_correlation_id(self, db: AsyncSession, correlation_id: str) -> list[WorklineTimeline]:
        columns = cast("Any", WorklineTimeline).__table__.c
        result = await db.execute(
            select(WorklineTimeline)
            .where(columns.correlation_id == correlation_id)
            .order_by(columns.seq_no.asc(), columns.occurred_at.asc())
        )
        return list(result.scalars().all())

    async def _merge_callbacks_by_correlation_id(
        self,
        db: AsyncSession,
        correlation_id: str,
        existing: list[Any],
    ) -> list[Any]:
        items = await self.callback_log_repo.get_by_correlation_id(db, correlation_id)
        return _merge_unique_by_id(existing, items)

    def _diagnostic_from_callbacks(self, trace: TraceContext, callbacks: list[Any]) -> list[DiagnosticContext]:
        return [
            build_diagnostic_context(
                trace=trace.with_request_id(getattr(callback, "request_id", None)).with_correlation_id(
                    getattr(callback, "correlation_id", None)
                ),
                extra=_callback_diagnostic_extra(callback),
            )
            for callback in callbacks
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
                    "awaiting_command_id": getattr(session, "awaiting_command_id", None),
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

    def _diagnostic_for_outboxes(self, trace: TraceContext, outboxes: list[WorklineOutbox]) -> list[DiagnosticContext]:
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


trace_query_service = TraceQueryService()


__all__ = ["TraceQueryResult", "TraceQueryService", "trace_query_service"]
