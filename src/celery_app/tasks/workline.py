"""
作业线编排 Celery 任务

消费 WorklineInbox 消息，调用 OrchestratorService 进行处理。

设计参考: 设计文档 phase2-orchestrator
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from contextlib import asynccontextmanager, suppress
from datetime import timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any, TypedDict, cast

from celery import Task
from sqlalchemy import text

# 预加载外键目标模型，确保独立 Celery worker 进程内 mapper/metadata 完整注册。
from src.app.device.models import parse_device_capabilities
from src.app.device.models.command import DeviceCommand  # noqa: F401
from src.app.device.models.device import Device  # noqa: F401
from src.celery_app.app import celery_app
from src.celery_app.constants import (
    DEFAULT_COMMAND_PRIORITY,
    DEFAULT_COMMAND_TIMEOUT_MS,
    EXTERNAL_HTTP_DECISION_TYPE,
    EXTERNAL_HTTP_INBOX_KIND,
    INBOX_PROCESS_TIMEOUT_SECONDS,
)
from src.core.logger import logger
from src.database.redis_client import get_redis
from src.utils.timezone import timezone
from src.workline_plugin_registry import get_plugin_contract_version
from src.workline_runtime.diagnostics import (
    ErrorCode,
    build_diagnostic_card,
    build_diagnostic_context,
    build_diagnostic_event,
    map_failure_to_diagnostic,
)
from src.workline_runtime.enums import FailureDomain
from src.workline_runtime.lock import RedisDistributedLock
from src.workline_runtime.orchestrator import OrchestratorResult, OrchestratorService
from src.workline_runtime.payloads import SixInOne
from src.workline_runtime.trace_context import TraceContext
from src.workline_runtime.types import FailureIntent

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from src.workline_runtime.utils import JsonDict


def _enqueue_outbox_dispatch() -> None:
    cast("Any", celery_app).send_task(
        "src.celery_app.tasks.workline.dispatch_outbox_batch",
        kwargs={"limit": 50},
    )


# ============================================
# 类型定义
# ============================================


class ProcessResult(TypedDict):
    """处理结果"""

    processed: int
    success: int
    failed: int
    skipped: int


class ScanResult(TypedDict):
    """扫描结果"""

    scanned: int
    timeouts_created: int
    errors: int


class DispatchResult(TypedDict):
    """派发结果"""

    dispatched: int
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
    services: Any | None


# 常量已提取到 src.celery_app.constants

_DEFAULT_DEVICE_COMMAND_CALLBACK_PATH = "/api/v1/device/command"


class _DeviceCommandGovernanceError(RuntimeError):
    """设备治理字段在运行时拒绝命令创建/派发时抛出的显式异常。"""

    def __init__(self, *, domain: str, code: str, message: str):
        super().__init__(message)
        self.domain = domain
        self.code = code
        self.message = message


# ============================================
# 辅助函数
# ============================================


def _resolve_entity_id(entity: Any) -> int | None:
    """从实体上提取真实整型主键。"""
    value = getattr(entity, "id", None)
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) else None


def _should_resolve_session(inbox: Any) -> bool:
    """仅在具备足够归属信息时才触发 SessionResolver。"""
    raw_payload = getattr(inbox, "payload_json", None)
    payload: dict[str, Any] = cast("dict[str, Any]", raw_payload) if isinstance(raw_payload, dict) else {}
    kind = getattr(getattr(inbox, "kind", None), "value", getattr(inbox, "kind", None))

    if kind == "DEVICE_EVENT":
        return bool(getattr(inbox, "device_id", None) or payload.get("device_code") or payload.get("business_key"))
    if kind == "COMMAND_RESULT":
        return bool(getattr(inbox, "command_id", None) or payload.get("command_code"))
    if kind == EXTERNAL_HTTP_INBOX_KIND:
        return bool(getattr(inbox, "correlation_id", None))
    if kind in {"TIMER_TIMEOUT", "MANUAL_HOLD", "MANUAL_RESUME", "MANUAL_CANCEL", "REPLAY_REQUEST"}:
        return isinstance(getattr(inbox, "session_id", None), int)
    return False


def _resolve_device_role(device: Any) -> str | None:
    """提取设备真实字符串角色。"""
    value = getattr(device, "device_role", None)
    return value if isinstance(value, str) and value else None


def _ensure_non_empty_retry_result(task_name: str, result: dict[str, int], retries: int) -> None:
    """避免“重试后空跑”被 Celery 误记为成功。"""
    if retries <= 0:
        return

    if any(value > 0 for value in result.values()):
        return

    raise RuntimeError(
        f"{task_name} returned an empty result after {retries} retries; refusing to mark it as succeeded"
    )


def _run_async(coro: Awaitable[Any]) -> Any:
    """在 Celery 同步任务中运行异步函数"""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _resolve_required_pk(entity: Any, entity_name: str, *field_names: str) -> int:
    """提取必需的整型主键，不存在时抛出 ValueError。"""
    _ = field_names
    pk = _resolve_entity_id(entity)
    if pk is None:
        raise ValueError(f"{entity_name} missing primary key")
    return pk


def _payload_dict(raw_payload: Any) -> JsonDict:
    return cast("JsonDict", raw_payload) if isinstance(raw_payload, dict) else {}


def _string_value(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _canonical_event_type(payload: dict[str, Any]) -> str | None:
    value = payload.get("canonical_event_type") or payload.get("event_type")
    return value if isinstance(value, str) and value else None


def _outbox_trace_extra(outbox: Any, trace: TraceContext | None = None) -> dict[str, Any]:
    """提取 Outbox 派发链路的稳定追踪字段。"""

    resolved_trace = trace.with_outbox(outbox) if trace is not None else TraceContext.from_runtime(outbox=outbox)
    return resolved_trace.project_outbox_trace(
        outbox=outbox,
        dispatch_type=getattr(getattr(outbox, "dispatch_type", None), "value", getattr(outbox, "dispatch_type", None)),
        target_code=getattr(outbox, "target_code", None),
    )


def _outbox_trace_log_suffix(outbox: Any, trace: TraceContext | None = None) -> str:
    """构造统一的 Outbox trace 日志后缀。"""

    trace_extra = _outbox_trace_extra(outbox, trace=trace)
    return (
        f"dispatch_type={trace_extra['dispatch_type']}, "
        f"dispatch_key={trace_extra['dispatch_key']}, "
        f"target_code={trace_extra['target_code']}"
    )


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
        await db.execute(text(f"SELECT pg_advisory_xact_lock({resource_id})"))
        yield

    return _pg_lock


def _log_diagnostic(
    *,
    inbox: Any | None,
    error_code: ErrorCode,
    message: str,
    session: Any | None = None,
    workline: Any | None = None,
    device: Any | None = None,
    command: Any | None = None,
    outbox: Any | None = None,
    transition: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    payload = _payload_dict(getattr(inbox, "payload_json", None)) if inbox is not None else {}
    trace = TraceContext.from_runtime(
        session=session,
        workline=workline,
        inbox=inbox,
        command=command,
        outbox=outbox,
        correlation_id=getattr(inbox, "correlation_id", None) or getattr(session, "correlation_id", None),
        canonical_event_type=_canonical_event_type(payload),
        transition=transition,
    )
    if device is not None:
        trace = trace.with_device(device)
    card = build_diagnostic_card(
        build_diagnostic_event(
            error_code=error_code,
            context=build_diagnostic_context(
                trace=trace,
                session=session,
                inbox=inbox,
                command=command,
                device=device,
                outbox=outbox,
                workline=workline,
                canonical_event_type=trace.canonical_event_type,
                transition=transition,
                extra=extra,
            ),
            message=message,
            technical_summary=message,
        )
    )
    logger.warning(f"[WorklineDiagnostic] {card.model_dump_json(exclude_none=True)}")


def _session_context(session: Any) -> dict[str, Any]:
    raw_context = getattr(session, "context_json", None)
    if isinstance(raw_context, dict):
        return dict(cast("JsonDict", raw_context))
    return {}


def _set_session_context(session: Any, context: dict[str, Any]) -> None:
    session.context_json = context


def _resolve_runtime_contract_version(*, workline: Any, plugin_key: str | None) -> str | None:
    """统一解析运行时 contract_version：优先 workline，缺失时回退 registry。"""

    workline_contract_version = getattr(workline, "contract_version", None)
    if isinstance(workline_contract_version, str) and workline_contract_version:
        return workline_contract_version

    contract_version = get_plugin_contract_version(plugin_key)
    return contract_version if isinstance(contract_version, str) and contract_version else None


def _sync_session_contract_snapshot(session: Any, *, workline: Any, context: dict[str, Any]) -> None:
    plugin_key = getattr(session, "plugin_key", None) or getattr(workline, "plugin_key", None)
    if isinstance(plugin_key, str) and plugin_key:
        session.plugin_key = plugin_key

    resolved_contract_version = _resolve_runtime_contract_version(workline=workline, plugin_key=plugin_key)
    if resolved_contract_version and getattr(session, "contract_version", None) != resolved_contract_version:
        session.contract_version = resolved_contract_version

    step_code = context.get("step_code")
    if isinstance(step_code, str) and step_code:
        session.step_code = step_code


def _clear_session_wait(session: Any) -> None:
    session.current_wait_type = None
    session.current_wait_token = None
    session.waiting_since = None
    session.deadline_at = None
    session.awaiting_command_id = None


def _clear_session_failure(session: Any) -> None:
    session.failure_domain = None
    session.failure_code = None
    session.failure_message = None


def _session_write_snapshot(session: Any) -> tuple[Any, Any, Any]:
    """提取写入前的最小 session 快照，用于锁内防止 stale write。"""

    return (
        getattr(session, "status", None),
        getattr(session, "step_code", None),
        getattr(session, "awaiting_command_id", None),
    )


def _wait_session_status(wait_type: str) -> str:
    if wait_type == "EXTERNAL_HTTP":
        return "WAITING_EXTERNAL"
    return "WAITING_DEVICE_RESULT"


def _device_map_from_roles(devices_by_role: dict[str, list[Any]]) -> dict[int, Any]:
    device_by_id: dict[int, Any] = {}
    for devices in devices_by_role.values():
        for device in devices:
            device_id = _resolve_entity_id(device)
            if device_id is not None:
                device_by_id[device_id] = device
    return device_by_id


def _map_command_task_type(action: str) -> str:
    if action == "PICK_AND_PUT":
        return "PICK_AND_PLACE"
    if action in {"MEASUREMENT_REEL", "MOVE_FORWARD"}:
        return "PROCESS"
    return action


def _utc_timestamp_ms() -> int:
    return int(timezone.now_utc().timestamp() * 1000)


def _normalize_command_task_type(command_task_type: Any) -> str:
    if isinstance(command_task_type, Enum):
        return _string_value(command_task_type.value)
    return _string_value(command_task_type)


def _build_command_code(task_type: str) -> str:
    date_str = timezone.now_for_db().strftime("%Y%m%d")
    return f"CMD-{date_str}-{task_type}-{uuid.uuid4().hex[:8].upper()}"


def _normalize_vendor_command_payload(
    parameters: Any,
    *,
    action: str,
    default_command_code: str,
) -> JsonDict:
    """归一化插件产出的设备协议 payload。

    目标是保留 vendor payload 语义，只补齐派发必须字段。
    """

    payload = dict(_payload_dict(parameters))
    payload["command_code"] = _string_value(payload.get("command_code")) or default_command_code
    payload["task_type"] = _string_value(payload.get("task_type"), action)

    if not isinstance(payload.get("priority"), int):
        payload["priority"] = DEFAULT_COMMAND_PRIORITY
    if not isinstance(payload.get("timeout"), int):
        payload["timeout"] = DEFAULT_COMMAND_TIMEOUT_MS
    if not isinstance(payload.get("timestamp"), int):
        payload["timestamp"] = _utc_timestamp_ms()

    return payload


def _build_outbox_payload(command: Any, *, device_code: str | None = None) -> dict[str, Any]:
    resolved_device_code = _string_value(device_code)
    command_params = _payload_dict(getattr(command, "params", None))
    if command_params:
        payload = dict(command_params)
        if resolved_device_code:
            payload["device_code"] = resolved_device_code
        return payload

    normalized_task_type = _normalize_command_task_type(getattr(command, "task_type", None))

    payload = {
        "command_code": command.command_code,
        "task_type": normalized_task_type,
        "priority": command.priority,
        "timeout": command.timeout_ms,
        "params": {},
        "timestamp": _utc_timestamp_ms(),
    }
    if resolved_device_code:
        payload["device_code"] = resolved_device_code
    return payload


def _resolve_command_type_for_governance(payload: dict[str, Any]) -> str | None:
    """为设备治理校验提取稳定 command_type。"""

    command_type = _string_value(payload.get("task_type")) or _string_value(payload.get("command_type"))
    return command_type or None


def _resolve_device_command_path(device: Any) -> str:
    """优先使用 device.callback_path，未配置时回退默认命令路径。"""

    callback_path = _string_value(getattr(device, "callback_path", None)) or _DEFAULT_DEVICE_COMMAND_CALLBACK_PATH
    if not callback_path.startswith("/"):
        callback_path = f"/{callback_path}"
    return callback_path


def _raise_device_command_governance_error(
    *,
    domain: str,
    code: str,
    message: str,
    cause: Exception | None = None,
) -> None:
    error = _DeviceCommandGovernanceError(domain=domain, code=code, message=message)
    if cause is not None:
        raise error from cause
    raise error


def _enforce_device_command_governance(
    device: Any,
    *,
    command_type: str | None,
    stage_label: str,
) -> None:
    """消费设备治理字段，拒绝不允许的命令创建/派发。"""

    device_code = _string_value(getattr(device, "device_code", None), "UNKNOWN_DEVICE")
    resolved_command_type = command_type or "UNKNOWN"

    if bool(getattr(device, "maintenance_mode", False)):
        _raise_device_command_governance_error(
            domain=FailureDomain.MANUAL_INTERVENTION.value,
            code="DEVICE_MAINTENANCE_MODE",
            message=f"设备 {device_code} 处于 maintenance_mode，拒绝{stage_label}: command_type={resolved_command_type}",
        )

    try:
        capabilities = parse_device_capabilities(getattr(device, "capabilities_json", None))
    except (TypeError, ValueError) as exc:
        _raise_device_command_governance_error(
            domain=FailureDomain.CONFIG.value,
            code="DEVICE_CAPABILITY_CONFIG_INVALID",
            message=f"设备 {device_code} capabilities_json 配置非法，拒绝{stage_label}: {exc}",
            cause=exc,
        )

    if not capabilities.supports_command(command_type):
        _raise_device_command_governance_error(
            domain=FailureDomain.CONFIG.value,
            code="UNSUPPORTED_COMMAND_TYPE",
            message=f"设备 {device_code} 不支持 command_type={resolved_command_type}，拒绝{stage_label}",
        )


async def _add_timeline(db: Any, timeline: Any, *, seq_no: int | None = None) -> int:
    from sqlalchemy import func, select

    from src.app.workline.models.timeline import WorklineTimeline

    assigned_seq_no = seq_no
    if assigned_seq_no is None:
        result = await db.execute(
            select(func.max(WorklineTimeline.seq_no)).where(WorklineTimeline.session_id == timeline.session_id)  # type: ignore[arg-type]
        )
        max_seq_no = result.scalar_one_or_none()
        assigned_seq_no = (max_seq_no or 0) + 1

    timeline.seq_no = assigned_seq_no
    db.add(timeline)
    return assigned_seq_no


class EffectApplyContext(TypedDict):
    """Celery 侧 effect 执行上下文。

    这里故意保持为轻量字典，而不是引入更重的执行框架：
    - 便于 Phase 2 逐步拆分 `_apply_orchestrator_effects`
    - 便于测试按 handler 粒度观察状态变化
    - 不会过早把当前实现固化成难以演进的抽象层
    """

    db: Any
    session: Any
    workline: Any
    inbox: Any
    devices_by_role: dict[str, list[Any]]
    orch_result: OrchestratorResult
    current_status: str | None
    correlation_id: str | None
    trace: TraceContext
    session_ctx: dict[str, Any]
    now: Any
    awaiting_command_id: int | None
    awaiting_command_code: str | None
    next_timeline_seq_no: int | None


async def _emit_timeline(ctx: EffectApplyContext, **kwargs: Any) -> None:
    """统一 timeline 生成入口。

    对三方插件开发者而言，最重要的是看清“状态变化发生时会留下什么痕迹”。
    这里把 timeline 生成集中到一个 helper，后续若要补 diagnostics/timeline 对齐，
    只需要沿这个入口扩展即可。
    """

    from src.workline_runtime.timeline_generator import timeline_generator

    timeline = timeline_generator.generate(
        session=ctx["session"],
        **kwargs,
    )

    assigned_seq_no = await _add_timeline(
        ctx["db"],
        timeline,
        seq_no=ctx["next_timeline_seq_no"],
    )
    if not isinstance(assigned_seq_no, int):
        timeline_seq_no = getattr(timeline, "seq_no", None)
        assigned_seq_no = timeline_seq_no if isinstance(timeline_seq_no, int) else None
    if isinstance(assigned_seq_no, int):
        ctx["next_timeline_seq_no"] = assigned_seq_no + 1


def _build_effect_apply_context(
    *,
    db: Any,
    session: Any,
    workline: Any,
    inbox: Any,
    devices_by_role: dict[str, list[Any]],
    orch_result: OrchestratorResult,
) -> EffectApplyContext:
    trace = TraceContext.from_runtime(
        session=session,
        workline=workline,
        inbox=inbox,
        correlation_id=getattr(inbox, "correlation_id", None) or getattr(session, "correlation_id", None),
    )
    return {
        "db": db,
        "session": session,
        "workline": workline,
        "inbox": inbox,
        "devices_by_role": devices_by_role,
        "orch_result": orch_result,
        "current_status": getattr(session, "status", None),
        "correlation_id": trace.correlation_id,
        "trace": trace,
        "session_ctx": _session_context(session),
        "now": timezone.now_for_db(),
        "awaiting_command_id": None,
        "awaiting_command_code": None,
        "next_timeline_seq_no": None,
    }


def _apply_context_patch(ctx: EffectApplyContext) -> None:
    """先应用 context patch，再执行后续 effect。

    顺序很关键：
    - command effect 需要读取最新的 `step_code`
    - session contract snapshot 也依赖最新 context
    - 第三方插件开发者通常会把运行时决策写进 context，这些值应立即对后续 effect 可见
    """

    orch_result = ctx["orch_result"]
    session = ctx["session"]
    workline = ctx["workline"]
    session_ctx = ctx["session_ctx"]

    if orch_result.context_patch:
        session_ctx.update(orch_result.context_patch)
        _set_session_context(session, session_ctx)
        _sync_session_contract_snapshot(session, workline=workline, context=session_ctx)
        # 同步 barcode 到 session 字段，便于主数据和排障视图直接读取。
        if "barcode" in orch_result.context_patch:
            barcode_value = orch_result.context_patch["barcode"]
            if barcode_value:
                session.barcode = barcode_value
        return

    _sync_session_contract_snapshot(session, workline=workline, context=session_ctx)


def _sync_effect_trace_fields(ctx: EffectApplyContext) -> None:
    """同步 effect 执行后的基础追踪字段。"""

    session = ctx["session"]
    correlation_id = ctx["correlation_id"]

    if correlation_id and getattr(session, "correlation_id", None) != correlation_id:
        session.correlation_id = correlation_id

    session.last_inbox_id = _resolve_entity_id(ctx["inbox"])


def _timeline_inbox_id(ctx: EffectApplyContext) -> int | None:
    """统一提取 timeline 关联的 inbox 主键。"""

    return _resolve_entity_id(ctx["inbox"])


def _effect_trace_payload(ctx: EffectApplyContext) -> dict[str, Any]:
    """构造跨 effect 共用的追踪字段。"""

    payload = _payload_dict(getattr(ctx["inbox"], "payload_json", None))
    trace = ctx["trace"].with_inbox(ctx["inbox"]) if ctx.get("inbox") is not None else ctx["trace"]
    return trace.project_timeline_payload(canonical_event_type=_canonical_event_type(payload))


def _decision_timeline_payload(ctx: EffectApplyContext) -> dict[str, Any]:
    """构造“插件做出决策”这一类 timeline payload。"""

    orch_result = ctx["orch_result"]
    return {
        **_effect_trace_payload(ctx),
        "transition": orch_result.transition,
        "context_patch": orch_result.context_patch or {},
    }


def _external_decision_timeline_payload(
    ctx: EffectApplyContext,
    *,
    dispatch_key: str,
    target_code: str,
    payload_json: dict[str, Any],
) -> dict[str, Any]:
    """构造外部调用准备阶段的 timeline payload。"""

    return {
        **_effect_trace_payload(ctx),
        "dispatch_key": dispatch_key,
        "target_code": target_code,
        "payload": payload_json,
    }


def _command_timeline_payload(
    ctx: EffectApplyContext,
    *,
    command_code: str,
    command_type: str,
    parameters: dict[str, Any],
    dispatch_key: str,
) -> dict[str, Any]:
    """构造命令派发阶段的 timeline payload。"""

    return {
        **_effect_trace_payload(ctx),
        "command_code": command_code,
        "command_type": command_type,
        "dispatch_key": dispatch_key,
        "parameters": parameters,
    }


def _wait_timeline_payload(
    ctx: EffectApplyContext, *, wait_type: str, wait_token: str, deadline_seconds: int
) -> dict[str, Any]:
    """构造等待态开始时的 timeline payload。"""

    return {
        **_effect_trace_payload(ctx),
        "wait_type": wait_type,
        "wait_token": wait_token,
        "deadline_seconds": deadline_seconds,
    }


def _failure_timeline_payload(ctx: EffectApplyContext, *, message: str) -> dict[str, Any]:
    """构造失败态 timeline payload。"""

    return {
        **_effect_trace_payload(ctx),
        "message": message,
    }


async def _apply_transition_timeline(ctx: EffectApplyContext) -> None:
    """记录插件做出的 transition 决策。

    这条 timeline 只负责“插件决定了什么”，不负责“系统最终进入了什么状态”。
    终态/等待态 timeline 由后续专门的 transition handlers 写入。
    """

    from src.app.workline.models.timeline import TimelineActionType, TimelineActorType, TimelineStage

    orch_result = ctx["orch_result"]
    if not orch_result.transition:
        return

    await _emit_timeline(
        ctx,
        stage=TimelineStage.DECISION,
        action_type=TimelineActionType.DECISION_MADE,
        payload=_decision_timeline_payload(ctx),
        actor_type=TimelineActorType.PLUGIN,
        actor_code=getattr(ctx["workline"], "plugin_key", None),
        related_inbox_id=_timeline_inbox_id(ctx),
    )


async def _apply_external_decisions(ctx: EffectApplyContext) -> None:
    """应用 EXTERNAL_HTTP decisions。

    当前仍保持最小可用实现：只落 Outbox 与对应 timeline，
    不额外引入 decision handler registry，避免 Phase 2 过度工程化。
    """

    from src.app.workline.models.timeline import TimelineActionType, TimelineActorType, TimelineStage, TimelineStatus

    db = ctx["db"]

    for decision in ctx["orch_result"].decisions or []:
        if not isinstance(decision, dict):
            continue

        decision_type = _string_value(decision.get("decision_type"))
        if decision_type != EXTERNAL_HTTP_DECISION_TYPE:
            continue

        dispatch_key = _string_value(decision.get("dispatch_key"))
        target_code = _string_value(decision.get("target_code"))
        payload_json = _payload_dict(decision.get("payload"))
        source_system = _string_value(decision.get("source_system"), "EXTERNAL_SYSTEM")
        if not dispatch_key:
            raise ValueError("EXTERNAL_HTTP decision missing dispatch_key")
        if not target_code:
            raise ValueError("EXTERNAL_HTTP decision missing target_code")
        if not payload_json:
            raise ValueError("EXTERNAL_HTTP decision missing payload")

        db.add(
            _build_external_http_outbox_model(
                ctx,
                dispatch_key=dispatch_key,
                target_code=target_code,
                payload_json=payload_json,
            )
        )
        await _emit_timeline(
            ctx,
            stage=TimelineStage.DISPATCH_PREPARE,
            action_type=TimelineActionType.EXTERNAL_CALL_STARTED,
            payload=_external_decision_timeline_payload(
                ctx,
                dispatch_key=dispatch_key,
                target_code=target_code,
                payload_json=payload_json,
            ),
            actor_type=TimelineActorType.EXTERNAL_SYSTEM,
            actor_code=source_system,
            related_inbox_id=_timeline_inbox_id(ctx),
            status=TimelineStatus.PENDING,
        )


async def _resolve_target_device(ctx: EffectApplyContext, *, device_repo: Any, target_device_id: int) -> Any:
    """先从 workline 设备映射里取设备，取不到再回库加载。"""

    device_by_id = _device_map_from_roles(ctx["devices_by_role"])
    target_device = device_by_id.get(target_device_id)
    if target_device is None:
        target_device = await device_repo.get_by_id(ctx["db"], target_device_id)
    if target_device is None:
        raise ValueError(f"Target device not found: {target_device_id}")
    return target_device


async def _validate_command_effects(ctx: EffectApplyContext) -> None:
    """在真正落外部 decision / command 前，先消费设备治理字段。"""

    from src.app.device.repositories import DeviceRepository

    device_repo = DeviceRepository()
    for command_intent in ctx["orch_result"].commands or []:
        target_device = await _resolve_target_device(
            ctx,
            device_repo=device_repo,
            target_device_id=command_intent.target_device_id,
        )
        _enforce_device_command_governance(
            target_device,
            command_type=command_intent.action,
            stage_label="命令创建",
        )


def _build_command_create_payload(
    ctx: EffectApplyContext,
    *,
    command_intent: Any,
    vendor_payload: dict[str, Any],
    target_device_id: int,
    resolved_command_code: str,
) -> dict[str, Any]:
    """将 plugin command intent 转成 DeviceCommand 创建载荷。"""

    vendor_task_type = _string_value(vendor_payload.get("task_type"), command_intent.action)
    priority_value = vendor_payload.get("priority")
    timeout_value = vendor_payload.get("timeout")
    session = ctx["session"]
    workline = ctx["workline"]

    return {
        "command_code": resolved_command_code,
        "device_id": target_device_id,
        "task_type": _map_command_task_type(vendor_task_type),
        "priority": priority_value if isinstance(priority_value, int) else DEFAULT_COMMAND_PRIORITY,
        "timeout_ms": timeout_value if isinstance(timeout_value, int) else DEFAULT_COMMAND_TIMEOUT_MS,
        "params": vendor_payload,
        "correlation_id": ctx["trace"].correlation_id or ctx["correlation_id"],
        "session_id": str(session.id),
        "workline_id": session.workline_id,
        "plugin_key": getattr(session, "plugin_key", None) or getattr(workline, "plugin_key", None),
        "contract_version": getattr(session, "contract_version", None),
        "step_code": ctx["session_ctx"].get("step_code"),
    }


def _build_external_http_outbox_model(
    ctx: EffectApplyContext,
    *,
    dispatch_key: str,
    target_code: str,
    payload_json: dict[str, Any],
) -> Any:
    """将 external decision 投影为 Outbox 模型。

    这是后续 replay/debug 的稳定锚点之一：
    给定同一条 decision，开发者可以清楚看到最终会落成怎样的 outbox 记录。
    """

    from src.app.workline.models.outbox import DispatchType, TargetType, WorklineOutbox

    session = ctx["session"]
    return WorklineOutbox(
        session_id=session.id,
        workline_id=session.workline_id,
        dispatch_type=DispatchType.EXTERNAL_HTTP,
        dispatch_key=dispatch_key,
        target_type=TargetType.HTTP_ENDPOINT,
        target_code=target_code,
        payload_json=payload_json,
    )


def _build_command_outbox_model(ctx: EffectApplyContext, *, command: Any, device_code: str) -> Any:
    """将已创建的 DeviceCommand 投影为设备派发 Outbox。

    这里单独收口的价值不在“少几行代码”，而在于明确：
    - Command 是业务持久化对象
    - Outbox 是派发持久化对象
    - 二者的映射规则是稳定且可测试的
    """

    from src.app.workline.models.outbox import DispatchType, TargetType, WorklineOutbox

    session = ctx["session"]
    return WorklineOutbox(
        session_id=session.id,
        workline_id=session.workline_id,
        dispatch_type=DispatchType.DEVICE_COMMAND,
        dispatch_key=f"device-command:{command.command_code}",
        target_type=TargetType.DEVICE,
        target_code=device_code,
        payload_json=_build_outbox_payload(command, device_code=device_code),
    )


async def _apply_command_effects(ctx: EffectApplyContext) -> None:
    """应用 plugin 产出的命令 effect。

    这里坚持两条原则：
    1. 先创建 DeviceCommand，再创建对应 Outbox
    2. `awaiting_command_id` 取第一条命令，供 wait transition 复用

    这样做可以让“等待哪个命令结果”在 session 上有稳定锚点，
    也更利于后续 replay/debug 能力落地。
    """

    from src.app.device.repositories import DeviceRepository
    from src.app.device.repositories.command_repository import DeviceCommandRepository
    from src.app.workline.models.timeline import TimelineActionType, TimelineActorType, TimelineStage, TimelineStatus

    db = ctx["db"]
    command_repo = DeviceCommandRepository()
    device_repo = DeviceRepository()

    for command_intent in ctx["orch_result"].commands or []:
        target_device_id = command_intent.target_device_id
        target_device = await _resolve_target_device(ctx, device_repo=device_repo, target_device_id=target_device_id)
        _enforce_device_command_governance(
            target_device,
            command_type=command_intent.action,
            stage_label="命令创建",
        )

        device_code = getattr(target_device, "device_code", None)
        if not isinstance(device_code, str) or not device_code:
            raise ValueError(f"Target device missing device_code: {target_device_id}")

        generated_command_code = _build_command_code(_map_command_task_type(command_intent.action))
        vendor_payload = _normalize_vendor_command_payload(
            command_intent.parameters,
            action=command_intent.action,
            default_command_code=generated_command_code,
        )
        logger.info(
            f"[Orchestrator] Command parameters: command_intent.parameters={command_intent.parameters}, "
            f"vendor_payload={vendor_payload}"
        )
        resolved_command_code = _string_value(vendor_payload.get("command_code"), generated_command_code)
        command_data = _build_command_create_payload(
            ctx,
            command_intent=command_intent,
            vendor_payload=vendor_payload,
            target_device_id=target_device_id,
            resolved_command_code=resolved_command_code,
        )
        command = await command_repo.create(db, command_data)
        if command is None:
            raise RuntimeError("Failed to create device command from PluginResult")

        if ctx["awaiting_command_id"] is None:
            ctx["awaiting_command_id"] = command.id
            ctx["awaiting_command_code"] = command.command_code

        command_outbox = _build_command_outbox_model(ctx, command=command, device_code=device_code)
        db.add(command_outbox)
        await _emit_timeline(
            ctx,
            stage=TimelineStage.DISPATCH_PREPARE,
            action_type=TimelineActionType.COMMAND_SENT,
            payload=_command_timeline_payload(
                ctx,
                command_code=command.command_code,
                command_type=command_intent.action,
                dispatch_key=command_outbox.dispatch_key,
                parameters=vendor_payload,
            ),
            actor_type=TimelineActorType.ORCHESTRATOR,
            actor_code=device_code,
            related_inbox_id=_timeline_inbox_id(ctx),
            related_command_id=command.id,
            status=TimelineStatus.PENDING,
        )


async def _apply_failure_transition(ctx: EffectApplyContext) -> bool:
    from src.app.workline.models.timeline import TimelineActionType, TimelineActorType, TimelineStage, TimelineStatus

    failure = ctx["orch_result"].failure
    if failure is None:
        return False

    session = ctx["session"]
    session.status = "FAILED"
    _clear_session_wait(session)
    session.ended_at = ctx["now"]
    session.failure_domain = failure.domain
    session.failure_code = failure.code
    session.failure_message = failure.message
    await _emit_timeline(
        ctx,
        stage=TimelineStage.FAIL,
        action_type=TimelineActionType.SESSION_FAILED,
        payload=_failure_timeline_payload(ctx, message=failure.message),
        from_status=ctx["current_status"],
        to_status="FAILED",
        actor_type=TimelineActorType.ORCHESTRATOR,
        related_inbox_id=_timeline_inbox_id(ctx),
        status=TimelineStatus.FAILED,
        failure_domain=failure.domain,
        message=failure.message,
    )
    return True


async def _apply_manual_cancel_transition(ctx: EffectApplyContext) -> bool:
    from src.app.workline.models.timeline import TimelineActionType, TimelineActorType, TimelineStage

    if ctx["orch_result"].transition != "manual_cancel":
        return False

    session = ctx["session"]
    session.status = "CANCELLED"
    _clear_session_wait(session)
    session.ended_at = ctx["now"]
    await _emit_timeline(
        ctx,
        stage=TimelineStage.MANUAL,
        action_type=TimelineActionType.SESSION_CANCELLED,
        from_status=ctx["current_status"],
        to_status="CANCELLED",
        actor_type=TimelineActorType.ORCHESTRATOR,
        related_inbox_id=_timeline_inbox_id(ctx),
    )
    return True


async def _apply_completion_transition(ctx: EffectApplyContext) -> bool:
    from src.app.workline.models.timeline import TimelineActionType, TimelineActorType, TimelineStage

    if not ctx["orch_result"].complete:
        return False

    session = ctx["session"]
    session.status = "COMPLETED"
    _clear_session_wait(session)
    session.ended_at = ctx["now"]
    await _emit_timeline(
        ctx,
        stage=TimelineStage.COMPLETE,
        action_type=TimelineActionType.SESSION_COMPLETED,
        from_status=ctx["current_status"],
        to_status="COMPLETED",
        actor_type=TimelineActorType.ORCHESTRATOR,
        related_inbox_id=_timeline_inbox_id(ctx),
    )
    return True


async def _apply_wait_transition(ctx: EffectApplyContext) -> bool:
    from src.app.workline.models.timeline import TimelineActionType, TimelineActorType, TimelineStage, TimelineStatus

    wait = ctx["orch_result"].wait
    if wait is None:
        return False

    session = ctx["session"]
    resolved_wait_token = wait.wait_token
    if wait.wait_type == "COMMAND_RESULT":
        resolved_wait_token = ctx["awaiting_command_code"] or wait.wait_token

    session.status = _wait_session_status(wait.wait_type)
    session.current_wait_type = wait.wait_type
    session.current_wait_token = resolved_wait_token
    session.waiting_since = ctx["now"]
    session.deadline_at = ctx["now"] + timedelta(seconds=wait.deadline_seconds)
    session.awaiting_command_id = ctx["awaiting_command_id"]
    session.ended_at = None
    await _emit_timeline(
        ctx,
        stage=TimelineStage.WAITING,
        action_type=TimelineActionType.WAIT_STARTED,
        payload=_wait_timeline_payload(
            ctx,
            wait_type=wait.wait_type,
            wait_token=resolved_wait_token,
            deadline_seconds=wait.deadline_seconds,
        ),
        from_status=ctx["current_status"],
        to_status=session.status,
        actor_type=TimelineActorType.ORCHESTRATOR,
        related_inbox_id=_timeline_inbox_id(ctx),
        related_command_id=ctx["awaiting_command_id"],
        status=TimelineStatus.PENDING,
    )
    return True


def _apply_non_terminal_transition(ctx: EffectApplyContext) -> bool:
    """应用非终态 transition。

    `manual_hold` / `manual_resume` 不写终态 timeline，
    只负责把 session 恢复到正确的状态上，保留已有等待上下文。
    """

    session = ctx["session"]
    transition = ctx["orch_result"].transition

    if transition == "manual_hold":
        session.status = "MANUAL_HOLD"
        session.ended_at = None
        return True

    if transition == "manual_resume":
        if session.current_wait_type:
            session.status = _wait_session_status(session.current_wait_type)
        else:
            session.status = "RUNNING"
        session.ended_at = None
        return True

    return False


def _apply_running_fallback(ctx: EffectApplyContext) -> None:
    """在存在有效 effect 但没有进入终态/等待态时，保持 session 为 RUNNING。"""

    orch_result = ctx["orch_result"]
    session = ctx["session"]

    if orch_result.transition or orch_result.context_patch or orch_result.commands or orch_result.decisions:
        session.status = "RUNNING"

    _clear_session_wait(session)
    session.ended_at = None


async def _apply_orchestrator_effects(
    db: Any,
    *,
    session: Any,
    workline: Any,
    inbox: Any,
    devices_by_role: dict[str, list[Any]],
    orch_result: OrchestratorResult,
) -> None:
    """应用 OrchestratorResult 到 Session / Command / Outbox / Timeline。

    Phase 2 起，这个入口只负责组织执行顺序；
    具体 effect 交由更小的内部 handlers 处理。

    执行顺序说明：
    1. 先落 context patch 与 trace fields
    2. 先消费设备治理字段，再创建 decisions / commands（因为 wait 可能依赖 command id）
    3. 最后应用 failure / cancel / complete / wait / fallback 状态变更
    """

    ctx = _build_effect_apply_context(
        db=db,
        session=session,
        workline=workline,
        inbox=inbox,
        devices_by_role=devices_by_role,
        orch_result=orch_result,
    )

    _apply_context_patch(ctx)
    _sync_effect_trace_fields(ctx)
    await _apply_transition_timeline(ctx)

    try:
        await _validate_command_effects(ctx)
        await _apply_external_decisions(ctx)
        await _apply_command_effects(ctx)
    except _DeviceCommandGovernanceError as exc:
        orch_result.failure = FailureIntent(domain=exc.domain, code=exc.code, message=exc.message)
        orch_result.decisions = []
        orch_result.commands = []
        orch_result.wait = None
        orch_result.complete = False

    # failure / cancel / complete / wait 的优先级必须稳定，避免出现“既完成又取消”的歧义。
    if await _apply_failure_transition(ctx):
        return

    _clear_session_failure(session)

    if await _apply_manual_cancel_transition(ctx):
        return
    if await _apply_completion_transition(ctx):
        return
    if await _apply_wait_transition(ctx):
        return
    if _apply_non_terminal_transition(ctx):
        return

    _apply_running_fallback(ctx)


async def _load_workline_session(db: Any, inbox: Any, session_repo: Any) -> Any | None:
    """按 inbox.session_id 加载 Session。"""
    session_id = getattr(inbox, "session_id", None)
    if not session_id:
        return None
    return await session_repo.get_by_id(db, session_id)


async def _load_workline_entity(db: Any, inbox: Any, session: Any, workline_repo: Any | None = None) -> Any | None:
    """按 inbox/workline session 归属加载 Workline。"""
    if workline_repo is None:
        from src.app.workline.repositories import WorkLineRepository

        workline_repo = WorkLineRepository()

    workline_id = getattr(inbox, "workline_id", None)
    if workline_id:
        return await workline_repo.get_by_id(db, workline_id)

    session_workline_id = getattr(session, "workline_id", None)
    if session_workline_id:
        return await workline_repo.get_by_id(db, session_workline_id)

    return None


async def _load_command_entity(db: Any, inbox: Any, command_repo: Any) -> Any | None:
    """按 command_id 或 payload.command_code 加载命令（带缓存），并回填 inbox.command_id。"""
    from src.app.device.services import device_command_service
    from src.database.redis_cache import get_cache

    command_id = getattr(inbox, "command_id", None)
    if command_id:
        # 使用 Service 层缓存
        cache = get_cache()
        return await device_command_service.get_by_id(db, cache, command_id)

    raw_payload = getattr(inbox, "payload_json", None)
    payload: dict[str, Any] = cast("dict[str, Any]", raw_payload) if isinstance(raw_payload, dict) else {}
    command_code = payload.get("command_code")
    if not command_code:
        return None

    # command_code 查询仍使用 repo（无对应 Service 方法）
    command = await command_repo.get_by_command_code(db, command_code)
    if command is not None:
        inbox.command_id = command.id
    return command


def _hydrate_inbox_from_command(inbox: Any, command: Any | None) -> None:
    """从命令记录回填 inbox 归属信息。"""
    if command is None:
        return

    if getattr(inbox, "device_id", None) is None:
        inbox.device_id = command.device_id
    if getattr(inbox, "workline_id", None) is None and command.workline_id is not None:
        inbox.workline_id = command.workline_id
    if getattr(inbox, "correlation_id", None) is None and command.correlation_id:
        inbox.correlation_id = command.correlation_id


async def _load_device_entity(db: Any, inbox: Any, device_repo: Any) -> Any | None:
    """按 device_id 或 payload.device_code 加载设备（带缓存），并回填 inbox.device_id。"""
    from src.app.device.services import device_service
    from src.database.redis_cache import get_cache

    device_id = getattr(inbox, "device_id", None)
    if device_id:
        cache = get_cache()
        return await device_service.get_by_id(db, cache, device_id)

    raw_payload = getattr(inbox, "payload_json", None)
    payload: dict[str, Any] = cast("dict[str, Any]", raw_payload) if isinstance(raw_payload, dict) else {}
    device_code = payload.get("device_code")
    if not device_code:
        return None

    # 使用 DeviceService 查询（带缓存）
    device = await device_service.get_device_by_code(db, device_code)
    if device is not None:
        inbox.device_id = device.id
    return device


async def _backfill_workline_from_device(db: Any, inbox: Any, device: Any, workline_repo: Any) -> Any | None:
    """按设备归属补全 Workline，并回填 inbox.workline_id。"""
    device_workline_id = getattr(device, "work_line_id", None)
    if device is None or not device_workline_id:
        return None

    workline = await workline_repo.get_by_id(db, device_workline_id)
    if workline is not None:
        inbox.workline_id = workline.id
    return workline


async def _load_devices_by_role(db: Any, workline: Any, device_repo: Any) -> dict[str, list[Any]]:
    """加载工作线下设备并按角色分组。"""
    workline_pk = _resolve_entity_id(workline)
    if workline is None or workline_pk is None:
        return {}

    devices = await device_repo.get_by_work_line_id(db, workline_pk)
    if not devices:
        return {}

    devices_by_role: dict[str, list[Any]] = {}
    for device in devices:
        role = _resolve_device_role(device)
        if role:
            devices_by_role.setdefault(role, []).append(device)

    return devices_by_role


async def _load_related_entities(db: Any, inbox: Any) -> LoadedEntities:
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

    devices_by_role = await _load_devices_by_role(db, workline, device_repo)

    if session is None and _should_resolve_session(inbox):
        session = await session_resolver.resolve_or_create(
            db=db,
            inbox=inbox,
            workline=workline,
            devices_by_role=devices_by_role,
        )
        session_pk = _resolve_entity_id(session)
        if session_pk is not None:
            inbox.session_id = session_pk
        if workline is None:
            workline = await _load_workline_entity(db, inbox, session, workline_repo)
            if workline is None and device is not None:
                workline = await _backfill_workline_from_device(db, inbox, device, workline_repo)
            devices_by_role = await _load_devices_by_role(db, workline, device_repo)

    # 服务容器（Phase 2 简化实现）
    services: dict[str, Any] = {}

    return {
        "session": session,
        "workline": workline,
        "device": device,
        "command": command,
        "devices_by_role": devices_by_role,
        "services": services,
    }


# ============================================
# Celery 任务
# ============================================


class WorklineTask(Task):
    """作业线任务基类 - 提供数据库会话管理"""

    def __init__(self) -> None:
        super().__init__()
        self._db: Any | None = None

    @property
    def db(self) -> Any:
        """懒加载数据库会话"""
        if self._db is None:
            from src.database.db import AsyncSessionLocal as AsyncSessionLocalDynamic

            session_local = AsyncSessionLocalDynamic
            if session_local is None:
                raise RuntimeError("数据库未初始化，请先调用 init_db()")
            self._db = session_local()
        return self._db

    def cleanup(self) -> None:
        """清理资源"""
        if self._db:
            try:
                loop = asyncio.new_event_loop()
                loop.run_until_complete(self._db.close())
                loop.close()
            except Exception:
                pass
            self._db = None

    def on_failure(
        self, exc: Exception, task_id: str, args: tuple[Any, ...], kwargs: dict[str, Any], einfo: Any
    ) -> None:
        """任务失败时清理资源"""
        _ = args, kwargs, einfo
        self.cleanup()
        logger.error(f"任务 {task_id} 失败: {exc}")

    def on_success(self, retval: Any, task_id: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        """任务成功时清理资源"""
        _ = retval, args, kwargs
        self.cleanup()
        logger.info(f"任务 {task_id} 成功完成")


class ProcessInboxMessages:
    """处理 Inbox 消息的内部类（批量处理核心逻辑）

    职责：
    - 批量获取待处理 Inbox 消息
    - 并发控制：防止多 worker 重复处理同一消息
    - 前置验证：检查 SCAN_COMPLETED 事件必填字段
    - 编排执行：调用 OrchestratorService 处理消息
    - 结果应用：执行编排产出的命令/决策/状态转换
    - 状态更新：标记消息为成功/失败/跳过

    调用方式：
    - 通过 Celery 任务 process_inbox_batch 间接调用
    - 可直接调用 _process_batch() 进行单元测试
    """

    @staticmethod
    async def _process_batch(db: Any, limit: int = 10) -> ProcessResult:
        """批量处理 Inbox 消息

        处理流程：
        1. 从数据库获取 status='NEW' 的待处理消息（limit 限制数量）
        2. 遍历每个消息：
           a. 尝试加锁标记为 PROCESSING（并发控制）
           b. 前置验证：SCAN_COMPLETED 事件必须包含条码信息
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

        result: ProcessResult = {
            "processed": 0,
            "success": 0,
            "failed": 0,
            "skipped": 0,
        }

        # 获取待处理消息
        messages = await inbox_service.get_new_messages(db, limit=limit)

        for inbox in messages:
            inbox_pk_text = str(getattr(inbox, "id", "unknown"))
            inbox_pk: int | None = None  # 初始化，避免 basedpyright 警告
            try:
                inbox_pk = _resolve_required_pk(inbox, "inbox", "id", "inbox_id")
                # 尝试标记为处理中（并发控制）
                processor_token = str(uuid.uuid4())
                try:
                    _ = await inbox_service.mark_as_processing(db, inbox_pk, processor_token, auto_commit=False)
                except ValueError:
                    # 已被其他 worker 处理
                    result["skipped"] += 1
                    continue

                # ========== 前置验证：检查必填字段 ==========
                raw_payload = getattr(inbox, "payload_json", None)
                payload: dict[str, Any] = cast("dict[str, Any]", raw_payload) if isinstance(raw_payload, dict) else {}
                resolved_event_type = _canonical_event_type(payload)
                data_field: dict[str, Any] = payload.get("data") or {}

                # SCAN_COMPLETED 事件必须包含条码信息。
                # 优先使用 canonical_event_type，缺失时回退 event_type，
                # 保持 vendor event 映射后一致校验。
                if resolved_event_type == "SCAN_COMPLETED":
                    # 从 data 字段和 payload 中收集条码
                    barcode_values: list[Any] = [data_field.get(f) for f in SixInOne.BARCODE_FIELDS]
                    barcode_values.extend(
                        [
                            payload.get("LotCode"),
                            payload.get("DateCode"),
                            payload.get("barcode"),
                        ]
                    )
                    if not any(barcode_values):
                        error_msg = "SCAN_COMPLETED 缺少条码信息（LotCode/DateCode/PONumber/MfrPN/ProductNo/Qty）"
                        logger.warning(f"Inbox {inbox_pk} {error_msg}")
                        _log_diagnostic(inbox=inbox, error_code=ErrorCode.CALLBACK_SCHEMA_INVALID, message=error_msg)
                        _ = await inbox_service.mark_as_failed(db, inbox_pk, error_msg, auto_commit=False)
                        await db.commit()
                        result["failed"] += 1
                        result["processed"] += 1
                        continue

                # 加载关联实体
                entities = await _load_related_entities(db, inbox)
                session = entities["session"]
                workline = entities["workline"]
                if session is None or workline is None:
                    error_msg = "Inbox processing missing session/workline context"
                    _log_diagnostic(
                        inbox=inbox,
                        error_code=ErrorCode.SESSION_CONTEXT_MISSING,
                        message=error_msg,
                        session=session,
                        workline=workline,
                        device=entities["device"],
                        command=entities["command"],
                    )
                    _ = await inbox_service.mark_as_failed(db, inbox_pk, error_msg, auto_commit=False)
                    await db.commit()
                    result["failed"] += 1
                    result["processed"] += 1
                    logger.warning(f"Inbox {inbox_pk} 处理失败: {error_msg}")
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
                    _inbox_pk: int = inbox_pk,
                    _session_snapshot: tuple[Any, Any, Any] = session_snapshot,
                ) -> None:
                    nonlocal write_effects_applied, enqueue_outbox_dispatch
                    try:
                        await db.refresh(_session)
                        if _session_write_snapshot(_session) != _session_snapshot:
                            raise RuntimeError(
                                "Session state changed before WRITE apply; refusing stale orchestrator effects"
                            )
                        from src.workline_runtime.session_resolver import reapply_pending_session_ingress_metadata

                        reapply_pending_session_ingress_metadata(_session)
                        await _apply_orchestrator_effects(
                            db,
                            session=_session,
                            workline=_workline,
                            inbox=_inbox,
                            devices_by_role=_devices_by_role,
                            orch_result=write_result,
                        )
                        _ = await inbox_service.mark_as_processed(db, _inbox_pk, auto_commit=False)
                        await db.commit()
                        write_effects_applied = True
                        enqueue_outbox_dispatch = bool(write_result.commands or write_result.decisions)
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
                        correlation_id=inbox.correlation_id or "",
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
                    error_msg = orch_result.error or (
                        orch_result.failure.message if orch_result.failure is not None else "Unknown error"
                    )
                    mapped_error_code, _ = map_failure_to_diagnostic(
                        failure=orch_result.failure,
                        error_code=orch_result.error_code,
                    )
                    _log_diagnostic(
                        inbox=inbox,
                        error_code=mapped_error_code,
                        message=error_msg,
                        session=session,
                        workline=workline,
                        device=entities["device"],
                        command=entities["command"],
                        transition=orch_result.transition,
                    )
                    _ = await inbox_service.mark_as_failed(db, inbox_pk, error_msg, auto_commit=False)
                    await db.commit()
                    result["failed"] += 1
                    logger.warning(f"Inbox {inbox_pk} 处理失败: {error_msg}")

                result["processed"] += 1

            except TimeoutError:
                # 处理超时，不阻塞其他消息
                logger.error(f"Inbox {inbox_pk} 处理超时 (> {INBOX_PROCESS_TIMEOUT_SECONDS}s)")
                _log_diagnostic(
                    inbox=inbox,
                    error_code=ErrorCode.DEVICE_TIMEOUT,
                    message=f"Inbox processing timeout (> {INBOX_PROCESS_TIMEOUT_SECONDS}s)",
                )
                with suppress(Exception):
                    await db.rollback()
                try:
                    # 使用已解析的 inbox_pk（如果在前面解析成功）
                    pk_to_mark = locals().get("inbox_pk") or _resolve_entity_id(inbox)
                    if pk_to_mark is not None:
                        _ = await inbox_service.mark_as_failed(
                            db,
                            pk_to_mark,
                            f"处理超时 (> {INBOX_PROCESS_TIMEOUT_SECONDS}s)",
                            auto_commit=False,
                        )
                        await db.commit()
                except Exception as mark_error:
                    logger.warning(f"Inbox 超时标记失败: {mark_error}")
                result["failed"] += 1
                result["processed"] += 1

            except Exception as e:
                logger.exception(f"Inbox {inbox_pk_text} 处理异常")
                _log_diagnostic(
                    inbox=inbox,
                    error_code=ErrorCode.UNKNOWN,
                    message=str(e),
                )
                with suppress(Exception):
                    await db.rollback()
                try:
                    inbox_pk = _resolve_entity_id(inbox)
                    if inbox_pk is not None:
                        _ = await inbox_service.mark_as_failed(db, inbox_pk, str(e), auto_commit=False)
                        await db.commit()
                except Exception as mark_error:
                    logger.warning(f"Inbox {inbox_pk_text} 异常补记失败: {mark_error}")
                result["failed"] += 1
                result["processed"] += 1

        return result


