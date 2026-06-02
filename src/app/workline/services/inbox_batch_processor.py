import asyncio
import hashlib
import uuid
from collections.abc import Mapping
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypedDict, cast

from loguru import logger
from sqlalchemy import text

from src.app.workline.constants import (
    EXTERNAL_HTTP_INBOX_KIND,
    INBOX_BUCKET_LOCK_TTL_SECONDS,
    INBOX_PROCESS_TIMEOUT_SECONDS,
    WORKLINE_INBOX_BATCH_MAX_PARALLELISM,
    WORKLINE_INBOX_BATCH_PARALLELISM,
    WORKLINE_INBOX_PROCESSING_STALE_SECONDS,
)
from src.app.workline.diagnostic_support import _record_diagnostic
from src.app.workline.repositories.inbox_repository import WorklineInboxClaim
from src.app.workline.services.safety_service import WorkLineSafetyBlocked
from src.core.task_queue_gateway import task_queue_gateway
from src.database.redis_client import get_redis
from src.utils.timezone import timezone
from src.utils.value_normalization import (
    canonical_event_type,
    optional_int,
    optional_str,
    resolve_entity_id,
    resolve_required_pk,
    string_value,
)
from src.workline_plugin_registry import get_workline_plugin_definition, parse_workline_six_in_one
from src.workline_runtime.diagnostics import ErrorCode, ErrorDomain, ProblemClass
from src.workline_runtime.diagnostics.failure_mapper import map_failure_to_diagnostic
from src.workline_runtime.lock import RedisDistributedLock
from src.workline_runtime.orchestrator import OrchestratorResult, OrchestratorService
from src.workline_runtime.runtime_events import RESERVED_RUNTIME_EVENTS
from src.workline_runtime.runtime_intent import RuntimeIntentKind
from src.workline_runtime.services import WorklineRuntimeServices, build_workline_runtime_services
from src.workline_runtime.session_resolver import SessionResolveError
from src.workline_runtime.utils import payload_dict

if TYPE_CHECKING:
    from src.workline_runtime.utils import JsonDict


class ProcessResult(TypedDict):
    """处理结果"""

    processed: int
    success: int
    failed: int
    skipped: int


class LoadedEntities(TypedDict):
    """加载的关联实体"""

    session: Any | None
    workline: Any | None
    device: Any | None
    command: Any | None
    devices_by_role: dict[str, list[Any]]
    services: WorklineRuntimeServices
    safety_checked: bool


class InboxBucketProcessingError(Exception):
    """bucket 调度层异常，携带尚未进入终态更新的 claim。"""

    def __init__(self, bucket_key: str, claims: list[WorklineInboxClaim], cause: Exception) -> None:
        super().__init__(str(cause))
        self.bucket_key = bucket_key
        self.claims = claims
        self.cause = cause


@dataclass(frozen=True, slots=True)
class _InboxDiagnosticSnapshot:
    """Inbox 诊断快照，避免 rollback 后访问已过期 ORM 字段。"""

    id: int | None
    kind: Any | None
    source_message_id: str | None
    trace_id: str | None
    event_id: str | None
    causation_id: str | None
    workline_id: int | None
    session_id: int | None
    device_id: int | None
    command_id: int | None
    payload_json: dict[str, Any]


_ENTRY_DEVICE_EVENT_TYPES = frozenset({"SCAN_COMPLETED"})
_TERMINAL_SESSION_STATUSES = frozenset({"COMPLETED", "FAILED", "CANCELLED"})
_BUSY_SESSION_STATUSES = frozenset({"WAITING_DEVICE_RESULT", "WAITING_EXTERNAL", "MANUAL_HOLD"})
_TERMINAL_COMMAND_STATUSES = frozenset({"COMPLETED", "FAILED", "TIMEOUT", "CANCELLED"})


def _scan_completed_has_any_barcode_payload(payload: dict[str, Any]) -> bool:
    """SCAN_COMPLETED 的最小通用 gate。
    这里只判断 payload.data 中是否出现过任何扫码事实，作为入站后的 malformed
    payload 拦截，不把错误归因绑定到 workline / plugin registry / parser 上。
    真正的协议映射和 SixInOne 解析仍由各插件/编排路径负责。
    注意：白皮书已禁止拍平 payload，只接受嵌套 data 结构。
    """
    data_field = payload.get("data")
    data = cast("dict[str, Any]", data_field) if isinstance(data_field, dict) else {}
    fields = ("HHPN", "MfrPN", "Qty", "DateCode", "LotCode", "PkgID", "ProductNo", "PONumber", "barcode")
    return any(isinstance(data.get(field), str) and data.get(field) for field in fields)


def _enqueue_outbox_dispatch() -> None:
    task_queue_gateway.enqueue_outbox(limit=50)


def _result_requires_outbox_dispatch(result: OrchestratorResult) -> bool:
    for intent in result.intents or []:
        if intent.kind == RuntimeIntentKind.COMMAND:
            return True
        if intent.kind == RuntimeIntentKind.EXTERNAL_REQUEST:
            return True
        if intent.kind == RuntimeIntentKind.RACK_OPERATION_REQUEST:
            return True
        if intent.kind == RuntimeIntentKind.CONTINUE_NEXT and intent.action:
            return True
    return False


def _snapshot_inbox_for_diagnostic(inbox: Any) -> _InboxDiagnosticSnapshot:
    """在事务回滚前提取诊断需要的 Inbox 字段。"""
    return _InboxDiagnosticSnapshot(
        id=resolve_entity_id(inbox),
        kind=getattr(inbox, "kind", None),
        source_message_id=optional_str(getattr(inbox, "source_message_id", None)),
        trace_id=optional_str(getattr(inbox, "trace_id", None)),
        event_id=optional_str(getattr(inbox, "event_id", None)),
        causation_id=optional_str(getattr(inbox, "causation_id", None)),
        workline_id=optional_int(getattr(inbox, "workline_id", None)),
        session_id=optional_int(getattr(inbox, "session_id", None)),
        device_id=optional_int(getattr(inbox, "device_id", None)),
        command_id=optional_int(getattr(inbox, "command_id", None)),
        payload_json=dict(payload_dict(getattr(inbox, "payload_json", None))),
    )


