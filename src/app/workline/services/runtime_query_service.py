"""RuntimeQueryService - 运行监控中心只读聚合查询服务。"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, TypeVar, cast

from sqlalchemy import and_, exists, func, or_, select

from src.app.callback.models import CallbackLog
from src.app.callback.repositories.callback_log_repository import callback_log_repository
from src.app.device.models import Device, DeviceCommand
from src.app.device.repositories import device_repository
from src.app.resource.services.active_rack_snapshot_service import smt_active_rack_snapshot_service
from src.app.sys.models import SystemOutbox, SystemOutboxStatus
from src.app.workline.models import (
    InboxKind,
    MaterialUnit,
    WorkLine,
    WorklineInbox,
    WorklineSession,
    WorklineTimeline,
)
from src.app.workline.models.runtime import (
    RuntimeBlockingReason,
    RuntimeDeviceDetailResponse,
    RuntimeDeviceHealthSummary,
    RuntimeDeviceSummary,
    RuntimeMonitorActionCandidates,
    RuntimeMonitorCommandSnapshot,
    RuntimeMonitorDeviceNode,
    RuntimeMonitorEvidenceSection,
    RuntimeMonitorReconciliationCandidate,
    RuntimeMonitorSessionItem,
    RuntimeMonitorSessionSection,
    RuntimeMonitorTraceItem,
    RuntimeMonitorTraceSection,
    RuntimeOverviewResponse,
    RuntimeRackOperationWait,
    RuntimeResourceEvidenceItem,
    RuntimeResourceEvidenceKind,
    RuntimeResourceKind,
    RuntimeSingleLayerRackSnapshot,
    RuntimeStatCard,
    RuntimeStationLease,
    RuntimeTraceDeviceAction,
    RuntimeTraceDevicePathNode,
    RuntimeTraceListItem,
    RuntimeTraceListResponse,
    RuntimeTracePathResponse,
    RuntimeTraceTimelineGroup,
    RuntimeWorklineBoundary,
    RuntimeWorklineDetailResponse,
    RuntimeWorklineDeviceItem,
    RuntimeWorklineMonitorProjectionResponse,
    RuntimeWorklineReadiness,
    RuntimeWorklineSummary,
    TraceCallbackLogItem,
    TraceCommandItem,
    TraceQueryRequest,
)
from src.app.workline.repositories.runtime_hold_repository import runtime_hold_repository
from src.app.workline.services.diagnosis_verdict_builder import diagnosis_verdict_builder
from src.app.workline.services.station_lease_service import station_lease_service
from src.app.workline.services.trace_resource_view_builder import build_trace_resource_view
from src.app.workline.services.trace_response_builder import (
    _blocked_wait_seconds,
    _resource_wait_detail_summary,
    build_trace_session_item,
    build_trace_timeline_item,
)
from src.core.base_service import BaseService
from src.utils.timezone import timezone
from src.utils.value_normalization import optional_enum_str
from src.workline_plugin_registry import get_workline_plugin_definition
from src.workline_runtime.business_identity import resolve_payload_display_identity
from src.workline_runtime.utils import ensure_dict

T = TypeVar("T")

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
_COMPLETED_SESSION_STATUSES = {"COMPLETED"}
_ABNORMAL_DEVICE_STATUSES = {"ERROR", "OFFLINE", "MAINTENANCE"}
_PENDING_COMMAND_STATUSES = {"PENDING", "SENT", "ACK_RECEIVED"}
_INBOX_BACKLOG_STATUSES = {"NEW", "RETRY", "PROCESSING"}
_OUTBOX_BACKLOG_STATUSES = {"NEW", "DISPATCHING"}
_RECENT_FAILURE_HOURS = 24
_RUNTIME_DETAIL_ACTIVE_SESSION_LIMIT = 200
_RUNTIME_RESOURCE_EVIDENCE_ITEM_LIMIT = 50
_RUNTIME_RESOURCE_EVIDENCE_KIND_PRIORITY = {
    RuntimeResourceEvidenceKind.WES_ACTIVE_SNAPSHOT: 0,
    RuntimeResourceEvidenceKind.WMS_CALLBACK_EVIDENCE: 1,
    RuntimeResourceEvidenceKind.TRACE_RESOURCE_EVIDENCE: 2,
    RuntimeResourceEvidenceKind.GENERIC_EVIDENCE: 3,
    RuntimeResourceEvidenceKind.UNKNOWN: 4,
}
_RUNTIME_RESOURCE_KIND_PRIORITY = {
    RuntimeResourceKind.RACK: 0,
    RuntimeResourceKind.SLOT: 1,
    RuntimeResourceKind.BIN: 2,
    RuntimeResourceKind.CELL: 3,
    RuntimeResourceKind.PKG: 4,
    RuntimeResourceKind.PART_SN: 5,
    RuntimeResourceKind.MAGAZINE: 6,
    RuntimeResourceKind.UNKNOWN: 7,
}
_RUNTIME_STATION_CODE_KEYS = ("station_code", "work_station_code", "target_station_code")
_RUNTIME_STATION_NESTED_CODE_KEYS = ("station_code", "code")
_RUNTIME_STATION_METADATA_GROUP = ("station", *_RUNTIME_STATION_CODE_KEYS)
_RUNTIME_POSITION_CODE_KEYS = ("target_position_code", "work_position_code", "source_position_code", "position_code")
_ORCHESTRATOR_TIMELINE_ACTIONS = {
    "SESSION_CREATED",
    "SESSION_STARTED",
    "SESSION_RESUMED",
    "SESSION_COMPLETED",
    "SESSION_FAILED",
    "SESSION_CANCELLED",
    "STATUS_CHANGED",
    "WAIT_STARTED",
    "WAIT_RESUMED",
    "WAIT_TIMEOUT",
    "DECISION_MADE",
}


@dataclass(frozen=True, slots=True)
class _DeviceIdentity:
    device_id: int
    device_code: str | None = None
    device_name: str | None = None


@dataclass(frozen=True, slots=True)
class _BlockedOutboxProjection:
    count_by_device_id: dict[int, int]
    command_codes_by_device_id: dict[int, set[str]]
    summary_by_device_id: dict[int, dict[str, Any]]


@dataclass(frozen=True, slots=True)
class _RuntimeEvidenceSession:
    id: int
    trace_id: str | None
    context_json: dict[str, Any] | None
    last_ingress_at: datetime | None
    started_at: datetime | None
    created_at: datetime | None


@dataclass(frozen=True, slots=True)
class _RuntimeResourceEvidenceProjection:
    kind: RuntimeResourceEvidenceKind
    items: list[RuntimeResourceEvidenceItem]
    total_count: int
    truncated: bool
    has_non_single_layer: bool


def _status_str(value: Any) -> str:
    return optional_enum_str(value) or "UNKNOWN"


def _api_utc_datetime(value: Any) -> datetime | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return timezone.to_utc(value)
    if isinstance(value, (int, float)):
        return timezone.to_utc(float(value))
    parsed = timezone.parse_datetime(value)
    return timezone.to_utc(parsed) if parsed is not None else None


def _is_maintenance_device(item: Any) -> bool:
    return bool(getattr(item, "maintenance_mode", False)) or (
        optional_enum_str(getattr(item, "device_status", None)) == "MAINTENANCE"
    )


def _activity_dt(session: WorklineSession) -> Any:
    return (
        session.last_ingress_at or session.waiting_since or session.ended_at or session.started_at or session.created_at
    )


def _latest_activity_at(sessions: list[WorklineSession]) -> Any:
    if not sessions:
        return None
    return _activity_dt(max(sessions, key=_activity_dt))


def _is_timed_out(session: WorklineSession, now: Any) -> bool:
    status = optional_enum_str(getattr(session, "status", None))
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


def _payload_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _event_type_from_payload(payload: dict[str, Any]) -> str | None:
    return _first_payload_str(payload, ("canonical_event_type", "event_type"))


def _first_payload_str(payload: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _session_initial_payload_display_identity(session: WorklineSession) -> str | None:
    context = ensure_dict(getattr(session, "context_json", None))
    initial_payload = ensure_dict(context.get("initial_payload"))
    return resolve_payload_display_identity(initial_payload)


def _build_device_identity_maps(devices: list[Any]) -> tuple[dict[int, _DeviceIdentity], dict[str, _DeviceIdentity]]:
    by_id: dict[int, _DeviceIdentity] = {}
    by_code: dict[str, _DeviceIdentity] = {}
    for device in devices:
        device_id = getattr(device, "id", None)
        if device_id is None:
            continue
        identity = _DeviceIdentity(
            device_id=device_id,
            device_code=getattr(device, "device_code", None),
            device_name=getattr(device, "device_name", None),
        )
        by_id[device_id] = identity
        if identity.device_code:
            by_code[identity.device_code] = identity
    return by_id, by_code


def _device_identity_from_source(source: Any) -> _DeviceIdentity | None:
    device_id = getattr(source, "device_id", None)
    if device_id is None:
        return None
    device = getattr(source, "device", None)
    return _DeviceIdentity(
        device_id=device_id,
        device_code=getattr(source, "device_code", None) or getattr(device, "device_code", None),
        device_name=getattr(source, "device_name", None) or getattr(device, "device_name", None),
    )


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


def _trace_current_action(command: DeviceCommand | None, timeline: WorklineTimeline | None) -> str | None:
    if command is not None:
        return optional_enum_str(command.task_type) or command.command_code
    if timeline is not None:
        return optional_enum_str(timeline.action_type)
    return None


def _trace_action_source(
    awaiting_command: DeviceCommand | None,
    latest_command: DeviceCommand | None,
    timeline: WorklineTimeline | None,
) -> str:
    if awaiting_command is not None:
        return "AWAITING_COMMAND"
    if latest_command is not None:
        return "LATEST_COMMAND"
    if timeline is not None:
        return "TIMELINE"
    return "NONE"


def _device_session_clause(session_columns: Any, device_id: int) -> Any:
    command_columns = cast("Any", DeviceCommand).__table__.c
    inbox_columns = cast("Any", WorklineInbox).__table__.c
    return or_(
        exists(
            select(command_columns.id).where(
                command_columns.device_id == device_id,
                command_columns.session_id_int == session_columns.id,
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

    async def get_overview(self, db: Any, *, include_sim: bool = False) -> RuntimeOverviewResponse:
        worklines = await self.list_worklines(db, exclude_simulation=not include_sim)
        devices = await self.list_devices(db)
        recent_failed_sessions = await self._load_recent_failed_sessions(db, limit=10)
        recent_failed_traces = await self._build_trace_list_items(db, recent_failed_sessions)
        device_health = self._build_device_health_summary(devices)

        sim_workline_ids = await self._load_simulation_workline_ids(db) if not include_sim else []
        running_sessions = await self._count_by_status(
            db, WorklineSession, {"RUNNING"}, exclude_workline_ids=sim_workline_ids
        )
        waiting_sessions = await self._count_waiting_sessions(db, exclude_workline_ids=sim_workline_ids)
        failed_sessions = await self._count_failed_or_timed_out_sessions(db, exclude_workline_ids=sim_workline_ids)
        inbox_backlog = await self._count_by_status(db, WorklineInbox, _INBOX_BACKLOG_STATUSES)
        outbox_backlog = await self._count_by_status(db, SystemOutbox, _OUTBOX_BACKLOG_STATUSES)
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

    async def list_worklines(self, db: Any, *, exclude_simulation: bool = False) -> list[RuntimeWorklineSummary]:
        workline_columns = cast("Any", WorkLine).__table__.c
        query = select(WorkLine).where(workline_columns.is_deleted.is_(False))
        if exclude_simulation:
            query = query.where(workline_columns.run_mode != "SIMULATION")
        workline_result = await db.execute(query.order_by(workline_columns.id.asc()))
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

    async def _load_workline_session_summary_counts(
        self, db: Any, workline_id: int, now: Any
    ) -> tuple[dict[str, int], int, int]:
        session_columns = cast("Any", WorklineSession).__table__.c
        active_status_count_result = await db.execute(
            select(session_columns.status, func.count(session_columns.id))
            .where(
                session_columns.workline_id == workline_id,
                session_columns.status.in_(list(_ACTIVE_SESSION_STATUSES)),
            )
            .group_by(session_columns.status)
        )
        active_status_counts = {
            optional_enum_str(status) or str(status): int(count) for status, count in active_status_count_result.all()
        }

        waiting_count_result = await db.execute(
            select(func.count(session_columns.id)).where(
                session_columns.workline_id == workline_id,
                _waiting_not_timed_out_clause(session_columns, now),
            )
        )
        waiting_sessions_total = int(waiting_count_result.scalar_one() or 0)

        recent_since = _recent_failure_since()
        failed_count_result = await db.execute(
            select(func.count(session_columns.id)).where(
                session_columns.workline_id == workline_id,
                _recent_failure_or_timeout_clause(session_columns, now, recent_since),
            )
        )
        recent_failed_total = int(failed_count_result.scalar_one() or 0)
        return active_status_counts, waiting_sessions_total, recent_failed_total

    async def get_workline_detail(self, db: Any, workline_id: int) -> RuntimeWorklineDetailResponse | None:
        workline_columns = cast("Any", WorkLine).__table__.c
        workline_result = await db.execute(
            select(WorkLine).where(workline_columns.id == workline_id, workline_columns.is_deleted.is_(False))
        )
        workline = workline_result.scalar_one_or_none()
        if workline is None or getattr(workline, "is_deleted", False):
            return None

        devices = await device_repository.get_by_work_line_id(db, workline_id)
        all_active_sessions = await self._load_active_sessions_for_workline(
            db,
            workline_id,
            limit=_RUNTIME_DETAIL_ACTIVE_SESSION_LIMIT,
        )
        active_sessions = all_active_sessions[:20]
        recent_failed_sessions = await self._load_recent_failed_sessions_for_workline(db, workline_id, limit=10)
        recent_completed_sessions = await self._load_recent_completed_sessions_for_workline(db, workline_id, limit=10)
        active_session_ids = {session.id for session in all_active_sessions}
        visible_terminal_sessions = [
            item for item in [*recent_failed_sessions, *recent_completed_sessions] if item.id not in active_session_ids
        ]
        all_sessions = all_active_sessions + visible_terminal_sessions

        blocked_outbox_projection = await self._load_blocked_outbox_projection(db, devices)
        open_command_map = await self._load_open_command_count_map(
            db,
            [item.id for item in devices if item.id is not None],
            blocked_command_codes_by_device=blocked_outbox_projection.command_codes_by_device_id,
        )
        active_hold_ids_map = await self._load_active_runtime_hold_ids_map(
            db, [item.id for item in devices if item.id is not None]
        )
        summary = self._build_workline_summary(workline, devices, all_sessions)
        if len(all_active_sessions) >= _RUNTIME_DETAIL_ACTIVE_SESSION_LIMIT or len(recent_failed_sessions) >= 10:
            (
                active_status_counts,
                waiting_sessions_total,
                recent_failed_total,
            ) = await self._load_workline_session_summary_counts(db, workline_id, timezone.now_for_db())
            summary.active_session_count = sum(
                active_status_counts.get(status, 0) for status in _IN_PROGRESS_SESSION_STATUSES
            )
            summary.waiting_session_count = waiting_sessions_total
            summary.failed_session_count = recent_failed_total
        device_items = [
            self._build_workline_device_item(
                device,
                open_command_count=open_command_map.get(device.id or 0, 0),
                blocked_outbox_count=blocked_outbox_projection.count_by_device_id.get(device.id or 0, 0),
                blocked_outbox_summary=blocked_outbox_projection.summary_by_device_id.get(device.id or 0),
                active_runtime_hold_ids=active_hold_ids_map.get(device.id or 0, []),
            )
            for device in devices
        ]
        active_trace_items = await self._build_trace_list_items(db, active_sessions)
        failed_trace_items = await self._build_trace_list_items(db, recent_failed_sessions)
        completed_trace_items = await self._build_trace_list_items(db, recent_completed_sessions)
        structured_boundary = await self._build_workline_runtime_boundary(db, workline, all_active_sessions)

        return RuntimeWorklineDetailResponse(
            summary=summary,
            workline_readiness=structured_boundary["workline_readiness"],
            station_lease=structured_boundary["station_lease"],
            single_layer_rack_snapshot=structured_boundary["single_layer_rack_snapshot"],
            rack_operation_wait=structured_boundary["rack_operation_wait"],
            resource_evidence_kind=structured_boundary["resource_evidence_kind"],
            resource_evidence_items=structured_boundary["resource_evidence_items"],
            resource_evidence_total_count=structured_boundary["resource_evidence_total_count"],
            resource_evidence_truncated=structured_boundary["resource_evidence_truncated"],
            devices=device_items,
            active_sessions=active_trace_items,
            recent_failed_traces=failed_trace_items,
            recent_completed_traces=completed_trace_items,
        )

    async def get_workline_monitor_projection(
        self, db: Any, workline_id: int
    ) -> RuntimeWorklineMonitorProjectionResponse | None:
        workline_columns = cast("Any", WorkLine).__table__.c
        workline_result = await db.execute(
            select(WorkLine).where(workline_columns.id == workline_id, workline_columns.is_deleted.is_(False))
        )
        workline = workline_result.scalar_one_or_none()
        if workline is None or getattr(workline, "is_deleted", False):
            return None

        devices = await device_repository.get_by_work_line_id(db, workline_id)

        now = timezone.now_for_db()
        (
            active_status_counts,
            waiting_sessions_total,
            recent_failed_total,
        ) = await self._load_workline_session_summary_counts(
            db,
            workline_id,
            now,
        )
        active_sessions_total = sum(active_status_counts.values())

        active_sessions = await self._load_active_sessions_for_workline(
            db,
            workline_id,
            limit=20,
        )
        boundary_active_sessions = await self._load_active_sessions_for_workline(
            db,
            workline_id,
            limit=_RUNTIME_DETAIL_ACTIVE_SESSION_LIMIT,
        )
        active_sessions_truncated = active_sessions_total > len(active_sessions)

        recent_failed_sessions = await self._load_recent_failed_sessions_for_workline(db, workline_id, limit=10)
        recent_failed_truncated = recent_failed_total > 10

        completed_columns = cast("Any", WorklineSession).__table__.c
        completed_count_query = select(func.count(completed_columns.id)).where(
            completed_columns.workline_id == workline_id,
            completed_columns.status.in_(list(_COMPLETED_SESSION_STATUSES)),
        )
        completed_count_result = await db.execute(completed_count_query)
        recent_completed_total = completed_count_result.scalar_one() or 0
        recent_completed_sessions = await self._load_recent_completed_sessions_for_workline(db, workline_id, limit=10)
        recent_completed_truncated = recent_completed_total > 10

        active_session_ids = {session.id for session in boundary_active_sessions}
        visible_terminal_sessions = [
            item for item in [*recent_failed_sessions, *recent_completed_sessions] if item.id not in active_session_ids
        ]
        all_sessions = boundary_active_sessions + visible_terminal_sessions

        blocked_outbox_projection = await self._load_blocked_outbox_projection(db, devices)
        open_command_map = await self._load_open_command_count_map(
            db,
            [item.id for item in devices if item.id is not None],
            blocked_command_codes_by_device=blocked_outbox_projection.command_codes_by_device_id,
        )
        active_hold_ids_map = await self._load_active_runtime_hold_ids_map(
            db, [item.id for item in devices if item.id is not None]
        )
        current_command_ids = [
            device.current_command_id for device in devices if getattr(device, "current_command_id", None) is not None
        ]
        current_command_rows = await self._load_command_map_by_ids(db, current_command_ids)
        current_command_snapshots: dict[int, RuntimeMonitorCommandSnapshot] = {}
        for command_id, command_row in current_command_rows.items():
            current_command_snapshots[command_id] = RuntimeMonitorCommandSnapshot(
                id=command_id,
                command_code=command_row.command_code,
                status=_status_str(command_row.status),
                sent_at=_api_utc_datetime(command_row.sent_at),
                ack_received_at=_api_utc_datetime(command_row.ack_received_at),
                ack_code=command_row.ack_code,
                ack_message=command_row.ack_message,
            )
        summary = self._build_workline_summary(workline, devices, all_sessions)
        summary.active_session_count = sum(
            active_status_counts.get(status, 0) for status in _IN_PROGRESS_SESSION_STATUSES
        )
        summary.waiting_session_count = waiting_sessions_total
        summary.failed_session_count = int(recent_failed_total)
        summary.stopped_at = _api_utc_datetime(summary.stopped_at)
        summary.resumed_at = _api_utc_datetime(summary.resumed_at)
        summary.start_admission_checked_at = _api_utc_datetime(summary.start_admission_checked_at)
        summary.last_activity_at = _api_utc_datetime(summary.last_activity_at)

        device_nodes = [
            self._build_monitor_device_node(
                device,
                open_command_count=open_command_map.get(device.id or 0, 0),
                blocked_outbox_count=blocked_outbox_projection.count_by_device_id.get(device.id or 0, 0),
                blocked_outbox_summary=blocked_outbox_projection.summary_by_device_id.get(device.id or 0),
                active_runtime_hold_ids=active_hold_ids_map.get(device.id or 0, []),
                current_command=(
                    current_command_snapshots.get(device.current_command_id)
                    if getattr(device, "current_command_id", None) is not None
                    else None
                ),
            )
            for device in devices
        ]

        active_trace_items = await self._build_trace_list_items(db, active_sessions)
        failed_trace_items = await self._build_trace_list_items(db, recent_failed_sessions)
        completed_trace_items = await self._build_trace_list_items(db, recent_completed_sessions)
        structured_boundary = await self._build_workline_runtime_boundary(
            db,
            workline,
            boundary_active_sessions,
            full_resource_evidence=active_sessions_total > _RUNTIME_DETAIL_ACTIVE_SESSION_LIMIT,
        )

        reconcile_columns = cast("Any", WorklineSession).__table__.c
        reconciliation_result = await db.execute(
            select(WorklineSession)
            .where(reconcile_columns.workline_id == workline_id, reconcile_columns.reconciliation_state == "PENDING")
            .order_by(reconcile_columns.reconciliation_occurred_at.desc(), reconcile_columns.id.desc())
            .limit(1)
        )
        pending_session = reconciliation_result.scalar_one_or_none()
        pending_reconciliation = None
        if pending_session is not None:
            reason_str = ""
            if pending_session.reconciliation_reason:
                reason_str = (
                    pending_session.reconciliation_reason.value
                    if hasattr(pending_session.reconciliation_reason, "value")
                    else str(pending_session.reconciliation_reason)
                )
            source_kind_str = ""
            if pending_session.reconciliation_source_kind:
                source_kind_str = (
                    pending_session.reconciliation_source_kind.value
                    if hasattr(pending_session.reconciliation_source_kind, "value")
                    else str(pending_session.reconciliation_source_kind)
                )
            pending_reconciliation = RuntimeMonitorReconciliationCandidate(
                session_id=pending_session.id,
                session_code=pending_session.session_code,
                trace_id=pending_session.trace_id,
                request_id=getattr(pending_session, "last_request_id", None)
                or getattr(pending_session, "request_id", None),
                reason=reason_str,
                source_kind=source_kind_str,
                device_id=pending_session.reconciliation_device_id,
                command_id=pending_session.reconciliation_command_id,
                wait_token=pending_session.reconciliation_wait_token,
                occurred_at=_api_utc_datetime(pending_session.reconciliation_occurred_at or pending_session.updated_at)
                or timezone.now_utc(),
                deadline_at=_api_utc_datetime(pending_session.reconciliation_deadline_at),
                late_evidence_received=getattr(pending_session, "reconciliation_late_evidence_received", False),
            )

        def to_monitor_session_item(item) -> RuntimeMonitorSessionItem:
            return RuntimeMonitorSessionItem(
                session_id=item.session_id,
                session_code=item.session_code,
                trace_id=item.trace_id,
                request_id=item.request_id,
                last_inbox_id=item.last_inbox_id,
                barcode=item.barcode,
                workline_id=item.workline_id,
                device_id=item.device_id,
                device_name=item.device_name,
                device_code=item.device_code,
                status=item.status,
                current_wait_type=item.current_wait_type,
                failure_domain=item.failure_domain,
                failure_code=item.failure_code,
                latest_timeline_action=item.latest_timeline_action,
                latest_timeline_status=item.latest_timeline_status,
                latest_timeline_message=item.latest_timeline_message,
                started_at=_api_utc_datetime(item.started_at),
                last_ingress_at=_api_utc_datetime(item.last_ingress_at),
                deadline_at=_api_utc_datetime(item.deadline_at),
                is_timed_out=item.is_timed_out,
            )

        def to_monitor_trace_item(item) -> RuntimeMonitorTraceItem:
            return RuntimeMonitorTraceItem(
                session_id=item.session_id,
                session_code=item.session_code,
                trace_id=item.trace_id,
                request_id=item.request_id,
                barcode=item.barcode,
                workline_id=item.workline_id,
                device_id=item.device_id,
                device_name=item.device_name,
                device_code=item.device_code,
                status=item.status,
                failure_domain=item.failure_domain,
                failure_code=item.failure_code,
                latest_timeline_action=item.latest_timeline_action,
                latest_timeline_status=item.latest_timeline_status,
                latest_timeline_message=item.latest_timeline_message,
                started_at=_api_utc_datetime(item.started_at),
                last_ingress_at=_api_utc_datetime(item.last_ingress_at),
                deadline_at=_api_utc_datetime(item.deadline_at),
                is_timed_out=item.is_timed_out,
            )

        resource_items = [
            RuntimeResourceEvidenceItem(
                resource_kind=item.resource_kind,
                resource_code=item.resource_code,
                display_label=item.display_label,
                evidence_kind=item.evidence_kind,
                station_code=item.station_code,
                position_code=item.position_code,
                rack_code=item.rack_code,
                bin_code=item.bin_code,
                slot_code=item.slot_code,
                cell_code=item.cell_code,
                pkg_code=item.pkg_code,
                part_sn=item.part_sn,
                material_code=item.material_code,
                date_code=item.date_code,
                lot_code=item.lot_code,
                reel_count=item.reel_count,
                reel_code=item.reel_code,
                position_index=item.position_index,
                source_session_id=item.source_session_id,
                source_trace_id=item.source_trace_id,
                occurred_at=_api_utc_datetime(item.occurred_at),
            )
            for item in structured_boundary["resource_evidence_items"]
        ]

        generated_at = timezone.now_utc()

        return RuntimeWorklineMonitorProjectionResponse(
            summary=summary,
            boundary=RuntimeWorklineBoundary(
                workline_readiness=structured_boundary["workline_readiness"],
                station_lease=structured_boundary["station_lease"],
                single_layer_rack_snapshot=structured_boundary["single_layer_rack_snapshot"],
                rack_operation_wait=structured_boundary["rack_operation_wait"],
            ),
            device_nodes=device_nodes,
            active_sessions=RuntimeMonitorSessionSection(
                items=[to_monitor_session_item(item) for item in active_trace_items],
                total_count=active_sessions_total,
                truncated=active_sessions_truncated,
            ),
            recent_failed_traces=RuntimeMonitorTraceSection(
                items=[to_monitor_trace_item(item) for item in failed_trace_items],
                total_count=recent_failed_total,
                truncated=recent_failed_truncated,
            ),
            recent_completed_traces=RuntimeMonitorTraceSection(
                items=[to_monitor_trace_item(item) for item in completed_trace_items],
                total_count=recent_completed_total,
                truncated=recent_completed_truncated,
            ),
            resource_evidence=RuntimeMonitorEvidenceSection(
                kind=structured_boundary["resource_evidence_kind"],
                items=resource_items,
                total_count=structured_boundary["resource_evidence_total_count"],
                truncated=structured_boundary["resource_evidence_truncated"],
            ),
            action_candidates=RuntimeMonitorActionCandidates(pending_reconciliation=pending_reconciliation),
            generated_at=generated_at,
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
        blocked_outbox_projection = await self._load_blocked_outbox_projection(db, devices)
        open_command_map = await self._load_open_command_count_map(
            db,
            [item.id for item in devices if item.id is not None],
            blocked_command_codes_by_device=blocked_outbox_projection.command_codes_by_device_id,
        )
        active_hold_ids_map = await self._load_active_runtime_hold_ids_map(
            db, [item.id for item in devices if item.id is not None]
        )
        callback_time_map = await self._load_recent_callback_time_map(db, [item.device_code for item in devices])

        return [
            self._build_device_summary(
                device,
                workline_map.get(device.work_line_id),
                open_command_map.get(device.id or 0, 0),
                callback_time_map.get(device.device_code),
                blocked_outbox_count=blocked_outbox_projection.count_by_device_id.get(device.id or 0, 0),
                blocked_outbox_summary=blocked_outbox_projection.summary_by_device_id.get(device.id or 0),
                active_runtime_hold_ids=active_hold_ids_map.get(device.id or 0, []),
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

        blocked_outbox_projection = await self._load_blocked_outbox_projection(db, [device])
        open_command_map = await self._load_open_command_count_map(
            db,
            [device_id],
            blocked_command_codes_by_device=blocked_outbox_projection.command_codes_by_device_id,
        )
        active_hold_ids_map = await self._load_active_runtime_hold_ids_map(db, [device_id])
        callback_time_map = await self._load_recent_callback_time_map(db, [device.device_code])
        summary = self._build_device_summary(
            device,
            workline,
            open_command_map.get(device_id, 0),
            callback_time_map.get(device.device_code),
            blocked_outbox_count=blocked_outbox_projection.count_by_device_id.get(device_id, 0),
            blocked_outbox_summary=blocked_outbox_projection.summary_by_device_id.get(device_id),
            active_runtime_hold_ids=active_hold_ids_map.get(device_id, []),
        )

        recent_commands = await self._load_recent_commands_for_device(db, device_id, limit=20)
        recent_callbacks = await callback_log_repository.get_by_subject_code(db, device.device_code, limit=20)
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

    async def _load_simulation_workline_ids(self, db: Any) -> list[int]:
        columns = cast("Any", WorkLine).__table__.c
        result = await db.execute(
            select(columns.id).where(columns.is_deleted.is_(False), columns.run_mode == "SIMULATION")
        )
        return [row[0] for row in result.all() if row[0] is not None]

    async def _count_by_status(
        self,
        db: Any,
        model: Any,
        statuses: set[str],
        *,
        exclude_workline_ids: list[int] | None = None,
    ) -> int:
        columns = cast("Any", model).__table__.c
        filters: list[Any] = [columns.status.in_(list(statuses))]
        if exclude_workline_ids and hasattr(columns, "workline_id"):
            filters.append(columns.workline_id.not_in(exclude_workline_ids))
        result = await db.execute(select(func.count()).select_from(model).where(*filters))
        return int(result.scalar_one())

    async def _count_waiting_sessions(self, db: Any, *, exclude_workline_ids: list[int] | None = None) -> int:
        columns = cast("Any", WorklineSession).__table__.c
        now = timezone.now_for_db()
        filters: list[Any] = [_waiting_not_timed_out_clause(columns, now)]
        if exclude_workline_ids:
            filters.append(columns.workline_id.not_in(exclude_workline_ids))
        result = await db.execute(select(func.count()).select_from(WorklineSession).where(*filters))
        return int(result.scalar_one())

    async def _count_failed_or_timed_out_sessions(
        self, db: Any, *, exclude_workline_ids: list[int] | None = None
    ) -> int:
        columns = cast("Any", WorklineSession).__table__.c
        recent_since = _recent_failure_since()
        now = timezone.now_for_db()
        filters: list[Any] = [_recent_failure_or_timeout_clause(columns, now, recent_since)]
        if exclude_workline_ids:
            filters.append(columns.workline_id.not_in(exclude_workline_ids))
        result = await db.execute(select(func.count()).select_from(WorklineSession).where(*filters))
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

    async def _load_recent_completed_sessions_for_workline(
        self, db: Any, workline_id: int, limit: int
    ) -> list[WorklineSession]:
        columns = cast("Any", WorklineSession).__table__.c
        result = await db.execute(
            select(WorklineSession)
            .where(columns.workline_id == workline_id, columns.status.in_(list(_COMPLETED_SESSION_STATUSES)))
            .order_by(columns.ended_at.desc().nullslast(), columns.updated_at.desc(), columns.id.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def _load_active_sessions_for_workline(
        self,
        db: Any,
        workline_id: int,
        limit: int | None,
    ) -> list[WorklineSession]:
        columns = cast("Any", WorklineSession).__table__.c
        query = (
            select(WorklineSession)
            .where(columns.workline_id == workline_id, columns.status.in_(list(_ACTIVE_SESSION_STATUSES)))
            .order_by(columns.last_ingress_at.desc().nullslast(), columns.id.desc())
        )
        if limit is not None:
            query = query.limit(limit)
        result = await db.execute(query)
        return list(result.scalars().all())

    async def _load_open_command_count_map(
        self,
        db: Any,
        device_ids: list[int],
        *,
        blocked_command_codes_by_device: dict[int, set[str]] | None = None,
    ) -> dict[int, int]:
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
            blocked_codes = blocked_command_codes_by_device or {}
            if item.command_code in blocked_codes.get(item.device_id, set()):
                continue
            mapping[item.device_id] += 1
        return mapping

    async def _load_blocked_outbox_projection(self, db: Any, devices: list[Device]) -> _BlockedOutboxProjection:
        device_ids = [item.id for item in devices if item.id is not None]
        if not device_ids:
            return _BlockedOutboxProjection(
                count_by_device_id={}, command_codes_by_device_id={}, summary_by_device_id={}
            )

        by_id, by_code = _build_device_identity_maps(devices)
        columns = cast("Any", SystemOutbox).__table__.c
        result = await db.execute(
            select(SystemOutbox).where(
                columns.status == SystemOutboxStatus.BLOCKED_RESOURCE,
                or_(
                    columns.blocked_device_id.in_(device_ids),
                    columns.target_code.in_([item.device_code for item in devices]),
                ),
            )
        )
        count_by_device_id: dict[int, int] = defaultdict(int)
        command_codes_by_device_id: dict[int, set[str]] = defaultdict(set)
        head_by_device_id: dict[int, Any] = {}
        for outbox in result.scalars().all():
            device_id = outbox.blocked_device_id
            if device_id is None:
                target_identity = by_code.get(outbox.target_code)
                device_id = target_identity.device_id if target_identity is not None else None
            if device_id is None or device_id not in by_id:
                continue
            count_by_device_id[device_id] += 1
            current_head = head_by_device_id.get(device_id)
            if current_head is None or _blocked_outbox_is_earlier(outbox, current_head):
                head_by_device_id[device_id] = outbox
            payload = _payload_dict(outbox.payload_json)
            command_code = payload.get("command_code")
            if isinstance(command_code, str) and command_code:
                command_codes_by_device_id[device_id].add(command_code)

        return _BlockedOutboxProjection(
            count_by_device_id=dict(count_by_device_id),
            command_codes_by_device_id={key: set(value) for key, value in command_codes_by_device_id.items()},
            summary_by_device_id={
                device_id: {
                    "blocked_reason": getattr(outbox, "blocked_reason", None),
                    "blocked_wait_seconds": _blocked_wait_seconds(getattr(outbox, "blocked_at", None)),
                    "blocked_check_count": getattr(outbox, "blocked_check_count", None),
                    "blocked_detail_json": _resource_wait_detail_summary(getattr(outbox, "blocked_detail_json", None)),
                }
                for device_id, outbox in head_by_device_id.items()
            },
        )

    async def _load_active_runtime_hold_ids_map(self, db: Any, device_ids: list[int]) -> dict[int, list[int]]:
        if not device_ids:
            return {}
        holds_by_device = await runtime_hold_repository.list_active_by_device_ids(db, device_ids=device_ids)
        return {
            device_id: [cast("int", hold.id) for hold in holds if hold.id is not None]
            for device_id, holds in holds_by_device.items()
        }

    async def _load_recent_callback_time_map(self, db: Any, device_codes: list[str]) -> dict[str, Any]:
        if not device_codes:
            return {}
        columns = cast("Any", CallbackLog).__table__.c
        result = await db.execute(
            select(CallbackLog)
            .where(columns.subject_code.in_(device_codes))
            .order_by(columns.subject_code.asc(), columns.created_at.desc())
        )
        mapping: dict[str, Any] = {}
        for item in result.scalars().all():
            if item.subject_code not in mapping:
                mapping[item.subject_code] = item.created_at
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
        awaiting_command_ids = [
            session.awaiting_command_id for session in sessions if isinstance(session.awaiting_command_id, int)
        ]
        awaiting_command_by_id = await self._load_command_map_by_ids(db, awaiting_command_ids)
        latest_inbox_by_session = await self._load_latest_inbox_by_session(db, session_ids)
        latest_event_inbox_by_session = await self._load_latest_event_inbox_by_session(db, session_ids)
        latest_timeline_by_session = await self._load_latest_timeline_by_session(db, session_ids)

        device_ids = {
            item.device_id
            for item in [
                *latest_command_by_session.values(),
                *awaiting_command_by_id.values(),
                *latest_inbox_by_session.values(),
            ]
            if item.device_id is not None
        }
        device_map = await self._load_device_map(db, list(device_ids))
        items: list[RuntimeTraceListItem] = []
        for session in sorted(sessions, key=_activity_dt, reverse=True):
            if session.id is None:
                continue
            latest_command = latest_command_by_session.get(session.id)
            awaiting_command_id = session.awaiting_command_id
            awaiting_command = (
                awaiting_command_by_id.get(awaiting_command_id) if isinstance(awaiting_command_id, int) else None
            )
            command = awaiting_command or latest_command
            inbox = latest_inbox_by_session.get(session.id)
            event_inbox = latest_event_inbox_by_session.get(session.id)
            timeline = latest_timeline_by_session.get(session.id)
            workline = workline_map.get(session.workline_id)
            device = _resolve_trace_device(command, inbox, device_map)
            latest_device = _resolve_trace_device(latest_command, inbox, device_map)
            items.append(
                self._build_trace_list_item(
                    session,
                    workline,
                    device,
                    command,
                    timeline,
                    now,
                    inbox=event_inbox,
                    latest_device=latest_device,
                    action_source=_trace_action_source(awaiting_command, latest_command, timeline),
                )
            )
        return items

    async def _load_command_map_by_ids(self, db: Any, command_ids: list[int]) -> dict[int, DeviceCommand]:
        command_ids = [item for item in command_ids if isinstance(item, int)]
        if not command_ids:
            return {}
        columns = cast("Any", DeviceCommand).__table__.c
        result = await db.execute(select(DeviceCommand).where(columns.id.in_(command_ids)))
        return {item.id: item for item in result.scalars().all() if item.id is not None}

    async def _load_latest_command_by_session(self, db: Any, session_ids: list[int]) -> dict[int, DeviceCommand]:
        if not session_ids:
            return {}
        columns = cast("Any", DeviceCommand).__table__.c
        latest_ids = _latest_rows_subquery(
            columns=columns,
            partition_by=columns.session_id_int,
            order_by=(columns.created_at.desc(), columns.id.desc()),
            filters=[columns.session_id_int.in_(session_ids)],
        )
        result = await db.execute(
            select(DeviceCommand).join(latest_ids, columns.id == latest_ids.c.id).where(latest_ids.c.rn == 1)
        )
        mapping: dict[int, DeviceCommand] = {}
        for item in result.scalars().all():
            session_id = getattr(item, "session_id_int", None)
            if isinstance(session_id, int) and session_id not in mapping:
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

    async def _load_latest_event_inbox_by_session(self, db: Any, session_ids: list[int]) -> dict[int, WorklineInbox]:
        if not session_ids:
            return {}
        columns = cast("Any", WorklineInbox).__table__.c
        latest_ids = _latest_rows_subquery(
            columns=columns,
            partition_by=columns.session_id,
            order_by=(columns.received_at.desc(), columns.id.desc()),
            filters=[columns.session_id.in_(session_ids), columns.kind == InboxKind.DEVICE_EVENT],
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
        active_count = sum(
            1 for item in sessions if (optional_enum_str(item.status) or "") in _IN_PROGRESS_SESSION_STATUSES
        )
        waiting_count = sum(
            1
            for item in sessions
            if (optional_enum_str(item.status) or "") in _WAITING_SESSION_STATUSES and not _is_timed_out(item, now)
        )
        failed_count = sum(
            1
            for item in sessions
            if (optional_enum_str(item.status) or "") in _FAILURE_SESSION_STATUSES or _is_timed_out(item, now)
        )
        error_devices = sum(1 for item in devices if (optional_enum_str(item.device_status) or "") == "ERROR")
        offline_devices = sum(1 for item in devices if (optional_enum_str(item.device_status) or "") == "OFFLINE")
        maintenance_devices = sum(1 for item in devices if _is_maintenance_device(item))

        return RuntimeWorklineSummary(
            id=_require_int_id(workline.id, "workline.id"),
            line_code=workline.line_code,
            line_name=workline.line_name,
            line_type=_status_str(workline.line_type),
            zone_name=workline.zone_name,
            plugin_key=workline.plugin_key,
            contract_version=workline.contract_version,
            is_active=workline.is_active,
            device_count=len(devices),
            active_session_count=active_count,
            waiting_session_count=waiting_count,
            failed_session_count=failed_count,
            error_device_count=error_devices,
            offline_device_count=offline_devices,
            maintenance_device_count=maintenance_devices,
            run_mode=optional_enum_str(workline.run_mode) or "AUTO",
            runtime_status=optional_enum_str(getattr(workline, "runtime_status", None)) or "STOPPED",
            active_safety_incident_id=getattr(workline, "active_safety_incident_id", None),
            stopped_at=getattr(workline, "stopped_at", None),
            stopped_reason=getattr(workline, "stopped_reason", None),
            resumed_at=getattr(workline, "resumed_at", None),
            start_admission_status=getattr(workline, "start_admission_status", None),
            start_admission_message=getattr(workline, "start_admission_message", None),
            start_admission_failed_device_code=getattr(workline, "start_admission_failed_device_code", None),
            start_admission_checked_at=getattr(workline, "start_admission_checked_at", None),
            last_start_request_id=getattr(workline, "last_start_request_id", None),
            last_start_trace_id=getattr(workline, "last_start_trace_id", None),
            last_activity_at=_latest_activity_at(sessions),
        )

    async def _build_workline_runtime_boundary(
        self,
        db: Any,
        workline: WorkLine,
        sessions: list[WorklineSession],
        *,
        full_resource_evidence: bool = False,
    ) -> dict[str, Any]:
        station_lease = RuntimeStationLease.UNKNOWN
        single_layer_rack_snapshot = RuntimeSingleLayerRackSnapshot.UNKNOWN
        resource_evidence_kind = RuntimeResourceEvidenceKind.UNKNOWN
        active_snapshots: list[tuple[str, dict[str, Any]]] = []
        position_codes = self._single_layer_boundary_positions(workline)

        if position_codes:
            station_lease = await self._load_runtime_station_lease(db, workline, position_codes)
            single_layer_rack_snapshot, active_snapshots = await self._load_single_layer_rack_snapshot_projection(
                db,
                workline,
                position_codes,
            )
            if single_layer_rack_snapshot == RuntimeSingleLayerRackSnapshot.ACTIVE:
                resource_evidence_kind = RuntimeResourceEvidenceKind.WES_ACTIVE_SNAPSHOT

        rack_operation_wait = self._runtime_rack_operation_wait(sessions)
        if full_resource_evidence:
            resource_evidence_projection = await self._load_runtime_resource_evidence_projection_for_workline(
                db,
                _require_int_id(workline.id, "workline.id"),
                active_snapshots=active_snapshots,
                current=resource_evidence_kind,
                rack_operation_wait=rack_operation_wait,
            )
        else:
            resource_evidence_projection = self._runtime_resource_evidence_projection(
                sessions,
                active_snapshots=active_snapshots,
                current=resource_evidence_kind,
                rack_operation_wait=rack_operation_wait,
            )
        resource_evidence_kind = resource_evidence_projection.kind
        resource_evidence_projection = await self._with_material_unit_locations(db, resource_evidence_projection)
        if (
            single_layer_rack_snapshot == RuntimeSingleLayerRackSnapshot.MISSING
            and resource_evidence_projection.has_non_single_layer
        ):
            single_layer_rack_snapshot = RuntimeSingleLayerRackSnapshot.NON_SINGLE_LAYER_EVIDENCE

        return {
            "workline_readiness": self._runtime_workline_readiness(workline).value,
            "station_lease": station_lease.value,
            "single_layer_rack_snapshot": single_layer_rack_snapshot.value,
            "rack_operation_wait": rack_operation_wait.value,
            "resource_evidence_kind": resource_evidence_kind.value,
            "resource_evidence_items": resource_evidence_projection.items,
            "resource_evidence_total_count": resource_evidence_projection.total_count,
            "resource_evidence_truncated": resource_evidence_projection.truncated,
        }

    def _runtime_resource_evidence_projection(
        self,
        sessions: list[WorklineSession],
        *,
        active_snapshots: list[tuple[str, dict[str, Any]]],
        current: RuntimeResourceEvidenceKind,
        rack_operation_wait: RuntimeRackOperationWait,
    ) -> _RuntimeResourceEvidenceProjection:
        resource_evidence_kind = self._runtime_resource_evidence_kind(
            sessions,
            current=current,
            rack_operation_wait=rack_operation_wait,
        )
        resource_evidence_items = self._runtime_resource_evidence_items(
            sessions,
            active_snapshots=active_snapshots,
        )
        total_count = len(resource_evidence_items)
        return _RuntimeResourceEvidenceProjection(
            kind=resource_evidence_kind,
            items=resource_evidence_items[:_RUNTIME_RESOURCE_EVIDENCE_ITEM_LIMIT],
            total_count=total_count,
            truncated=total_count > _RUNTIME_RESOURCE_EVIDENCE_ITEM_LIMIT,
            has_non_single_layer=self._has_non_single_layer_resource_evidence(sessions),
        )

    async def _load_runtime_resource_evidence_projection_for_workline(
        self,
        db: Any,
        workline_id: int,
        *,
        active_snapshots: list[tuple[str, dict[str, Any]]],
        current: RuntimeResourceEvidenceKind,
        rack_operation_wait: RuntimeRackOperationWait,
    ) -> _RuntimeResourceEvidenceProjection:
        states: list[RuntimeResourceEvidenceKind] = []
        if current != RuntimeResourceEvidenceKind.UNKNOWN:
            states.append(current)

        deduped: dict[
            tuple[str, str, str, int | None, str | None, str | None, str | None, str | None],
            RuntimeResourceEvidenceItem,
        ] = {}

        def merge_items(items: list[RuntimeResourceEvidenceItem]) -> None:
            for item in items:
                key = _runtime_resource_evidence_item_key(item)
                if key not in deduped:
                    deduped[key] = item

        merge_items(self._runtime_resource_evidence_items([], active_snapshots=active_snapshots))

        has_non_single_layer = False
        columns = cast("Any", WorklineSession).__table__.c
        offset = 0
        while True:
            result = await db.execute(
                select(
                    columns.id.label("id"),
                    columns.trace_id.label("trace_id"),
                    columns.context_json.label("context_json"),
                    columns.last_ingress_at.label("last_ingress_at"),
                    columns.started_at.label("started_at"),
                    columns.created_at.label("created_at"),
                )
                .where(columns.workline_id == workline_id, columns.status.in_(list(_ACTIVE_SESSION_STATUSES)))
                .order_by(columns.last_ingress_at.desc().nullslast(), columns.id.desc())
                .offset(offset)
                .limit(_RUNTIME_DETAIL_ACTIVE_SESSION_LIMIT)
            )
            rows = list(result.mappings().all())
            if not rows:
                break

            sessions = [
                cast(
                    "WorklineSession",
                    _RuntimeEvidenceSession(
                        id=row["id"],
                        trace_id=row["trace_id"],
                        context_json=row["context_json"],
                        last_ingress_at=row["last_ingress_at"],
                        started_at=row["started_at"],
                        created_at=row["created_at"],
                    ),
                )
                for row in rows
            ]
            for session in sessions:
                context = ensure_dict(getattr(session, "context_json", None))
                if ensure_dict(context.get("active_bin_rack")):
                    states.append(RuntimeResourceEvidenceKind.GENERIC_EVIDENCE)
                states.extend(
                    _runtime_resource_evidence_kind_from_payload(evidence)
                    for evidence in _runtime_resource_evidence_payloads(context)
                )
                if not has_non_single_layer:
                    has_non_single_layer = self._has_non_single_layer_resource_evidence([session])

            merge_items(self._runtime_resource_evidence_items(sessions, active_snapshots=[]))

            if len(rows) < _RUNTIME_DETAIL_ACTIVE_SESSION_LIMIT:
                break
            offset += _RUNTIME_DETAIL_ACTIVE_SESSION_LIMIT

        if not states and rack_operation_wait == RuntimeRackOperationWait.WAITING_WMS:
            states.append(RuntimeResourceEvidenceKind.GENERIC_EVIDENCE)

        resource_evidence_items = sorted(deduped.values(), key=_runtime_resource_evidence_item_sort_key)
        total_count = len(resource_evidence_items)
        return _RuntimeResourceEvidenceProjection(
            kind=_highest_priority_state(
                states,
                list(_RUNTIME_RESOURCE_EVIDENCE_KIND_PRIORITY),
                RuntimeResourceEvidenceKind.UNKNOWN,
            ),
            items=resource_evidence_items[:_RUNTIME_RESOURCE_EVIDENCE_ITEM_LIMIT],
            total_count=total_count,
            truncated=total_count > _RUNTIME_RESOURCE_EVIDENCE_ITEM_LIMIT,
            has_non_single_layer=has_non_single_layer,
        )

    async def _with_material_unit_locations(
        self,
        db: Any,
        projection: _RuntimeResourceEvidenceProjection,
    ) -> _RuntimeResourceEvidenceProjection:
        pkg_codes = {
            item.pkg_code or item.resource_code
            for item in projection.items
            if item.resource_kind == RuntimeResourceKind.PKG and (item.pkg_code or item.resource_code)
        }
        if not pkg_codes:
            return projection
        columns = cast("Any", MaterialUnit).__table__.c
        result = await db.execute(
            select(MaterialUnit).where(columns.pkg_code.in_(sorted(pkg_codes)), columns.current_location.isnot(None))
        )
        rows = result.scalars().all()
        location_by_pkg = {
            material_unit.pkg_code: material_unit.current_location
            for material_unit in rows
            if material_unit.pkg_code and material_unit.current_location
        }
        if not location_by_pkg:
            return projection
        return _RuntimeResourceEvidenceProjection(
            kind=projection.kind,
            items=[
                item.model_copy(update={"position_code": location_by_pkg.get(item.pkg_code or item.resource_code)})
                if (
                    item.resource_kind == RuntimeResourceKind.PKG
                    and item.position_code is None
                    and location_by_pkg.get(item.pkg_code or item.resource_code)
                )
                else item
                for item in projection.items
            ],
            total_count=projection.total_count,
            truncated=projection.truncated,
            has_non_single_layer=projection.has_non_single_layer,
        )

    @staticmethod
    def _runtime_workline_readiness(workline: WorkLine) -> RuntimeWorklineReadiness:
        runtime_status = optional_enum_str(getattr(workline, "runtime_status", None))
        if runtime_status == "READY":
            return RuntimeWorklineReadiness.READY
        if runtime_status in {"STOPPED", "STARTING", "ESTOPPED", "RECONCILING"}:
            return RuntimeWorklineReadiness.NOT_READY
        return RuntimeWorklineReadiness.UNKNOWN

    @staticmethod
    def _single_layer_boundary_positions(workline: WorkLine) -> list[str]:
        definition = get_workline_plugin_definition(getattr(workline, "plugin_key", None))
        if definition is None:
            return []
        position_codes: list[str] = []
        for boundary in getattr(definition.manifest, "resource_boundaries", ()):
            if getattr(boundary, "rack_kind", None) == "SINGLE_LAYER":
                position_code = str(getattr(boundary, "rack_position_code", "") or "").strip()
                if position_code and position_code not in position_codes:
                    position_codes.append(position_code)
        return position_codes

    @staticmethod
    def _manifest_position_metadata_by_code(workline: WorkLine) -> dict[str, dict[str, str]]:
        definition = get_workline_plugin_definition(getattr(workline, "plugin_key", None))
        if definition is None:
            return {}

        metadata_by_code: dict[str, dict[str, str]] = {}
        for position in getattr(definition.manifest, "rack_positions", ()):
            position_code = str(getattr(position, "code", "") or "").strip()
            if not position_code:
                continue
            metadata: dict[str, str] = {"position_code": position_code}
            station_code = str(getattr(position, "station_code", "") or "").strip()
            if station_code:
                metadata["station_code"] = station_code
            station_role = str(getattr(position, "role", "") or "").strip()
            if station_role:
                metadata["station_role"] = station_role
            metadata_by_code[position_code] = metadata
        return metadata_by_code

    async def _load_runtime_station_lease(
        self,
        db: Any,
        workline: WorkLine,
        position_codes: list[str],
    ) -> RuntimeStationLease:
        workline_id = getattr(workline, "id", None)
        workline_code = str(getattr(workline, "line_code", "") or "")
        if workline_id is None or not workline_code:
            return RuntimeStationLease.UNKNOWN
        states: list[RuntimeStationLease] = []
        for position_code in position_codes:
            try:
                status = await station_lease_service.get_station_lease_status(
                    db,
                    workline_id=workline_id,
                    workline_code=workline_code,
                    position_code=position_code,
                )
            except ValueError:
                states.append(RuntimeStationLease.UNKNOWN)
                continue
            if getattr(status, "available", False):
                states.append(RuntimeStationLease.IDLE)
                continue
            reason_code = getattr(status, "reason_code", None)
            normalized = str(getattr(reason_code, "value", reason_code or ""))
            try:
                states.append(RuntimeStationLease(normalized))
            except ValueError:
                states.append(RuntimeStationLease.UNKNOWN)
        return _highest_priority_state(
            states,
            [
                RuntimeStationLease.ACTIVE_RACK_BOUND,
                RuntimeStationLease.ACTIVE_DISPATCH_LEASE,
                RuntimeStationLease.ACTIVE_SESSION_BOUND,
                RuntimeStationLease.UNKNOWN,
                RuntimeStationLease.IDLE,
            ],
            RuntimeStationLease.UNKNOWN,
        )

    async def _load_single_layer_rack_snapshot_state(
        self,
        db: Any,
        workline: WorkLine,
        position_codes: list[str],
    ) -> RuntimeSingleLayerRackSnapshot:
        state, _ = await self._load_single_layer_rack_snapshot_projection(db, workline, position_codes)
        return state

    async def _load_single_layer_rack_snapshot_projection(
        self,
        db: Any,
        workline: WorkLine,
        position_codes: list[str],
    ) -> tuple[RuntimeSingleLayerRackSnapshot, list[tuple[str, dict[str, Any]]]]:
        states: list[RuntimeSingleLayerRackSnapshot] = []
        active_snapshots: list[tuple[str, dict[str, Any]]] = []
        position_metadata_by_code = self._manifest_position_metadata_by_code(workline)
        for position_code in position_codes:
            try:
                snapshot = await smt_active_rack_snapshot_service.get_active_bin_rack(
                    db,
                    workline=workline,
                    context={"station": {"position_code": position_code}},
                )
            except ValueError:
                states.append(RuntimeSingleLayerRackSnapshot.INVALID)
                continue
            if snapshot and isinstance(snapshot, Mapping):
                snapshot_payload = _runtime_payload_with_metadata_defaults(
                    dict(snapshot),
                    position_metadata_by_code.get(position_code, {"position_code": position_code}),
                )
                active_snapshots.append((position_code, snapshot_payload))
            states.append(RuntimeSingleLayerRackSnapshot.ACTIVE if snapshot else RuntimeSingleLayerRackSnapshot.MISSING)
        return (
            _highest_priority_state(
                states,
                [
                    RuntimeSingleLayerRackSnapshot.ACTIVE,
                    RuntimeSingleLayerRackSnapshot.INVALID,
                    RuntimeSingleLayerRackSnapshot.NON_SINGLE_LAYER_EVIDENCE,
                    RuntimeSingleLayerRackSnapshot.MISSING,
                    RuntimeSingleLayerRackSnapshot.UNKNOWN,
                ],
                RuntimeSingleLayerRackSnapshot.UNKNOWN,
            ),
            active_snapshots,
        )

    @staticmethod
    def _runtime_rack_operation_wait(sessions: list[WorklineSession]) -> RuntimeRackOperationWait:
        states: list[RuntimeRackOperationWait] = []
        now = timezone.now_for_db()
        for session in sessions:
            context = ensure_dict(getattr(session, "context_json", None))
            rack_operation = ensure_dict(context.get("rack_operation"))
            has_rack_wait = bool(context.get("waiting_rack_operation_key") or rack_operation.get("operation_key"))
            if not has_rack_wait:
                continue
            operation_status = str(rack_operation.get("status") or "").upper()
            session_status = optional_enum_str(getattr(session, "status", None))
            if operation_status in {"ARRIVED", "SUCCEEDED", "COMPLETED", "DONE"}:
                states.append(RuntimeRackOperationWait.WMS_CALLBACK_RECEIVED)
                continue
            if operation_status == "TIMEOUT":
                states.append(RuntimeRackOperationWait.TIMEOUT)
                continue
            if operation_status in {"FAILED", "CANCELLED", "REJECTED"} or session_status in _FAILURE_SESSION_STATUSES:
                states.append(RuntimeRackOperationWait.FAILED)
                continue
            deadline_at = getattr(session, "deadline_at", None)
            if deadline_at is not None and deadline_at <= now:
                states.append(RuntimeRackOperationWait.TIMEOUT)
                continue
            if session_status == "WAITING_EXTERNAL" or context.get("waiting_rack_operation_key"):
                states.append(RuntimeRackOperationWait.WAITING_WMS)
                continue
            states.append(RuntimeRackOperationWait.UNKNOWN)
        return _highest_priority_state(
            states,
            [
                RuntimeRackOperationWait.FAILED,
                RuntimeRackOperationWait.TIMEOUT,
                RuntimeRackOperationWait.WAITING_WMS,
                RuntimeRackOperationWait.WMS_CALLBACK_RECEIVED,
                RuntimeRackOperationWait.UNKNOWN,
            ],
            RuntimeRackOperationWait.NONE,
        )

    @staticmethod
    def _runtime_resource_evidence_kind(
        sessions: list[WorklineSession],
        *,
        current: RuntimeResourceEvidenceKind,
        rack_operation_wait: RuntimeRackOperationWait,
    ) -> RuntimeResourceEvidenceKind:
        states: list[RuntimeResourceEvidenceKind] = []
        if current != RuntimeResourceEvidenceKind.UNKNOWN:
            states.append(current)
        for session in sessions:
            context = ensure_dict(getattr(session, "context_json", None))
            if ensure_dict(context.get("active_bin_rack")):
                states.append(RuntimeResourceEvidenceKind.GENERIC_EVIDENCE)
            states.extend(
                _runtime_resource_evidence_kind_from_payload(evidence)
                for evidence in _runtime_resource_evidence_payloads(context)
            )
        if not states and rack_operation_wait == RuntimeRackOperationWait.WAITING_WMS:
            states.append(RuntimeResourceEvidenceKind.GENERIC_EVIDENCE)
        return _highest_priority_state(
            states,
            list(_RUNTIME_RESOURCE_EVIDENCE_KIND_PRIORITY),
            RuntimeResourceEvidenceKind.UNKNOWN,
        )

    @staticmethod
    def _runtime_resource_evidence_items(
        sessions: list[WorklineSession],
        *,
        active_snapshots: list[tuple[str, dict[str, Any]]],
    ) -> list[RuntimeResourceEvidenceItem]:
        items: list[RuntimeResourceEvidenceItem] = []
        for position_code, snapshot in active_snapshots:
            items.extend(
                _runtime_resource_evidence_items_from_active_snapshot(
                    snapshot,
                    evidence_kind=RuntimeResourceEvidenceKind.WES_ACTIVE_SNAPSHOT,
                    fallback_position_code=position_code,
                )
            )

        for session in sessions:
            context = ensure_dict(getattr(session, "context_json", None))
            active_snapshot = ensure_dict(context.get("active_bin_rack"))
            if active_snapshot:
                items.extend(
                    _runtime_resource_evidence_items_from_active_snapshot(
                        active_snapshot,
                        evidence_kind=RuntimeResourceEvidenceKind.GENERIC_EVIDENCE,
                        session=session,
                        context=context,
                    )
                )
            for evidence in _runtime_resource_evidence_payloads(context):
                evidence_kind = _runtime_resource_evidence_kind_from_payload(evidence)
                items.extend(
                    _runtime_resource_evidence_items_from_payload(
                        evidence,
                        evidence_kind=evidence_kind,
                        session=session,
                        context=context,
                    )
                )
                nested_active_snapshot = ensure_dict(evidence.get("active_bin_rack"))
                if nested_active_snapshot:
                    items.extend(
                        _runtime_resource_evidence_items_from_active_snapshot(
                            nested_active_snapshot,
                            evidence_kind=evidence_kind,
                            session=session,
                            context={**context, **evidence},
                        )
                    )

        return _dedupe_runtime_resource_evidence_items(items)

    @staticmethod
    def _has_non_single_layer_resource_evidence(sessions: list[WorklineSession]) -> bool:
        for session in sessions:
            context = ensure_dict(getattr(session, "context_json", None))
            active_bin_rack = ensure_dict(context.get("active_bin_rack"))
            for evidence in [active_bin_rack, *_runtime_resource_evidence_payloads(context)]:
                rack_kind = _runtime_resource_evidence_rack_kind(evidence)
                if rack_kind is not None and rack_kind != "SINGLE_LAYER":
                    return True
        return False

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

    def _build_workline_device_item(
        self,
        device: Device,
        *,
        open_command_count: int = 0,
        blocked_outbox_count: int = 0,
        blocked_outbox_summary: dict[str, Any] | None = None,
        active_runtime_hold_ids: list[int] | None = None,
    ) -> RuntimeWorklineDeviceItem:
        hold_ids = active_runtime_hold_ids or []
        blocked_summary = blocked_outbox_summary or {}
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
            open_command_count=open_command_count,
            pending_command_count=open_command_count,
            blocked_outbox_count=blocked_outbox_count,
            blocked_reason=blocked_summary.get("blocked_reason"),
            blocked_wait_seconds=blocked_summary.get("blocked_wait_seconds"),
            blocked_check_count=blocked_summary.get("blocked_check_count"),
            blocked_detail_json=blocked_summary.get("blocked_detail_json"),
            open_issue_count=len(hold_ids),
            active_runtime_hold_ids=hold_ids,
            last_heartbeat_at=device.last_heartbeat_at,
            error_code=device.error_code,
        )

    def _build_monitor_device_node(
        self,
        device: Device,
        *,
        open_command_count: int = 0,
        blocked_outbox_count: int = 0,
        blocked_outbox_summary: dict[str, Any] | None = None,
        active_runtime_hold_ids: list[int] | None = None,
        current_command: RuntimeMonitorCommandSnapshot | None = None,
    ) -> RuntimeMonitorDeviceNode:
        hold_ids = active_runtime_hold_ids or []
        blocked_summary = blocked_outbox_summary or {}
        return RuntimeMonitorDeviceNode(
            id=_require_int_id(device.id, "device.id"),
            device_code=device.device_code,
            device_name=device.device_name,
            device_role=device.device_role,
            role_index=device.role_index,
            upstream_device_id=device.upstream_device_id,
            device_status=_status_str(device.device_status),
            maintenance_mode=device.maintenance_mode,
            current_command_id=device.current_command_id,
            current_command=current_command,
            open_command_count=open_command_count,
            pending_command_count=open_command_count,
            blocked_outbox_count=blocked_outbox_count,
            blocked_reason=blocked_summary.get("blocked_reason"),
            blocked_wait_seconds=blocked_summary.get("blocked_wait_seconds"),
            blocked_check_count=blocked_summary.get("blocked_check_count"),
            open_issue_count=len(hold_ids),
            active_runtime_hold_ids=hold_ids,
            last_heartbeat_at=_api_utc_datetime(device.last_heartbeat_at),
            error_code=device.error_code,
        )

    def _build_device_summary(
        self,
        device: Device,
        workline: WorkLine | None,
        open_command_count: int,
        recent_callback_at: Any,
        *,
        blocked_outbox_count: int = 0,
        blocked_outbox_summary: dict[str, Any] | None = None,
        active_runtime_hold_ids: list[int] | None = None,
    ) -> RuntimeDeviceSummary:
        hold_ids = active_runtime_hold_ids or []
        blocked_summary = blocked_outbox_summary or {}
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
            open_command_count=open_command_count,
            pending_command_count=open_command_count,
            blocked_outbox_count=blocked_outbox_count,
            blocked_reason=blocked_summary.get("blocked_reason"),
            blocked_wait_seconds=blocked_summary.get("blocked_wait_seconds"),
            blocked_check_count=blocked_summary.get("blocked_check_count"),
            blocked_detail_json=blocked_summary.get("blocked_detail_json"),
            open_issue_count=len(hold_ids),
            active_runtime_hold_ids=hold_ids,
            last_heartbeat_at=device.last_heartbeat_at,
            recent_callback_at=recent_callback_at,
            error_code=device.error_code,
        )

    def _build_callback_item(self, item: CallbackLog) -> TraceCallbackLogItem:
        return TraceCallbackLogItem(
            id=_require_int_id(item.id, "callback_log.id"),
            callback_type=item.callback_type,
            subject_code=item.subject_code,
            request_id=item.request_id,
            trace_id=item.trace_id,
            event_id=item.event_id,
            causation_id=item.causation_id,
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
            result=optional_enum_str(item.result),
            retry_count=item.retry_count,
            sent_at=item.sent_at,
            ack_received_at=item.ack_received_at,
            completed_at=item.completed_at,
            ack_code=item.ack_code,
            ack_message=item.ack_message,
            ack_trace_id=item.ack_trace_id,
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
        *,
        inbox: WorklineInbox | None = None,
        latest_device: Device | None,
        action_source: str,
    ) -> RuntimeTraceListItem:
        event_payload = _payload_dict(getattr(inbox, "payload_json", None))
        return RuntimeTraceListItem(
            session_id=_require_int_id(session.id, "session.id"),
            session_code=session.session_code,
            trace_id=session.trace_id,
            request_id=session.last_request_id,
            last_inbox_id=getattr(session, "last_inbox_id", None),
            event_type=_event_type_from_payload(event_payload),
            event_payload=event_payload or None,
            business_key=session.business_key,
            barcode=session.barcode or _session_initial_payload_display_identity(session),
            workline_id=session.workline_id,
            workline_name=workline.line_name if workline is not None else None,
            workline_code=workline.line_code if workline is not None else None,
            device_id=device.id if device is not None else None,
            device_name=device.device_name if device is not None else None,
            device_code=device.device_code if device is not None else None,
            command_code=command.command_code if command is not None else None,
            current_device_id=device.id if device is not None else None,
            current_device_name=device.device_name if device is not None else None,
            current_device_code=device.device_code if device is not None else None,
            current_action=_trace_current_action(command, timeline),
            current_action_source=action_source,
            last_device_id=latest_device.id if latest_device is not None else None,
            last_device_name=latest_device.device_name if latest_device is not None else None,
            last_device_code=latest_device.device_code if latest_device is not None else None,
            status=_status_str(session.status),
            current_wait_type=session.current_wait_type,
            failure_domain=session.failure_domain,
            failure_code=session.failure_code,
            latest_timeline_action=optional_enum_str(timeline.action_type) if timeline is not None else None,
            latest_timeline_status=optional_enum_str(timeline.status) if timeline is not None else None,
            latest_timeline_message=timeline.message if timeline is not None else None,
            started_at=session.started_at,
            last_ingress_at=session.last_ingress_at,
            deadline_at=session.deadline_at,
            is_timed_out=_is_timed_out(session, now),
        )

    async def get_session_path(self, db: Any, session_id: int) -> RuntimeTracePathResponse | None:
        """聚合 Session 粒度的设备路径视图。"""
        from src.app.workline.services.trace_query_service import trace_query_service

        result = await trace_query_service.path_by_session_id(db, session_id)
        if result.session is None:
            return None
        devices = await device_repository.get_by_work_line_id(db, result.session.workline_id)
        return self._build_trace_path(result, devices=devices)

    async def get_trace_path(self, db: Any, trace_id: str) -> RuntimeTracePathResponse | None:
        """聚合 Trace ID 粒度的设备路径视图。"""
        from src.app.workline.services.trace_query_service import trace_query_service

        result = await trace_query_service.path_by_trace_id(db, trace_id)
        if result.session is None and not _trace_path_has_facts(result):
            return None
        devices = (
            await device_repository.get_by_work_line_id(db, result.session.workline_id)
            if result.session is not None
            else []
        )
        return self._build_trace_path(result, devices=devices)

    def _build_trace_path(self, result: Any, *, devices: list[Any] | None = None) -> RuntimeTracePathResponse:
        """从 TraceQueryResult 构建设备路径视图。"""

        session = result.session
        devices_map: dict[int, RuntimeTraceDevicePathNode] = {}
        device_identity_by_id, device_identity_by_code = _build_device_identity_maps(devices or [])

        def _ensure_node(device_id: int | None) -> RuntimeTraceDevicePathNode | None:
            if device_id is None:
                return None
            node = devices_map.get(device_id)
            if node is None:
                identity = device_identity_by_id.get(device_id)
                node = RuntimeTraceDevicePathNode(
                    device_id=device_id,
                    device_code=identity.device_code if identity is not None else None,
                    device_name=identity.device_name if identity is not None else None,
                )
                devices_map[device_id] = node
            return node

        for source in [*result.commands, *result.inboxes]:
            identity = _device_identity_from_source(source)
            if identity is None:
                continue
            existing = device_identity_by_id.get(identity.device_id)
            if existing is None or (not existing.device_name and identity.device_name):
                device_identity_by_id[identity.device_id] = identity
            if identity.device_code:
                device_identity_by_code[identity.device_code] = device_identity_by_id[identity.device_id]

        for cmd in result.commands:
            node = _ensure_node(cmd.device_id)
            if node is not None:
                node.actions.append(
                    RuntimeTraceDeviceAction(
                        kind="command",
                        label=optional_enum_str(cmd.task_type) or cmd.command_code or "COMMAND",
                        status=optional_enum_str(cmd.status),
                        timestamp=cmd.completed_at or cmd.sent_at,
                        message=f"{cmd.command_code} · {optional_enum_str(cmd.result) or ''}",
                    )
                )

        for inbox in result.inboxes:
            node = _ensure_node(inbox.device_id)
            if node is not None:
                node.actions.append(
                    RuntimeTraceDeviceAction(
                        kind="inbox",
                        label=f"INBOX {optional_enum_str(inbox.kind) or ''}",
                        status=optional_enum_str(inbox.status),
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
                        reason=f"等待设备响应 {optional_enum_str(cmd.task_type) or cmd.command_code}",
                        detail=f"command #{cmd.id} · {optional_enum_str(cmd.status)} · awaiting_command_id={session.awaiting_command_id}",
                    )
            if blocking_device_id is None and session.failure_domain:
                blocking_reason = RuntimeBlockingReason(
                    reason=session.failure_domain,
                    detail=session.failure_message,
                )

        if blocking_device_id is not None and blocking_device_id in devices_map:
            devices_map[blocking_device_id].is_current = True

        trace_id = result.trace.trace_id if result.trace else None

        timeline_groups = self._build_trace_timeline_groups(
            result.timelines,
            commands_by_id={cmd.id: cmd for cmd in result.commands if cmd.id is not None},
            inboxes_by_id={inbox.id: inbox for inbox in result.inboxes if inbox.id is not None},
            device_identity_by_id=device_identity_by_id,
            device_identity_by_code=device_identity_by_code,
            blocking_device_id=blocking_device_id,
        )
        session_items = [
            item
            for item in (
                build_trace_session_item(session_item, include_context_json=False)
                for session_item in getattr(result, "sessions", [])
            )
            if item is not None
        ]

        return RuntimeTracePathResponse(
            workline_id=session.workline_id if session else None,
            session_id=session.id if session else None,
            trace_id=trace_id,
            diagnosis_verdict=diagnosis_verdict_builder.build(result),
            sessions=session_items,
            resource_view=build_trace_resource_view(result),
            devices=list(devices_map.values()),
            timeline_groups=timeline_groups,
            current_blocking_device_id=blocking_device_id,
            blocking_reason=blocking_reason,
        )

    def _build_trace_timeline_groups(
        self,
        timelines: list[Any],
        *,
        commands_by_id: dict[int, Any],
        inboxes_by_id: dict[int, Any],
        device_identity_by_id: dict[int, _DeviceIdentity],
        device_identity_by_code: dict[str, _DeviceIdentity],
        blocking_device_id: int | None,
    ) -> list[RuntimeTraceTimelineGroup]:
        groups: dict[str, RuntimeTraceTimelineGroup] = {}

        for timeline in sorted(
            timelines,
            key=lambda item: (
                getattr(item, "seq_no", 0),
                getattr(item, "occurred_at", None) or "",
                getattr(item, "id", 0),
            ),
        ):
            event = build_trace_timeline_item(timeline)
            group = self._resolve_timeline_group(
                timeline,
                commands_by_id=commands_by_id,
                inboxes_by_id=inboxes_by_id,
                device_identity_by_id=device_identity_by_id,
                device_identity_by_code=device_identity_by_code,
                blocking_device_id=blocking_device_id,
            )
            if group.group_key not in groups:
                groups[group.group_key] = group
            groups[group.group_key].events.append(event)

        for group in groups.values():
            group.events.sort(key=lambda item: (item.seq_no, item.occurred_at, item.id))

        return sorted(
            groups.values(),
            key=lambda group: (
                group.events[0].seq_no if group.events else 0,
                group.events[0].occurred_at if group.events else "",
                group.group_key,
            ),
        )

    def _resolve_timeline_group(
        self,
        timeline: Any,
        *,
        commands_by_id: dict[int, Any],
        inboxes_by_id: dict[int, Any],
        device_identity_by_id: dict[int, _DeviceIdentity],
        device_identity_by_code: dict[str, _DeviceIdentity],
        blocking_device_id: int | None,
    ) -> RuntimeTraceTimelineGroup:
        related_command_id = getattr(timeline, "related_command_id", None)
        command = commands_by_id.get(related_command_id) if isinstance(related_command_id, int) else None
        if command is not None:
            return self._device_timeline_group(
                command.device_id,
                source=command,
                device_identity_by_id=device_identity_by_id,
                blocking_device_id=blocking_device_id,
            )

        related_inbox_id = getattr(timeline, "related_inbox_id", None)
        inbox = inboxes_by_id.get(related_inbox_id) if isinstance(related_inbox_id, int) else None
        if inbox is not None:
            return self._device_timeline_group(
                inbox.device_id,
                source=inbox,
                device_identity_by_id=device_identity_by_id,
                blocking_device_id=blocking_device_id,
            )

        action_type = _status_str(getattr(timeline, "action_type", None))
        actor_type = _status_str(getattr(timeline, "actor_type", None))
        actor_code = getattr(timeline, "actor_code", None)
        payload = _payload_dict(getattr(timeline, "payload_json", None))
        sandbox_trigger = _first_payload_str(payload, ("trigger", "source", "submitted_by"))

        if actor_type == "MANUAL_OPERATOR" or (sandbox_trigger and "sandbox" in sandbox_trigger.lower()):
            code = actor_code or sandbox_trigger or "sandbox"
            return RuntimeTraceTimelineGroup(
                group_key=f"operator:{code}",
                group_type="operator",
                display_name="Sandbox 操作员" if "sandbox" in code.lower() else f"操作员 {code}",
            )

        if actor_type == "EXTERNAL_SYSTEM" or action_type.startswith("EXTERNAL_CALL_"):
            code = actor_code or "external"
            return RuntimeTraceTimelineGroup(
                group_key=f"external:{code}",
                group_type="external",
                display_name=f"外部系统 {code}",
            )

        if action_type in _ORCHESTRATOR_TIMELINE_ACTIONS or actor_type in {"ORCHESTRATOR", "PLUGIN"}:
            return RuntimeTraceTimelineGroup(
                group_key="orchestrator:session",
                group_type="orchestrator",
                display_name="编排 / Session",
            )

        if actor_type == "DEVICE" and actor_code:
            return self._device_code_timeline_group(
                actor_code,
                device_identity_by_code=device_identity_by_code,
                blocking_device_id=blocking_device_id,
            )

        device_code = _first_payload_str(
            payload,
            ("device_code", "target_code", "source_device_code", "source_device", "target_device_code"),
        )
        if device_code:
            return self._device_code_timeline_group(
                device_code,
                device_identity_by_code=device_identity_by_code,
                blocking_device_id=blocking_device_id,
            )

        return RuntimeTraceTimelineGroup(
            group_key="unknown:timeline",
            group_type="unknown",
            display_name="未归属事件",
        )

    def _device_timeline_group(
        self,
        device_id: int | None,
        *,
        source: Any,
        device_identity_by_id: dict[int, _DeviceIdentity],
        blocking_device_id: int | None,
    ) -> RuntimeTraceTimelineGroup:
        if device_id is None:
            return RuntimeTraceTimelineGroup(
                group_key="unknown:timeline",
                group_type="unknown",
                display_name="未归属事件",
            )
        identity = device_identity_by_id.get(device_id) or _device_identity_from_source(source)
        device_code = identity.device_code if identity is not None else None
        device_name = identity.device_name if identity is not None else None
        return RuntimeTraceTimelineGroup(
            group_key=f"device:{device_id}",
            group_type="device",
            display_name=device_name or device_code or f"设备 #{device_id}",
            device_id=device_id,
            device_code=device_code,
            is_current=device_id == blocking_device_id,
            is_blocked=device_id == blocking_device_id,
        )

    def _device_code_timeline_group(
        self,
        device_code: str,
        *,
        device_identity_by_code: dict[str, _DeviceIdentity],
        blocking_device_id: int | None,
    ) -> RuntimeTraceTimelineGroup:
        identity = device_identity_by_code.get(device_code)
        if identity is not None:
            return RuntimeTraceTimelineGroup(
                group_key=f"device:{identity.device_id}",
                group_type="device",
                display_name=identity.device_name or identity.device_code or f"设备 #{identity.device_id}",
                device_id=identity.device_id,
                device_code=identity.device_code,
                is_current=identity.device_id == blocking_device_id,
                is_blocked=identity.device_id == blocking_device_id,
            )
        return RuntimeTraceTimelineGroup(
            group_key=f"device-code:{device_code}",
            group_type="device",
            display_name=device_code,
            device_code=device_code,
            is_current=False,
            is_blocked=False,
        )


def _highest_priority_state(values: list[T], priority: list[T], fallback: T) -> T:
    for item in priority:
        if item in values:
            return item
    return fallback


def _blocked_outbox_is_earlier(candidate: Any, current: Any) -> bool:
    candidate_created_at = getattr(candidate, "created_at", None)
    current_created_at = getattr(current, "created_at", None)
    if not isinstance(candidate_created_at, datetime):
        return False
    if not isinstance(current_created_at, datetime):
        return True
    return candidate_created_at < current_created_at


def _runtime_resource_evidence_items_from_active_snapshot(
    snapshot: dict[str, Any],
    *,
    evidence_kind: RuntimeResourceEvidenceKind,
    session: WorklineSession | None = None,
    context: dict[str, Any] | None = None,
    fallback_position_code: str | None = None,
) -> list[RuntimeResourceEvidenceItem]:
    items = _runtime_resource_evidence_items_from_payload(
        snapshot,
        evidence_kind=evidence_kind,
        session=session,
        context=context,
        fallback_position_code=fallback_position_code,
    )
    rack_code = _first_text(snapshot, ("rack_code", "rack_id"))
    for cell_payload in _runtime_active_snapshot_cell_payloads(snapshot, rack_code=rack_code):
        items.extend(
            _runtime_resource_evidence_items_from_payload(
                cell_payload,
                evidence_kind=evidence_kind,
                session=session,
                context=context,
                fallback_position_code=fallback_position_code,
            )
        )
    return items


def _runtime_active_snapshot_parent_metadata(snapshot: dict[str, Any], *, rack_code: str | None) -> dict[str, Any]:
    parent_keys = (
        "station",
        *_RUNTIME_STATION_CODE_KEYS,
        *_RUNTIME_POSITION_CODE_KEYS,
        "rack_code",
        "rack_id",
    )
    metadata = {key: snapshot[key] for key in parent_keys if key in snapshot}
    if rack_code is not None:
        metadata.setdefault("rack_code", rack_code)
    return _runtime_normalized_station_position_defaults(metadata)


def _runtime_normalized_station_position_defaults(defaults: dict[str, Any]) -> dict[str, Any]:
    result = dict(defaults)
    station = ensure_dict(result.get("station"))
    if _first_text(result, _RUNTIME_STATION_CODE_KEYS) is None:
        station_code = _first_text(station, _RUNTIME_STATION_NESTED_CODE_KEYS)
        if station_code is not None:
            result["station_code"] = station_code
    if _first_text(result, _RUNTIME_POSITION_CODE_KEYS) is None:
        position_code = _first_text(station, ("position_code",))
        if position_code is not None:
            result["position_code"] = position_code
    return result


def _runtime_active_snapshot_cell_payloads(snapshot: dict[str, Any], *, rack_code: str | None) -> list[dict[str, Any]]:
    parent_metadata = _runtime_active_snapshot_parent_metadata(snapshot, rack_code=rack_code)
    payloads = _runtime_active_snapshot_flat_cell_payloads(snapshot, parent_metadata=parent_metadata)
    payloads.extend(_runtime_active_snapshot_nested_bin_cell_payloads(snapshot, parent_metadata=parent_metadata))
    return payloads


def _runtime_active_snapshot_flat_cell_payloads(
    snapshot: dict[str, Any], *, parent_metadata: dict[str, Any]
) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for key in ("cells", "bin_cells", "cell_snapshots"):
        if key not in snapshot:
            continue
        cells = snapshot[key]
        if not isinstance(cells, list):
            continue
        for cell in cells:
            if not isinstance(cell, dict):
                continue
            payloads.append(_runtime_payload_with_metadata_defaults(cell, parent_metadata))
    return payloads


def _runtime_active_snapshot_nested_bin_cell_payloads(
    snapshot: dict[str, Any],
    *,
    parent_metadata: dict[str, Any],
) -> list[dict[str, Any]]:
    bins = snapshot.get("bins")
    if not isinstance(bins, list):
        return []

    payloads: list[dict[str, Any]] = []
    for bin_payload in bins:
        if not isinstance(bin_payload, dict):
            continue
        bin_metadata = {key: value for key, value in bin_payload.items() if key != "cells"}
        bin_metadata = _runtime_payload_with_metadata_defaults(bin_metadata, parent_metadata)
        bin_metadata = _runtime_normalized_station_position_defaults(bin_metadata)
        payloads.append(bin_metadata)
        cells = bin_payload.get("cells")
        if not isinstance(cells, list):
            continue
        for cell in cells:
            if not isinstance(cell, dict):
                continue
            payloads.append(_runtime_payload_with_metadata_defaults(cell, bin_metadata))
    return payloads


def _runtime_payload_with_metadata_defaults(
    payload: dict[str, Any],
    defaults: dict[str, Any],
) -> dict[str, Any]:
    result = dict(payload)
    metadata_groups = (
        _RUNTIME_STATION_METADATA_GROUP,
        _RUNTIME_POSITION_CODE_KEYS,
        ("rack_code", "rack_id"),
    )
    grouped_keys = {key for group in metadata_groups for key in group}
    defaults = _runtime_normalized_station_position_defaults(defaults)

    for group in metadata_groups:
        if _runtime_metadata_default_group_has_value(payload, group):
            continue
        for key in group:
            if key in defaults:
                _runtime_set_metadata_default_value(result, key, defaults[key])

    for key, value in defaults.items():
        if key not in grouped_keys:
            _runtime_set_metadata_default_value(result, key, value)
    return result


def _runtime_metadata_default_group_has_value(payload: dict[str, Any], group: tuple[str, ...]) -> bool:
    if group == _RUNTIME_STATION_METADATA_GROUP:
        station = ensure_dict(payload.get("station"))
        return (
            _first_text(payload, _RUNTIME_STATION_CODE_KEYS) is not None
            or _first_text(station, _RUNTIME_STATION_NESTED_CODE_KEYS) is not None
        )
    if group == _RUNTIME_POSITION_CODE_KEYS:
        station = ensure_dict(payload.get("station"))
        return _first_text(payload, group) is not None or _first_text(station, ("position_code",)) is not None
    return _first_text(payload, group) is not None


def _runtime_set_metadata_default_value(payload: dict[str, Any], key: str, value: Any) -> None:
    current = payload.get(key)
    if isinstance(current, dict):
        if current:
            return
    elif _non_empty_text(current) is not None:
        return
    payload[key] = value


def _runtime_resource_evidence_items_from_payload(
    payload: dict[str, Any],
    *,
    evidence_kind: RuntimeResourceEvidenceKind,
    session: WorklineSession | None = None,
    context: dict[str, Any] | None = None,
    fallback_position_code: str | None = None,
) -> list[RuntimeResourceEvidenceItem]:
    metadata = _runtime_resource_evidence_metadata(
        payload,
        session=session,
        context=context,
        fallback_position_code=fallback_position_code,
    )
    items: list[RuntimeResourceEvidenceItem] = []

    direct_kind = _runtime_resource_kind(payload.get("resource_kind") or payload.get("resource_type"))
    direct_code = _first_text(payload, ("resource_code", "resource_id"))
    if direct_code is not None:
        item = _make_runtime_resource_evidence_item(
            resource_kind=direct_kind,
            resource_code=direct_code,
            display_label=_first_text(payload, ("display_label",)),
            evidence_kind=evidence_kind,
            metadata=metadata,
        )
        if item is not None:
            items.append(item)

    for resource_kind, code in (
        (RuntimeResourceKind.RACK, metadata["rack_code"]),
        (RuntimeResourceKind.SLOT, metadata["slot_code"]),
        (RuntimeResourceKind.BIN, metadata["bin_code"]),
        (RuntimeResourceKind.CELL, _runtime_cell_code(payload)),
        (RuntimeResourceKind.PKG, metadata["pkg_code"]),
        (RuntimeResourceKind.PART_SN, metadata["part_sn"]),
        (RuntimeResourceKind.MAGAZINE, _first_text(payload, ("magazine_code", "magazine_id"))),
    ):
        item = _make_runtime_resource_evidence_item(
            resource_kind=resource_kind,
            resource_code=code,
            display_label=None,
            evidence_kind=evidence_kind,
            metadata=metadata,
        )
        if item is not None:
            items.append(item)

    for reel_payload in _runtime_resource_evidence_reel_payloads(payload):
        reel_metadata = _runtime_resource_evidence_metadata(
            _runtime_payload_with_metadata_defaults(reel_payload, payload),
            session=session,
            context=context,
            fallback_position_code=fallback_position_code,
        )
        item = _make_runtime_resource_evidence_item(
            resource_kind=RuntimeResourceKind.PKG,
            resource_code=reel_metadata["pkg_code"],
            display_label=_first_text(reel_payload, ("display_label",)),
            evidence_kind=evidence_kind,
            metadata=reel_metadata,
        )
        if item is not None:
            items.append(item)

    return items


def _runtime_resource_evidence_reel_payloads(payload: dict[str, Any]) -> list[dict[str, Any]]:
    reels = payload.get("reels")
    if not isinstance(reels, list):
        return []
    return [dict(reel) for reel in reels if isinstance(reel, dict)]


def _runtime_resource_evidence_metadata(
    payload: dict[str, Any],
    *,
    session: WorklineSession | None,
    context: dict[str, Any] | None,
    fallback_position_code: str | None,
) -> dict[str, Any]:
    context_payload = context or {}
    station = ensure_dict(payload.get("station")) or ensure_dict(context_payload.get("station"))
    return {
        "station_code": _first_text(payload, _RUNTIME_STATION_CODE_KEYS)
        or _first_text(station, _RUNTIME_STATION_NESTED_CODE_KEYS),
        "position_code": _first_text(
            payload,
            _RUNTIME_POSITION_CODE_KEYS,
        )
        or _first_text(
            context_payload,
            _RUNTIME_POSITION_CODE_KEYS,
        )
        or _first_text(station, ("position_code",))
        or fallback_position_code,
        "rack_code": _first_text(payload, ("rack_code", "rack_id")),
        "bin_code": _first_text(payload, ("bin_code", "bin_id")),
        "slot_code": _first_text(payload, ("rack_slot_code", "slot_code", "rack_slot_location_code")),
        "cell_code": _runtime_cell_code(payload),
        "pkg_code": _first_text(payload, ("pkg_code", "package_code", "PkgID", "pkg_id", "material_identity_key")),
        "part_sn": _first_text(payload, ("part_sn", "part_serial_no", "serial_no")),
        "material_code": _first_text(payload, ("material_code", "material_id", "MaterialCode", "MaterialID")),
        "date_code": _first_text(payload, ("date_code", "DateCode")),
        "lot_code": _first_text(payload, ("lot_code", "LotCode")),
        "reel_count": _int_or_none(_first_value(payload, ("reel_count", "ReelCount"))),
        "reel_code": _first_text(payload, ("reel_code", "reel_id", "ReelCode", "ReelID")),
        "position_index": _int_or_none(
            _first_value(payload, ("position_index", "stack_index", "cell_stack_position", "CellStackPosition"))
        ),
        "source_session_id": _int_or_none(getattr(session, "id", None)),
        "source_trace_id": _first_text(payload, ("source_trace_id", "trace_id"))
        or _first_text(context_payload, ("source_trace_id", "trace_id"))
        or _non_empty_text(getattr(session, "trace_id", None)),
        "occurred_at": _datetime_or_none(
            payload.get("occurred_at")
            or payload.get("received_at")
            or payload.get("created_at")
            or getattr(session, "last_ingress_at", None)
            or getattr(session, "started_at", None)
            or getattr(session, "created_at", None)
        ),
    }


def _runtime_resource_evidence_kind_from_payload(payload: dict[str, Any]) -> RuntimeResourceEvidenceKind:
    explicit = _first_text(payload, ("resource_evidence_kind", "evidence_kind"))
    if explicit is not None:
        try:
            return RuntimeResourceEvidenceKind(explicit.upper())
        except ValueError:
            return RuntimeResourceEvidenceKind.UNKNOWN

    source_system = str(payload.get("source_system") or "").upper()
    callback_type = str(payload.get("callback_type") or "").upper()
    if source_system in {"WMS", "RCS", "WMS_RCS"} or callback_type.startswith("WMS_"):
        return RuntimeResourceEvidenceKind.WMS_CALLBACK_EVIDENCE
    return RuntimeResourceEvidenceKind.GENERIC_EVIDENCE


def _runtime_resource_evidence_rack_kind(payload: dict[str, Any]) -> str | None:
    rack_kind = _first_text(payload, ("rack_kind", "rack_type"))
    if rack_kind is not None:
        return rack_kind.upper()
    active_bin_rack = ensure_dict(payload.get("active_bin_rack"))
    nested_rack_kind = _first_text(active_bin_rack, ("rack_kind", "rack_type"))
    if nested_rack_kind is not None:
        return nested_rack_kind.upper()
    return None


def _runtime_resource_kind(value: Any) -> RuntimeResourceKind:
    normalized = str(getattr(value, "value", value or "")).strip().upper()
    mapping = {
        "RACK": RuntimeResourceKind.RACK,
        "BIN": RuntimeResourceKind.BIN,
        "PKG": RuntimeResourceKind.PKG,
        "PACKAGE": RuntimeResourceKind.PKG,
        "MATERIAL": RuntimeResourceKind.PKG,
        "SLOT": RuntimeResourceKind.SLOT,
        "RACK_SLOT": RuntimeResourceKind.SLOT,
        "CELL": RuntimeResourceKind.CELL,
        "BIN_CELL": RuntimeResourceKind.CELL,
        "MAGAZINE": RuntimeResourceKind.MAGAZINE,
        "PART_SN": RuntimeResourceKind.PART_SN,
        "PART_SERIAL_NO": RuntimeResourceKind.PART_SN,
    }
    return mapping.get(normalized, RuntimeResourceKind.UNKNOWN)


def _runtime_cell_code(payload: dict[str, Any]) -> str | None:
    return _first_text(payload, ("bin_cell_code", "cell_code", "bin_cell_location"))


def _make_runtime_resource_evidence_item(
    *,
    resource_kind: RuntimeResourceKind,
    resource_code: str | None,
    display_label: str | None,
    evidence_kind: RuntimeResourceEvidenceKind,
    metadata: dict[str, Any],
) -> RuntimeResourceEvidenceItem | None:
    normalized_code = _non_empty_text(resource_code)
    if normalized_code is None:
        return None
    normalized_display_label = _non_empty_text(display_label)
    return RuntimeResourceEvidenceItem(
        resource_kind=resource_kind,
        resource_code=normalized_code,
        display_label=normalized_display_label or f"{resource_kind.value} {normalized_code}",
        evidence_kind=evidence_kind,
        station_code=metadata["station_code"],
        position_code=metadata["position_code"],
        rack_code=metadata["rack_code"],
        bin_code=metadata["bin_code"],
        slot_code=metadata["slot_code"],
        cell_code=metadata["cell_code"],
        pkg_code=metadata["pkg_code"],
        part_sn=metadata["part_sn"],
        material_code=metadata["material_code"],
        date_code=metadata["date_code"],
        lot_code=metadata["lot_code"],
        reel_count=metadata["reel_count"],
        reel_code=metadata["reel_code"],
        position_index=metadata["position_index"],
        source_session_id=metadata["source_session_id"],
        source_trace_id=metadata["source_trace_id"],
        occurred_at=metadata["occurred_at"],
    )


def _dedupe_runtime_resource_evidence_items(
    items: list[RuntimeResourceEvidenceItem],
) -> list[RuntimeResourceEvidenceItem]:
    deduped: dict[
        tuple[str, str, str, int | None, str | None, str | None, str | None, str | None],
        RuntimeResourceEvidenceItem,
    ] = {}
    for item in items:
        key = _runtime_resource_evidence_item_key(item)
        if key not in deduped:
            deduped[key] = item
    return sorted(deduped.values(), key=_runtime_resource_evidence_item_sort_key)


def _runtime_resource_evidence_item_key(
    item: RuntimeResourceEvidenceItem,
) -> tuple[str, str, str, int | None, str | None, str | None, str | None, str | None]:
    cell_rack_code = item.rack_code if item.resource_kind == RuntimeResourceKind.CELL else None
    cell_bin_code = item.bin_code if item.resource_kind == RuntimeResourceKind.CELL else None
    return (
        item.resource_kind.value,
        item.resource_code,
        item.evidence_kind.value,
        item.source_session_id,
        item.source_trace_id,
        item.position_code,
        cell_rack_code,
        cell_bin_code,
    )


def _runtime_resource_evidence_item_sort_key(item: RuntimeResourceEvidenceItem) -> tuple[int, int, str, str, str, str]:
    return (
        _RUNTIME_RESOURCE_EVIDENCE_KIND_PRIORITY.get(item.evidence_kind, 99),
        _RUNTIME_RESOURCE_KIND_PRIORITY.get(item.resource_kind, 99),
        item.position_code or "",
        item.resource_code,
        item.rack_code or "",
        item.bin_code or "",
    )


def _first_text(payload: dict[str, Any], aliases: tuple[str, ...]) -> str | None:
    for alias in aliases:
        value = _non_empty_text(payload.get(alias))
        if value is not None:
            return value
    return None


def _first_value(payload: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    for alias in aliases:
        if alias in payload and payload[alias] is not None:
            return payload[alias]
    return None


def _non_empty_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(getattr(value, "value", value)).strip()
    return text or None


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except ValueError:
        return None


def _datetime_or_none(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return None
        try:
            return datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _runtime_resource_evidence_payloads(context: dict[str, Any]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for key in ("resource_evidence", "resource_fact", "last_resource_fact", "rack_operation"):
        value = context.get(key)
        if isinstance(value, dict):
            payloads.append(value)
    events = context.get("resource_state_events")
    if isinstance(events, list):
        payloads.extend(item for item in events if isinstance(item, dict))
    return payloads


def _trace_path_has_facts(result: Any) -> bool:
    return any(
        getattr(result, attr, None)
        for attr in (
            "callback_logs",
            "sessions",
            "commands",
            "outboxes",
            "inboxes",
            "timelines",
            "diagnostics",
        )
    )


runtime_query_service = RuntimeQueryService()


__all__ = ["RuntimeQueryService", "runtime_query_service"]
