from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypedDict, cast

from src.app.runtime.orchestration.trace_context import TraceContext
from src.app.workline.domain.services.session_lifecycle_service import workline_session_lifecycle_service
from src.app.workline.utils import payload_dict
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


def _clear_session_wait(session: Any) -> None:
    workline_session_lifecycle_service.clear_wait(session)


def _clear_session_failure(session: Any) -> None:
    workline_session_lifecycle_service.clear_failure(session)


def _wait_session_status(wait_type: str) -> str:
    if wait_type in {"EXTERNAL_HTTP", "RACK_OPERATION"}:
        return "WAITING_EXTERNAL"
    return "WAITING_DEVICE_RESULT"


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
    session_ctx = ctx["session_ctx"]
    context_patch = ctx["effect_state"].context_patch
    if context_patch:
        session_ctx.update(context_patch)
        _set_session_context(session, session_ctx)
        if "barcode" in context_patch:
            barcode_value = context_patch["barcode"]
            if barcode_value:
                session.barcode = barcode_value
        return


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
    workline_session_lifecycle_service.fail(
        session,
        occurred_at=ctx["now"],
        failure_domain=failure.domain,
        failure_code=failure.code,
        failure_message=failure.message,
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
    from src.app.runtime.orchestration.models.session import SessionStatus
    from src.app.runtime.orchestration.repositories.session_repository import WorklineSessionRepository
    from src.utils.value_normalization import resolve_required_pk

    session = ctx["session"]
    session_already_completed = string_value(getattr(session, "status", None)) == SessionStatus.COMPLETED.value
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