class TimeoutScanner:
    """超时扫描器内部类

    职责：
    - 扫描超时的 Session（deadline_at < now）
    - 为超时 Session 创建 timeout 类型的 Inbox 消息
    - 触发后续编排流程处理超时

    调用方式：
    - 通过 Celery 任务 scan_timeouts_batch 间接调用
    - 可直接调用 _scan() 进行单元测试
    """

    @staticmethod
    async def _scan(db: Any, limit: int = 100) -> ScanResult:
        """扫描超时 Session 并创建 Timeout Inbox

        处理流程：
        1. 查询 deadline_at < NOW() 的超时 Session
        2. 遍历每个超时会话：
           a. 创建 type='timeout' 的 Inbox 消息
           b. 继承原 Session 的 correlation_id
        3. 提交数据库事务

        Args:
            db: 数据库会话
            limit: 批处理数量，默认 100

        Returns:
            扫描结果统计 {
                "scanned": 扫描的 Session 数,
                "timeouts_created": 创建的超时 Inbox 数,
                "errors": 错误数
            }
        """
        from src.app.workline.repositories.session_repository import (
            WorklineSessionRepository,
        )
        from src.app.workline.services.inbox_service import inbox_service

        result: ScanResult = {
            "scanned": 0,
            "timeouts_created": 0,
            "errors": 0,
        }

        # 获取超时 Session
        session_repo = WorklineSessionRepository()
        sessions = await session_repo.get_timed_out_sessions(db, limit=limit)
        result["scanned"] = len(sessions)

        for session in sessions:
            try:
                session_pk = _resolve_entity_id(session)
                if session_pk is None:
                    raise ValueError("Timed out session missing primary key")

                # 创建超时 Inbox
                _ = await inbox_service.create_timeout_inbox(
                    db=db,
                    session_id=session_pk,
                    workline_id=session.workline_id,
                    deadline_at=session.deadline_at,
                    correlation_id=session.correlation_id,
                    auto_commit=False,
                )
                result["timeouts_created"] += 1
                logger.info(f"Session {session_pk} 超时，已创建 Timeout Inbox")
            except Exception as e:
                session_pk = _resolve_entity_id(session)
                logger.error(f"Session {session_pk or 'unknown'} 创建超时 Inbox 失败: {e}")
                result["errors"] += 1

        # 提交事务
        await db.commit()

        return result