def _should_resolve_session(inbox: Any) -> bool:
    """仅在具备足够归属信息时才触发 SessionResolver。"""
    payload = payload_dict(getattr(inbox, "payload_json", None))
    kind = getattr(getattr(inbox, "kind", None), "value", getattr(inbox, "kind", None))
    if canonical_event_type(payload) in RESERVED_RUNTIME_EVENTS:
        return False
    if kind == "DEVICE_EVENT":
        return bool(getattr(inbox, "device_id", None) or payload.get("device_code") or payload.get("business_key"))
    if kind == "COMMAND_RESULT":
        return bool(getattr(inbox, "command_id", None) or payload.get("command_code"))
    if kind == EXTERNAL_HTTP_INBOX_KIND:
        return bool(getattr(inbox, "trace_id", None))
    if kind in {"TIMER_TIMEOUT", "MANUAL_HOLD", "MANUAL_RESUME", "MANUAL_CANCEL", "REPLAY_REQUEST"}:
        return isinstance(getattr(inbox, "session_id", None), int)
    return False


def _kind_value(entity: Any) -> str | None:
    value = getattr(getattr(entity, "kind", None), "value", getattr(entity, "kind", None))
    return value if isinstance(value, str) and value else None


def _session_status_value(session: Any) -> str | None:
    value = getattr(getattr(session, "status", None), "value", getattr(session, "status", None))
    return value if isinstance(value, str) and value else None


def _command_status_value(command: Any) -> str | None:
    value = getattr(getattr(command, "status", None), "value", getattr(command, "status", None))
    return value if isinstance(value, str) else None


def _command_code_value(command: Any, payload: dict[str, Any]) -> str | None:
    command_code = getattr(command, "command_code", None)
    if isinstance(command_code, str) and command_code:
        return command_code
    payload_command_code = payload.get("command_code")
    return payload_command_code if isinstance(payload_command_code, str) and payload_command_code else None


def _entry_event_types_for_workline(workline: Any | None) -> frozenset[str]:
    """从插件 manifest 获取入口事件类型，缺省时保留当前 SMT 入口。"""
    plugin_key = string_value(getattr(workline, "plugin_key", None)) if workline is not None else ""
    definition = get_workline_plugin_definition(plugin_key)
    if definition is None:
        return _ENTRY_DEVICE_EVENT_TYPES
    event_source_roles = getattr(definition.manifest, "event_source_roles", None)
    if not isinstance(event_source_roles, Mapping):
        return _ENTRY_DEVICE_EVENT_TYPES
    event_types = frozenset(
        event_type for event_type in event_source_roles if isinstance(event_type, str) and event_type
    )
    return event_types or _ENTRY_DEVICE_EVENT_TYPES


def _is_payload_invalid_entry_replay(
    *,
    payload: dict[str, Any],
    session: Any,
) -> bool:
    """允许 payload 校验失败后的人工 replay 重新进入插件处理。"""
    replay_of_event_id = payload.get("replay_of_event_id")
    if not isinstance(replay_of_event_id, str) or not replay_of_event_id:
        return False
    if _session_status_value(session) != "MANUAL_HOLD":
        return False
    if string_value(getattr(session, "failure_code", None)) != "PAYLOAD_INVALID":
        return False
    if getattr(session, "awaiting_command_id", None) is not None:
        return False
    return not bool(string_value(getattr(session, "current_wait_type", None)))


def _is_duplicate_entry_event_for_session(
    *,
    inbox: Any,
    payload: dict[str, Any],
    session: Any | None,
    workline: Any | None,
) -> bool:
    """识别同一处理周期内重复/迟到的入口事件。
    入口事件的业务语义是“为一个物料处理周期建因”。同一 session 一旦离开
    初始态，后续相同入口事件只应作为证据归档，不能进入普通失败重试；否则
    retry 到期后可能在已完成 session 上重放整条命令链。
    """
    if session is None:
        return False
    if _kind_value(inbox) != "DEVICE_EVENT":
        return False
    if canonical_event_type(payload) not in _entry_event_types_for_workline(workline):
        return False
    if _is_payload_invalid_entry_replay(payload=payload, session=session):
        return False
    status = _session_status_value(session)
    if status in _TERMINAL_SESSION_STATUSES or status in _BUSY_SESSION_STATUSES:
        return True
    if getattr(session, "awaiting_command_id", None) is not None:
        return True
    current_wait_type = string_value(getattr(session, "current_wait_type", None))
    return bool(current_wait_type)


def _is_current_wait_command_result(*, session: Any, command: Any, payload: dict[str, Any]) -> bool:
    """判断 COMMAND_RESULT 是否仍对应 session 当前声明的等待锚点。"""
    _ = payload
    command_id = resolve_entity_id(command)
    awaiting_command_id = optional_int(getattr(session, "awaiting_command_id", None))
    return command_id is not None and awaiting_command_id == command_id


def _is_late_or_duplicate_command_result_for_session(
    *,
    inbox: Any,
    payload: dict[str, Any],
    session: Any | None,
    command: Any | None,
) -> bool:
    """识别已消费过或迟到的 COMMAND_RESULT。
    callback 服务会先把 DeviceCommand 更新成终态，再写 COMMAND_RESULT inbox。
    因此“命令已终态”本身不能说明 inbox 是重复；真正的单一事实来源是
    session 当前等待锚点。只有当结果不再匹配当前等待命令，或 session 已经
    终态时，才把它作为历史证据归档。
    """
    if session is None or command is None:
        return False
    if _kind_value(inbox) != "COMMAND_RESULT":
        return False
    command_status = _command_status_value(command)
    if command_status not in _TERMINAL_COMMAND_STATUSES:
        return False
    if _session_status_value(session) in _TERMINAL_SESSION_STATUSES:
        return True
    return not _is_current_wait_command_result(session=session, command=command, payload=payload)


def _session_context(session: Any) -> dict[str, Any]:
    raw_context = getattr(session, "context_json", None)
    if isinstance(raw_context, dict):
        return dict(cast("JsonDict", raw_context))
    return {}


