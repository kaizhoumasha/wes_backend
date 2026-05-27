"""
作业线编排 Celery 任务

本文件提供 Workline 核心流程的 Celery 任务入口。
核心业务逻辑（如 Inbox 批量处理、Orchestrator 写回、出站下发等）
已抽离至 `src/app/workline/services/` 目录下。
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

if TYPE_CHECKING:
    from src.workline_runtime.orchestrator import OrchestratorResult
from celery import Task
from sqlalchemy import text

# 预加载外键目标模型，确保独立 Celery worker 进程内 mapper/metadata 完整注册。
from src.app.device.models.command import DeviceCommand
from src.app.device.models.device import Device  # noqa: F401
from src.app.workline.services.device_command_gateway import (  # noqa: F401
    _DeviceCommandGovernanceError,
    _enforce_device_command_governance,
)
from src.app.workline.services.safety_service import WorkLineSafetyBlocked  # noqa: F401
from src.celery_app.app import celery_app

# Backwards compatible exports for things that were moved
from src.celery_app.constants import (
    DEFAULT_COMMAND_PRIORITY,
    DEFAULT_COMMAND_TIMEOUT_MS,
    DEVICE_HEARTBEAT_TIMEOUT_SECONDS,
    EXTERNAL_HTTP_DECISION_TYPE,
    INBOX_PROCESS_TIMEOUT_SECONDS,  # noqa: F401
)
from src.core.logger import logger
from src.database import db as db_module
from src.database.redis_client import get_redis
from src.utils.timezone import timezone
from src.utils.value_normalization import (
    canonical_event_type,
    enum_value,
    resolve_entity_id,
    string_value,
)
from src.workline_plugin_registry import (
    get_plugin_contract_version,
)
from src.workline_runtime.diagnostics import (
    ErrorDomain,
    ProblemClass,
)
from src.workline_runtime.diagnostics.failure_mapper import map_failure_to_diagnostic  # noqa: F401
from src.workline_runtime.lock import RedisDistributedLock
from src.workline_runtime.runtime_intent import RuntimeIntentKind
from src.workline_runtime.session_resolver import SessionResolveError  # noqa: F401
from src.workline_runtime.trace_context import TraceContext
from src.workline_runtime.utils import payload_dict

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from src.workline_runtime.utils import JsonDict


def _enqueue_outbox_dispatch() -> None:
    cast("Any", celery_app).send_task(
        "src.celery_app.tasks.sys.dispatch_system_outbox_batch",
        kwargs={"limit": 50},
    )


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
    ack_timeouts_reconciled: int
    errors: int


class DeviceHeartbeatScanResult(TypedDict):
    """设备心跳扫描结果"""

    scanned: int
    marked_offline: int


class DispatchResult(TypedDict):
    """派发结果"""

    dispatched: int
    success: int
    failed: int
    skipped: int


_WORKLINE_TASK_LOOP: asyncio.AbstractEventLoop | None = None


def _ensure_non_empty_retry_result(task_name: str, result: dict[str, int], retries: int) -> None:
    """避免“重试后空跑”被 Celery 误记为成功。"""
    if retries <= 0:
        return
    if any(value > 0 for value in result.values()):
        return
    raise RuntimeError(
        f"{task_name} returned an empty result after {retries} retries; refusing to mark it as succeeded"
    )


def _get_sync_event_loop() -> asyncio.AbstractEventLoop:
    global _WORKLINE_TASK_LOOP
    try:
        _ = asyncio.get_running_loop()
    except RuntimeError:
        if _WORKLINE_TASK_LOOP is None or _WORKLINE_TASK_LOOP.is_closed():
            _WORKLINE_TASK_LOOP = asyncio.new_event_loop()
        asyncio.set_event_loop(_WORKLINE_TASK_LOOP)
        return _WORKLINE_TASK_LOOP
    raise RuntimeError("当前事件循环正在运行，无法同步执行 Workline Celery 任务")


def _lazy_init_db(loop: asyncio.AbstractEventLoop | None = None) -> None:
    """在直接调用或 Worker 子进程未完成 signal 初始化时懒初始化数据库。"""
    if db_module.AsyncSessionLocal is not None:
        return
    from src.database.db import init_db

    init_loop = loop or _get_sync_event_loop()
    init_loop.run_until_complete(init_db())
    logger.info("✓ Workline Celery 任务懒初始化数据库连接成功")


def _run_async(coro: Awaitable[Any]) -> Any:
    """在 Celery 同步任务中运行异步函数。"""
    try:
        loop = _get_sync_event_loop()
        _lazy_init_db(loop)
    except Exception:
        with suppress(Exception):
            cast("Any", coro).close()
        raise
    return loop.run_until_complete(coro)


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


def _problem_class_for_error_domain(error_domain: ErrorDomain | None) -> ProblemClass | None:
    """为 UNKNOWN 等兜底码补充更接近现场语义的问题大类。"""
    if error_domain in {ErrorDomain.DEVICE, ErrorDomain.NETWORK}:
        return ProblemClass.HARDWARE
    return None


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


def _sync_session_contract_snapshot(session: Any, *, workline: Any) -> None:
    plugin_key = getattr(session, "plugin_key", None) or getattr(workline, "plugin_key", None)
    if isinstance(plugin_key, str) and plugin_key:
        session.plugin_key = plugin_key
    resolved_contract_version = _resolve_runtime_contract_version(workline=workline, plugin_key=plugin_key)
    if resolved_contract_version and getattr(session, "contract_version", None) != resolved_contract_version:
        session.contract_version = resolved_contract_version


def _clear_session_wait(session: Any) -> None:
    session.current_wait_type = None
    session.waiting_since = None
    session.deadline_at = None
    session.current_wait_timeout_seconds = None
    session.awaiting_command_id = None


def _clear_session_failure(session: Any) -> None:
    session.failure_domain = None
    session.failure_code = None
    session.failure_message = None


def _session_write_snapshot(session: Any) -> tuple[Any, Any]:
    """提取写入前的最小 session 快照，用于锁内防止 stale write。"""
    return (
        getattr(session, "status", None),
        getattr(session, "awaiting_command_id", None),
    )


def _wait_session_status(wait_type: str) -> str:
    if wait_type in {"EXTERNAL_HTTP", "RACK_OPERATION"}:
        return "WAITING_EXTERNAL"
    return "WAITING_DEVICE_RESULT"


def _device_map_from_roles(devices_by_role: dict[str, list[Any]]) -> dict[int, Any]:
    device_by_id: dict[int, Any] = {}
    for devices in devices_by_role.values():
        for device in devices:
            device_id = resolve_entity_id(device)
            if device_id is not None:
                device_by_id[device_id] = device
    return device_by_id


def _map_command_task_type(action: str) -> str:
    # DeviceCommand.task_type 已允许插件扩展字符串；这里必须保留插件协议值，
    # 否则下游 mock/设备和命令结果路由会看到旧的通用任务类型。
    return action


def _utc_timestamp_ms() -> int:
    return int(timezone.now_utc().timestamp() * 1000)


def _normalize_command_task_type(command_task_type: Any) -> str:
    if isinstance(command_task_type, Enum):
        return string_value(command_task_type.value)
    return string_value(command_task_type)


def _build_command_code(task_type: str) -> str:
    date_str = timezone.now_for_db().strftime("%Y%m%d")
    return f"CMD-{date_str}-{task_type}-{uuid.uuid4().hex[:8].upper()}"


_DEVICE_COMMAND_RESERVED_FIELDS = {
    "device_code",
    "command_code",
    "task_type",
    "command_type",
    "priority",
    "timeout",
    "params",
    "timestamp",
}


def _normalize_vendor_command_payload(
    parameters: Any,
    *,
    action: str,
    default_command_code: str,
) -> JsonDict:
    """归一化插件产出的设备协议 payload。
    目标是保留 vendor payload 语义，并强制收口到白皮书定义的包络：
    - 系统字段保留在顶层
    - 业务参数统一进入 params
    """
    raw_payload = dict(payload_dict(parameters))
    nested_params = payload_dict(raw_payload.get("params"))
    business_params = {key: value for key, value in raw_payload.items() if key not in _DEVICE_COMMAND_RESERVED_FIELDS}
    device_code = string_value(raw_payload.get("device_code"))
    priority = raw_payload.get("priority")
    timeout = raw_payload.get("timeout")
    timestamp = raw_payload.get("timestamp")
    payload: JsonDict = {
        "command_code": string_value(raw_payload.get("command_code")) or default_command_code,
        "task_type": string_value(raw_payload.get("task_type"), action),
        "command_type": string_value(raw_payload.get("command_type"))
        or string_value(raw_payload.get("task_type"), action),
        "priority": priority if isinstance(priority, int) else DEFAULT_COMMAND_PRIORITY,
        "timeout": timeout if isinstance(timeout, int) else DEFAULT_COMMAND_TIMEOUT_MS,
        "params": {
            **business_params,
            **nested_params,
        },
        "timestamp": timestamp if isinstance(timestamp, int) else _utc_timestamp_ms(),
    }
    if device_code:
        payload["device_code"] = device_code
    return payload


def _build_outbox_payload(command: Any, *, device_code: str | None = None) -> dict[str, Any]:
    resolved_device_code = string_value(device_code)
    normalized_task_type = _normalize_command_task_type(getattr(command, "task_type", None))
    command_params = payload_dict(getattr(command, "params", None))
    payload: dict[str, Any] = {
        "command_code": command.command_code,
        "task_type": normalized_task_type,
        "command_type": normalized_task_type,
        "priority": command.priority,
        "timeout": command.timeout_ms,
        "params": command_params,
        "timestamp": _utc_timestamp_ms(),
    }
    if resolved_device_code:
        payload["device_code"] = resolved_device_code
    return payload


async def _add_timeline(db: Any, timeline: Any, *, seq_no: int | None = None) -> int:
    from src.app.workline.services.timeline_sequence_service import add_timeline_with_sequence

    return await add_timeline_with_sequence(db, timeline, seq_no=seq_no)


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
    source_device: Any | None
    orch_result: OrchestratorResult
    current_status: str | None
    trace_id: str | None
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
    source_device: Any | None,
    orch_result: OrchestratorResult,
) -> EffectApplyContext:
    trace = TraceContext.from_runtime(
        session=session,
        workline=workline,
        inbox=inbox,
        trace_id=getattr(inbox, "trace_id", None) or getattr(session, "trace_id", None),
    )
    return {
        "db": db,
        "session": session,
        "workline": workline,
        "inbox": inbox,
        "devices_by_role": devices_by_role,
        "source_device": source_device,
        "orch_result": orch_result,
        "current_status": getattr(session, "status", None),
        "trace_id": trace.trace_id,
        "trace": trace,
        "session_ctx": _session_context(session),
        "now": timezone.now_for_db(),
        "awaiting_command_id": None,
        "awaiting_command_code": None,
        "next_timeline_seq_no": None,
    }


def _apply_context_patch(ctx: EffectApplyContext) -> None:
    """先应用 context patch，再执行后续 effect。
    第三方插件开发者只能把业务数据写进 context；runtime-owned
    字段已在 Orchestrator 阶段被拦截。
    """
    orch_result = ctx["orch_result"]
    session = ctx["session"]
    workline = ctx["workline"]
    session_ctx = ctx["session_ctx"]
    context_patch = getattr(orch_result, "context_patch", None)
    if context_patch:
        session_ctx.update(context_patch)
        _set_session_context(session, session_ctx)
        _sync_session_contract_snapshot(session, workline=workline)
        # 同步 barcode 到 session 字段，便于主数据和排障视图直接读取。
        if "barcode" in context_patch:
            barcode_value = context_patch["barcode"]
            if barcode_value:
                session.barcode = barcode_value
        return
    _sync_session_contract_snapshot(session, workline=workline)


def _sync_effect_trace_fields(ctx: EffectApplyContext) -> None:
    """同步 effect 执行后的基础追踪字段。"""
    session = ctx["session"]
    trace_id = ctx["trace_id"]
    if trace_id and getattr(session, "trace_id", None) != trace_id:
        session.trace_id = trace_id
    session.last_inbox_id = resolve_entity_id(ctx["inbox"])


def _timeline_inbox_id(ctx: EffectApplyContext) -> int | None:
    """统一提取 timeline 关联的 inbox 主键。"""
    return resolve_entity_id(ctx["inbox"])


def _effect_trace_payload(ctx: EffectApplyContext) -> dict[str, Any]:
    """构造跨 effect 共用的追踪字段。"""
    payload = payload_dict(getattr(ctx["inbox"], "payload_json", None))
    trace = ctx["trace"].with_inbox(ctx["inbox"]) if ctx.get("inbox") is not None else ctx["trace"]
    return trace.project_timeline_payload(canonical_event_type=canonical_event_type(payload))


def _decision_timeline_payload(ctx: EffectApplyContext) -> dict[str, Any]:
    """构造“插件做出决策”这一类 timeline payload。"""
    orch_result = ctx["orch_result"]
    return {
        **_effect_trace_payload(ctx),
        "transition": orch_result.transition,
        "context_patch": orch_result.context_patch or {},
    }


def _business_decision_timeline_payload(ctx: EffectApplyContext, *, decision: Any) -> dict[str, Any]:
    """构造业务判定 timeline payload。
    业务 NG 是插件给出的业务结果，不代表系统失败；这里仅沉淀可检索投影。
    """
    return {
        **_effect_trace_payload(ctx),
        "classification": decision.classification,
        "reason_code": decision.reason_code,
        "message": decision.message,
        "evidence": decision.evidence,
        "business_key": decision.business_key,
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
    if not getattr(orch_result, "transition", None):
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


async def _apply_business_decisions(ctx: EffectApplyContext) -> None:
    """记录插件业务判定，不改变失败归因。"""
    from src.app.workline.models.timeline import TimelineActionType, TimelineActorType, TimelineStage, TimelineStatus

    for decision in getattr(ctx["orch_result"], "business_decisions", None) or []:
        await _emit_timeline(
            ctx,
            stage=TimelineStage.DECISION,
            action_type=TimelineActionType.DECISION_MADE,
            payload=_business_decision_timeline_payload(ctx, decision=decision),
            actor_type=TimelineActorType.PLUGIN,
            actor_code=getattr(ctx["workline"], "plugin_key", None),
            message=decision.message,
            related_inbox_id=_timeline_inbox_id(ctx),
            status=TimelineStatus.SUCCESS,
        )


async def _apply_external_decisions(ctx: EffectApplyContext) -> None:
    """应用 EXTERNAL_HTTP decisions。
    当前仍保持最小可用实现：只落 Outbox 与对应 timeline，
    不额外引入 decision handler registry，避免 Phase 2 过度工程化。
    """
    from src.app.workline.models.timeline import TimelineActionType, TimelineActorType, TimelineStage, TimelineStatus

    db = ctx["db"]
    for decision in getattr(ctx["orch_result"], "decisions", None) or []:
        if not isinstance(decision, dict):
            continue
        decision_type = string_value(decision.get("decision_type"))
        if decision_type != EXTERNAL_HTTP_DECISION_TYPE:
            continue
        dispatch_key = string_value(decision.get("dispatch_key"))
        target_code = string_value(decision.get("target_code"))
        payload_json = payload_dict(decision.get("payload"))
        source_system = string_value(decision.get("source_system"), "EXTERNAL_SYSTEM")
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


def _build_command_create_payload(
    ctx: EffectApplyContext,
    *,
    command_intent: Any,
    vendor_payload: dict[str, Any],
    target_device_id: int,
    resolved_command_code: str,
) -> dict[str, Any]:
    """将 plugin command intent 转成 DeviceCommand 创建载荷。"""
    vendor_task_type = string_value(vendor_payload.get("task_type"), command_intent.action)
    priority_value = vendor_payload.get("priority")
    timeout_value = vendor_payload.get("timeout")
    business_params = payload_dict(vendor_payload.get("params"))
    session = ctx["session"]
    workline = ctx["workline"]
    return {
        "command_code": resolved_command_code,
        "device_id": target_device_id,
        "task_type": _map_command_task_type(vendor_task_type),
        "priority": priority_value if isinstance(priority_value, int) else DEFAULT_COMMAND_PRIORITY,
        "timeout_ms": timeout_value if isinstance(timeout_value, int) else DEFAULT_COMMAND_TIMEOUT_MS,
        "params": business_params,
        "trace_id": ctx["trace"].trace_id or ctx["trace_id"],
        "event_id": ctx["trace"].event_id,
        "causation_id": ctx["trace"].causation_id,
        "session_id": str(session.id),
        "session_id_int": session.id,
        "workline_id": session.workline_id,
        "plugin_key": getattr(session, "plugin_key", None) or getattr(workline, "plugin_key", None),
        "contract_version": getattr(session, "contract_version", None),
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
    from src.app.sys.models import SystemOutbox, SystemOutboxDispatchType, SystemOutboxTargetType

    session = ctx["session"]
    return SystemOutbox(
        session_id=session.id,
        workline_id=session.workline_id,
        dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
        dispatch_key=dispatch_key,
        target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
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
    from src.app.sys.models import SystemOutbox, SystemOutboxDispatchType, SystemOutboxTargetType

    session = ctx["session"]
    return SystemOutbox(
        session_id=session.id,
        workline_id=session.workline_id,
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        dispatch_key=f"device-command:{command.command_code}",
        target_type=SystemOutboxTargetType.DEVICE,
        target_code=device_code,
        payload_json=_build_outbox_payload(command, device_code=device_code),
    )


async def _apply_failure_transition(ctx: EffectApplyContext) -> bool:
    from src.app.workline.models.timeline import TimelineActionType, TimelineActorType, TimelineStage, TimelineStatus

    failure = getattr(ctx["orch_result"], "failure", None)
    if failure is None:
        return False
    session = ctx["session"]
    should_cancel_pending_outboxes = bool(
        getattr(session, "awaiting_command_id", None) is not None
        or getattr(session, "current_wait_type", None) == "COMMAND_RESULT"
    )
    session.status = "FAILED"
    _clear_session_wait(session)
    session.ended_at = ctx["now"]
    session.failure_domain = failure.domain
    session.failure_code = failure.code
    session.failure_message = failure.message
    session_id = resolve_entity_id(session)
    if should_cancel_pending_outboxes and session_id is not None:
        from src.app.sys.repositories import SystemOutboxRepository

        _ = await SystemOutboxRepository().cancel_active_by_session(
            ctx["db"],
            session_id=session_id,
            reason=failure.code,
        )
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
    from src.app.workline.services.ng_return_item_service import ng_return_item_service

    if not getattr(ctx["orch_result"], "complete", False):
        return False
    session = ctx["session"]
    _ = await ng_return_item_service.record_completed_ng_flow(
        ctx["db"],
        session=session,
        workline=ctx["workline"],
        inbox=ctx["inbox"],
        transition=getattr(ctx["orch_result"], "transition", None),
        occurred_at=ctx["now"],
    )
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

    wait = getattr(ctx["orch_result"], "wait", None)
    if wait is None:
        return False
    session = ctx["session"]
    resolved_wait_token = wait.wait_token
    if wait.wait_type == "COMMAND_RESULT":
        resolved_wait_token = ctx["awaiting_command_code"] or wait.wait_token
    session.status = _wait_session_status(wait.wait_type)
    session.current_wait_type = wait.wait_type
    session.waiting_since = ctx["now"]
    session.awaiting_command_id = ctx["awaiting_command_id"]
    session.current_wait_timeout_seconds = wait.deadline_seconds
    if wait.wait_type == "COMMAND_RESULT":
        session.deadline_at = None
    else:
        session.deadline_at = ctx["now"] + timedelta(seconds=wait.deadline_seconds)
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
    transition = getattr(ctx["orch_result"], "transition", None)
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
    if (
        getattr(orch_result, "transition", None)
        or getattr(orch_result, "context_patch", None)
        or getattr(orch_result, "business_decisions", None)
        or getattr(orch_result, "commands", None)
        or getattr(orch_result, "decisions", None)
    ):
        session.status = "RUNNING"
    _clear_session_wait(session)
    session.ended_at = None


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
            _lazy_init_db()
            session_local = db_module.AsyncSessionLocal
            if session_local is None:
                raise RuntimeError("数据库未初始化，请先调用 init_db()")
            self._db = session_local()
        return self._db

    def cleanup(self) -> None:
        """清理资源"""
        if self._db:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(self._db.close())
            except Exception as exc:
                logger.warning(f"关闭任务数据库会话失败: {exc}")
            finally:
                loop.close()
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


class TimeoutScanner:
    """系统 timeout inbox 扫描器内部类
    职责：
    - 扫描 ACK 后执行等待超时的 Session（deadline_at < now）
    - 为超时 Session 幂等创建系统 TIMER_TIMEOUT Inbox
    - 后续由 runtime reconciliation handler 处理，不进入插件 timeout 编排
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
           b. 继承原 Session 的 trace_id
        3. 提交数据库事务
        Args:
            db: 数据库会话
            limit: 批处理数量，默认 100
        Returns:
            扫描结果统计 {
                "scanned": 扫描的 Session 数,
                "timeouts_created": 创建的超时 Inbox 数,
                "ack_timeouts_reconciled": ACK 前超时并进入对账的指令数,
                "errors": 错误数
            }
        """
        from src.app.workline.repositories.session_repository import (
            WorklineSessionRepository,
        )
        from src.app.workline.services.inbox_service import inbox_service
        from src.app.workline.services.runtime_reconciliation_service import workline_runtime_reconciliation_service

        result: ScanResult = {
            "scanned": 0,
            "timeouts_created": 0,
            "ack_timeouts_reconciled": 0,
            "errors": 0,
        }
        from src.app.device.repositories.command_repository import DeviceCommandRepository
        from src.app.device.repositories.device_repository import device_repository
        from src.app.sys.repositories import SystemOutboxRepository

        # 获取 ACK 后执行等待超时 Session
        session_repo = WorklineSessionRepository()
        sessions = await session_repo.get_timed_out_sessions(db, limit=limit)
        result["scanned"] = len(sessions)
        for session in sessions:
            try:
                session_pk = resolve_entity_id(session)
                if session_pk is None:
                    raise ValueError("Timed out session missing primary key")
                awaiting_command_id = getattr(session, "awaiting_command_id", None)
                command = (
                    await db.get(DeviceCommand, awaiting_command_id) if isinstance(awaiting_command_id, int) else None
                )
                if enum_value(getattr(session, "status", None)) == "WAITING_DEVICE_RESULT" and command is None:
                    raise ValueError(f"Timed out session awaiting command missing: session_id={session_pk}")
                command_device_id = getattr(command, "device_id", None)
                device = await device_repository.get_by_id(db, command_device_id) if command_device_id else None
                # 幂等创建系统 timeout Inbox
                _ = await inbox_service.create_timeout_inbox(
                    db=db,
                    session_id=session_pk,
                    workline_id=session.workline_id,
                    deadline_at=session.deadline_at,
                    trace_id=session.trace_id,
                    wait_token=getattr(command, "command_code", None),
                    wait_type=getattr(session, "current_wait_type", None),
                    awaiting_command_id=awaiting_command_id,
                    command_code=getattr(command, "command_code", None),
                    device_id=command_device_id,
                    device_code=getattr(device, "device_code", None),
                    command_status=enum_value(getattr(command, "status", None)) if command is not None else None,
                    ack_received_at=getattr(command, "ack_received_at", None),
                    auto_commit=False,
                )
                result["timeouts_created"] += 1
                logger.info(f"Session {session_pk} 超时，已创建 Timeout Inbox")
            except Exception as e:
                session_pk = resolve_entity_id(session)
                logger.error(f"Session {session_pk or 'unknown'} 创建超时 Inbox 失败: {e}")
                result["errors"] += 1
        # 获取 ACK 前通信等待超时 Command：设备已经接收出站指令派发，但一直没有 ACK。
        command_repo = DeviceCommandRepository()
        outbox_repo = SystemOutboxRepository()
        ack_timeout_commands = await command_repo.get_ack_timed_out_commands(db, limit=limit)
        result["scanned"] += len(ack_timeout_commands)
        for command in ack_timeout_commands:
            command_code = getattr(command, "command_code", None)
            try:
                if not isinstance(command_code, str) or not command_code:
                    raise ValueError("ACK timed out command missing command_code")
                outbox = await outbox_repo.get_by_dispatch_key(db, f"device-command:{command_code}")
                if outbox is None:
                    raise ValueError(f"ACK timed out command outbox missing: command_code={command_code}")
                session = await workline_runtime_reconciliation_service.handle_dispatch_ack_exhausted(
                    db,
                    outbox=outbox,
                    command=command,
                    error_message="COMMAND_ACK_TIMEOUT",
                )
                if session is not None:
                    result["ack_timeouts_reconciled"] += 1
                    logger.info(f"Command {command_code} ACK 等待超时，已进入 runtime reconciliation")
            except Exception as e:
                logger.error(f"Command {command_code or 'unknown'} ACK 等待超时处理失败: {e}")
                result["errors"] += 1
        # 提交事务
        await db.commit()
        from src.app.sys.services.event_stream_service import publish_deferred_sse_events

        await publish_deferred_sse_events(db)
        return result


class DeviceHeartbeatScanner:
    """设备心跳超时扫描器。
    设备任务状态仍由 DeviceCommand 记录；这里仅维护设备健康/占用投影，
    将心跳超时的 IDLE/RUNNING 设备标记为 OFFLINE。
    """

    @staticmethod
    async def _scan(
        db: Any,
        *,
        threshold_seconds: int = DEVICE_HEARTBEAT_TIMEOUT_SECONDS,
        limit: int = 100,
    ) -> DeviceHeartbeatScanResult:
        from src.app.device.services import device_service

        marked_offline = await device_service.mark_stale_heartbeats_offline(
            db,
            threshold_seconds=threshold_seconds,
            limit=limit,
            auto_commit=False,
        )
        await db.commit()
        from src.app.sys.services.event_stream_service import publish_deferred_sse_events

        await publish_deferred_sse_events(db)
        return {
            "scanned": marked_offline,
            "marked_offline": marked_offline,
        }


@celery_app.task(
    name="src.celery_app.tasks.workline.process_inbox_batch",
    base=WorklineTask,
    bind=True,
    max_retries=3,
    default_retry_delay=5,
)
def process_inbox_batch(self: WorklineTask, limit: int = 10) -> ProcessResult:
    """批量处理 Inbox 消息 (Celery 任务入口)
    从数据库获取 status='NEW' 的 Inbox 消息，调用 InboxBatchProcessor.process_batch() 执行处理。
    处理流程（详见 InboxBatchProcessor）：
    1. 批量获取待处理消息（limit 限制）
    2. 并发控制：标记为 PROCESSING，防止重复处理
    3. 入站后 malformed gate：拦截完全空的 SCAN_COMPLETED payload
    4. 加载关联实体：session/workline/device/devices_by_role
    5. 调用 OrchestratorService 执行编排
    6. 应用编排结果：command/outbox/timeline
    7. 更新状态：PROCESSED/FAILED
    执行模式：
    - bind=True：任务方法接收 self（WorklineTask 实例）
    - max_retries=3：失败后自动重试最多 3 次
    - default_retry_delay=5：重试间隔 5 秒（指数退避）
    调用链：
        process_inbox_batch() → InboxBatchProcessor.process_batch()
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
            from src.app.workline.services.inbox_batch_processor import InboxBatchProcessor
            from src.app.workline.services.write_back_service import orchestrator_write_back_service

            processor = InboxBatchProcessor(write_back_service=orchestrator_write_back_service)
            return await processor.process_batch(db, limit=limit)

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
       b. 继承原 Session 的 trace_id
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
            "ack_timeouts_reconciled": ACK 前超时并进入对账的指令数,
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


