"""RuntimeQueryService - 运行监控中心只读聚合查询服务。"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Any, cast

from sqlalchemy import and_, or_, select

from src.app.callback.models import CallbackLog
from src.app.callback.repositories.callback_log_repository import callback_log_repository
from src.app.device.models import Device, DeviceCommand
from src.app.device.repositories import device_repository
from src.app.workline.models import WorkLine, WorklineInbox, WorklineOutbox, WorklineSession, WorklineTimeline
from src.app.workline.models.runtime import (
    RuntimeDeviceDetailResponse,
    RuntimeDeviceHealthSummary,
    RuntimeDeviceSummary,
    RuntimeOverviewResponse,
    RuntimeStatCard,
    RuntimeTraceListItem,
    RuntimeTraceListResponse,
    RuntimeWorklineDetailResponse,
    RuntimeWorklineDeviceItem,
    RuntimeWorklineSummary,
    TraceCallbackLogItem,
    TraceCommandItem,
    TraceQueryRequest,
)
from src.core.base_service import BaseService
from src.utils.timezone import timezone

_ACTIVE_SESSION_STATUSES = {
    "NEW",
    "RUNNING",
    "WAITING_DEVICE_RESULT",
    "WAITING_EXTERNAL",
    "MANUAL_HOLD",
}
_WAITING_SESSION_STATUSES = {"WAITING_DEVICE_RESULT", "WAITING_EXTERNAL", "MANUAL_HOLD"}
_FAILURE_SESSION_STATUSES = {"FAILED", "CANCELLED"}
_ABNORMAL_DEVICE_STATUSES = {"ERROR", "OFFLINE"}
_PENDING_COMMAND_STATUSES = {"PENDING", "SENT", "ACK_RECEIVED"}
_INBOX_BACKLOG_STATUSES = {"NEW", "RETRY", "PROCESSING"}
_OUTBOX_BACKLOG_STATUSES = {"NEW", "DISPATCHING"}
_RECENT_FAILURE_HOURS = 24


def _enum_str(value: Any) -> str | None:
    return getattr(value, "value", value) if value is not None else None


def _activity_dt(session: WorklineSession) -> Any:
    return session.last_ingress_at or session.waiting_since or session.started_at or session.created_at


def _is_timed_out(session: WorklineSession, now: Any) -> bool:
    status = _enum_str(getattr(session, "status", None))
    deadline_at = getattr(session, "deadline_at", None)
    return status in _WAITING_SESSION_STATUSES and deadline_at is not None and deadline_at < now


def _recent_failure_since() -> Any:
    return timezone.now_for_db() - timedelta(hours=_RECENT_FAILURE_HOURS)


def _waiting_timeout_clause(columns: Any, now: Any) -> Any:
    return and_(
        columns.status.in_(list(_WAITING_SESSION_STATUSES)),
        columns.deadline_at.isnot(None),
        columns.deadline_at < now,
    )


def _recent_failed_clause(columns: Any, recent_since: Any) -> Any:
    return and_(columns.status.in_(list(_FAILURE_SESSION_STATUSES)), columns.updated_at >= recent_since)


def _parse_session_id(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _command_duration_ms(command: DeviceCommand) -> int | None:
    try:
        return command.get_duration_ms()
    except Exception:
        return None


class RuntimeQueryService(BaseService[Any, Any]):
    """运行监控中心只读聚合查询服务。"""

    def __init__(self) -> None:
        super().__init__(device_repository, enable_cache=False)

    async def get_overview(self, db: Any) -> RuntimeOverviewResponse:
        worklines = await self.list_worklines(db)
        devices = await self.list_devices(db)
        recent_failed_sessions = await self._load_recent_failed_sessions(db, limit=10)
        recent_failed_traces = await self._build_trace_list_items(db, recent_failed_sessions)

        running_sessions = await self._count_sessions_by_status(db, {"RUNNING"})
        waiting_sessions = await self._count_sessions_by_status(db, _WAITING_SESSION_STATUSES)
        failed_sessions = len(recent_failed_sessions)
        inbox_backlog = await self._count_inboxes_by_status(db, _INBOX_BACKLOG_STATUSES)
        outbox_backlog = await self._count_outboxes_by_status(db, _OUTBOX_BACKLOG_STATUSES)
        abnormal_devices = sum(1 for item in devices if item.device_status in _ABNORMAL_DEVICE_STATUSES)

        hot_worklines = sorted(
            worklines,
            key=lambda item: item.active_session_count + item.waiting_session_count + item.failed_session_count,
            reverse=True,
        )[:5]
        abnormal_device_items = [item for item in devices if item.device_status in _ABNORMAL_DEVICE_STATUSES][:10]
        maintenance_devices = sum(1 for item in devices if item.maintenance_mode)
        loaded_devices = sum(
            1
            for item in devices
            if item.pending_command_count > 0 and item.device_status not in _ABNORMAL_DEVICE_STATUSES
        )
        healthy_devices = sum(
            1 for item in devices if item.device_status not in _ABNORMAL_DEVICE_STATUSES and not item.maintenance_mode
        )

        return RuntimeOverviewResponse(
            stats=[
                RuntimeStatCard(
                    key="running_sessions", label="运行中 Session", value=running_sessions, status="warning"
                ),
                RuntimeStatCard(key="waiting_sessions", label="等待中 Session", value=waiting_sessions, status="info"),
                RuntimeStatCard(
                    key="failed_sessions", label="失败 / 超时 Session", value=failed_sessions, status="danger"
                ),
                RuntimeStatCard(key="inbox_backlog", label="Inbox 积压", value=inbox_backlog, status="warning"),
                RuntimeStatCard(key="outbox_backlog", label="Outbox 积压", value=outbox_backlog, status="warning"),
                RuntimeStatCard(key="abnormal_devices", label="异常设备", value=abnormal_devices, status="danger"),
            ],
            recent_failed_traces=recent_failed_traces,
            hot_worklines=hot_worklines,
            abnormal_devices=abnormal_device_items,
            device_health=RuntimeDeviceHealthSummary(
                total=len(devices),
                abnormal=abnormal_devices,
                maintenance=maintenance_devices,
                loaded=loaded_devices,
                healthy=healthy_devices,
            ),
        )

    async def get_trace_list(self, db: Any, payload: TraceQueryRequest) -> RuntimeTraceListResponse:
        columns = cast("Any", WorklineSession).__table__.c
        query = select(WorklineSession)

        if payload.workline_id is not None:
            query = query.where(columns.workline_id == payload.workline_id)
        if payload.status:
            query = query.where(columns.status == payload.status)
        if payload.step_code:
            query = query.where(columns.step_code == payload.step_code)
        if payload.only_active:
            query = query.where(columns.status.in_(list(_ACTIVE_SESSION_STATUSES)))
        if payload.only_failed:
            recent_since = _recent_failure_since()
            now = timezone.now_for_db()
            query = query.where(
                or_(columns.status.in_(list(_FAILURE_SESSION_STATUSES)), _waiting_timeout_clause(columns, now)),
                columns.updated_at >= recent_since,
            )
        if payload.keyword:
            keyword = f"%{payload.keyword}%"
            query = query.where(
                or_(
                    columns.session_code.ilike(keyword),
                    columns.correlation_id.ilike(keyword),
                    columns.business_key.ilike(keyword),
                    columns.barcode.ilike(keyword),
                    columns.last_request_id.ilike(keyword),
                )
            )

        result = await db.execute(query.order_by(columns.last_ingress_at.desc().nullslast(), columns.id.desc()))
        sessions = list(result.scalars().all())

        if payload.device_id is not None:
            sessions = await self._filter_sessions_by_device_id(db, sessions, payload.device_id)

        total = len(sessions)
        page_items = sessions[payload.offset : payload.offset + payload.limit]
        items = await self._build_trace_list_items(db, page_items)
        return RuntimeTraceListResponse(total=total, items=items)

    async def list_worklines(self, db: Any) -> list[RuntimeWorklineSummary]:
        workline_columns = cast("Any", WorkLine).__table__.c
        workline_result = await db.execute(
            select(WorkLine)
            .where(workline_columns.is_deleted.is_(False))
            .order_by(workline_columns.sort_order.asc(), workline_columns.id.asc())
        )
        worklines = list(workline_result.scalars().all())
        if not worklines:
            return []

        workline_ids = [item.id for item in worklines if item.id is not None]
        devices_by_workline = await self._load_devices_by_workline_ids(db, workline_ids)
        sessions_by_workline = await self._load_sessions_by_workline_ids(db, workline_ids)

        summaries: list[RuntimeWorklineSummary] = []
        for workline in worklines:
            if workline.id is None:
                continue
            devices = devices_by_workline.get(workline.id, [])
            sessions = sessions_by_workline.get(workline.id, [])
            summaries.append(self._build_workline_summary(workline, devices, sessions))
        return summaries

    async def get_workline_detail(self, db: Any, workline_id: int) -> RuntimeWorklineDetailResponse | None:
        workline_result = await db.execute(select(WorkLine).where(cast("Any", WorkLine).__table__.c.id == workline_id))
        workline = workline_result.scalar_one_or_none()
        if workline is None:
            return None

        devices = await device_repository.get_by_work_line_id(db, workline_id)
        active_sessions = await self._load_active_sessions_for_workline(db, workline_id, limit=20)
        recent_failed_sessions = await self._load_recent_failed_sessions_for_workline(db, workline_id, limit=10)
        all_sessions = active_sessions + [
            item for item in recent_failed_sessions if item.id not in {s.id for s in active_sessions}
        ]

        summary = self._build_workline_summary(workline, devices, all_sessions)
        device_items = [self._build_workline_device_item(device) for device in devices]
        active_trace_items = await self._build_trace_list_items(db, active_sessions)
        failed_trace_items = await self._build_trace_list_items(db, recent_failed_sessions)

        return RuntimeWorklineDetailResponse(
            summary=summary,
            devices=device_items,
            active_sessions=active_trace_items,
            recent_failed_traces=failed_trace_items,
        )

    async def list_devices(self, db: Any, workline_id: int | None = None) -> list[RuntimeDeviceSummary]:
        device_columns = cast("Any", Device).__table__.c
        query = select(Device).where(device_columns.is_deleted.is_(False))
        if workline_id is not None:
            query = query.where(device_columns.work_line_id == workline_id)

        device_result = await db.execute(query.order_by(device_columns.sort_order.asc(), device_columns.id.asc()))
        devices = list(device_result.scalars().all())
        if not devices:
            return []

        workline_ids = [item.work_line_id for item in devices if item.work_line_id is not None]
        workline_map = await self._load_workline_map(db, workline_ids)
        pending_command_map = await self._load_pending_command_count_map(
            db, [item.id for item in devices if item.id is not None]
        )
        callback_time_map = await self._load_recent_callback_time_map(db, [item.device_code for item in devices])

        return [
            self._build_device_summary(
                device,
                workline_map.get(device.work_line_id),
                pending_command_map.get(device.id or 0, 0),
                callback_time_map.get(device.device_code),
            )
            for device in devices
            if device.id is not None
        ]

    async def get_device_detail(
        self, db: Any, device_id: int, workline_id: int | None = None
    ) -> RuntimeDeviceDetailResponse | None:
        device = await device_repository.get_by_id(db, device_id)
        if device is None:
            return None
        if workline_id is not None and device.work_line_id != workline_id:
            return None

        workline = None
        if device.work_line_id is not None:
            workline_map = await self._load_workline_map(db, [device.work_line_id])
            workline = workline_map.get(device.work_line_id)

        pending_command_map = await self._load_pending_command_count_map(db, [device_id])
        callback_time_map = await self._load_recent_callback_time_map(db, [device.device_code])
        summary = self._build_device_summary(
            device,
            workline,
            pending_command_map.get(device_id, 0),
            callback_time_map.get(device.device_code),
        )

        recent_commands = await self._load_recent_commands_for_device(db, device_id, limit=20)
        recent_callbacks = await callback_log_repository.get_by_device_id(db, device.device_code, limit=20)
        active_sessions = await self._load_active_sessions_for_device(db, device_id, limit=10)

        return RuntimeDeviceDetailResponse(
            summary=summary,
            recent_commands=[self._build_command_item(item) for item in recent_commands],
            recent_callbacks=[self._build_callback_item(item) for item in recent_callbacks],
            active_sessions=await self._build_trace_list_items(db, active_sessions),
        )

    async def _count_sessions_by_status(self, db: Any, statuses: set[str]) -> int:
        columns = cast("Any", WorklineSession).__table__.c
        result = await db.execute(select(WorklineSession).where(columns.status.in_(list(statuses))))
        return len(list(result.scalars().all()))

    async def _count_inboxes_by_status(self, db: Any, statuses: set[str]) -> int:
        columns = cast("Any", WorklineInbox).__table__.c
        result = await db.execute(select(WorklineInbox).where(columns.status.in_(list(statuses))))
        return len(list(result.scalars().all()))

    async def _count_outboxes_by_status(self, db: Any, statuses: set[str]) -> int:
        columns = cast("Any", WorklineOutbox).__table__.c
        result = await db.execute(select(WorklineOutbox).where(columns.status.in_(list(statuses))))
        return len(list(result.scalars().all()))

    async def _load_workline_map(self, db: Any, workline_ids: list[int | None]) -> dict[int, WorkLine]:
        resolved_ids = [item for item in workline_ids if item is not None]
        if not resolved_ids:
            return {}
        columns = cast("Any", WorkLine).__table__.c
        result = await db.execute(select(WorkLine).where(columns.id.in_(resolved_ids)))
        return {item.id: item for item in result.scalars().all() if item.id is not None}

    async def _load_devices_by_workline_ids(self, db: Any, workline_ids: list[int]) -> dict[int, list[Device]]:
        if not workline_ids:
            return {}
        columns = cast("Any", Device).__table__.c
        result = await db.execute(
            select(Device)
            .where(columns.is_deleted.is_(False), columns.work_line_id.in_(workline_ids))
            .order_by(columns.sort_order.asc(), columns.role_index.asc(), columns.id.asc())
        )
        mapping: dict[int, list[Device]] = defaultdict(list)
        for item in result.scalars().all():
            if item.work_line_id is not None:
                mapping[item.work_line_id].append(item)
        return mapping

    async def _load_sessions_by_workline_ids(
        self, db: Any, workline_ids: list[int]
    ) -> dict[int, list[WorklineSession]]:
        if not workline_ids:
            return {}
        columns = cast("Any", WorklineSession).__table__.c
        recent_since = _recent_failure_since()
        result = await db.execute(
            select(WorklineSession).where(
                columns.workline_id.in_(workline_ids),
                or_(
                    columns.status.in_(list(_ACTIVE_SESSION_STATUSES)),
                    _recent_failed_clause(columns, recent_since),
                    and_(columns.status.in_(list(_WAITING_SESSION_STATUSES)), columns.deadline_at.isnot(None)),
                ),
            )
        )
        mapping: dict[int, list[WorklineSession]] = defaultdict(list)
        for item in result.scalars().all():
            mapping[item.workline_id].append(item)
        return mapping

    async def _load_recent_failed_sessions(self, db: Any, limit: int) -> list[WorklineSession]:
        columns = cast("Any", WorklineSession).__table__.c
        recent_since = _recent_failure_since()
        now = timezone.now_for_db()
        result = await db.execute(
            select(WorklineSession)
            .where(or_(_recent_failed_clause(columns, recent_since), _waiting_timeout_clause(columns, now)))
            .order_by(columns.updated_at.desc(), columns.id.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def _load_recent_failed_sessions_for_workline(
        self, db: Any, workline_id: int, limit: int
    ) -> list[WorklineSession]:
        columns = cast("Any", WorklineSession).__table__.c
        recent_since = _recent_failure_since()
        now = timezone.now_for_db()
        result = await db.execute(
            select(WorklineSession)
            .where(
                columns.workline_id == workline_id,
                or_(_recent_failed_clause(columns, recent_since), _waiting_timeout_clause(columns, now)),
            )
            .order_by(columns.updated_at.desc(), columns.id.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def _load_active_sessions_for_workline(self, db: Any, workline_id: int, limit: int) -> list[WorklineSession]:
        columns = cast("Any", WorklineSession).__table__.c
        result = await db.execute(
            select(WorklineSession)
            .where(columns.workline_id == workline_id, columns.status.in_(list(_ACTIVE_SESSION_STATUSES)))
            .order_by(columns.last_ingress_at.desc().nullslast(), columns.id.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def _load_pending_command_count_map(self, db: Any, device_ids: list[int]) -> dict[int, int]:
        if not device_ids:
            return {}
        columns = cast("Any", DeviceCommand).__table__.c
        result = await db.execute(
            select(DeviceCommand).where(
                columns.device_id.in_(device_ids), columns.status.in_(list(_PENDING_COMMAND_STATUSES))
            )
        )
        mapping: dict[int, int] = defaultdict(int)
        for item in result.scalars().all():
            mapping[item.device_id] += 1
        return mapping

    async def _load_recent_callback_time_map(self, db: Any, device_codes: list[str]) -> dict[str, Any]:
        if not device_codes:
            return {}
        columns = cast("Any", CallbackLog).__table__.c
        result = await db.execute(
            select(CallbackLog)
            .where(columns.device_id.in_(device_codes))
            .order_by(columns.device_id.asc(), columns.created_at.desc())
        )
        mapping: dict[str, Any] = {}
        for item in result.scalars().all():
            if item.device_id not in mapping:
                mapping[item.device_id] = item.created_at
        return mapping

    async def _load_recent_commands_for_device(self, db: Any, device_id: int, limit: int) -> list[DeviceCommand]:
        columns = cast("Any", DeviceCommand).__table__.c
        result = await db.execute(
            select(DeviceCommand)
            .where(columns.device_id == device_id)
            .order_by(columns.created_at.desc(), columns.id.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def _load_active_sessions_for_device(self, db: Any, device_id: int, limit: int) -> list[WorklineSession]:
        command_columns = cast("Any", DeviceCommand).__table__.c
        inbox_columns = cast("Any", WorklineInbox).__table__.c
        session_ids: set[int] = set()

        command_result = await db.execute(
            select(DeviceCommand)
            .where(command_columns.device_id == device_id)
            .order_by(command_columns.created_at.desc(), command_columns.id.desc())
            .limit(50)
        )
        for command in command_result.scalars().all():
            session_id = _parse_session_id(command.session_id)
            if session_id is not None:
                session_ids.add(session_id)

        inbox_result = await db.execute(
            select(WorklineInbox)
            .where(inbox_columns.device_id == device_id)
            .order_by(inbox_columns.received_at.desc(), inbox_columns.id.desc())
            .limit(50)
        )
        for inbox in inbox_result.scalars().all():
            if inbox.session_id is not None:
                session_ids.add(inbox.session_id)

        if not session_ids:
            return []

        session_columns = cast("Any", WorklineSession).__table__.c
        result = await db.execute(
            select(WorklineSession)
            .where(
                session_columns.id.in_(list(session_ids)), session_columns.status.in_(list(_ACTIVE_SESSION_STATUSES))
            )
            .order_by(session_columns.last_ingress_at.desc().nullslast(), session_columns.id.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def _filter_sessions_by_device_id(
        self,
        db: Any,
        sessions: list[WorklineSession],
        device_id: int,
    ) -> list[WorklineSession]:
        if not sessions:
            return []
        trace_items = await self._build_trace_list_items(db, sessions)
        allowed_session_ids = {item.session_id for item in trace_items if item.device_id == device_id}
        return [item for item in sessions if item.id in allowed_session_ids]

    async def _build_trace_list_items(self, db: Any, sessions: list[WorklineSession]) -> list[RuntimeTraceListItem]:
        if not sessions:
            return []
        now = timezone.now_for_db()
        session_ids = [item.id for item in sessions if item.id is not None]
        workline_map = await self._load_workline_map(db, [item.workline_id for item in sessions])
        latest_command_by_session = await self._load_latest_command_by_session(db, session_ids)
        latest_inbox_by_session = await self._load_latest_inbox_by_session(db, session_ids)
        latest_timeline_by_session = await self._load_latest_timeline_by_session(db, session_ids)

        device_ids = set()
        for command in latest_command_by_session.values():
            if command.device_id is not None:
                device_ids.add(command.device_id)
        for inbox in latest_inbox_by_session.values():
            if inbox.device_id is not None:
                device_ids.add(inbox.device_id)

        device_map = await self._load_device_map(db, list(device_ids))
        items: list[RuntimeTraceListItem] = []
        for session in sorted(sessions, key=_activity_dt, reverse=True):
            if session.id is None:
                continue
            command = latest_command_by_session.get(session.id)
            inbox = latest_inbox_by_session.get(session.id)
            timeline = latest_timeline_by_session.get(session.id)
            device = None
            if command is not None and command.device_id is not None:
                device = device_map.get(command.device_id)
            if device is None and inbox is not None and inbox.device_id is not None:
                device = device_map.get(inbox.device_id)
            workline = workline_map.get(session.workline_id)

            items.append(
                RuntimeTraceListItem(
                    session_id=session.id,
                    session_code=session.session_code,
                    correlation_id=session.correlation_id,
                    request_id=session.last_request_id,
                    workline_id=session.workline_id,
                    workline_name=workline.line_name if workline is not None else None,
                    workline_code=workline.line_code if workline is not None else None,
                    device_id=device.id if device is not None else None,
                    device_name=device.device_name if device is not None else None,
                    device_code=device.device_code if device is not None else None,
                    command_code=command.command_code if command is not None else None,
                    status=_enum_str(session.status) or "UNKNOWN",
                    step_code=session.step_code,
                    current_wait_type=session.current_wait_type,
                    failure_domain=session.failure_domain,
                    failure_code=session.failure_code,
                    latest_timeline_action=_enum_str(timeline.action_type) if timeline is not None else None,
                    latest_timeline_status=_enum_str(timeline.status) if timeline is not None else None,
                    latest_timeline_message=timeline.message if timeline is not None else None,
                    started_at=session.started_at,
                    last_ingress_at=session.last_ingress_at,
                    deadline_at=session.deadline_at,
                    is_timed_out=_is_timed_out(session, now),
                )
            )
        return items

    async def _load_latest_command_by_session(self, db: Any, session_ids: list[int]) -> dict[int, DeviceCommand]:
        if not session_ids:
            return {}
        columns = cast("Any", DeviceCommand).__table__.c
        session_id_values = [str(item) for item in session_ids]
        result = await db.execute(
            select(DeviceCommand)
            .where(columns.session_id.in_(session_id_values))
            .order_by(columns.session_id.asc(), columns.created_at.desc(), columns.id.desc())
        )
        mapping: dict[int, DeviceCommand] = {}
        for item in result.scalars().all():
            session_id = _parse_session_id(item.session_id)
            if session_id is not None and session_id not in mapping:
                mapping[session_id] = item
        return mapping

    async def _load_latest_inbox_by_session(self, db: Any, session_ids: list[int]) -> dict[int, WorklineInbox]:
        if not session_ids:
            return {}
        columns = cast("Any", WorklineInbox).__table__.c
        result = await db.execute(
            select(WorklineInbox)
            .where(columns.session_id.in_(session_ids))
            .order_by(columns.session_id.asc(), columns.received_at.desc(), columns.id.desc())
        )
        mapping: dict[int, WorklineInbox] = {}
        for item in result.scalars().all():
            if item.session_id is not None and item.session_id not in mapping:
                mapping[item.session_id] = item
        return mapping

    async def _load_latest_timeline_by_session(self, db: Any, session_ids: list[int]) -> dict[int, WorklineTimeline]:
        if not session_ids:
            return {}
        columns = cast("Any", WorklineTimeline).__table__.c
        result = await db.execute(
            select(WorklineTimeline)
            .where(columns.session_id.in_(session_ids))
            .order_by(columns.session_id.asc(), columns.seq_no.desc(), columns.occurred_at.desc(), columns.id.desc())
        )
        mapping: dict[int, WorklineTimeline] = {}
        for item in result.scalars().all():
            if item.session_id not in mapping:
                mapping[item.session_id] = item
        return mapping

    async def _load_device_map(self, db: Any, device_ids: list[int]) -> dict[int, Device]:
        if not device_ids:
            return {}
        columns = cast("Any", Device).__table__.c
        result = await db.execute(select(Device).where(columns.id.in_(device_ids)))
        return {item.id: item for item in result.scalars().all() if item.id is not None}

    def _build_workline_summary(
        self,
        workline: WorkLine,
        devices: list[Device],
        sessions: list[WorklineSession],
    ) -> RuntimeWorklineSummary:
        now = timezone.now_for_db()
        active_count = sum(1 for item in sessions if (_enum_str(item.status) or "") in _ACTIVE_SESSION_STATUSES)
        waiting_count = sum(1 for item in sessions if (_enum_str(item.status) or "") in _WAITING_SESSION_STATUSES)
        failed_count = sum(
            1
            for item in sessions
            if (_enum_str(item.status) or "") in _FAILURE_SESSION_STATUSES or _is_timed_out(item, now)
        )
        error_devices = sum(1 for item in devices if (_enum_str(item.device_status) or "") == "ERROR")
        offline_devices = sum(1 for item in devices if (_enum_str(item.device_status) or "") == "OFFLINE")
        maintenance_devices = sum(1 for item in devices if item.maintenance_mode)
        last_activity_at = None
        sorted_sessions = sorted(sessions, key=_activity_dt, reverse=True)
        if sorted_sessions:
            last_activity_at = _activity_dt(sorted_sessions[0])

        return RuntimeWorklineSummary(
            id=workline.id,
            line_code=workline.line_code,
            line_name=workline.line_name,
            line_type=_enum_str(workline.line_type) or "UNKNOWN",
            zone_name=workline.zone_name,
            plugin_key=workline.plugin_key,
            contract_version=workline.contract_version,
            owner_team=workline.owner_team,
            support_contact=workline.support_contact,
            is_active=workline.is_active,
            device_count=len(devices),
            active_session_count=active_count,
            waiting_session_count=waiting_count,
            failed_session_count=failed_count,
            error_device_count=error_devices,
            offline_device_count=offline_devices,
            maintenance_device_count=maintenance_devices,
            last_activity_at=last_activity_at,
        )

    def _build_workline_device_item(self, device: Device) -> RuntimeWorklineDeviceItem:
        return RuntimeWorklineDeviceItem(
            id=device.id,
            device_code=device.device_code,
            device_name=device.device_name,
            device_role=device.device_role,
            role_index=device.role_index,
            upstream_device_id=device.upstream_device_id,
            device_status=_enum_str(device.device_status) or "UNKNOWN",
            maintenance_mode=device.maintenance_mode,
            current_command_id=device.current_command_id,
            last_heartbeat_at=device.last_heartbeat_at,
            error_code=device.error_code,
        )

    def _build_device_summary(
        self,
        device: Device,
        workline: WorkLine | None,
        pending_command_count: int,
        recent_callback_at: Any,
    ) -> RuntimeDeviceSummary:
        return RuntimeDeviceSummary(
            id=device.id,
            device_code=device.device_code,
            device_name=device.device_name,
            device_role=device.device_role,
            role_index=device.role_index,
            workline_id=device.work_line_id,
            workline_name=workline.line_name if workline is not None else None,
            workline_code=workline.line_code if workline is not None else None,
            device_status=_enum_str(device.device_status) or "UNKNOWN",
            maintenance_mode=device.maintenance_mode,
            current_command_id=device.current_command_id,
            pending_command_count=pending_command_count,
            last_heartbeat_at=device.last_heartbeat_at,
            recent_callback_at=recent_callback_at,
            error_code=device.error_code,
        )

    def _build_callback_item(self, item: CallbackLog) -> TraceCallbackLogItem:
        return TraceCallbackLogItem(
            id=item.id,
            callback_type=item.callback_type,
            device_id=item.device_id,
            request_id=item.request_id,
            correlation_id=item.correlation_id,
            response_status=item.response_status,
            response_time_ms=item.response_time_ms,
            error_message=item.error_message,
            ingress_outcome=item.ingress_outcome,
            failure_stage=item.failure_stage,
            request_body=item.request_body,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )

    def _build_command_item(self, item: DeviceCommand) -> TraceCommandItem:
        return TraceCommandItem(
            id=item.id,
            device_id=item.device_id,
            command_code=item.command_code,
            correlation_id=item.correlation_id,
            workline_id=item.workline_id,
            session_id=item.session_id,
            task_type=_enum_str(item.task_type) or "UNKNOWN",
            status=_enum_str(item.status) or "UNKNOWN",
            result=_enum_str(item.result),
            retry_count=item.retry_count,
            sent_at=item.sent_at,
            ack_received_at=item.ack_received_at,
            completed_at=item.completed_at,
            ack_code=item.ack_code,
            ack_message=item.ack_message,
            ack_trace_id=item.ack_trace_id,
            step_code=item.step_code,
            params=item.params,
            result_data=item.result_data,
            error_detail=item.error_detail,
            duration_ms=_command_duration_ms(item),
        )


runtime_query_service = RuntimeQueryService()


__all__ = ["RuntimeQueryService", "runtime_query_service"]