@celery_app.task(
    name="src.celery_app.tasks.workline.process_inbox_batch",
    base=WorklineTask,
    bind=True,
    max_retries=3,
    default_retry_delay=5,
)
def process_inbox_batch(self: WorklineTask, limit: int = 10) -> ProcessResult:
    """批量处理 Inbox 消息 (Celery 任务入口)

    从数据库获取 status='NEW' 的 Inbox 消息，调用 ProcessInboxMessages._process_batch() 执行处理。

    处理流程（详见 ProcessInboxMessages）：
    1. 批量获取待处理消息（limit 限制）
    2. 并发控制：标记为 PROCESSING，防止重复处理
    3. 前置验证：检查 SCAN_COMPLETED 事件必填字段
    4. 加载关联实体：session/workline/device/devices_by_role
    5. 调用 OrchestratorService 执行编排
    6. 应用编排结果：command/outbox/timeline
    7. 更新状态：PROCESSED/FAILED

    执行模式：
    - bind=True：任务方法接收 self（WorklineTask 实例）
    - max_retries=3：失败后自动重试最多 3 次
    - default_retry_delay=5：重试间隔 5 秒（指数退避）

    调用链：
        process_inbox_batch() → ProcessInboxMessages._process_batch()

    Args:
        self: Celery 任务实例（bind=True）
        limit: 批处理数量，默认 10

    Returns:
        处理结果统计 {
            "processed": 处理总数,
            "success": 成功数,
            "failed": 失败数,
            "skipped": 跳过数
        }

    触发方式：
        celery beat 定时调度（默认每 5 秒）
        手动调用：process_inbox_batch.delay(limit=10)
    """
    logger.debug(f"开始处理 Inbox 消息, limit={limit}")

    async def _process() -> ProcessResult:
        async with self.db as db:
            return await ProcessInboxMessages._process_batch(db, limit=limit)

    try:
        result = _run_async(_process())
        _ensure_non_empty_retry_result(
            "process_inbox_batch",
            result,
            int(getattr(self.request, "retries", 0) or 0),
        )
        if result.get("processed", 0) > 0:
            logger.info(f"Inbox 处理完成: {result}")
        else:
            logger.debug(f"Inbox 处理完成: {result}")
        return result
    except Exception as e:
        logger.error(f"Inbox 处理失败: {e}")
        countdown = 5 * (2**self.request.retries)
        raise self.retry(exc=e, countdown=countdown) from None


