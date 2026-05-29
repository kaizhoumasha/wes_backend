import uuid
from enum import Enum
from typing import TYPE_CHECKING, Any, TypedDict, cast

from src.app.workline.constants import DEFAULT_COMMAND_PRIORITY, DEFAULT_COMMAND_TIMEOUT_MS, EXTERNAL_HTTP_DECISION_TYPE
from src.app.workline.domain.services.session_lifecycle_service import workline_session_lifecycle_service
from src.app.workline.services.device_command_gateway import (
    _DeviceCommandGovernanceError,  # noqa: F401 - RuntimeIntentEffectApplier accesses via module alias
    _enforce_device_command_governance,  # noqa: F401 - RuntimeIntentEffectApplier accesses via module alias
)
from src.utils.timezone import timezone
from src.utils.value_normalization import canonical_event_type, resolve_entity_id, string_value
from src.workline_plugin_registry import get_plugin_contract_version
from src.workline_runtime.orchestrator import OrchestratorResult
from src.workline_runtime.trace_context import TraceContext
from src.workline_runtime.utils import payload_dict

if TYPE_CHECKING:
    from src.workline_runtime.utils import JsonDict


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
) -> "JsonDict":
    """归一化插件产出的设备协议 payload。"""
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
    """Effect 执行上下文。"""

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
    """统一 timeline 生成入口。"""
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
    """先应用 context patch，再执行后续 effect。"""
    orch_result = ctx["orch_result"]
    session = ctx["session"]
    workline = ctx["workline"]
    session_ctx = ctx["session_ctx"]
    context_patch = getattr(orch_result, "context_patch", None)
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


def _decision_timeline_payload(ctx: EffectApplyContext) -> dict[str, Any]:
    """构造“插件做出决策”这一类 timeline payload。"""
    orch_result = ctx["orch_result"]
    return {
        **_effect_trace_payload(ctx),
        "transition": orch_result.transition,
        "context_patch": orch_result.context_patch or {},
    }


def _business_decision_timeline_payload(ctx: EffectApplyContext, *, decision: Any) -> dict[str, Any]:
    """构造业务判定 timeline payload。"""
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
    """记录插件做出的 transition 决策。"""
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
    """应用 EXTERNAL_HTTP decisions。"""
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
    """将 external decision 投影为 Outbox 模型。"""
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
    workline_session_lifecycle_service.fail(
        session,
        occurred_at=ctx["now"],
        failure_domain=failure.domain,
        failure_code=failure.code,
        failure_message=failure.message,
    )
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
    workline_session_lifecycle_service.cancel(session, occurred_at=ctx["now"])
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
    workline_session_lifecycle_service.complete(session, occurred_at=ctx["now"])
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
    workline_session_lifecycle_service.start_wait(
        session,
        wait_type=wait.wait_type,
        occurred_at=ctx["now"],
        awaiting_command_id=ctx["awaiting_command_id"],
        deadline_seconds=wait.deadline_seconds,
    )
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
    """应用非终态 transition。"""
    session = ctx["session"]
    transition = getattr(ctx["orch_result"], "transition", None)
    if transition == "manual_hold":
        workline_session_lifecycle_service.manual_hold(session, occurred_at=ctx["now"])
        return True
    if transition == "manual_resume":
        workline_session_lifecycle_service.resume(session)
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
        workline_session_lifecycle_service.running(session)
    _clear_session_wait(session)
    session.ended_at = None


class OrchestratorWriteBackService:
    async def write_back(
        self,
        db: Any,
        *,
        session: Any,
        workline: Any,
        inbox: Any,
        devices_by_role: dict[str, list[Any]],
        source_device: Any | None,
        orch_result: OrchestratorResult,
    ) -> None:
        """应用 OrchestratorResult 到 Session / Command / Outbox / Timeline。"""
        ctx = _build_effect_apply_context(
            db=db,
            session=session,
            workline=workline,
            inbox=inbox,
            devices_by_role=devices_by_role,
            source_device=source_device,
            orch_result=orch_result,
        )
        db.add(session)

        from src.workline_runtime.runtime_intent_effects import RuntimeIntentEffectApplier

        await RuntimeIntentEffectApplier().apply(ctx, orch_result.intents or [])


orchestrator_write_back_service = OrchestratorWriteBackService()
