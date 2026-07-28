import uuid
from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
from typing import TYPE_CHECKING, Any, TypedDict, cast

from src.app.runtime.orchestration.services.device_command_gateway import (
    _DeviceCommandGovernanceError,  # noqa: F401 - RuntimeIntentEffectApplier accesses via module alias
    _enforce_device_command_governance,  # noqa: F401 - RuntimeIntentEffectApplier accesses via module alias
)
from src.app.runtime.workline_plugins.registry import get_workline_contract_version
from src.app.workline.constants import DEFAULT_COMMAND_PRIORITY, DEFAULT_COMMAND_TIMEOUT_MS
from src.app.workline.domain.services.session_lifecycle_service import workline_session_lifecycle_service
from src.app.workline.trace_context import TraceContext
from src.app.workline.utils import payload_dict
from src.utils.timezone import timezone
from src.utils.value_normalization import canonical_event_type, resolve_entity_id, string_value

if TYPE_CHECKING:
    from src.app.workline.utils import JsonDict


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
    contract_version = get_workline_contract_version(plugin_key)
    return contract_version if isinstance(contract_version, str) and contract_version else None


def _sync_session_contract_snapshot(session: Any, *, workline: Any) -> None:
    plugin_key = getattr(session, "plugin_key", None) or getattr(workline, "plugin_key", None)
    if isinstance(plugin_key, str) and plugin_key:
        session.plugin_key = plugin_key
    resolved_contract_version = _resolve_runtime_contract_version(workline=workline, plugin_key=plugin_key)
    if resolved_contract_version and getattr(session, "contract_version", None) != resolved_contract_version:
        session.contract_version = resolved_contract_version


def _clear_session_wait(session: Any) -> None:
    workline_session_lifecycle_service.clear_wait(session)


def _clear_session_failure(session: Any) -> None:
    workline_session_lifecycle_service.clear_failure(session)


def _wait_session_status(wait_type: str) -> str:
    if wait_type in {"EXTERNAL_HTTP", "RACK_OPERATION"}:
        return "WAITING_EXTERNAL"
    return "WAITING_DEVICE_RESULT"


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


def _normalize_command_code_segment(value: str) -> str:
    normalized_chars: list[str] = []
    previous_was_separator = False
    for char in string_value(value).upper():
        is_allowed = "A" <= char <= "Z" or "0" <= char <= "9" or char == "_"
        if is_allowed:
            normalized_chars.append(char)
            previous_was_separator = False
        elif not previous_was_separator:
            normalized_chars.append("_")
            previous_was_separator = True
    normalized = "".join(normalized_chars).strip("_")
    return normalized[:50] or "UNKNOWN"


def _build_command_code(task_type: str, *, session_id: int | None = None) -> str:
    date_str = timezone.now_for_db().strftime("%Y%m%d")
    session_segment = f"S{session_id}" if session_id is not None else "SNA"
    task_segment = _normalize_command_code_segment(task_type)
    return f"CMD-{date_str}-{session_segment}-{task_segment}-{uuid.uuid4().hex[:8].upper()}"


def _resolve_command_correlation_id(ctx: "EffectApplyContext") -> str | None:
    """解析 DeviceCommand 目标态 correlation key, 不用 trace/session alias 伪造。"""
    raw_value = ctx.get("correlation_id")
    if isinstance(raw_value, str) and raw_value:
        return raw_value
    return None


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
) -> "JsonDict":
    """归一化插件产出的设备协议 payload。"""
    raw_payload = dict(payload_dict(parameters))
    if "command_code" in raw_payload:
        raise ValueError("command_code is generated by WES and must not be provided by plugin payload")
    nested_params = payload_dict(raw_payload.get("params"))
    if "command_code" in nested_params:
        raise ValueError("command_code is generated by WES and must not be provided by plugin payload")
    business_params = {key: value for key, value in raw_payload.items() if key not in _DEVICE_COMMAND_RESERVED_FIELDS}
    device_code = string_value(raw_payload.get("device_code"))
    priority = raw_payload.get("priority")
    timeout = raw_payload.get("timeout")
    timestamp = raw_payload.get("timestamp")
    payload: JsonDict = {
        "command_code": default_command_code,
        "task_type": string_value(raw_payload.get("task_type"), action),
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
        "priority": command.priority,
        "timeout": command.timeout_ms,
        "params": command_params,
        "timestamp": _utc_timestamp_ms(),
    }
    if resolved_device_code:
        payload["device_code"] = resolved_device_code
    return payload