@celery_app.task(
    name="src.celery_app.tasks.workline.scan_timeouts_batch",
    base=WorklineTask,
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def scan_timeouts_batch(self: WorklineTask, limit: int = 100) -> ScanResult:
    """扫描超时 Session (Celery 任务入口)

    扫描 deadline_at < NOW() 的超时 Session，为每个超时 Session 创建 timeout 类型的 Inbox 消息，
    触发后续编排流程处理超时。

    处理流程（详见 TimeoutScanner）：
    1. 查询 deadline_at < NOW() 的超时 Session
    2. 遍历每个超时会话：
       a. 创建 type='TIMEOUT' 的 Inbox 消息
       b. 继承原 Session 的 correlation_id
    3. 提交数据库事务

    执行模式：
    - bind=True：任务方法接收 self（WorklineTask 实例）
    - max_retries=3：失败后自动重试最多 3 次
    - default_retry_delay=60：重试间隔 60 秒（超时场景不频繁）

    调用链：
        scan_timeouts_batch() → TimeoutScanner._scan()

    Args:
        self: Celery 任务实例（bind=True）
        limit: 批处理数量，默认 100

    Returns:
        扫描结果统计 {
            "scanned": 扫描的 Session 数,
            "timeouts_created": 创建的超时 Inbox 数,
            "errors": 错误数
        }

    触发方式：
        celery beat 定时调度（默认每 30 秒）
        手动调用：scan_timeouts_batch.delay(limit=100)

    注意：
        - 使用幂等性键防止重复创建 timeout Inbox
        - 创建的 Inbox 类型为 InboxKind.TIMER_TIMEOUT
    """
    logger.info(f"开始扫描超时 Session, limit={limit}")

    async def _scan() -> ScanResult:
        async with self.db as db:
            return await TimeoutScanner._scan(db, limit=limit)

    try:
        result = _run_async(_scan())
        _ensure_non_empty_retry_result(
            "scan_timeouts_batch",
            result,
            int(getattr(self.request, "retries", 0) or 0),
        )
        logger.info(f"超时扫描完成: {result}")
        return result
    except Exception as e:
        logger.error(f"超时扫描失败: {e}")
        countdown = 60 * (2**self.request.retries)
        raise self.retry(exc=e, countdown=countdown) from None


class OutboxDispatcher:
    """Outbox 派发器内部类（用于测试）"""

    MAX_RETRIES = 3

    @staticmethod
    async def _dispatch(db: Any, limit: int = 50) -> DispatchResult:
        """派发 Outbox 消息

        Args:
            db: 数据库会话
            limit: 批处理数量

        Returns:
            派发结果统计
        """
        from src.app.workline.repositories.outbox_repository import (
            WorklineOutboxRepository,
        )

        result: DispatchResult = {
            "dispatched": 0,
            "success": 0,
            "failed": 0,
            "skipped": 0,
        }

        # 获取待派发消息
        outbox_repo = WorklineOutboxRepository()
        messages = await outbox_repo.get_pending_messages(db, limit=limit)

        for outbox in messages:
            outbox_pk_text = str(getattr(outbox, "id", "unknown"))
            trace = TraceContext.from_runtime(outbox=outbox)
            try:
                outbox_pk = _resolve_required_pk(outbox, "outbox", "id", "outbox_id")
                # 尝试标记为派发中（并发控制）
                updated = await outbox_repo.mark_as_dispatching(db, outbox_pk)
                if updated is None:
                    # 已被其他 worker 处理
                    result["skipped"] += 1
                    continue

                # 派发消息
                success = await OutboxDispatcher._dispatch_single(db, outbox)

                if success:
                    trace_extra = _outbox_trace_extra(outbox, trace=trace)
                    _ = await outbox_repo.mark_as_sent(db, outbox_pk)
                    result["success"] += 1
                    logger.info(f"Outbox {outbox_pk} 派发成功 ({_outbox_trace_log_suffix(outbox, trace=trace)})")
                else:
                    trace_extra = _outbox_trace_extra(outbox, trace=trace)
                    _log_diagnostic(
                        inbox=None,
                        outbox=outbox,
                        error_code=ErrorCode.OUTBOX_DISPATCH_FAILED,
                        message="Dispatch failed",
                        extra=trace_extra,
                    )
                    _ = await outbox_repo.mark_as_failed(db, outbox_pk, "Dispatch failed", OutboxDispatcher.MAX_RETRIES)
                    result["failed"] += 1
                    logger.warning(f"Outbox {outbox_pk} 派发失败 ({_outbox_trace_log_suffix(outbox, trace=trace)})")

                result["dispatched"] += 1

            except Exception as e:
                logger.error(f"Outbox {outbox_pk_text} 派发异常: {e} ({_outbox_trace_log_suffix(outbox, trace=trace)})")
                _log_diagnostic(
                    inbox=None,
                    outbox=outbox,
                    error_code=ErrorCode.OUTBOX_DISPATCH_FAILED,
                    message=str(e),
                    extra=_outbox_trace_extra(outbox, trace=trace),
                )
                try:
                    outbox_pk = _resolve_entity_id(outbox)
                    if outbox_pk is not None:
                        _ = await outbox_repo.mark_as_failed(db, outbox_pk, str(e), OutboxDispatcher.MAX_RETRIES)
                except Exception as mark_error:
                    logger.warning(f"Outbox {outbox_pk_text} 异常补记失败: {mark_error}")
                result["failed"] += 1
                result["dispatched"] += 1

        # 提交事务
        await db.commit()

        return result

    @staticmethod
    async def _dispatch_single(db: Any, outbox: Any) -> bool:
        """派发单个 Outbox 消息

        Args:
            db: 数据库会话
            outbox: Outbox 消息

        Returns:
            是否成功
        """
        from src.app.workline.models.outbox import DispatchType

        if outbox.dispatch_type == DispatchType.DEVICE_COMMAND:
            return await OutboxDispatcher._dispatch_device_command(db, outbox)
        if outbox.dispatch_type == DispatchType.EXTERNAL_HTTP:
            return await OutboxDispatcher._dispatch_external_http(outbox)
        if outbox.dispatch_type == DispatchType.INTERNAL_SIGNAL:
            return await OutboxDispatcher._dispatch_internal_signal(outbox)
        logger.warning(f"未知的派发类型: {outbox.dispatch_type}")
        return False

    @staticmethod
    async def _dispatch_device_command(db: Any, outbox: Any) -> bool:
        """派发设备指令。"""
        try:
            import httpx

            from src.app.device.repositories.device_repository import device_repository

            device = await device_repository.get_by_device_code(db, outbox.target_code)
            if device is None or not device.host or not device.port:
                logger.error(f"设备不存在或通信配置不完整: {outbox.target_code}")
                return False

            payload = _payload_dict(getattr(outbox, "payload_json", None))
            _enforce_device_command_governance(
                device,
                command_type=_resolve_command_type_for_governance(payload),
                stage_label="命令派发",
            )

            # 确保 scheme 是 http 或 https
            protocol_value = getattr(device, "protocol", None)
            if protocol_value:
                scheme = str(protocol_value).lower()
                if scheme not in ("http", "https"):
                    scheme = "http"
            else:
                scheme = "http"

            callback_path = _resolve_device_command_path(device)
            url = f"{scheme}://{device.host}:{device.port}{callback_path}"
            logger.info(f"发送设备指令到 {url}: {payload.get('command_code')}")
            timeout = (device.timeout or 10000) / 1000
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, json=payload)
                if response.status_code == 200:
                    logger.info(f"设备指令发送成功: {payload.get('command_code')}")
                    return True
                response_body = response.text.strip()
                if response_body:
                    logger.warning(f"设备指令发送失败: HTTP {response.status_code}, body={response_body}")
                else:
                    logger.warning(f"设备指令发送失败: HTTP {response.status_code}")
                return False
        except _DeviceCommandGovernanceError as e:
            logger.warning(str(e))
            raise RuntimeError(str(e)) from e
        except Exception as e:
            logger.error(f"设备指令派发失败: {e}")
            return False

    @staticmethod
    async def _dispatch_external_http(outbox: Any) -> bool:
        """派发外部 HTTP 调用"""
        import httpx

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    outbox.target_code,
                    json=outbox.payload_json,
                )
                return response.status_code == 200
        except Exception as e:
            logger.error(f"外部 HTTP 派发失败: {e}")
            return False

    @staticmethod
    async def _dispatch_internal_signal(outbox: Any) -> bool:
        """派发内部信号"""
        try:
            from src.celery_app.app import celery_app

            # 发送到目标服务的任务队列
            celery_app.send_task(
                f"src.celery_app.tasks.{outbox.target_code}.process_signal",
                kwargs={"payload": outbox.payload_json},
            )
            return True
        except Exception as e:
            logger.error(f"内部信号派发失败: {e}")
            return False


