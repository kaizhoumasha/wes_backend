"""RuntimeIntent 到 Session / Command / Outbox / Timeline 的 effect 落地。"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any

from src.workline_runtime.material_target_resolver import resolve_destination_device
from src.workline_runtime.runtime_intent import (
    BlockScope,
    Destination,
    DestinationKind,
    RuntimeIntent,
    RuntimeIntentKind,
)

_SUPPORTED_INTENT_KINDS = {
    RuntimeIntentKind.UPDATE_CONTEXT,
    RuntimeIntentKind.MARK_NG,
    RuntimeIntentKind.COMMAND,
    RuntimeIntentKind.EXTERNAL_REQUEST,
    RuntimeIntentKind.DEVICE_EVENT,
    RuntimeIntentKind.COMPLETE,
    RuntimeIntentKind.BLOCK,
    RuntimeIntentKind.CONTINUE_NEXT,
}
_TERMINAL_INTENT_KINDS = {RuntimeIntentKind.COMPLETE, RuntimeIntentKind.BLOCK}


def _all_devices(devices_by_role: dict[str, list[Any]]) -> list[Any]:
    return [device for devices in devices_by_role.values() for device in devices]


def _merge_context_patch(ctx: Any, patch: dict[str, Any]) -> None:
    if not patch:
        return

    orch_result = ctx["orch_result"]
    merged = dict(getattr(orch_result, "context_patch", None) or {})
    merged.update(patch)
    orch_result.context_patch = merged


def _is_command_producing_intent(intent: RuntimeIntent) -> bool:
    return intent.kind in {RuntimeIntentKind.COMMAND, RuntimeIntentKind.EXTERNAL_REQUEST} or (
        intent.kind == RuntimeIntentKind.CONTINUE_NEXT and intent.action is not None
    )


def _command_producing_intent_to_command_intent(intent: RuntimeIntent) -> RuntimeIntent:
    if intent.kind == RuntimeIntentKind.COMMAND:
        return intent

    if intent.kind == RuntimeIntentKind.CONTINUE_NEXT and intent.action is not None:
        return RuntimeIntent.command(
            action=intent.action,
            device_role=intent.device_role,
            payload=dict(intent.payload_json),
            destination=intent.destination or Destination.next(),
            target_device_id=intent.target_device_id,
            timeout_seconds=intent.timeout_seconds,
        )

    raise ValueError(f"RuntimeIntent is not command-producing: {intent.kind.value}")


def _validate_command_destination(intent: RuntimeIntent) -> None:
    if intent.kind != RuntimeIntentKind.COMMAND:
        raise ValueError(f"Expected COMMAND intent, got {intent.kind.value}")
    if intent.action is None:
        raise ValueError("COMMAND intent requires action")

    destination = intent.destination
    if destination is None:
        return
    if destination.kind in {
        DestinationKind.CURRENT,
        DestinationKind.NEXT,
        DestinationKind.ROLE,
        DestinationKind.DEVICE,
        DestinationKind.NG_ROUTE,
        DestinationKind.PASS_ROUTE,
    }:
        return
    raise ValueError(f"unsupported COMMAND destination: {destination.kind.value}")


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
            if intent.kind != RuntimeIntentKind.EXTERNAL_REQUEST:
                _validate_command_destination(_command_producing_intent_to_command_intent(intent))

        if intent.kind in _TERMINAL_INTENT_KINDS and index != len(intents) - 1:
            raise ValueError("terminal RuntimeIntent must be final intent")
        if intent.kind in _TERMINAL_INTENT_KINDS and command_producing_seen:
            raise ValueError("terminal RuntimeIntent cannot follow command-producing RuntimeIntent")

    if command_producing_count > 1:
        raise ValueError("multiple command-producing RuntimeIntents are not supported in one callback")


def _runtime_route_roles(ctx: Any) -> dict[str, str]:
    workline = ctx["workline"]
    route_roles: dict[str, str] = {}
    for source in (getattr(workline, "runtime_config_json", None), getattr(workline, "config", None)):
        if not isinstance(source, dict):
            continue
        raw_routes = source.get("route_roles") or source.get("routes")
        if not isinstance(raw_routes, dict):
            continue
        route_roles.update(
            {
                key: value
                for key, value in raw_routes.items()
                if isinstance(key, str) and isinstance(value, str) and value
            }
        )
    return route_roles


def _resolve_target_device(ctx: Any, intent: RuntimeIntent) -> Any:
    if intent.target_device_id is not None:
        destination = Destination.device(intent.target_device_id)
    else:
        destination = intent.destination or Destination.current()

    if destination.kind == DestinationKind.NEXT and intent.device_role:
        destination = Destination.role(intent.device_role)

    source_device = ctx["source_device"]
    if source_device is None:
        raise ValueError("Cannot resolve command target without source device")
    return resolve_destination_device(
        destination=destination,
        source_device=source_device,
        devices=_all_devices(ctx["devices_by_role"]),
        route_roles=_runtime_route_roles(ctx),
    )


class RuntimeIntentEffectApplier:
    def __init__(
        self,
        *,
        full_box_exchange_task_service: Any | None = None,
        inbox_service: Any | None = None,
    ) -> None:
        self._full_box_exchange_task_service = full_box_exchange_task_service
        self._inbox_service = inbox_service

    async def apply(self, ctx: Any, intents: list[RuntimeIntent]) -> None:
        _validate_runtime_intents(intents)

        from src.celery_app.tasks import workline as workline_effects

        workline_effects._sync_effect_trace_fields(ctx)
        if not intents:
            await self._apply_noop_completion(ctx)
            return

        for intent in intents:
            if intent.kind == RuntimeIntentKind.UPDATE_CONTEXT:
                _merge_context_patch(ctx, intent.context_patch)
                workline_effects._apply_context_patch(ctx)
                continue

            if intent.kind == RuntimeIntentKind.MARK_NG:
                await self._apply_mark_ng(ctx, intent)
                continue

            if intent.kind == RuntimeIntentKind.COMMAND:
                await self._apply_command(ctx, intent)
                continue

            if intent.kind == RuntimeIntentKind.EXTERNAL_REQUEST:
                await self._apply_external_request(ctx, intent)
                continue

            if intent.kind == RuntimeIntentKind.DEVICE_EVENT:
                await self._apply_device_event(ctx, intent)
                continue

            if intent.kind == RuntimeIntentKind.COMPLETE:
                _merge_context_patch(ctx, intent.context_patch)
                workline_effects._apply_context_patch(ctx)
                workline_effects._clear_session_failure(ctx["session"])
                ctx["orch_result"].complete = True
                _ = await workline_effects._apply_completion_transition(ctx)
                continue

            if intent.kind == RuntimeIntentKind.BLOCK:
                await self._apply_block(ctx, intent)
                continue

            if intent.kind == RuntimeIntentKind.CONTINUE_NEXT:
                await self._apply_continue_next(ctx, intent)
                continue

            raise ValueError(f"unsupported RuntimeIntent kind: {intent.kind.value}")

    async def _apply_noop_completion(self, ctx: Any) -> None:
        from src.app.workline.models.timeline import (
            TimelineActionType,
            TimelineActorType,
            TimelineStage,
            TimelineStatus,
        )
        from src.celery_app.tasks import workline as workline_effects

        session = ctx["session"]
        if (
            getattr(session, "status", None) != "NEW"
            or getattr(session, "current_wait_type", None) is not None
            or getattr(session, "awaiting_command_id", None) is not None
        ):
            return

        session.status = "COMPLETED"
        workline_effects._clear_session_wait(session)
        workline_effects._clear_session_failure(session)
        session.ended_at = ctx["now"]
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
        from src.app.workline.models.timeline import (
            TimelineActionType,
            TimelineActorType,
            TimelineStage,
            TimelineStatus,
        )
        from src.celery_app.tasks import workline as workline_effects

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
            actor_type=TimelineActorType.PLUGIN,
            actor_code=getattr(ctx["workline"], "plugin_key", None),
            message=intent.message,
            related_inbox_id=workline_effects._timeline_inbox_id(ctx),
            status=TimelineStatus.SUCCESS,
        )

    async def _apply_command(self, ctx: Any, intent: RuntimeIntent) -> None:
        from src.app.device.repositories.command_repository import DeviceCommandRepository
        from src.app.workline.models.timeline import (
            TimelineActionType,
            TimelineActorType,
            TimelineStage,
            TimelineStatus,
        )
        from src.celery_app.tasks import workline as workline_effects

        try:
            target_device = _resolve_target_device(ctx, intent)
        except ValueError as exc:
            await self._apply_destination_failure(ctx, exc)
            return
        target_device_id = workline_effects._resolve_required_pk(target_device, "target_device")
        try:
            workline_effects._enforce_device_command_governance(
                target_device,
                command_type=intent.action,
                stage_label="命令创建",
                allow_busy=True,
            )
        except workline_effects._DeviceCommandGovernanceError as exc:
            await self._apply_governance_failure(ctx, exc)
            return

        device_code = getattr(target_device, "device_code", None)
        if not isinstance(device_code, str) or not device_code:
            raise ValueError(f"Target device missing device_code: {target_device_id}")

        generated_command_code = workline_effects._build_command_code(
            workline_effects._map_command_task_type(str(intent.action))
        )
        vendor_payload = workline_effects._normalize_vendor_command_payload(
            intent.payload_json,
            action=str(intent.action),
            default_command_code=generated_command_code,
        )
        resolved_command_code = workline_effects._string_value(
            vendor_payload.get("command_code"), generated_command_code
        )
        command_data = workline_effects._build_command_create_payload(
            ctx,
            command_intent=SimpleNamespace(action=str(intent.action), parameters=dict(intent.payload_json)),
            vendor_payload=vendor_payload,
            target_device_id=target_device_id,
            resolved_command_code=resolved_command_code,
        )
        command = await DeviceCommandRepository().create(ctx["db"], command_data)
        if command is None:
            raise RuntimeError("Failed to create device command from RuntimeIntent")

        ctx["awaiting_command_id"] = command.id
        ctx["awaiting_command_code"] = command.command_code

        command_outbox = workline_effects._build_command_outbox_model(ctx, command=command, device_code=device_code)
        ctx["db"].add(command_outbox)
        await workline_effects._emit_timeline(
            ctx,
            stage=TimelineStage.DISPATCH_PREPARE,
            action_type=TimelineActionType.COMMAND_SENT,
            payload=workline_effects._command_timeline_payload(
                ctx,
                command_code=command.command_code,
                command_type=str(intent.action),
                dispatch_key=command_outbox.dispatch_key,
                parameters=workline_effects.payload_dict(vendor_payload.get("params")),
            ),
            actor_type=TimelineActorType.ORCHESTRATOR,
            actor_code=device_code,
            related_inbox_id=workline_effects._timeline_inbox_id(ctx),
            related_command_id=command.id,
            status=TimelineStatus.PENDING,
        )

        workline_effects._clear_session_failure(ctx["session"])
        if intent.timeout_seconds is None:
            ctx["session"].status = "RUNNING"
            workline_effects._clear_session_wait(ctx["session"])
            ctx["session"].ended_at = None
            return

        await self._apply_command_wait(ctx, intent)

    async def _apply_external_request(self, ctx: Any, intent: RuntimeIntent) -> None:
        from src.app.workline.models.timeline import (
            TimelineActionType,
            TimelineActorType,
            TimelineStage,
            TimelineStatus,
        )
        from src.celery_app.tasks import workline as workline_effects

        dispatch_key = str(intent.dispatch_key)
        target_code = str(intent.target_code)
        payload_json = dict(intent.payload_json)
        timeout_seconds = int(intent.timeout_seconds or 0)
        session = ctx["session"]

        outbox = workline_effects._build_external_http_outbox_model(
            ctx,
            dispatch_key=dispatch_key,
            target_code=target_code,
            payload_json=payload_json,
        )
        ctx["db"].add(outbox)
        await self._record_full_box_exchange_task(
            ctx,
            outbox=outbox,
            dispatch_key=dispatch_key,
            target_code=target_code,
            payload_json=payload_json,
        )
        await workline_effects._emit_timeline(
            ctx,
            stage=TimelineStage.DISPATCH_PREPARE,
            action_type=TimelineActionType.EXTERNAL_CALL_STARTED,
            payload=workline_effects._external_decision_timeline_payload(
                ctx,
                dispatch_key=dispatch_key,
                target_code=target_code,
                payload_json=payload_json,
            ),
            actor_type=TimelineActorType.EXTERNAL_SYSTEM,
            actor_code=intent.source_system or "EXTERNAL_SYSTEM",
            related_inbox_id=workline_effects._timeline_inbox_id(ctx),
            status=TimelineStatus.PENDING,
        )

        workline_effects._clear_session_failure(session)
        session.status = workline_effects._wait_session_status("EXTERNAL_HTTP")
        session.current_wait_type = "EXTERNAL_HTTP"
        session.waiting_since = ctx["now"]
        session.awaiting_command_id = None
        session.current_wait_timeout_seconds = timeout_seconds
        session.deadline_at = ctx["now"] + timedelta(seconds=timeout_seconds)
        session.ended_at = None
        await workline_effects._emit_timeline(
            ctx,
            stage=TimelineStage.WAITING,
            action_type=TimelineActionType.WAIT_STARTED,
            payload=workline_effects._wait_timeline_payload(
                ctx,
                wait_type="EXTERNAL_HTTP",
                wait_token=dispatch_key,
                deadline_seconds=timeout_seconds,
            ),
            from_status=ctx["current_status"],
            to_status=session.status,
            actor_type=TimelineActorType.ORCHESTRATOR,
            related_inbox_id=workline_effects._timeline_inbox_id(ctx),
            status=TimelineStatus.PENDING,
        )

    async def _apply_device_event(self, ctx: Any, intent: RuntimeIntent) -> None:
        service = self._inbox_service
        if service is None:
            from src.app.workline.services import inbox_service

            service = inbox_service

        payload = dict(intent.payload_json)
        trace = ctx.get("trace") if isinstance(ctx, dict) else None
        trace_id = getattr(trace, "trace_id", None) or ctx.get("trace_id")
        _ = await service.create_device_event_inbox(
            db=ctx["db"],
            device_code=str(payload["device_code"]),
            event_type=str(payload["event_type"]),
            timestamp=int(payload["timestamp"]),
            data=dict(payload["data"]),
            trace_id=trace_id,
            event_id=payload.get("event_id"),
            causation_id=payload.get("causation_id"),
            canonical_event_type=payload.get("canonical_event_type"),
            auto_commit=False,
        )

    async def _record_full_box_exchange_task(
        self,
        ctx: Any,
        *,
        outbox: Any,
        dispatch_key: str,
        target_code: str,
        payload_json: dict[str, Any],
    ) -> None:
        service = self._full_box_exchange_task_service
        if service is None:
            from src.app.resource.services import full_box_exchange_task_service

            service = full_box_exchange_task_service

        trace = ctx.get("trace") if isinstance(ctx, dict) else None
        trace_id = getattr(trace, "trace_id", None) or ctx.get("trace_id")
        _ = await service.record_requested_from_external_request(
            db=ctx["db"],
            session=ctx["session"],
            outbox=outbox,
            dispatch_key=dispatch_key,
            target_code=target_code,
            payload_json=payload_json,
            trace_id=trace_id,
        )

    async def _apply_command_wait(self, ctx: Any, intent: RuntimeIntent) -> None:
        from src.app.workline.models.timeline import (
            TimelineActionType,
            TimelineActorType,
            TimelineStage,
            TimelineStatus,
        )
        from src.celery_app.tasks import workline as workline_effects

        session = ctx["session"]
        timeout_seconds = intent.timeout_seconds or 300
        session.status = workline_effects._wait_session_status("COMMAND_RESULT")
        session.current_wait_type = "COMMAND_RESULT"
        session.waiting_since = ctx["now"]
        session.awaiting_command_id = ctx["awaiting_command_id"]
        session.current_wait_timeout_seconds = timeout_seconds
        session.deadline_at = None
        session.ended_at = None
        await workline_effects._emit_timeline(
            ctx,
            stage=TimelineStage.WAITING,
            action_type=TimelineActionType.WAIT_STARTED,
            payload=workline_effects._wait_timeline_payload(
                ctx,
                wait_type="COMMAND_RESULT",
                wait_token=ctx["awaiting_command_code"],
                deadline_seconds=timeout_seconds,
            ),
            from_status=ctx["current_status"],
            to_status=session.status,
            actor_type=TimelineActorType.ORCHESTRATOR,
            related_inbox_id=workline_effects._timeline_inbox_id(ctx),
            related_command_id=ctx["awaiting_command_id"],
            status=TimelineStatus.PENDING,
        )

    async def _apply_governance_failure(self, ctx: Any, exc: Any) -> None:
        from src.celery_app.tasks import workline as workline_effects

        ctx["orch_result"].failure = SimpleNamespace(domain=exc.domain, code=exc.code, message=exc.message)
        _ = await workline_effects._apply_failure_transition(ctx)

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

    async def _apply_block(self, ctx: Any, intent: RuntimeIntent) -> None:
        from src.app.workline.models.timeline import (
            TimelineActionType,
            TimelineActorType,
            TimelineStage,
            TimelineStatus,
        )
        from src.celery_app.tasks import workline as workline_effects

        session = ctx["session"]
        session.status = "MANUAL_HOLD"
        workline_effects._clear_session_wait(session)
        session.ended_at = None
        session.failure_domain = intent.block_scope.value if intent.block_scope is not None else "BLOCK"
        session.failure_code = intent.reason_code
        session.failure_message = intent.message

        await workline_effects._emit_timeline(
            ctx,
            stage=TimelineStage.MANUAL,
            action_type=TimelineActionType.MANUAL_HOLD,
            payload={
                **workline_effects._effect_trace_payload(ctx),
                "block_scope": session.failure_domain,
                "reason_code": intent.reason_code,
                "message": intent.message,
                "suggested_action": intent.suggested_action,
            },
            from_status=ctx["current_status"],
            to_status="MANUAL_HOLD",
            actor_type=TimelineActorType.ORCHESTRATOR,
            related_inbox_id=workline_effects._timeline_inbox_id(ctx),
            status=TimelineStatus.PENDING,
            failure_domain=session.failure_domain,
            message=intent.message,
        )

    async def _apply_continue_next(self, ctx: Any, intent: RuntimeIntent) -> None:
        from src.celery_app.tasks import workline as workline_effects

        if intent.action:
            await self._apply_command(ctx, _command_producing_intent_to_command_intent(intent))
            return

        session = ctx["session"]
        session.status = "RUNNING"
        workline_effects._clear_session_wait(session)
        workline_effects._clear_session_failure(session)
        session.ended_at = None


__all__ = ["RuntimeIntentEffectApplier"]