async def _add_timeline(db: Any, timeline: Any, *, seq_no: int | None = None) -> int:
    from src.app.runtime.orchestration.services.trace.timeline_sequence_service import add_timeline_with_sequence

    return await add_timeline_with_sequence(db, timeline, seq_no=seq_no)


@dataclass(frozen=True, slots=True)
class EffectFailure:
    """Generated effect 失败证据。"""

    domain: str
    code: str
    message: str


@dataclass(slots=True)
class EffectApplyState:
    """Generated effect applier 在单次事务内允许修改的最小状态。"""

    context_patch: dict[str, Any] = field(default_factory=dict)
    failure: EffectFailure | None = None
    skip_next_material_unit_intent: bool = False


class EffectApplyContext(TypedDict):
    """Effect 执行上下文。"""

    db: Any
    session: Any
    workline: Any
    inbox: Any
    devices_by_role: dict[str, list[Any]]
    source_device: Any | None
    effect_state: EffectApplyState
    current_status: str | None
    trace_id: str | None
    trace: TraceContext
    session_ctx: dict[str, Any]
    now: Any
    awaiting_device_command_pk: int | None
    awaiting_command_code: str | None
    next_timeline_seq_no: int | None


async def _emit_timeline(ctx: EffectApplyContext, **kwargs: Any) -> None:
    """统一 timeline 生成入口。"""
    from src.app.runtime.orchestration.timeline_generator import timeline_generator

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


def _apply_context_patch(ctx: EffectApplyContext) -> None:
    """先应用 context patch，再执行后续 effect。"""
    session = ctx["session"]
    workline = ctx["workline"]
    session_ctx = ctx["session_ctx"]
    context_patch = ctx["effect_state"].context_patch
    if context_patch:
        session_ctx.update(context_patch)
        _set_session_context(session, session_ctx)
        _sync_session_contract_snapshot(session, workline=workline)
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
    task_type: str,
    parameters: dict[str, Any],
    dispatch_key: str,
) -> dict[str, Any]:
    """构造命令派发阶段的 timeline payload。"""
    return {
        **_effect_trace_payload(ctx),
        "command_code": command_code,
        "task_type": task_type,
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


def _build_command_create_payload(
    ctx: EffectApplyContext,
    *,
    action: str,
    vendor_payload: dict[str, Any],
    target_device_id: int,
    resolved_command_code: str,
) -> dict[str, Any]:
    """将 plugin command intent 转成 DeviceCommand 创建载荷。"""
    vendor_task_type = string_value(vendor_payload.get("task_type"), action)
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
        "correlation_id": _resolve_command_correlation_id(ctx),
        "workline_id": session.workline_id,
        "plugin_key": getattr(session, "plugin_key", None) or getattr(workline, "plugin_key", None),
        "contract_version": getattr(session, "contract_version", None),
    }


def _build_command_outbox_model(ctx: EffectApplyContext, *, command: Any, device_code: str) -> Any:
    """将已创建的 DeviceCommand 投影为设备派发 Outbox。"""
    from src.app.sys.models import SystemOutbox, SystemOutboxDispatchType, SystemOutboxTargetType

    session = ctx["session"]
    return SystemOutbox(
        session_id=session.id,
        workline_id=session.workline_id,
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        dispatch_key=f"device-command:{command.command_code}",
        target_type=SystemOutboxTargetType.DEVICE,
        target_code=device_code,
        provider_profile_identity="ecs.device-command.v1",
        operation_identity="device.command",
        payload_json=_build_outbox_payload(command, device_code=device_code),
    )