def _normalized_entry_material_evidence(*, plugin_key: str | None, payload: dict[str, Any]) -> dict[str, str]:
    """提取插件拥有的入口物料证据，当前优先复用 SixInOne parser。"""
    try:
        six_in_one = parse_workline_six_in_one(plugin_key, payload_dict(payload.get("data")))
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
    """判断重复入口事件是否与会话初始物料证据冲突。"""
    plugin_key = string_value(getattr(session, "plugin_key", None)) or string_value(
        getattr(workline, "plugin_key", None)
    )
    if not plugin_key:
        return None
    session_ctx = _session_context(session)
    initial_payload = payload_dict(session_ctx.get("initial_payload") or session_ctx.get("source_payload"))
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


async def _add_timeline(db: Any, timeline: Any, *, seq_no: int | None = None) -> int:
    from src.app.workline.services.timeline_sequence_service import add_timeline_with_sequence

    return await add_timeline_with_sequence(db, timeline, seq_no=seq_no)


async def _record_duplicate_entry_archive_timeline(
    db: Any,
    *,
    session: Any,
    workline: Any,
    inbox: Any,
    payload: dict[str, Any],
    reason: str,
) -> None:
    """为重复入口归档留一条显式 timeline 证据。"""
    from src.app.workline.models.timeline import (
        TimelineActionType,
        TimelineActorType,
        TimelineStage,
        TimelineStatus,
        WorklineTimeline,
    )

    session_id = resolve_entity_id(session)
    workline_id = resolve_entity_id(workline) or optional_int(getattr(session, "workline_id", None))
    if session_id is None or workline_id is None:
        return
    timeline = WorklineTimeline(
        session_id=session_id,
        workline_id=workline_id,
        trace_id=optional_str(getattr(inbox, "trace_id", None)) or optional_str(getattr(session, "trace_id", None)),
        seq_no=0,
        occurred_at=timezone.now_for_db(),
        stage=TimelineStage.INGEST,
        action_type=TimelineActionType.EVENT_PROCESSED,
        actor_type=TimelineActorType.ORCHESTRATOR,
        actor_code="workline-inbox-consumer",
        status=TimelineStatus.SUCCESS,
        message="DUPLICATE_ENTRY_ARCHIVED",
        payload_json={
            "reason": reason,
            "event_type": canonical_event_type(payload),
            "inbox_id": resolve_entity_id(inbox),
            "session_status": _session_status_value(session),
            "awaiting_command_id": optional_int(getattr(session, "awaiting_command_id", None)),
        },
        related_inbox_id=resolve_entity_id(inbox),
    )
    try:
        _ = await _add_timeline(db, timeline)
    except Exception as exc:
        logger.warning(f"重复入口归档 timeline 记录失败: {exc}")


async def _record_late_command_result_archive_timeline(
    db: Any,
    *,
    session: Any,
    workline: Any,
    inbox: Any,
    command: Any,
    payload: dict[str, Any],
    reason: str,
) -> None:
    """为迟到/重复 COMMAND_RESULT 归档留一条显式 timeline 证据。"""
    from src.app.workline.models.timeline import (
        TimelineActionType,
        TimelineActorType,
        TimelineStage,
        TimelineStatus,
        WorklineTimeline,
    )

    session_id = resolve_entity_id(session)
    workline_id = resolve_entity_id(workline) or optional_int(getattr(session, "workline_id", None))
    if session_id is None or workline_id is None:
        return
    timeline = WorklineTimeline(
        session_id=session_id,
        workline_id=workline_id,
        trace_id=optional_str(getattr(inbox, "trace_id", None)) or optional_str(getattr(session, "trace_id", None)),
        seq_no=0,
        occurred_at=timezone.now_for_db(),
        stage=TimelineStage.INGEST,
        action_type=TimelineActionType.EVENT_PROCESSED,
        actor_type=TimelineActorType.ORCHESTRATOR,
        actor_code="workline-inbox-consumer",
        status=TimelineStatus.SUCCESS,
        message="LATE_COMMAND_RESULT_ARCHIVED",
        payload_json={
            "reason": reason,
            "command_code": _command_code_value(command, payload),
            "command_status": _command_status_value(command),
            "inbox_id": resolve_entity_id(inbox),
            "session_status": _session_status_value(session),
            "awaiting_command_id": optional_int(getattr(session, "awaiting_command_id", None)),
            "current_wait_type": string_value(getattr(session, "current_wait_type", None)),
        },
        related_inbox_id=resolve_entity_id(inbox),
        related_command_id=resolve_entity_id(command),
    )
    try:
        _ = await _add_timeline(db, timeline)
    except Exception as exc:
        logger.warning(f"迟到命令结果归档 timeline 记录失败: {exc}")


def _build_orchestrator_lock_provider(db: Any):
    """为 OrchestratorService 构建生产锁提供者。
    优先使用 Redis 分布式锁；Redis 不可用时回退到 PostgreSQL advisory lock，
    但绝不退化为无锁。
    """
    redis_client = get_redis()
    if redis_client is not None:
        lock = RedisDistributedLock(redis_client=cast("Any", redis_client), key_prefix="workline:orchestrator:")

        def _redis_lock(lock_key: str):
            return lock.acquire(lock_key, db=db)

        return _redis_lock
    logger.warning("Redis not available for orchestrator lock, falling back to PostgreSQL advisory xact lock")

    def _resource_id(resource: str) -> int:
        digest = hashlib.blake2b(resource.encode("utf-8"), digest_size=8).digest()
        return int.from_bytes(digest, byteorder="big", signed=False) % (2**63)

    @asynccontextmanager
    async def _pg_lock(lock_key: str):
        resource_id = _resource_id(lock_key)
        # 使用事务级 advisory lock，随 commit/rollback 自动释放，
        # 避免锁内 commit 后再依赖另一连接手动 unlock 造成悬挂锁。
        await db.execute(
            text("SELECT pg_advisory_xact_lock(:resource_id)"),
            {"resource_id": resource_id},
        )
        yield

    return _pg_lock


def _inbox_bucket_lock_ttl_seconds() -> int:
    """bucket 锁 TTL 覆盖一个最大并发 wave 的处理窗口。"""

    return INBOX_BUCKET_LOCK_TTL_SECONDS