@celery_app.task(
    name="src.celery_app.tasks.workline.scan_device_heartbeats_batch",
    base=WorklineTask,
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def scan_device_heartbeats_batch(
    self: WorklineTask,
    threshold_seconds: int = DEVICE_HEARTBEAT_TIMEOUT_SECONDS,
    limit: int = 100,
) -> DeviceHeartbeatScanResult:
    """扫描设备心跳超时，将已有心跳且超时的设备标记为 OFFLINE。"""
    logger.info(f"开始扫描设备心跳超时, threshold_seconds={threshold_seconds}, limit={limit}")

    async def _scan() -> DeviceHeartbeatScanResult:
        async with self.db as db:
            return await DeviceHeartbeatScanner._scan(db, threshold_seconds=threshold_seconds, limit=limit)

    try:
        result = _run_async(_scan())
        _ensure_non_empty_retry_result(
            "scan_device_heartbeats_batch",
            result,
            int(getattr(self.request, "retries", 0) or 0),
        )
        logger.info(f"设备心跳扫描完成: {result}")
        return result
    except Exception as e:
        logger.error(f"设备心跳扫描失败: {e}")
        countdown = 60 * (2**self.request.retries)
        raise self.retry(exc=e, countdown=countdown) from None


# 历史测试/脚本兼容入口
scan_timeouts = TimeoutScanner
device_heartbeat_scanner = DeviceHeartbeatScanner
# ============================================
# 导出
# ============================================
__all__ = [
    # Celery 任务入口（公共 API）
    "device_heartbeat_scanner",
    "process_inbox_batch",
    "process_signal",
    "scan_device_heartbeats_batch",
    "scan_timeouts",
    "scan_timeouts_batch",
]


@celery_app.task(
    name="src.celery_app.tasks.workline.process_signal",
    base=WorklineTask,
    bind=True,
    max_retries=3,
    default_retry_delay=10,
)
def process_signal(self: WorklineTask, payload: dict[str, Any]) -> None:
    logger.info(f"workline process_signal 接收到 payload: {payload}")