async def _apply_failure_transition(ctx: EffectApplyContext) -> bool:
    from src.app.runtime.orchestration.models.timeline import (
        TimelineActionType,
        TimelineActorType,
        TimelineStage,
        TimelineStatus,
    )

    failure = ctx["effect_state"].failure
    if failure is None:
        return False
    session = ctx["session"]
    should_cancel_pending_outboxes = bool(
        getattr(session, "awaiting_device_command_code", None) is not None
        or getattr(session, "current_wait_type", None) == "COMMAND_RESULT"
    )
    workline_session_lifecycle_service.fail(
        session,
        occurred_at=ctx["now"],
        failure_domain=failure.domain,
        failure_code=failure.code,
        failure_message=failure.message,
    )
    session_id = resolve_entity_id(session)
    if should_cancel_pending_outboxes and session_id is not None:
        from src.app.runtime.orchestration.services.system_outbox_cancellation_service import (
            system_outbox_cancellation_service,
        )

        _ = await system_outbox_cancellation_service.cancel_active_by_session(
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


async def _emit_completion_timeline(ctx: EffectApplyContext) -> None:
    from src.app.runtime.orchestration.models.timeline import TimelineActionType, TimelineActorType, TimelineStage

    await _emit_timeline(
        ctx,
        stage=TimelineStage.COMPLETE,
        action_type=TimelineActionType.SESSION_COMPLETED,
        from_status=ctx["current_status"],
        to_status="COMPLETED",
        actor_type=TimelineActorType.ORCHESTRATOR,
        related_inbox_id=_timeline_inbox_id(ctx),
    )


async def _apply_completion_transition(ctx: EffectApplyContext) -> bool:
    from src.app.runtime.capabilities.material_flow.ng_return_item_service import (
        NgMaterialConflictError,
        ng_return_item_service,
    )
    from src.app.runtime.orchestration.models.session import SessionStatus
    from src.app.runtime.orchestration.models.timeline import (
        TimelineActionType,
        TimelineActorType,
        TimelineStage,
        TimelineStatus,
    )
    from src.app.runtime.orchestration.repositories.session_repository import WorklineSessionRepository
    from src.app.runtime.orchestration.services.hold.runtime_hold_creation_service import runtime_hold_creation_service
    from src.utils.value_normalization import resolve_required_pk

    session = ctx["session"]
    session_already_completed = string_value(getattr(session, "status", None)) == SessionStatus.COMPLETED.value
    try:
        _ = await ng_return_item_service.record_completed_ng_flow(
            ctx["db"],
            session=session,
            workline=ctx["workline"],
            inbox=ctx["inbox"],
            transition=None,
            occurred_at=ctx["now"],
        )
    except NgMaterialConflictError as exc:
        if session_already_completed:
            await _emit_completion_timeline(ctx)
            return True
        evidence = dict(exc.evidence)
        source_event_id = _ng_material_conflict_source_event_id(
            exc=exc,
            evidence=evidence,
            session=session,
            inbox=ctx["inbox"],
        )
        hold = await runtime_hold_creation_service.create_for_resource_reconciliation(
            ctx["db"],
            workline_id=resolve_required_pk(ctx["workline"], "workline"),
            session_id=resolve_required_pk(session, "session"),
            trace_id=ctx.get("trace_id")
            or getattr(ctx["inbox"], "trace_id", None)
            or getattr(session, "trace_id", None),
            plugin_key=getattr(session, "plugin_key", None) or getattr(ctx["workline"], "plugin_key", None),
            contract_version=getattr(session, "contract_version", None)
            or getattr(ctx["workline"], "contract_version", None),
            source_reason=exc.reason_code,
            source_event_id=source_event_id,
            evidence=evidence,
        )
        session_context = _session_context(session)
        session_context["ng_material_conflict"] = {
            "reason_code": exc.reason_code,
            "material_identity_key": exc.material_identity_key,
            "runtime_hold_id": getattr(hold, "id", None),
            "evidence": evidence,
        }
        _set_session_context(session, session_context)
        workline_session_lifecycle_service.manual_hold(session, occurred_at=ctx["now"])
        await _emit_timeline(
            ctx,
            stage=TimelineStage.MANUAL,
            action_type=TimelineActionType.MANUAL_HOLD,
            payload=session_context["ng_material_conflict"],
            from_status=ctx["current_status"],
            to_status=session.status,
            actor_type=TimelineActorType.ORCHESTRATOR,
            related_inbox_id=_timeline_inbox_id(ctx),
            status=TimelineStatus.PENDING,
            message="NG 物料已存在不同来源回流项，进入人工处理",
        )
        return True
    if session_already_completed:
        await _emit_completion_timeline(ctx)
        return True
    workline_session_lifecycle_service.complete(session, occurred_at=ctx["now"])
    await WorklineSessionRepository().persist_completed(
        ctx["db"],
        session_id=resolve_required_pk(session, "session"),
        occurred_at=ctx["now"],
        context_json=getattr(session, "context_json", None),
    )
    await _emit_completion_timeline(ctx)
    return True


def _ng_material_conflict_source_event_id(
    *,
    exc: Any,
    evidence: dict[str, Any],
    session: Any,
    inbox: Any,
) -> str:
    source_event_id = string_value(evidence.get("new_source_event_id"))
    if source_event_id:
        return source_event_id
    command_or_inbox_id = (
        evidence.get("new_source_command_id")
        or getattr(session, "awaiting_device_command_code", None)
        or getattr(inbox, "id", None)
        or "unknown"
    )
    identity_hash = sha256(string_value(exc.material_identity_key).encode("utf-8")).hexdigest()[:16]
    return f"ng-material-conflict:{getattr(session, 'id', 'unknown')}:{command_or_inbox_id}:{identity_hash}"