def _build_inbox_bucket_lock_provider(db: Any):
    """构建跨 worker 的 Inbox bucket 锁。

    Redis 可用时使用自动续期分布式锁；Redis 不可用时不使用 PostgreSQL session-level
    advisory lock，因为单条消息处理内部会 commit，连接池可能在 commit 后切换连接。
    此退化路径依赖 claim 阶段“每 bucket 只 claim 队首”和 token fencing 保持正确性。
    """

    ttl_seconds = _inbox_bucket_lock_ttl_seconds()
    redis_client = get_redis()
    if redis_client is not None:
        lock = RedisDistributedLock(
            redis_client=cast("Any", redis_client),
            key_prefix="workline:inbox-bucket:",
            default_ttl=ttl_seconds,
            auto_renewal=True,
            renewal_interval=10.0,
            fallback_to_pg=False,
        )

        def _redis_lock(bucket_key: str):
            return lock.acquire(bucket_key, ttl=ttl_seconds, db=db)

        return _redis_lock

    @asynccontextmanager
    async def _pg_lock(bucket_key: str):
        _ = bucket_key, db
        yield

    return _pg_lock


def _problem_class_for_error_domain(error_domain: ErrorDomain | None) -> ProblemClass | None:
    """为 UNKNOWN 等兜底码补充更接近现场语义的问题大类。"""
    if error_domain in {ErrorDomain.DEVICE, ErrorDomain.NETWORK}:
        return ProblemClass.HARDWARE
    return None


def _session_write_snapshot(session: Any) -> tuple[Any, Any]:
    """提取写入前的最小 session 快照，用于锁内防止 stale write。"""
    return (
        getattr(session, "status", None),
        getattr(session, "awaiting_command_id", None),
    )


async def _load_workline_session(db: Any, inbox: Any, session_repo: Any) -> Any | None:
    session_id = getattr(inbox, "session_id", None)
    if isinstance(session_id, int):
        return await session_repo.get_by_id(db, session_id)
    return None


async def _load_workline_entity(db: Any, inbox: Any, session: Any, workline_repo: Any | None = None) -> Any | None:
    from src.app.workline.repositories import WorkLineRepository

    repo = workline_repo or WorkLineRepository()
    workline_id = getattr(inbox, "workline_id", None) or getattr(session, "workline_id", None)
    if isinstance(workline_id, int):
        return await repo.get_by_id(db, workline_id)
    return None


async def _load_command_entity(db: Any, inbox: Any, command_repo: Any) -> Any | None:
    command_id = getattr(inbox, "command_id", None)
    if isinstance(command_id, int):
        return await command_repo.get_by_id(db, command_id)
    payload = payload_dict(getattr(inbox, "payload_json", None))
    command_code = payload.get("command_code")
    if isinstance(command_code, str) and command_code:
        return await command_repo.get_by_command_code(db, command_code)
    return None


def _hydrate_inbox_from_command(inbox: Any, command: Any | None) -> None:
    if command is None:
        return
    command_pk = resolve_entity_id(command)
    if command_pk is not None and not getattr(inbox, "command_id", None):
        inbox.command_id = command_pk
    session_id = getattr(command, "session_id", None)
    if isinstance(session_id, int) and not getattr(inbox, "session_id", None):
        inbox.session_id = session_id
    workline_id = getattr(command, "workline_id", None)
    if isinstance(workline_id, int) and not getattr(inbox, "workline_id", None):
        inbox.workline_id = workline_id


async def _load_device_entity(db: Any, inbox: Any, device_repo: Any) -> Any | None:
    device_id = getattr(inbox, "device_id", None)
    if isinstance(device_id, int):
        return await device_repo.get_by_id(db, device_id)
    payload = payload_dict(getattr(inbox, "payload_json", None))
    device_code = payload.get("device_code") or payload.get("location")
    if isinstance(device_code, str) and device_code:
        return await device_repo.get_by_device_code(db, device_code)
    return None


async def _backfill_workline_from_device(db: Any, inbox: Any, device: Any, workline_repo: Any) -> Any | None:
    workline_id = getattr(device, "work_line_id", None)
    if not isinstance(workline_id, int):
        return None
    inbox.workline_id = workline_id
    return await workline_repo.get_by_id(db, workline_id)


def _resolve_device_role(device: Any) -> str | None:
    """提取设备真实字符串角色。"""
    value = getattr(device, "device_role", None)
    return value if isinstance(value, str) and value else None


async def _load_devices_by_role(db: Any, workline: Any, device_repo: Any) -> dict[str, list[Any]]:
    workline_id = resolve_entity_id(workline)
    if workline_id is None:
        return {}
    devices = await device_repo.get_by_work_line_id(db, workline_id)
    devices_by_role: dict[str, list[Any]] = {}
    for device in devices:
        role = _resolve_device_role(device)
        if role:
            devices_by_role.setdefault(role, []).append(device)
    return devices_by_role


def _resolve_effect_source_device(inbox: Any, session: Any, devices_by_role: dict[str, list[Any]]) -> Any | None:
    """为 RuntimeIntent effect 层恢复无 device_id 回调的来源设备。"""
    payload = payload_dict(getattr(inbox, "payload_json", None))
    device_code = optional_str(payload.get("device_code")) or optional_str(payload.get("location"))
    if device_code is None:
        normalized_input = getattr(inbox, "normalized_input", None)
        device_code = optional_str(getattr(normalized_input, "device_code", None))
    if device_code is None:
        session_context = payload_dict(getattr(session, "context_json", None))
        rack_operation = payload_dict(session_context.get("rack_operation"))
        device_code = optional_str(rack_operation.get("resume_source_device_code")) or optional_str(
            session_context.get("resume_source_device_code")
        )
    if device_code is None:
        return None
    for devices in devices_by_role.values():
        for device in devices:
            if optional_str(getattr(device, "device_code", None)) == device_code:
                return device
    return None


