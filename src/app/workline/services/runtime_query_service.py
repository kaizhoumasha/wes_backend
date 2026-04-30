"""RuntimeQueryService - 运行监控中心只读聚合查询服务。"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Any, cast

from sqlalchemy import String, and_, exists, func, or_, select
from sqlalchemy import cast as sa_cast

from src.app.callback.models import CallbackLog
from src.app.callback.repositories.callback_log_repository import callback_log_repository
from src.app.device.models import Device, DeviceCommand
from src.app.device.repositories import device_repository
from src.app.workline.models import WorkLine, WorklineInbox, WorklineOutbox, WorklineSession, WorklineTimeline
from src.app.workline.models.runtime import (
    RuntimeBlockingReason,
    RuntimeDeviceDetailResponse,
    RuntimeDeviceHealthSummary,
    RuntimeDeviceSummary,
    RuntimeOverviewResponse,
    RuntimeStatCard,
    RuntimeTraceDeviceAction,
    RuntimeTraceDevicePathNode,
    RuntimeTraceListItem,
    RuntimeTraceListResponse,
    RuntimeTracePathResponse,
    RuntimeWorklineDetailResponse,
    RuntimeWorklineDeviceItem,
    RuntimeWorklineSummary,
    TraceCallbackLogItem,
    TraceCommandItem,
    TraceQueryRequest,
)
from src.app.workline.services.trace_response_builder import build_trace_response
from src.core.base_service import BaseService
from src.utils.timezone import timezone

_ACTIVE_SESSION_STATUSES = {
    "NEW",
    "RUNNING",
    "WAITING_DEVICE_RESULT",
    "WAITING_EXTERNAL",
    "MANUAL_HOLD",
}
_IN_PROGRESS_SESSION_STATUSES = {"NEW", "RUNNING"}
_WAITING_SESSION_STATUSES = {"WAITING_DEVICE_RESULT", "WAITING_EXTERNAL", "MANUAL_HOLD"}
_FAILURE_SESSION_STATUSES = {"FAILED", "CANCELLED"}
_ABNORMAL_DEVICE_STATUSES = {"ERROR", "OFFLINE", "MAINTENANCE"}
_PENDING_COMMAND_STATUSES = {"PENDING", "SENT", "ACK_RECEIVED"}
_INBOX_BACKLOG_STATUSES = {"NEW", "RETRY", "PROCESSING"}
_OUTBOX_BACKLOG_STATUSES = {"NEW", "DISPATCHING"}
_RECENT_FAILURE_HOURS = 24


def _enum_str(value: Any) -> str | None:
    return getattr(value, "value", value) if value is not None else None


def _status_str(value: Any) -> str:
    return _enum_str(value) or "UNKNOWN"


def _is_maintenance_device(item: Any) -> bool:
    return bool(getattr(item, "maintenance_mode", False)) or (
        _enum_str(getattr(item, "device_status", None)) == "MAINTENANCE"
    )


def _activity_dt(session: WorklineSession) -> Any:
    return session.last_ingress_at or session.waiting_since or session.started_at or session.created_at


def _latest_activity_at(sessions: list[WorklineSession]) -> Any:
    if not sessions:
        return None
    return _activity_dt(max(sessions, key=_activity_dt))


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


def _waiting_not_timed_out_clause(columns: Any, now: Any) -> Any:
    return and_(
        columns.status.in_(list(_WAITING_SESSION_STATUSES)),
        or_(columns.deadline_at.is_(None), columns.deadline_at >= now),
    )


def _recent_failed_clause(columns: Any, recent_since: Any) -> Any:
    return and_(columns.status.in_(list(_FAILURE_SESSION_STATUSES)), columns.updated_at >= recent_since)


def _recent_failure_or_timeout_clause(columns: Any, now: Any, recent_since: Any) -> Any:
    return and_(
        or_(columns.status.in_(list(_FAILURE_SESSION_STATUSES)), _waiting_timeout_clause(columns, now)),
        columns.updated_at >= recent_since,
    )


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


def _require_int_id(value: int | None, field_name: str) -> int:
    if value is None:
        raise ValueError(f"{field_name} must not be None when building runtime response")
    return value


def _resolve_trace_device(
    command: DeviceCommand | None,
    inbox: WorklineInbox | None,
    device_map: dict[int, Device],
) -> Device | None:
    if command is not None:
        return device_map.get(command.device_id)
    if inbox is not None and inbox.device_id is not None:
        return device_map.get(inbox.device_id)
    return None


def _device_session_clause(session_columns: Any, device_id: int) -> Any:
    command_columns = cast("Any", DeviceCommand).__table__.c
    inbox_columns = cast("Any", WorklineInbox).__table__.c
    return or_(
        exists(
            select(command_columns.id).where(
                command_columns.device_id == device_id,
                command_columns.session_id == sa_cast(session_columns.id, String()),
            )
        ),
        exists(
            select(inbox_columns.id).where(
                inbox_columns.device_id == device_id,
                inbox_columns.session_id == session_columns.id,
            )
        ),
    )


def _latest_rows_subquery(
    *,
    columns: Any,
    partition_by: Any,
    order_by: tuple[Any, ...],
    filters: list[Any],
) -> Any:
    return (
        select(
            columns.id.label("id"),
            func.row_number().over(partition_by=partition_by, order_by=order_by).label("rn"),
        )
        .where(*filters)
        .subquery()
    )


class RuntimeQueryService(BaseService[Any, Any]):
    """运行监控中心只读聚合查询服务。"""

    def __init__(self) -> None:
        super().__init__(device_repository, enable_cache=False)

    async def get_overview(self, db: Any) -> RuntimeOverviewResponse:
        worklines = await self.list_worklines(db)
        devices = await self.list_devices(db)
        recent_failed_sessions = await self._load_recent_failed_sessions(db, limit=10)
        recent_failed_traces = await self._build_trace_list_items(db, recent_failed_sessions)
        device_health = self._build_device_health_summary(devices)

        running_sessions = await self._count_by_status(db, WorklineSession, {"RUNNING"})
        waiting_sessions = await self._count_waiting_sessions(db)
        failed_sessions = await self._count_failed_or_timed_out_sessions(db)
        inbox_backlog = await self._count_by_status(db, WorklineInbox, _INBOX_BACKLOG_STATUSES)
        outbox_backlog = await self._count_by_status(db, WorklineOutbox, _OUTBOX_BACKLOG_STATUSES)
        abnormal_devices = device_health.abnormal

        hot_worklines = sorted(
            worklines,
            key=lambda item: item.active_session_count + item.waiting_session_count + item.failed_session_count,
            reverse=True,
        )[:5]
        abnormal_device_items = [
            item for item in devices if item.device_status in _ABNORMAL_DEVICE_STATUSES or _is_maintenance_device(item)
        ][:10]

        return RuntimeOverviewResponse(
            stats=self._build_overview_stats(
                running_sessions=running_sessions,
                waiting_sessions=waiting_sessions,
                failed_sessions=failed_sessions,
                inbox_backlog=inbox_backlog,
                outbox_backlog=outbox_backlog,
                abnormal_devices=abnormal_devices,
            ),
            recent_failed_traces=recent_failed_traces,
            hot_worklines=hot_worklines,
            abnormal_devices=abnormal_device_items,
            device_health=device_health,
        )

    async def get_trace_list(self, db: Any, payload: TraceQueryRequest) -> RuntimeTraceListResponse:
        columns = cast("Any", WorklineSession).__table__.c
        filters: list[Any] = []

        if payload.device_id is not None:
            filters.append(_device_session_clause(columns, payload.device_id))

        if payload.workline_id is not None:
            filters.append(columns.workline_id == payload.workline_id)
        if payload.status:
            filters.append(columns.status == payload.status)
        if payload.step_code:
            filters.append(columns.step_code == payload.step_code)
        if payload.only_active:
            filters.append(columns.status.in_(list(_ACTIVE_SESSION_STATUSES)))
        if payload.only_failed:
            recent_since = _recent_failure_since()
            now = timezone.now_for_db()
            filters.append(_recent_failure_or_timeout_clause(columns, now, recent_since))
        if payload.keyword:
            keyword = f"%{payload.keyword}%"
            filters.append(
                or_(
                    columns.session_code.ilike(keyword),
                    columns.trace_id.ilike(keyword),
                    columns.business_key.ilike(keyword),
                    columns.barcode.ilike(keyword),
                    columns.last_request_id.ilike(keyword),
                )
            )

        count_query = select(func.count()).select_from(WorklineSession).where(*filters)
        total_result = await db.execute(count_query)
        total = int(total_result.scalar_one())
        if total == 0:
            return RuntimeTraceListResponse(total=0, items=[])

        page_query = (
            select(WorklineSession)
            .where(*filters)
            .order_by(columns.last_ingress_at.desc().nullslast(), columns.id.desc())
            .offset(payload.offset)
            .limit(payload.limit)
        )
        page_result = await db.execute(page_query)
        page_items = list(page_result.scalars().all())
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
        workline_columns = cast("Any", WorkLine).__table__.c
        workline_result = await db.execute(
            select(WorkLine).where(workline_columns.id == workline_id, workline_columns.is_deleted.is_(False))
        )
        workline = workline_result.scalar_one_or_none()
        if workline is None or getattr(workline, "is_deleted", False):
            return None

        devices = await device_repository.get_by_work_line_id(db, workline_id)
        active_sessions = await self._load_active_sessions_for_workline(db, workline_id, limit=20)
        recent_failed_sessions = await self._load_recent_failed_sessions_for_workline(db, workline_id, limit=10)
        active_session_ids = {session.id for session in active_sessions}
        all_sessions = active_sessions + [item for item in recent_failed_sessions if item.id not in active_session_ids]

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

    async def list_workline_devices(self, db: Any, workline_id: int) -> list[RuntimeDeviceSummary]:
        return await self.list_devices(db, workline_id=workline_id)

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

    async def get_workline_device_detail(
        self,
        db: Any,
        workline_id: int,
        device_id: int,
    ) -> RuntimeDeviceDetailResponse | None:
        return await self.get_device_detail(db, device_id, workline_id=workline_id)

    async def _count_by_status(self, db: Any, model: Any, statuses: set[str]) -> int:
        columns = cast("Any", model).__table__.c
        result = await db.execute(select(func.count()).select_from(model).where(columns.status.in_(list(statuses))))
        return int(result.scalar_one())

    async def _count_waiting_sessions(self, db: Any) -> int:
        columns = cast("Any", WorklineSession).__table__.c
        now = timezone.now_for_db()
        result = await db.execute(
            select(func.count()).select_from(WorklineSession).where(_waiting_not_timed_out_clause(columns, now))
        )
        return int(result.scalar_one())

    async def _count_failed_or_timed_out_sessions(self, db: Any) -> int:
        columns = cast("Any", WorklineSession).__table__.c
        recent_since = _recent_failure_since()
        now = timezone.now_for_db()
        result = await db.execute(
            select(func.count())
            .select_from(WorklineSession)
            .where(_recent_failure_or_timeout_clause(columns, now, recent_since))
        )
        return int(result.scalar_one())

    async def _load_workline_map(self, db: Any, workline_ids: list[int | None]) -> dict[int, WorkLine]:
        resolved_ids = [item for item in workline_ids if item is not None]
        if not resolved_ids:
            return {}
        columns = cast("Any", WorkLine).__table__.c
        result = await db.execute(select(WorkLine).where(columns.id.in_(resolved_ids), columns.is_deleted.is_(False)))
        return {
            item.id: item
            for item in result.scalars().all()
            if item.id is not None and not getattr(item, "is_deleted", False)
        }

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
        now = timezone.now_for_db()
        result = await db.execute(
            select(WorklineSession).where(
                columns.workline_id.in_(workline_ids),
                or_(
                    columns.status.in_(list(_ACTIVE_SESSION_STATUSES)),
                    _recent_failure_or_timeout_clause(columns, now, recent_since),
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
            .where(_recent_failure_or_timeout_clause(columns, now, recent_since))
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
                _recent_failure_or_timeout_clause(columns, now, recent_since),
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
        session_columns = cast("Any", WorklineSession).__table__.c
        result = await db.execute(
            select(WorklineSession)
            .where(
                _device_session_clause(session_columns, device_id),
                session_columns.status.in_(list(_ACTIVE_SESSION_STATUSES)),
            )
            .order_by(session_columns.last_ingress_at.desc().nullslast(), session_columns.id.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def _build_trace_list_items(self, db: Any, sessions: list[WorklineSession]) -> list[RuntimeTraceListItem]:
        if not sessions:
            return []
        now = timezone.now_for_db()
        session_ids = [item.id for item in sessions if item.id is not None]
        workline_map = await self._load_workline_map(db, [item.workline_id for item in sessions])
        latest_command_by_session = await self._load_latest_command_by_session(db, session_ids)
        latest_inbox_by_session = await self._load_latest_inbox_by_session(db, session_ids)
        latest_timeline_by_session = await self._load_latest_timeline_by_session(db, session_ids)

        device_ids = {
            item.device_id
            for item in [*latest_command_by_session.values(), *latest_inbox_by_session.values()]
            if item.device_id is not None
        }
        device_map = await self._load_device_map(db, list(device_ids))
        items: list[RuntimeTraceListItem] = []
        for session in sorted(sessions, key=_activity_dt, reverse=True):
            if session.id is None:
                continue
            command = latest_command_by_session.get(session.id)
            inbox = latest_inbox_by_session.get(session.id)
            timeline = latest_timeline_by_session.get(session.id)
            workline = workline_map.get(session.workline_id)
            device = _resolve_trace_device(command, inbox, device_map)
            items.append(self._build_trace_list_item(session, workline, device, command, timeline, now))
        return items

    async def _load_latest_command_by_session(self, db: Any, session_ids: list[int]) -> dict[int, DeviceCommand]:
        if not session_ids:
            return {}
        columns = cast("Any", DeviceCommand).__table__.c
        session_id_values = [str(item) for item in session_ids]
        latest_ids = _latest_rows_subquery(
            columns=columns,
            partition_by=columns.session_id,
            order_by=(columns.created_at.desc(), columns.id.desc()),
            filters=[columns.session_id.in_(session_id_values)],
        )
        result = await db.execute(
            select(DeviceCommand).join(latest_ids, columns.id == latest_ids.c.id).where(latest_ids.c.rn == 1)
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
        latest_ids = _latest_rows_subquery(
            columns=columns,
            partition_by=columns.session_id,
            order_by=(columns.received_at.desc(), columns.id.desc()),
            filters=[columns.session_id.in_(session_ids)],
        )
        result = await db.execute(
            select(WorklineInbox).join(latest_ids, columns.id == latest_ids.c.id).where(latest_ids.c.rn == 1)
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
        latest_ids = _latest_rows_subquery(
            columns=columns,
            partition_by=columns.session_id,
            order_by=(columns.seq_no.desc(), columns.occurred_at.desc(), columns.id.desc()),
            filters=[columns.session_id.in_(session_ids)],
        )
        result = await db.execute(
            select(WorklineTimeline).join(latest_ids, columns.id == latest_ids.c.id).where(latest_ids.c.rn == 1)
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
        active_count = sum(1 for item in sessions if (_enum_str(item.status) or "") in _IN_PROGRESS_SESSION_STATUSES)
        waiting_count = sum(
            1
            for item in sessions
            if (_enum_str(item.status) or "") in _WAITING_SESSION_STATUSES and not _is_timed_out(item, now)
        )
        failed_count = sum(
            1
            for item in sessions
            if (_enum_str(item.status) or "") in _FAILURE_SESSION_STATUSES or _is_timed_out(item, now)
        )
        error_devices = sum(1 for item in devices if (_enum_str(item.device_status) or "") == "ERROR")
        offline_devices = sum(1 for item in devices if (_enum_str(item.device_status) or "") == "OFFLINE")
        maintenance_devices = sum(1 for item in devices if _is_maintenance_device(item))

        return RuntimeWorklineSummary(
            id=_require_int_id(workline.id, "workline.id"),
            line_code=workline.line_code,
            line_name=workline.line_name,
            line_type=_status_str(workline.line_type),
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
            run_mode=_enum_str(workline.run_mode) or "AUTO",
            last_activity_at=_latest_activity_at(sessions),
        )

    def _build_device_health_summary(
        self,
        devices: list[RuntimeDeviceSummary],
    ) -> RuntimeDeviceHealthSummary:
        abnormal = sum(1 for item in devices if item.device_status in _ABNORMAL_DEVICE_STATUSES)
        maintenance = sum(1 for item in devices if _is_maintenance_device(item))
        loaded = sum(
            1
            for item in devices
            if item.pending_command_count > 0
            and item.device_status not in _ABNORMAL_DEVICE_STATUSES
            and not _is_maintenance_device(item)
        )
        healthy = sum(
            1
            for item in devices
            if item.device_status not in _ABNORMAL_DEVICE_STATUSES and not _is_maintenance_device(item)
        )

        return RuntimeDeviceHealthSummary(
            total=len(devices),
            abnormal=abnormal,
            maintenance=maintenance,
            loaded=loaded,
            healthy=healthy,
        )

    def _build_workline_device_item(self, device: Device) -> RuntimeWorklineDeviceItem:
        return RuntimeWorklineDeviceItem(
            id=_require_int_id(device.id, "device.id"),
            device_code=device.device_code,
            device_name=device.device_name,
            device_role=device.device_role,
            role_index=device.role_index,
            upstream_device_id=device.upstream_device_id,
            device_status=_status_str(device.device_status),
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
            id=_require_int_id(device.id, "device.id"),
            device_code=device.device_code,
            device_name=device.device_name,
            device_role=device.device_role,
            role_index=device.role_index,
            workline_id=device.work_line_id,
            workline_name=workline.line_name if workline is not None else None,
            workline_code=workline.line_code if workline is not None else None,
            device_status=_status_str(device.device_status),
            maintenance_mode=device.maintenance_mode,
            current_command_id=device.current_command_id,
            pending_command_count=pending_command_count,
            last_heartbeat_at=device.last_heartbeat_at,
            recent_callback_at=recent_callback_at,
            error_code=device.error_code,
        )

    def _build_callback_item(self, item: CallbackLog) -> TraceCallbackLogItem:
        return TraceCallbackLogItem(
            id=_require_int_id(item.id, "callback_log.id"),
            callback_type=item.callback_type,
            device_id=item.device_id,
            request_id=item.request_id,
            trace_id=item.trace_id,
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
            id=_require_int_id(item.id, "device_command.id"),
            device_id=item.device_id,
            command_code=item.command_code,
            trace_id=item.trace_id,
            workline_id=item.workline_id,
            session_id=item.session_id,
            task_type=_status_str(item.task_type),
            status=_status_str(item.status),
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

    def _build_overview_stats(
        self,
        *,
        running_sessions: int,
        waiting_sessions: int,
        failed_sessions: int,
        inbox_backlog: int,
        outbox_backlog: int,
        abnormal_devices: int,
    ) -> list[RuntimeStatCard]:
        return [
            RuntimeStatCard(key="running_sessions", label="运行中 Session", value=running_sessions, status="warning"),
            RuntimeStatCard(key="waiting_sessions", label="等待中 Session", value=waiting_sessions, status="info"),
            RuntimeStatCard(key="failed_sessions", label="失败 / 超时 Session", value=failed_sessions, status="danger"),
            RuntimeStatCard(key="inbox_backlog", label="Inbox 积压", value=inbox_backlog, status="warning"),
            RuntimeStatCard(key="outbox_backlog", label="Outbox 积压", value=outbox_backlog, status="warning"),
            RuntimeStatCard(key="abnormal_devices", label="异常设备", value=abnormal_devices, status="danger"),
        ]

    def _build_trace_list_item(
        self,
        session: WorklineSession,
        workline: WorkLine | None,
        device: Device | None,
        command: DeviceCommand | None,
        timeline: WorklineTimeline | None,
        now: Any,
    ) -> RuntimeTraceListItem:
        return RuntimeTraceListItem(
            session_id=_require_int_id(session.id, "session.id"),
            session_code=session.session_code,
            trace_id=session.trace_id,
            request_id=session.last_request_id,
            workline_id=session.workline_id,
            workline_name=workline.line_name if workline is not None else None,
            workline_code=workline.line_code if workline is not None else None,
            device_id=device.id if device is not None else None,
            device_name=device.device_name if device is not None else None,
            device_code=device.device_code if device is not None else None,
            command_code=command.command_code if command is not None else None,
            status=_status_str(session.status),
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

    async def get_session_path(self, db: Any, session_id: int) -> RuntimeTracePathResponse | None:
        """聚合 Session 粒度的设备路径视图。"""
        from src.app.workline.services.trace_query_service import trace_query_service

        result = await trace_query_service.by_session_id(db, session_id)
        if result.session is None:
            return None
        return self._build_trace_path(result)

    async def get_trace_path(self, db: Any, trace_id: str) -> RuntimeTracePathResponse | None:
        """聚合 Trace ID 粒度的设备路径视图。"""
        from src.app.workline.services.trace_query_service import trace_query_service

        result = await trace_query_service.by_trace_id(db, trace_id)
        if result.session is None and not result.callback_logs:
            return None
        return self._build_trace_path(result)

    def _build_trace_path(self, result: Any) -> RuntimeTracePathResponse:
        """从 TraceQueryResult 构建设备路径视图。"""

        session = result.session
        devices_map: dict[int, RuntimeTraceDevicePathNode] = {}

        def _ensure_node(device_id: int | None) -> RuntimeTraceDevicePathNode | None:
            if device_id is None:
                return None
            node = devices_map.get(device_id)
            if node is None:
                node = RuntimeTraceDevicePathNode(device_id=device_id)
                devices_map[device_id] = node
            return node

        for cmd in result.commands:
            node = _ensure_node(cmd.device_id)
            if node is not None:
                node.actions.append(
                    RuntimeTraceDeviceAction(
                        kind="command",
                        label=_enum_str(cmd.task_type) or cmd.command_code or "COMMAND",
                        status=_enum_str(cmd.status),
                        timestamp=cmd.completed_at or cmd.sent_at,
                        message=f"{cmd.command_code} · {_enum_str(cmd.result) or ''}",
                    )
                )

        for inbox in result.inboxes:
            node = _ensure_node(inbox.device_id)
            if node is not None:
                node.actions.append(
                    RuntimeTraceDeviceAction(
                        kind="inbox",
                        label=f"INBOX {_enum_str(inbox.kind) or ''}",
                        status=_enum_str(inbox.status),
                        timestamp=inbox.processed_at or inbox.received_at,
                        message=inbox.error_message,
                    )
                )

        for node in devices_map.values():
            # 按时间戳排序，datetime 转为 ISO 字符串确保类型一致
            node.actions.sort(key=lambda a: a.timestamp.isoformat() if a.timestamp else "")

        blocking_device_id: int | None = None
        blocking_reason: RuntimeBlockingReason | None = None
        if session and session.current_wait_type:
            awaiting_cmd_id = session.awaiting_command_id
            if awaiting_cmd_id:
                cmd = next((c for c in result.commands if c.id == awaiting_cmd_id), None)
                if cmd:
                    blocking_device_id = cmd.device_id
                    blocking_reason = RuntimeBlockingReason(
                        device_id=cmd.device_id,
                        reason=f"等待设备响应 {_enum_str(cmd.task_type) or cmd.command_code}",
                        detail=f"command #{cmd.id} · {_enum_str(cmd.status)} · step={cmd.step_code}",
                    )
            if blocking_device_id is None and session.failure_domain:
                blocking_reason = RuntimeBlockingReason(
                    reason=session.failure_domain,
                    detail=session.failure_message,
                )

        trace_id = result.trace.trace_id if result.trace else None

        evidence = build_trace_response(result)

        return RuntimeTracePathResponse(
            workline_id=session.workline_id if session else None,
            session_id=session.id if session else None,
            trace_id=trace_id,
            devices=list(devices_map.values()),
            current_blocking_device_id=blocking_device_id,
            blocking_reason=blocking_reason,
            evidence=evidence,
        )


runtime_query_service = RuntimeQueryService()


__all__ = ["RuntimeQueryService", "runtime_query_service"]
