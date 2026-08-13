# 旧 runtime 镜像实现:src.workline_runtime.runtime_intent_effects 的平级副本
# 旧 runtime 入口删除后,本模块承载正式实现。
# 自引用 src.workline_runtime.{effect_result, material_target_resolver,
# resource_wait_evidence, runtime_intent} 已重定向到本目录 / 本目录 bridge。

"""RuntimeIntent 到 Session / Command / Outbox / Timeline 的 effect 落地。"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, cast

from pydantic import BaseModel
from sqlmodel import select

from src.app.runtime.capabilities.material_flow.runtime_identity import RECONCILIATION_RUNTIME_SOURCE
from src.app.runtime.orchestration.effect_result import RuntimeIntentEffectResult
from src.app.runtime.orchestration.models.session import SessionStatus
from src.app.runtime.orchestration.resource_wait_evidence_bridge import ResourceWaitEvidence
from src.app.runtime.orchestration.runtime_intent import (
    BlockScope,
    RuntimeIntent,
    RuntimeIntentKind,
)
from src.utils.timezone import timezone
from src.utils.value_normalization import coerce_optional_str, optional_int, resolve_required_pk, string_value

logger = logging.getLogger(__name__)

_SUPPORTED_INTENT_KINDS = {
    RuntimeIntentKind.SYSTEM_CAPABILITY,
    RuntimeIntentKind.UPDATE_CONTEXT,
    RuntimeIntentKind.MARK_NG,
    RuntimeIntentKind.EXTERNAL_REQUEST,
    RuntimeIntentKind.RESOURCE_FACT,
    RuntimeIntentKind.RESOURCE_RESERVATION,
    RuntimeIntentKind.RESOURCE_WAIT,
    RuntimeIntentKind.COMPLETE,
    RuntimeIntentKind.CANCEL,
    RuntimeIntentKind.BLOCK,
    RuntimeIntentKind.CONTINUE_NEXT,
    RuntimeIntentKind.CREATE_MATERIAL_UNIT,
    RuntimeIntentKind.UPDATE_MATERIAL_UNIT_STATUS,
}
_TERMINAL_INTENT_KINDS = {
    RuntimeIntentKind.COMPLETE,
    RuntimeIntentKind.CANCEL,
    RuntimeIntentKind.BLOCK,
    RuntimeIntentKind.RESOURCE_WAIT,
}


def _merge_context_patch(ctx: Any, patch: dict[str, Any]) -> None:
    if not patch:
        return

    ctx["effect_state"].context_patch.update(patch)


def _is_command_producing_intent(intent: RuntimeIntent) -> bool:
    return intent.kind == RuntimeIntentKind.EXTERNAL_REQUEST


def _validate_runtime_intents(intents: list[RuntimeIntent]) -> None:
    """在任何持久化副作用前验证 RuntimeIntent 组合。"""

    command_producing_count = 0
    command_producing_seen = False
    for index, intent in enumerate(intents):
        if intent.kind not in _SUPPORTED_INTENT_KINDS:
            raise ValueError(f"unsupported RuntimeIntent kind: {intent.kind.value}")

        if _is_command_producing_intent(intent):
            command_producing_count += 1
            command_producing_seen = True

        if intent.kind in _TERMINAL_INTENT_KINDS and index != len(intents) - 1:
            raise ValueError("terminal RuntimeIntent must be final intent")
        if intent.kind in _TERMINAL_INTENT_KINDS and command_producing_seen:
            raise ValueError("terminal RuntimeIntent cannot follow command-producing RuntimeIntent")

    if command_producing_count > 1:
        raise ValueError("multiple command-producing RuntimeIntents are not supported in one callback")


def _ctx_trace_id(ctx: Mapping[str, Any]) -> Any | None:
    trace = ctx.get("trace")
    return getattr(trace, "trace_id", None) or ctx.get("trace_id")


def _result_status_value(result: Any) -> str | None:
    status = getattr(result, "status", None)
    raw = getattr(status, "value", status)
    return raw if isinstance(raw, str) else None


def _state_value(value: Any) -> str:
    raw = getattr(value, "value", value)
    return string_value(raw, "")


def _apply_material_unit_status_write(
    ctx: Mapping[str, Any],
    material_unit: Any,
    *,
    from_state: str | None,
    to_status: Any,
) -> None:
    _ = ctx
    to_state = _state_value(to_status)
    if from_state is not None and to_state == "RECONCILING" and from_state != "RECONCILING":
        try:
            material_unit.reconciliation_from_state = type(to_status)(from_state)
        except (TypeError, ValueError):
            material_unit.reconciliation_from_state = from_state

    material_unit.status = to_state


# 终态 Session 可被其它 Session 回收其料盘所有权（正常 handoff）；
# 非终态 Session 仍持有料盘时，复用属于跨线并发或重复 claim，必须拒绝。
# 从 SessionStatus 枚举派生，避免硬编码字符串与枚举漂移；漂移校验在模块加载时执行。
_ACTIVE_SESSION_STATUSES: frozenset[str] = frozenset(
    {
        SessionStatus.NEW.value,
        SessionStatus.RUNNING.value,
        SessionStatus.WAITING_DEVICE_RESULT.value,
        SessionStatus.WAITING_EXTERNAL.value,
        SessionStatus.MANUAL_HOLD.value,
    }
)


async def _reject_reuse_when_owned_by_active_session(
    db: Any,
    material_unit: Any,
    *,
    current_session_id: int,
) -> None:
    """扫码 create 路径仅在料盘明确归属非终态 Session 时拒绝复用。"""
    owner_session_id = optional_int(getattr(material_unit, "current_session_id", None))
    if owner_session_id is None or owner_session_id == current_session_id:
        return
    if not hasattr(db, "execute"):
        return
    from src.app.runtime.orchestration.models.session import WorklineSession

    result = await db.execute(select(WorklineSession.status).where(WorklineSession.id == owner_session_id).limit(1))
    scalars = getattr(result, "scalars", None)
    if not callable(scalars):
        return
    row = scalars().first()
    owner_status = string_value(row, "")
    if owner_status in _ACTIVE_SESSION_STATUSES:
        raise ValueError(
            f"material unit {getattr(material_unit, 'id', None)} (pkg_code="
            f"{getattr(material_unit, 'pkg_code', None)}) is still owned by active session "
            f"{owner_session_id} (status={owner_status}), refuse silent takeover"
        )


_PENDING_CLEANUP_IDS_CONTEXT_KEY = "_runtime_pending_material_unit_cleanup_ids"


def _pending_cleanup_ids_from_session(session: Any) -> set[int]:
    context = getattr(session, "context_json", None) or {}
    raw = context.get("_runtime_pending_material_unit_cleanup_ids") if isinstance(context, Mapping) else None
    if not raw:
        return set()
    return {int(value) for value in raw if optional_int(value) is not None}


def _persist_pending_cleanup_ids(session: Any, cleanup_ids: set[int]) -> None:
    context = dict(getattr(session, "context_json", None) or {})
    context["_runtime_pending_material_unit_cleanup_ids"] = sorted(cleanup_ids)
    session.context_json = context


def _is_reconciling_result(result: Any) -> bool:
    return _result_status_value(result) == "RECONCILING"


def _is_duplicate_result(result: Any) -> bool:
    return _result_status_value(result) == "DUPLICATE"


class RuntimeIntentEffectApplier:
    def __init__(
        self,
        *,
        resource_projection_service: Any | None = None,
        bin_cell_reservation_service: Any | None = None,
        system_capability_effect_service: Any | None = None,
        material_unit_mutation_service: Any | None = None,
    ) -> None:
        self._resource_projection_service = resource_projection_service
        self._bin_cell_reservation_service = bin_cell_reservation_service
        self._system_capability_effect_service = system_capability_effect_service
        self._material_unit_mutation_service = material_unit_mutation_service

    async def persist_business_reject(self, ctx: Any, evidence: object) -> bool:
        """把 rollback 后的 typed 拒绝补偿委托给对应 System Capability。"""

        service = self._system_capability_effect_service
        if service is None:
            from src.app.runtime.orchestration.services.intent import system_capability_effect_service

            service = system_capability_effect_service
        return bool(await service.persist_business_reject(ctx, evidence))

    async def apply(self, ctx: Any, intents: list[RuntimeIntent]) -> RuntimeIntentEffectResult:  # noqa: PLR0912
        _validate_runtime_intents(intents)

        from src.app.workline.services import write_back_service as workline_effects

        workline_effects._sync_effect_trace_fields(ctx)
        if not intents:
            await self._apply_noop_completion(ctx)
            return RuntimeIntentEffectResult.processed()

        outbox_dispatch_targets = set()
        effect_state = ctx["effect_state"]
        for intent in intents:
            skip_material_unit_intent = effect_state.skip_next_material_unit_intent and intent.kind in {
                RuntimeIntentKind.CREATE_MATERIAL_UNIT,
                RuntimeIntentKind.UPDATE_MATERIAL_UNIT_STATUS,
            }
            if effect_state.skip_next_material_unit_intent and not skip_material_unit_intent:
                effect_state.skip_next_material_unit_intent = False

            if intent.kind == RuntimeIntentKind.SYSTEM_CAPABILITY:
                service = self._system_capability_effect_service
                if service is None:
                    from src.app.runtime.orchestration.services.intent import system_capability_effect_service

                    service = system_capability_effect_service
                result = await service.apply(ctx, intent)
                outbox_dispatch_targets.update(result.outbox_dispatch_targets)
                ctx.setdefault("system_capability_outcomes", []).append(result)
                from src.app.runtime.system_capabilities.outcomes import (
                    BusinessReject,
                    ContractViolation,
                    RetryableFailure,
                )

                if isinstance(result.outcome, RetryableFailure):
                    raise RuntimeError(f"system capability retryable failure: {result.outcome.error_code}")
                if isinstance(result.outcome, ContractViolation):
                    raise ValueError(f"system capability contract violation: {result.outcome.error_code}")
                if isinstance(result.outcome, BusinessReject):
                    evidence = getattr(result, "evidence", None)
                    if isinstance(evidence, BaseModel):
                        evidence_payload = evidence.model_dump(mode="json")
                    else:
                        evidence_payload = {
                            "capability_key": str(intent.capability_key),
                            "contract_version": str(intent.contract_version),
                            "operation_key": str(intent.operation_key),
                            "idempotency_key": str(intent.idempotency_key or intent.operation_key),
                            "payload_hash": str(intent.payload_hash),
                            "outcome_kind": result.outcome.kind,
                            "outcome_code": result.outcome.reason_code,
                            "outcome": result.outcome.model_dump(mode="json"),
                            "occurred_at_ms": int(timezone.now_utc().timestamp() * 1000),
                        }
                    return RuntimeIntentEffectResult.business_rejected(evidence_payload)
                continue

            if intent.kind == RuntimeIntentKind.UPDATE_CONTEXT:
                await self._apply_update_context(ctx, intent, workline_effects)
                continue

            if intent.kind == RuntimeIntentKind.MARK_NG:
                await self._apply_mark_ng(ctx, intent)
                continue

            if intent.kind == RuntimeIntentKind.EXTERNAL_REQUEST:
                await self._apply_external_request(ctx, intent)
                continue

            if intent.kind == RuntimeIntentKind.RESOURCE_FACT:
                result = await self._apply_resource_fact(ctx, intent)
                if _is_reconciling_result(result):
                    await self._apply_resource_reconciliation_hold(ctx, result)
                    return RuntimeIntentEffectResult.processed()
                effect_state.skip_next_material_unit_intent = _is_duplicate_result(result)
                continue

            if intent.kind == RuntimeIntentKind.RESOURCE_RESERVATION:
                result = await self._apply_resource_reservation(ctx, intent)
                if _is_reconciling_result(result):
                    await self._apply_current_material_unit_reconciliation_status(ctx)
                    return RuntimeIntentEffectResult.processed()
                continue

            if intent.kind == RuntimeIntentKind.RESOURCE_WAIT:
                return await self._apply_resource_wait(ctx, intent)

            if intent.kind == RuntimeIntentKind.CREATE_MATERIAL_UNIT:
                if skip_material_unit_intent:
                    effect_state.skip_next_material_unit_intent = False
                    continue
                await self._apply_create_material_unit(ctx, intent)
                continue

            if intent.kind == RuntimeIntentKind.UPDATE_MATERIAL_UNIT_STATUS:
                if skip_material_unit_intent:
                    effect_state.skip_next_material_unit_intent = False
                    continue
                await self._apply_update_material_unit_status(ctx, intent)
                continue

            if intent.kind == RuntimeIntentKind.COMPLETE:
                _merge_context_patch(ctx, intent.context_patch)
                workline_effects._apply_context_patch(ctx)
                workline_effects._clear_session_failure(ctx["session"])
                _ = await workline_effects._apply_completion_transition(ctx)
                await self._cleanup_completed_material_unit(ctx)
                continue

            if intent.kind == RuntimeIntentKind.CANCEL:
                await self._apply_cancel(ctx, intent)
                continue

            if intent.kind == RuntimeIntentKind.BLOCK:
                await self._apply_block(ctx, intent)
                continue

            if intent.kind == RuntimeIntentKind.CONTINUE_NEXT:
                await self._apply_continue_next(ctx, intent)
                continue

            raise ValueError(f"unsupported RuntimeIntent kind: {intent.kind.value}")

        return RuntimeIntentEffectResult.processed(
            outbox_dispatch_targets=frozenset(outbox_dispatch_targets),
        )

    async def _apply_create_material_unit(self, ctx: Any, intent: RuntimeIntent) -> None:
        service = self._material_unit_mutation_service
        if service is None:
            from src.app.runtime.orchestration.services import material_unit_mutation_service

            service = material_unit_mutation_service
        _ = await service.create(ctx, intent.payload_json)

    async def _apply_update_material_unit_status(self, ctx: Any, intent: RuntimeIntent) -> None:
        service = self._material_unit_mutation_service
        if service is None:
            from src.app.runtime.orchestration.services import material_unit_mutation_service

            service = material_unit_mutation_service
        _ = await service.update_status(ctx, intent.payload_json)

    async def _apply_current_material_unit_reconciliation_status(self, ctx: Any) -> None:
        from src.app.runtime.orchestration.models.material_unit import MaterialUnit, MaterialUnitStatus

        session = ctx["session"]
        material_unit_id = optional_int(getattr(session, "current_material_unit_id", None))
        if material_unit_id is None:
            return

        material_unit = await ctx["db"].get(MaterialUnit, material_unit_id)
        if material_unit is None:
            return

        from_status = _state_value(getattr(material_unit, "status", None))
        _apply_material_unit_status_write(
            ctx,
            material_unit,
            from_state=from_status,
            to_status=MaterialUnitStatus.RECONCILING,
        )
        # material_unit 来自 db.get，已在 identity map 中，status 写入即脏标记，无需 add。

    async def _cleanup_completed_material_unit(self, ctx: Any) -> None:
        from src.app.runtime.orchestration.models.material_unit import MaterialUnit, MaterialUnitStatus
        from src.app.runtime.orchestration.models.session import SessionStatus

        session = ctx["session"]
        if getattr(session, "status", None) != SessionStatus.COMPLETED.value:
            return
        # 清理登记可能来自本批次 ctx（同 inbox 直达 COMPLETE），
        # 也可能来自跨批次恢复（NG 冲突 MANUAL_HOLD → 后续 inbox COMPLETE），
        # 后者从 session.context_json 恢复待清理集合。
        cleanup_ids = set(ctx.pop("_runtime_material_unit_cleanup_ids", set()) or set())
        cleanup_ids |= _pending_cleanup_ids_from_session(session)
        if not cleanup_ids:
            return
        db = ctx["db"]
        for material_unit_id in cleanup_ids:
            material_unit = await db.get(MaterialUnit, material_unit_id)
            if material_unit is None:
                continue
            if getattr(material_unit, "status", None) != MaterialUnitStatus.NG:
                continue
            if getattr(material_unit, "current_session_id", None) != resolve_required_pk(session, "session"):
                continue
            if getattr(session, "current_material_unit_id", None) != material_unit_id:
                continue
            session.current_material_unit_id = None
            material_unit.current_session_id = None
        # 清理完成后清空持久化登记，避免重复处理。
        _persist_pending_cleanup_ids(session, set())
        flush = getattr(db, "flush", None)
        if flush is not None:
            await flush()

    async def _apply_update_context(
        self,
        ctx: Any,
        intent: RuntimeIntent,
        workline_effects: Any,
    ) -> None:
        _merge_context_patch(ctx, intent.context_patch)
        workline_effects._apply_context_patch(ctx)

    async def _apply_noop_completion(self, ctx: Any) -> None:
        from src.app.runtime.orchestration.models.timeline import (
            TimelineActionType,
            TimelineActorType,
            TimelineStage,
            TimelineStatus,
        )
        from src.app.runtime.orchestration.repositories.session_repository import WorklineSessionRepository
        from src.app.workline.services import write_back_service as workline_effects

        session = ctx["session"]
        if (
            getattr(session, "status", None) != "NEW"
            or getattr(session, "current_wait_type", None) is not None
            or getattr(session, "awaiting_device_command_code", None) is not None
        ):
            return

        workline_effects.workline_session_lifecycle_service.complete(session, occurred_at=ctx["now"])
        workline_effects._clear_session_failure(session)
        await WorklineSessionRepository().persist_completed(
            ctx["db"],
            session_id=resolve_required_pk(session, "session"),
            occurred_at=ctx["now"],
            context_json=getattr(session, "context_json", None),
        )
        await workline_effects._emit_timeline(
            ctx,
            stage=TimelineStage.COMPLETE,
            action_type=TimelineActionType.SESSION_COMPLETED,
            payload={
                **workline_effects._effect_trace_payload(ctx),
                "completion_reason": "NO_RUNTIME_INTENT",
            },
            from_status=ctx["current_status"],
            to_status="COMPLETED",
            actor_type=TimelineActorType.ORCHESTRATOR,
            related_inbox_id=workline_effects._timeline_inbox_id(ctx),
            status=TimelineStatus.SUCCESS,
        )

    async def _apply_mark_ng(self, ctx: Any, intent: RuntimeIntent) -> None:
        from src.app.runtime.orchestration.models.timeline import (
            TimelineActionType,
            TimelineActorType,
            TimelineStage,
            TimelineStatus,
        )
        from src.app.workline.services import write_back_service as workline_effects

        await workline_effects._emit_timeline(
            ctx,
            stage=TimelineStage.DECISION,
            action_type=TimelineActionType.DECISION_MADE,
            payload={
                **workline_effects._effect_trace_payload(ctx),
                "classification": "business_decision",
                "reason_code": intent.reason_code,
                "message": intent.message,
                "evidence": dict(intent.payload_json),
                "business_key": None,
            },
            actor_type=TimelineActorType.ORCHESTRATOR,
            actor_code=None,
            message=intent.message,
            related_inbox_id=workline_effects._timeline_inbox_id(ctx),
            status=TimelineStatus.SUCCESS,
        )

    async def _apply_external_request(self, ctx: Any, intent: RuntimeIntent) -> None:
        target_code = str(intent.target_code)
        del ctx
        source_system = str(intent.source_system or "")
        if source_system in {"WMS", "RCS"} or target_code.startswith(("WMS", "RCS")):
            raise RuntimeError("WMS external HTTP facade is removed; use the frozen 35-operation registry")
        raise RuntimeError("generic workline external HTTP facade is removed")

    async def _apply_resource_fact(self, ctx: Any, intent: RuntimeIntent) -> Any:
        fact_type = str(intent.action)
        if fact_type == "RUNTIME_LOCATION_EVENT":
            return await self._apply_runtime_location_event_fact(ctx, intent)
        if fact_type == "RECONCILIATION_EVIDENCE":
            return await self._apply_reconciliation_evidence_fact(ctx, intent)

        service = self._resource_projection_service
        if service is None:
            from src.app.resource.services import resource_projection_service

            service = resource_projection_service

        ctx_map = cast("Mapping[str, Any]", ctx)
        return await service.record_resource_fact(
            db=ctx_map["db"],
            session=ctx_map["session"],
            workline=ctx_map["workline"],
            fact_type=fact_type,
            payload_json=dict(intent.payload_json),
            idempotency_key=intent.idempotency_key,
            trace_id=_ctx_trace_id(ctx_map),
        )

    async def _apply_runtime_location_event_fact(self, ctx: Any, intent: RuntimeIntent) -> Any:
        from src.app.runtime.orchestration.services import runtime_location_event_service as location_event_module

        ctx_map = cast("Mapping[str, Any]", ctx)
        payload = dict(intent.payload_json)
        return await location_event_module.runtime_location_event_service.record(
            ctx_map["db"],
            object_type=str(payload["object_type"]),
            object_key=str(payload["object_key"]),
            location_scope=str(payload["location_scope"]),
            location_code=str(payload["location_code"]),
            business_step=str(payload["business_step"]),
            source=str(payload["source"]),
            evidence_json=dict(cast("Mapping[str, Any]", payload.get("evidence_json") or {})),
            correlation_id=coerce_optional_str(payload.get("correlation_id")),
            source_event_id=coerce_optional_str(payload.get("source_event_id")),
            source_version=coerce_optional_str(payload.get("source_version")),
            idempotency_key=intent.idempotency_key or coerce_optional_str(payload.get("idempotency_key")),
            external_reference_type=coerce_optional_str(payload.get("external_reference_type")),
            external_reference_value=coerce_optional_str(payload.get("external_reference_value")),
            provider_code=coerce_optional_str(payload.get("provider_code")),
            auto_commit=False,
        )

    async def _apply_reconciliation_evidence_fact(self, ctx: Any, intent: RuntimeIntent) -> Any:
        from src.app.runtime.orchestration.services import runtime_location_event_service as location_event_module

        ctx_map = cast("Mapping[str, Any]", ctx)
        payload = dict(intent.payload_json)
        external_reference = payload.get("external_reference")
        external_reference_map = external_reference if isinstance(external_reference, Mapping) else {}
        return await location_event_module.runtime_location_event_service.record(
            ctx_map["db"],
            object_type=str(payload["object_type"]),
            object_key=str(payload["object_key"]),
            location_scope="RECONCILIATION",
            location_code=(
                coerce_optional_str(payload.get("reason_code"))
                or coerce_optional_str(payload.get("scenario"))
                or "RECONCILIATION_EVIDENCE"
            ),
            business_step="RECONCILIATION_EVIDENCE",
            source=RECONCILIATION_RUNTIME_SOURCE,
            evidence_json=payload,
            correlation_id=coerce_optional_str(payload.get("correlation_id")),
            source_event_id=coerce_optional_str(payload.get("source_event_id")),
            source_version=coerce_optional_str(payload.get("source_version")),
            idempotency_key=intent.idempotency_key,
            external_reference_type=coerce_optional_str(external_reference_map.get("type")),
            external_reference_value=coerce_optional_str(external_reference_map.get("value")),
            provider_code=coerce_optional_str(payload.get("provider_code")),
            auto_commit=False,
        )

    async def _apply_resource_reconciliation_hold(self, ctx: Any, result: Any) -> None:
        from src.app.runtime.orchestration.repositories.session_repository import WorklineSessionRepository
        from src.app.workline.services import write_back_service as workline_effects

        session = ctx["session"]
        reason_code = string_value(getattr(result, "reason_code", None)) or "RESOURCE_PROJECTION_RECONCILING"
        message = string_value(getattr(result, "message", None)) or "资源事实投影进入调和状态，等待人工处理 RuntimeHold"

        await self._apply_current_material_unit_reconciliation_status(ctx)
        workline_effects.workline_session_lifecycle_service.manual_hold(session, occurred_at=ctx["now"])
        session.failure_domain = "RESOURCE_RECONCILIATION"
        session.failure_code = reason_code
        session.failure_message = message
        await WorklineSessionRepository().persist_manual_hold(
            ctx["db"],
            session_id=resolve_required_pk(session, "session"),
            occurred_at=ctx["now"],
            failure_domain=session.failure_domain,
            failure_code=session.failure_code,
            failure_message=session.failure_message,
        )

    async def _apply_resource_reservation(self, ctx: Any, intent: RuntimeIntent) -> Any:
        service = self._bin_cell_reservation_service
        if service is None:
            from src.app.runtime.capabilities.material_flow.bin_cell_reservation_service import (
                bin_cell_reservation_service,
            )

            service = bin_cell_reservation_service

        ctx_map = cast("Mapping[str, Any]", ctx)
        return await service.apply_runtime_reservation(
            db=ctx_map["db"],
            session=ctx_map["session"],
            workline=ctx_map["workline"],
            operation=str(intent.action),
            payload_json=dict(intent.payload_json),
            idempotency_key=intent.idempotency_key,
            trace_id=_ctx_trace_id(ctx_map),
        )

    async def _apply_resource_wait(self, ctx: Any, intent: RuntimeIntent) -> RuntimeIntentEffectResult:
        from src.app.runtime.orchestration.models.timeline import (
            TimelineActionType,
            TimelineActorType,
            TimelineStage,
            TimelineStatus,
        )
        from src.app.runtime.orchestration.repositories.session_repository import WorklineSessionRepository
        from src.app.workline.services import write_back_service as workline_effects
        from src.app.workline.services.diagnostic_service import workline_diagnostic_service

        session = ctx["session"]
        inbox = ctx["inbox"]
        workline = ctx["workline"]
        payload_json = dict(intent.payload_json)
        subject_type = str(payload_json["subject_type"])
        subject_key = str(payload_json["subject_key"])
        projection_type = str(payload_json["projection_type"])
        context_json = dict(getattr(session, "context_json", None) or {})
        existing_wait = context_json.get("resource_wait")
        existing_wait_evidence = (
            cast("dict[str, Any]", existing_wait)
            if isinstance(existing_wait, Mapping)
            and existing_wait.get("subject_type") == subject_type
            and existing_wait.get("subject_key") == subject_key
            and existing_wait.get("projection_type") == projection_type
            else None
        )
        evidence = ResourceWaitEvidence.build(
            inbox_id=resolve_required_pk(inbox, "inbox"),
            subject_type=subject_type,
            subject_key=subject_key,
            projection_type=projection_type,
            reason_code=str(intent.reason_code),
            message=str(intent.message),
            occurred_at=ctx["now"],
            session_id=optional_int(getattr(session, "id", None)),
            workline_id=optional_int(getattr(workline, "id", None))
            or optional_int(getattr(session, "workline_id", None)),
            trace_id=coerce_optional_str(_ctx_trace_id(cast("Mapping[str, Any]", ctx))),
            details={
                key: value
                for key, value in payload_json.items()
                if key not in {"subject_type", "subject_key", "projection_type"}
            },
            existing=existing_wait_evidence,
        )

        context_json["resource_wait"] = evidence.to_session_context()
        session.context_json = context_json
        ctx["session_ctx"] = dict(context_json)
        workline_effects.workline_session_lifecycle_service.start_wait(
            session,
            wait_type="RESOURCE_WAIT",
            occurred_at=ctx["now"],
            deadline_seconds=None,
        )
        workline_effects._clear_session_failure(session)
        await WorklineSessionRepository().persist_external_wait(
            ctx["db"],
            session_id=resolve_required_pk(session, "session"),
            wait_type="RESOURCE_WAIT",
            occurred_at=ctx["now"],
            timeout_seconds=None,
            context_json=getattr(session, "context_json", None),
        )
        _ = await workline_diagnostic_service.record_resource_wait(
            ctx["db"],
            evidence=evidence,
            inbox=inbox,
            session=session,
            workline=workline,
            auto_commit=False,
        )
        await workline_effects._emit_timeline(
            ctx,
            stage=TimelineStage.WAITING,
            action_type=TimelineActionType.WAIT_STARTED,
            payload={
                **workline_effects._wait_timeline_payload(
                    ctx,
                    wait_type="RESOURCE_WAIT",
                    wait_token=subject_key,
                    deadline_seconds=0,
                ),
                "subject_type": subject_type,
                "subject_key": subject_key,
                "projection_type": projection_type,
                "reason_code": intent.reason_code,
                "message": intent.message,
                "suggested_action": intent.suggested_action,
            },
            from_status=ctx["current_status"],
            to_status=session.status,
            actor_type=TimelineActorType.ORCHESTRATOR,
            related_inbox_id=workline_effects._timeline_inbox_id(ctx),
            status=TimelineStatus.PENDING,
        )
        return RuntimeIntentEffectResult.resource_retry()

    async def _apply_destination_failure(self, ctx: Any, exc: ValueError) -> None:
        await self._apply_block(
            ctx,
            RuntimeIntent.block(
                scope=BlockScope.MATERIAL,
                reason_code="DESTINATION_UNREACHABLE",
                message=str(exc),
                suggested_action="检查设备拓扑和作业线路由配置",
            ),
        )

    async def _apply_cancel(self, ctx: Any, intent: RuntimeIntent) -> None:
        from src.app.runtime.orchestration.models.timeline import TimelineActionType, TimelineActorType, TimelineStage
        from src.app.runtime.orchestration.repositories.session_repository import WorklineSessionRepository
        from src.app.runtime.orchestration.services.system_outbox_cancellation_service import (
            system_outbox_cancellation_service,
        )
        from src.app.workline.services import write_back_service as workline_effects

        session = ctx["session"]
        session_id = resolve_required_pk(session, "session")
        workline_effects.workline_session_lifecycle_service.cancel(session, occurred_at=ctx["now"])
        await WorklineSessionRepository().persist_cancelled(
            ctx["db"],
            session_id=session_id,
            occurred_at=ctx["now"],
        )
        _ = await system_outbox_cancellation_service.cancel_active_by_session(
            ctx["db"],
            session_id=session_id,
            reason=intent.reason_code or "SESSION_CANCELLED",
        )

        timeline_payload = {
            "reason_code": intent.reason_code,
            "message": intent.message,
            **dict(intent.payload_json),
        }
        await workline_effects._emit_timeline(
            ctx,
            stage=TimelineStage.MANUAL,
            action_type=TimelineActionType.SESSION_CANCELLED,
            payload=timeline_payload,
            from_status=ctx["current_status"],
            to_status="CANCELLED",
            actor_type=TimelineActorType.ORCHESTRATOR,
            related_inbox_id=workline_effects._timeline_inbox_id(ctx),
            message=intent.message,
        )

    async def _apply_block(self, ctx: Any, intent: RuntimeIntent) -> None:
        from src.app.runtime.orchestration.models.timeline import (
            TimelineActionType,
            TimelineActorType,
            TimelineStage,
            TimelineStatus,
        )
        from src.app.runtime.orchestration.repositories.session_repository import WorklineSessionRepository
        from src.app.workline.services import write_back_service as workline_effects

        session = ctx["session"]
        workline_effects.workline_session_lifecycle_service.manual_hold(session, occurred_at=ctx["now"])
        session.failure_domain = intent.block_scope.value if intent.block_scope is not None else "BLOCK"
        session.failure_code = intent.reason_code
        session.failure_message = intent.message
        await WorklineSessionRepository().persist_manual_hold(
            ctx["db"],
            session_id=resolve_required_pk(session, "session"),
            occurred_at=ctx["now"],
            failure_domain=session.failure_domain,
            failure_code=session.failure_code,
            failure_message=session.failure_message,
        )

        timeline_payload: dict[str, Any] = {
            **workline_effects._effect_trace_payload(ctx),
            "block_scope": session.failure_domain,
            "reason_code": intent.reason_code,
            "message": intent.message,
            "suggested_action": intent.suggested_action,
        }
        if intent.payload_json:
            timeline_payload["evidence"] = dict(intent.payload_json)

        await workline_effects._emit_timeline(
            ctx,
            stage=TimelineStage.MANUAL,
            action_type=TimelineActionType.MANUAL_HOLD,
            payload=timeline_payload,
            from_status=ctx["current_status"],
            to_status="MANUAL_HOLD",
            actor_type=TimelineActorType.ORCHESTRATOR,
            related_inbox_id=workline_effects._timeline_inbox_id(ctx),
            status=TimelineStatus.PENDING,
            failure_domain=session.failure_domain,
            message=intent.message,
        )

    async def _apply_continue_next(self, ctx: Any, intent: RuntimeIntent) -> None:
        from src.app.workline.services import write_back_service as workline_effects

        _ = intent
        session = ctx["session"]
        workline_effects.workline_session_lifecycle_service.running(session)
        workline_effects._clear_session_wait(session)
        workline_effects._clear_session_failure(session)


__all__ = ["RuntimeIntentEffectApplier"]