async def _assert_workline_accepting_runtime_event(
    db: Any,
    *,
    workline: Any | None,
    resolved_event_type: str | None,
) -> bool:
    if resolved_event_type is None or resolved_event_type == "ESTOP_PRESSED":
        return True
    workline_pk = resolve_entity_id(workline)
    if workline_pk is None:
        return False
    from src.app.workline.services.safety_service import workline_safety_service

    await workline_safety_service.assert_accepting_work(db, workline_id=workline_pk)
    return True


async def _load_related_entities(
    db: Any,
    inbox: Any,
    *,
    resolved_event_type: str | None = None,
) -> LoadedEntities:
    """加载关联实体
    Args:
        db: 数据库会话
        inbox: Inbox 消息
    Returns:
        加载的实体字典
    """
    from src.app.device.repositories import DeviceRepository
    from src.app.device.repositories.command_repository import DeviceCommandRepository
    from src.app.workline.repositories import WorkLineRepository
    from src.app.workline.repositories.session_repository import (
        WorklineSessionRepository,
    )
    from src.workline_runtime.session_resolver import session_resolver

    session_repo = WorklineSessionRepository()
    workline_repo = WorkLineRepository()
    device_repo = DeviceRepository()
    command_repo = DeviceCommandRepository()
    session = await _load_workline_session(db, inbox, session_repo)
    workline = await _load_workline_entity(db, inbox, session, workline_repo)
    command = await _load_command_entity(db, inbox, command_repo)
    _hydrate_inbox_from_command(inbox, command)
    device = await _load_device_entity(db, inbox, device_repo)
    if workline is None and device is not None:
        workline = await _backfill_workline_from_device(db, inbox, device, workline_repo)
    safety_checked = await _assert_workline_accepting_runtime_event(
        db, workline=workline, resolved_event_type=resolved_event_type
    )
    devices_by_role = await _load_devices_by_role(db, workline, device_repo)
    if session is None and _should_resolve_session(inbox):
        session = await session_resolver.resolve_or_create(
            db=db,
            inbox=inbox,
            workline=workline,
            devices_by_role=devices_by_role,
        )
        session_pk = resolve_entity_id(session)
        if session_pk is not None:
            inbox.session_id = session_pk
        if workline is None:
            workline = await _load_workline_entity(db, inbox, session, workline_repo)
            if workline is None and device is not None:
                workline = await _backfill_workline_from_device(db, inbox, device, workline_repo)
            devices_by_role = await _load_devices_by_role(db, workline, device_repo)
            if not safety_checked:
                safety_checked = await _assert_workline_accepting_runtime_event(
                    db, workline=workline, resolved_event_type=resolved_event_type
                )
    if device is None and session is not None:
        device = _resolve_effect_source_device(inbox, session, devices_by_role)
    return {
        "session": session,
        "workline": workline,
        "device": device,
        "command": command,
        "devices_by_role": devices_by_role,
        "services": build_workline_runtime_services(db=db, workline=workline, session=session),
        "safety_checked": safety_checked,
    }