@celery_app.task(
    name="src.celery_app.tasks.workline.dispatch_outbox_batch",
    base=WorklineTask,
    bind=True,
    max_retries=3,
    default_retry_delay=10,
)
def dispatch_outbox_batch(self: WorklineTask, limit: int = 50) -> DispatchResult:
    """批量派发 Outbox 消息 (Celery 任务入口)

    从数据库获取 status='PENDING' 的 Outbox 消息，根据 dispatch_type 执行派发：
    - DEVICE_COMMAND：调用设备 HTTP API
    - EXTERNAL_HTTP：调用外部系统 HTTP API
    - INTERNAL_SIGNAL：触发内部 Celery 任务

    处理流程（详见 OutboxDispatcher）：
    1. 批量获取待派发消息（limit 限制）
    2. 遍历每个消息：
       a. 标记为 DISPATCHING（并发控制）
       b. 根据 dispatch_type 调用对应派发方法
       c. 成功：标记为 SENT
       d. 失败：标记为 FAILED（超过最大重试次数）
    3. 提交数据库事务

    执行模式：
    - bind=True：任务方法接收 self（WorklineTask 实例）
    - max_retries=3：失败后自动重试最多 3 次
    - default_retry_delay=10：重试间隔 10 秒

    调用链：
        dispatch_outbox_batch() → OutboxDispatcher._dispatch()

    Args:
        self: Celery 任务实例（bind=True）
        limit: 批处理数量，默认 50

    Returns:
        派发结果统计 {
            "dispatched": 派发总数,
            "success": 成功数,
            "failed": 失败数,
            "skipped": 跳过数
        }

    触发方式：
        celery beat 定时调度（默认每 5 秒）
        手动调用：dispatch_outbox_batch.delay(limit=50)

    派发类型详解：
        - DEVICE_COMMAND: 向设备下发指令（HTTP POST /api/v1/device/command）
        - EXTERNAL_HTTP: 调用外部系统 API（HTTP POST dispatch_key）
        - INTERNAL_SIGNAL: 触发内部任务（celery.send_task）
    """
    logger.debug(f"开始派发 Outbox 消息, limit={limit}")

    async def _dispatch() -> DispatchResult:
        async with self.db as db:
            return await OutboxDispatcher._dispatch(db, limit=limit)

    try:
        result = _run_async(_dispatch())
        if result.get("dispatched", 0) > 0:
            logger.info(f"Outbox 派发完成: {result}")
        else:
            logger.debug(f"Outbox 派发完成: {result}")
        return result
    except Exception as e:
        logger.error(f"Outbox 派发失败: {e}")
        countdown = 10 * (2**self.request.retries)
        raise self.retry(exc=e, countdown=countdown) from None


class _DispatchOutboxCompat:
    """历史测试兼容入口，复用新的 OutboxDispatcher 实现。"""

    _dispatch = staticmethod(OutboxDispatcher._dispatch)
    _dispatch_single = staticmethod(OutboxDispatcher._dispatch_single)


dispatch_outbox = _DispatchOutboxCompat()

# 历史测试/脚本兼容入口
process_inbox_messages = ProcessInboxMessages
scan_timeouts = TimeoutScanner


# ============================================
# 导出
# ============================================

__all__ = [
    # 内部辅助函数
    "_load_related_entities",
    # Celery 任务入口（公共 API）
    "dispatch_outbox",
    "dispatch_outbox_batch",
    "process_inbox_batch",
    "process_inbox_messages",
    "scan_timeouts",
    "scan_timeouts_batch",
    # 内部类（已注释：不导出，仅供 Celery 任务内部使用）
    # "OutboxDispatcher",
    # "ProcessInboxMessages",
    # "TimeoutScanner",
]