class InboxBatchProcessor:
    def __init__(
        self,
        write_back_service: Any = None,
        session_factory: Any = None,
        bucket_lock_provider: Any = None,
    ) -> None:
        self.write_back_service = write_back_service
        if session_factory is None:
            from src.database.db import get_db_context

            session_factory = get_db_context
        self.session_factory = session_factory
        self.bucket_lock_provider = bucket_lock_provider

    @staticmethod
    def _empty_result() -> ProcessResult:
        return {
            "processed": 0,
            "success": 0,
            "failed": 0,
            "skipped": 0,
        }

    @staticmethod
    def _merge_result(target: ProcessResult, source: ProcessResult) -> None:
        target["processed"] += source.get("processed", 0)
        target["success"] += source.get("success", 0)
        target["failed"] += source.get("failed", 0)
        target["skipped"] += source.get("skipped", 0)

    @staticmethod
    def _clamp_parallelism(parallelism: int | None) -> int:
        raw = WORKLINE_INBOX_BATCH_PARALLELISM if parallelism is None else parallelism
        return max(1, min(int(raw or 1), WORKLINE_INBOX_BATCH_MAX_PARALLELISM))

    @staticmethod
    def _bucket_key(claim: WorklineInboxClaim) -> str:
        payload = payload_dict(claim.payload_json)
        if claim.session_id is not None:
            return f"session:{claim.session_id}"
        if claim.device_id is not None:
            return f"device:{claim.device_id}"
        device_code = optional_str(payload.get("device_code")) or optional_str(payload.get("location"))
        if device_code is not None:
            return f"device_code:{device_code}"
        if claim.workline_id is not None:
            return f"workline:{claim.workline_id}"
        return "serial:unknown"

    async def process_batch(self, db: Any, limit: int = 10, parallelism: int | None = None) -> ProcessResult:
        """claim-first 有界并发处理 Inbox。

        调度层只负责原子 claim 和 bucket 并发；每个 bucket 使用独立 AsyncSession，
        bucket 内按 received_at/id 串行，bucket 间受 semaphore 限制。
        """
        from src.app.workline.services.inbox_service import inbox_service

        if limit <= 0:
            return self._empty_result()

        parallelism_value = self._clamp_parallelism(parallelism)
        remaining = limit
        result = self._empty_result()
        while remaining > 0:
            claim_limit = min(remaining, parallelism_value)
            processor_token = str(uuid.uuid4())
            claims = await inbox_service.claim_pending_messages(
                db,
                limit=claim_limit,
                processor_token=processor_token,
                stale_after_seconds=WORKLINE_INBOX_PROCESSING_STALE_SECONDS,
            )
            if not claims:
                break
            wave_result = await self._process_claims(db, claims, parallelism=parallelism_value)
            self._merge_result(result, wave_result)
            remaining -= len(claims)
        return result

    async def _process_claims(self, db: Any, claims: list[WorklineInboxClaim], *, parallelism: int) -> ProcessResult:
        buckets: dict[str, list[WorklineInboxClaim]] = {}
        for claim in claims:
            buckets.setdefault(self._bucket_key(claim), []).append(claim)
        for bucket_claims in buckets.values():
            bucket_claims.sort(key=lambda item: (item.received_at or timezone.now_for_db(), item.id))

        semaphore = asyncio.Semaphore(parallelism)

        async def _run_bucket(bucket_key: str, bucket_claims: list[WorklineInboxClaim]) -> ProcessResult:
            async with semaphore:
                bucket_result = self._empty_result()
                pending_claims = list(bucket_claims)
                try:
                    async with self.session_factory() as bucket_db:
                        if self.bucket_lock_provider is not None:
                            lock_context = self.bucket_lock_provider(bucket_db, bucket_key)
                        else:
                            lock_context = _build_inbox_bucket_lock_provider(bucket_db)(bucket_key)
                        async with lock_context:
                            for claim in bucket_claims:
                                message_result = await self._process_claimed_message(bucket_db, claim)
                                self._merge_result(bucket_result, message_result)
                                pending_claims.pop(0)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    raise InboxBucketProcessingError(bucket_key, pending_claims, e) from e
                logger.debug(f"Inbox bucket processed: bucket={bucket_key}, result={bucket_result}")
                return bucket_result

        result = self._empty_result()
        bucket_results = await asyncio.gather(
            *(_run_bucket(bucket_key, bucket_claims) for bucket_key, bucket_claims in buckets.items()),
            return_exceptions=True,
        )
        for (bucket_key, _bucket_claims), bucket_result in zip(buckets.items(), bucket_results, strict=True):
            if isinstance(bucket_result, asyncio.CancelledError):
                logger.warning(f"Inbox bucket processing cancelled: bucket={bucket_key}")
                raise bucket_result
            if isinstance(bucket_result, InboxBucketProcessingError):
                failed_result = await self._mark_bucket_claims_failed(db, bucket_result.claims, bucket_result.cause)
                self._merge_result(result, failed_result)
                logger.error(f"Inbox bucket processing failed: bucket={bucket_key}, error={bucket_result.cause}")
                continue
            if isinstance(bucket_result, BaseException):
                logger.error(f"Inbox bucket processing failed: bucket={bucket_key}, error={bucket_result}")
                raise bucket_result
            self._merge_result(result, bucket_result)
        return result

    async def _mark_bucket_claims_failed(
        self,
        db: Any,
        claims: list[WorklineInboxClaim],
        error: Exception,
    ) -> ProcessResult:
        """bucket 调度失败时，先把已 claim 但未处理的消息落到可重试终态。"""

        from src.app.workline.services.inbox_service import inbox_service

        result = self._empty_result()
        if not claims:
            return result

        error_message = f"Inbox bucket processing failed before message handler: {error}"
        try:
            for claim in claims:
                _ = await inbox_service.mark_as_failed(
                    db,
                    claim.id,
                    error_message,
                    processor_token=claim.processor_token,
                    auto_commit=False,
                )
                result["processed"] += 1
                result["failed"] += 1
            await db.commit()
        except Exception:
            with suppress(Exception):
                await db.rollback()
            raise
        return result

    async def _process_claimed_message(self, db: Any, claim: WorklineInboxClaim) -> ProcessResult:  # noqa: PLR0912
        """批量处理 Inbox 消息

        处理流程：
        1. 从数据库获取 status='NEW' 的待处理消息（limit 限制数量）
        2. 遍历每个消息：
           a. 尝试加锁标记为 PROCESSING（并发控制）
           b. 入站后 malformed gate：空的 SCAN_COMPLETED payload 直接失败
           c. 加载关联实体（session/workline/device/devices_by_role）
           d. 调用 OrchestratorService.process_inbox() 执行编排
           e. 成功：应用编排结果，更新状态为 PROCESSED
           f. 失败：更新状态为 FAILED
        3. 提交数据库事务

        并发控制：
        - 使用 SELECT ... FOR UPDATE SKIP LOCKED 获取消息
        - 使用 processor_token 标记处理 worker
        - 已被锁定的消息会被标记为 SKIPPED

        Args:
            db: 数据库会话
            limit: 批处理数量，默认 10

        Returns:
            处理结果统计 {
                "processed": 处理总数,
                "success": 成功数,
                "failed": 失败数,
                "skipped": 跳过数（已被其他 worker 锁定）
            }
        """
        from src.app.workline.services.inbox_service import inbox_service

        result: ProcessResult = self._empty_result()

        inbox = await inbox_service.repo.get_by_id(db, claim.id)
        if inbox is None:
            result["skipped"] += 1
            return result

        processor_token = claim.processor_token
        for inbox_message in [inbox]:
            inbox = inbox_message
            diagnostic_inbox = _snapshot_inbox_for_diagnostic(inbox)
            inbox_pk_text = str(diagnostic_inbox.id or getattr(inbox, "id", "unknown"))
            inbox_pk: int | None = None  # 初始化，避免 basedpyright 警告
            try:
                inbox_pk = resolve_required_pk(inbox, "inbox", "id", "inbox_id")
                if inbox_pk != claim.id:
                    result["skipped"] += 1
                    continue

                # ========== 前置验证：检查必填字段 ==========
                payload = payload_dict(getattr(inbox, "payload_json", None))
                resolved_event_type = canonical_event_type(payload)

                # SCAN_COMPLETED 事件必须包含条码信息。
                # 优先使用 canonical_event_type，缺失时回退 event_type。
                # 这里只做 registry 无关的 payload 最小校验，避免把错误归因绑定到
                # plugin_key / registry / session 解析结果上。
                if resolved_event_type == "SCAN_COMPLETED" and not _scan_completed_has_any_barcode_payload(payload):
                    error_msg = "SCAN_COMPLETED 缺少条码信息（HHPN/MfrPN/Qty/DateCode/LotCode/PkgID 或 barcode）"
                    logger.warning(f"Inbox {inbox_pk} {error_msg}")
                    await _record_diagnostic(
                        db,
                        inbox=inbox,
                        error_code=ErrorCode.CALLBACK_SCHEMA_INVALID,
                        message=error_msg,
                    )
                    _ = await inbox_service.mark_as_failed(
                        db, inbox_pk, error_msg, processor_token=processor_token, auto_commit=False
                    )
                    await db.commit()
                    result["failed"] += 1
                    result["processed"] += 1
                    continue

                # 加载关联实体
                entities = await _load_related_entities(db, inbox, resolved_event_type=resolved_event_type)
                session = entities["session"]
                workline = entities["workline"]

                if resolved_event_type == "ESTOP_PRESSED":
                    from src.app.workline.services.safety_service import workline_safety_service

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
                            device=entities["device"],
                            command=entities["command"],
                        )
                        _ = await inbox_service.mark_as_failed(
                            db, inbox_pk, error_msg, processor_token=processor_token, auto_commit=False
                        )
                        await db.commit()
                        result["failed"] += 1
                        result["processed"] += 1
                        logger.warning(f"Inbox {inbox_pk} 处理失败: {error_msg}")
                        continue

                    incident = await workline_safety_service.handle_estop(
                        db,
                        workline_id=workline_pk,
                        source_inbox_id=inbox_pk,
                        source_device_id=resolve_entity_id(entities["device"]) or getattr(inbox, "device_id", None),
                        source_command_id=resolve_entity_id(entities["command"]) or getattr(inbox, "command_id", None),
                        trigger_payload=payload,
                    )
                    _ = await inbox_service.mark_as_processed(
                        db, inbox_pk, processor_token=processor_token, auto_commit=False
                    )
                    await db.commit()
                    result["success"] += 1
                    result["processed"] += 1
                    logger.warning(f"Inbox {inbox_pk} 已处理 WorkLine 急停: incident_id={incident.id}")
                    continue

                inbox_kind = getattr(getattr(inbox, "kind", None), "value", getattr(inbox, "kind", None))
                if inbox_kind == "TIMER_TIMEOUT":
                    from src.app.workline.services.runtime_reconciliation_service import (
                        workline_runtime_reconciliation_service,
                    )

                    _ = await workline_runtime_reconciliation_service.handle_timer_timeout(
                        db, inbox=inbox, processor_token=processor_token
                    )
                    await db.commit()
                    result["success"] += 1
                    result["processed"] += 1
                    logger.warning(f"Inbox {inbox_pk} 已处理系统级 TIMER_TIMEOUT 对账")
                    continue

                if not entities.get("safety_checked", True):
                    _ = await _assert_workline_accepting_runtime_event(
                        db, workline=workline, resolved_event_type=resolved_event_type
                    )

                if session is None or workline is None:
                    error_msg = "Inbox processing missing session/workline context"
                    await _record_diagnostic(
                        db,
                        inbox=inbox,
                        error_code=ErrorCode.SESSION_CONTEXT_MISSING,
                        message=error_msg,
                        session=session,
                        workline=workline,
                        device=entities["device"],
                        command=entities["command"],
                    )
                    _ = await inbox_service.mark_as_failed(
                        db, inbox_pk, error_msg, processor_token=processor_token, auto_commit=False
                    )
                    await db.commit()
                    result["failed"] += 1
                    result["processed"] += 1
                    logger.warning(f"Inbox {inbox_pk} 处理失败: {error_msg}")
                    continue

                if _is_duplicate_entry_event_for_session(
                    inbox=inbox, payload=payload, session=session, workline=workline
                ):
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
                            device=entities["device"],
                            command=entities["command"],
                            extra=conflict_details,
                        )
                        _ = await inbox_service.mark_as_dead_letter(
                            db,
                            inbox_pk,
                            conflict_message,
                            processor_token=processor_token,
                            auto_commit=False,
                        )
                        await db.commit()
                        result["failed"] += 1
                        result["processed"] += 1
                        logger.warning(
                            f"Inbox {inbox_pk} rejected conflicting duplicate entry event: "
                            f"session_id={resolve_entity_id(session)}, conflicts={conflict_details['conflicts']}"
                        )
                        continue

                    await _record_duplicate_entry_archive_timeline(
                        db,
                        session=session,
                        workline=workline,
                        inbox=inbox,
                        payload=payload,
                        reason="SESSION_ALREADY_IN_PROGRESS_OR_TERMINAL",
                    )
                    _ = await inbox_service.mark_as_processed(
                        db, inbox_pk, processor_token=processor_token, auto_commit=False
                    )
                    await db.commit()
                    result["success"] += 1
                    result["processed"] += 1
                    logger.warning(
                        f"Inbox {inbox_pk} archived duplicate entry event: "
                        f"session_id={resolve_entity_id(session)}, "
                        f"status={_session_status_value(session)}, "
                        f"awaiting_command_id={getattr(session, 'awaiting_command_id', None)}"
                    )
                    continue

                if _is_late_or_duplicate_command_result_for_session(
                    inbox=inbox,
                    payload=payload,
                    session=session,
                    command=entities["command"],
                ):
                    await _record_late_command_result_archive_timeline(
                        db,
                        session=session,
                        workline=workline,
                        inbox=inbox,
                        command=entities["command"],
                        payload=payload,
                        reason="COMMAND_RESULT_NO_LONGER_MATCHES_SESSION_WAIT",
                    )
                    _ = await inbox_service.mark_as_processed(
                        db, inbox_pk, processor_token=processor_token, auto_commit=False
                    )
                    await db.commit()
                    result["success"] += 1
                    result["processed"] += 1
                    logger.warning(
                        f"Inbox {inbox_pk} archived late command result: "
                        f"session_id={resolve_entity_id(session)}, "
                        f"command_id={resolve_entity_id(entities['command'])}, "
                        f"status={_session_status_value(session)}, "
                        f"awaiting_command_id={getattr(session, 'awaiting_command_id', None)}"
                    )
                    continue

                write_effects_applied = False
                enqueue_outbox_dispatch = False
                session_snapshot = _session_write_snapshot(session)

                async def _write_callback(
                    write_result: OrchestratorResult,
                    _session: Any = session,
                    _workline: Any = workline,
                    _inbox: Any = inbox,
                    _devices_by_role: dict[str, list[Any]] = entities["devices_by_role"],
                    _device: Any | None = entities["device"],
                    _command: Any | None = entities["command"],
                    _inbox_pk: int = inbox_pk,
                    _session_snapshot: tuple[Any, Any] = session_snapshot,
                    _processor_token: str = processor_token,
                ) -> None:
                    nonlocal write_effects_applied, enqueue_outbox_dispatch
                    try:
                        await db.refresh(_session)
                        _payload = payload_dict(getattr(_inbox, "payload_json", None))
                        if _is_late_or_duplicate_command_result_for_session(
                            inbox=_inbox,
                            payload=_payload,
                            session=_session,
                            command=_command,
                        ):
                            await _record_late_command_result_archive_timeline(
                                db,
                                session=_session,
                                workline=_workline,
                                inbox=_inbox,
                                command=_command,
                                payload=_payload,
                                reason="COMMAND_RESULT_BECAME_STALE_BEFORE_WRITE",
                            )
                            _ = await inbox_service.mark_as_processed(
                                db, _inbox_pk, processor_token=_processor_token, auto_commit=False
                            )
                            await db.commit()
                            write_effects_applied = True
                            enqueue_outbox_dispatch = False
                            return

                        if _session_write_snapshot(_session) != _session_snapshot:
                            raise RuntimeError(
                                "Session state changed before WRITE apply; refusing stale orchestrator effects"
                            )
                        from src.workline_runtime.session_resolver import reapply_pending_session_ingress_metadata

                        _ = reapply_pending_session_ingress_metadata(_session)
                        write_back_service = self.write_back_service
                        if write_back_service is None:
                            from src.app.workline.services.write_back_service import orchestrator_write_back_service

                            write_back_service = orchestrator_write_back_service

                        await write_back_service.write_back(
                            db,
                            session=_session,
                            workline=_workline,
                            inbox=_inbox,
                            devices_by_role=_devices_by_role,
                            source_device=_device,
                            orch_result=write_result,
                        )
                        _ = await inbox_service.mark_as_processed(
                            db, _inbox_pk, processor_token=_processor_token, auto_commit=False
                        )
                        await db.commit()
                        write_effects_applied = True
                        enqueue_outbox_dispatch = _result_requires_outbox_dispatch(write_result)
                        # 通知前端工作线运行态已变更，key 用于增量刷新定位
                        from src.app.sys.services.event_stream_service import (
                            WORKLINE_RUNTIME_CHANGED_EVENT,
                            defer_sse_event,
                        )

                        defer_sse_event(
                            db,
                            WORKLINE_RUNTIME_CHANGED_EVENT,
                            {
                                "domain": "workline_trace",
                                "entity": "session",
                                "action": "updated",
                                "keys": {
                                    "workline_id": getattr(_workline, "id", None),
                                    "session_id": getattr(_session, "id", None),
                                },
                            },
                        )
                    except Exception:
                        await db.rollback()
                        raise

                # 调用编排器（带超时保护）
                orchestrator = OrchestratorService(lock_provider=_build_orchestrator_lock_provider(db))
                orch_result: OrchestratorResult = await asyncio.wait_for(
                    orchestrator.process_inbox(
                        session=session,
                        workline=workline,
                        inbox=inbox,
                        devices_by_role=entities["devices_by_role"],
                        services=entities["services"],
                        trace_id=inbox.trace_id or "",
                        write_callback=_write_callback,
                    ),
                    timeout=INBOX_PROCESS_TIMEOUT_SECONDS,
                )

                # 根据结果更新状态
                if orch_result.success:
                    if not write_effects_applied:
                        raise RuntimeError("WRITE lock callback was not executed for successful orchestrator result")

                    result["success"] += 1
                    logger.info(f"Inbox {inbox_pk} 处理成功")

                    if enqueue_outbox_dispatch:
                        _enqueue_outbox_dispatch()
                else:
                    error_msg = orch_result.error or "Unknown error"
                    mapped_error_code, mapped_error_domain = map_failure_to_diagnostic(
                        failure=None,
                        error_code=orch_result.error_code,
                    )
                    await _record_diagnostic(
                        db,
                        inbox=inbox,
                        error_code=mapped_error_code,
                        error_domain=mapped_error_domain,
                        problem_class=_problem_class_for_error_domain(mapped_error_domain),
                        message=error_msg,
                        session=session,
                        workline=workline,
                        device=entities["device"],
                        command=entities["command"],
                    )
                    _ = await inbox_service.mark_as_failed(
                        db, inbox_pk, error_msg, processor_token=processor_token, auto_commit=False
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
                    inbox_pk = diagnostic_inbox.id
                    if inbox_pk is not None:
                        _ = await inbox_service.mark_as_failed(
                            db, inbox_pk, str(e), processor_token=processor_token, auto_commit=False
                        )
                        await db.commit()
                except Exception as mark_error:
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
                    inbox_pk = diagnostic_inbox.id
                    if inbox_pk is not None:
                        _ = await inbox_service.park_for_retry(
                            db, inbox_pk, str(e), processor_token=processor_token, auto_commit=False, delay_seconds=10
                        )
                        await db.commit()
                except Exception as mark_error:
                    logger.warning(f"Inbox {inbox_pk_text} safety blocked 补记失败: {mark_error}")
                result["failed"] += 1
                result["processed"] += 1

            except TimeoutError:
                # 处理超时，不阻塞其他消息
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
                    # 使用已解析的 inbox_pk（如果在前面解析成功）
                    pk_to_mark = locals().get("inbox_pk") or diagnostic_inbox.id
                    if pk_to_mark is not None:
                        _ = await inbox_service.mark_as_failed(
                            db,
                            pk_to_mark,
                            f"处理超时 (> {INBOX_PROCESS_TIMEOUT_SECONDS}s)",
                            processor_token=processor_token,
                            auto_commit=False,
                        )
                        await db.commit()
                except Exception as mark_error:
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
                    inbox_pk = diagnostic_inbox.id
                    if inbox_pk is not None:
                        _ = await inbox_service.mark_as_failed(
                            db, inbox_pk, str(e), processor_token=processor_token, auto_commit=False
                        )
                        await db.commit()
                except Exception as mark_error:
                    logger.warning(f"Inbox {inbox_pk_text} 异常补记失败: {mark_error}")
                result["failed"] += 1
                result["processed"] += 1

        from src.app.sys.services.event_stream_service import publish_deferred_sse_events

        await publish_deferred_sse_events(db)
        return result
