"""RuntimeIntent 到 Session / Command / Outbox / Timeline 的 effect 落地。"""

from __future__ import annotations

from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any, cast
from urllib.parse import urlparse

from src.utils.value_normalization import optional_int, resolve_required_pk, string_value
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
    RuntimeIntentKind.RACK_OPERATION_REQUEST,
    RuntimeIntentKind.BIN_OPERATION_REQUEST,
    RuntimeIntentKind.RACK_BIN_EXCHANGE_REQUEST,
    RuntimeIntentKind.DEVICE_EVENT,
    RuntimeIntentKind.RESOURCE_FACT,
    RuntimeIntentKind.RESOURCE_RESERVATION,
    RuntimeIntentKind.COMPLETE,
    RuntimeIntentKind.BLOCK,
    RuntimeIntentKind.CONTINUE_NEXT,
}
_TERMINAL_INTENT_KINDS = {RuntimeIntentKind.COMPLETE, RuntimeIntentKind.BLOCK}
_DEFAULT_RACK_OPERATION_TARGET_CODE = "WMS_RCS_RACK_OPERATION"
_DEFAULT_COMMAND_RESULT_TIMEOUT_SECONDS = 300


def _all_devices(devices_by_role: dict[str, list[Any]]) -> list[Any]:
    return [device for devices in devices_by_role.values() for device in devices]


def _normalize_rack_operation_target_code(value: Any) -> str:
    target_code = str(value or "")
    parsed = urlparse(target_code)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return _DEFAULT_RACK_OPERATION_TARGET_CODE
    return target_code


def _merge_context_patch(ctx: Any, patch: dict[str, Any]) -> None:
    if not patch:
        return

    orch_result = ctx["orch_result"]
    merged = dict(getattr(orch_result, "context_patch", None) or {})
    merged.update(patch)
    orch_result.context_patch = merged


def _is_command_producing_intent(intent: RuntimeIntent) -> bool:
    return intent.kind in {
        RuntimeIntentKind.COMMAND,
        RuntimeIntentKind.EXTERNAL_REQUEST,
        RuntimeIntentKind.RACK_OPERATION_REQUEST,
        RuntimeIntentKind.BIN_OPERATION_REQUEST,
        RuntimeIntentKind.RACK_BIN_EXCHANGE_REQUEST,
    } or (intent.kind == RuntimeIntentKind.CONTINUE_NEXT and intent.action is not None)


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
            if intent.kind in {RuntimeIntentKind.COMMAND, RuntimeIntentKind.CONTINUE_NEXT}:
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
        if not isinstance(source, Mapping):
            continue
        source_map = cast("Mapping[str, Any]", source)
        raw_routes = source_map.get("route_roles") or source_map.get("routes")
        if not isinstance(raw_routes, Mapping):
            continue
        route_map = cast("Mapping[Any, Any]", raw_routes)
        route_roles.update(
            {
                key: value
                for key, value in route_map.items()
                if isinstance(key, str) and isinstance(value, str) and value
            }
        )
    return route_roles


def _ctx_trace_id(ctx: Mapping[str, Any]) -> Any | None:
    trace = ctx.get("trace")
    return getattr(trace, "trace_id", None) or ctx.get("trace_id")


def _non_empty_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int_value(value: Any) -> int | None:
    return optional_int(value)


def _resolve_command_result_timeout_seconds(intent: RuntimeIntent) -> int:
    explicit_timeout = _int_value(intent.timeout_seconds)
    if explicit_timeout is not None:
        return max(1, explicit_timeout)

    payload = intent.payload_json if isinstance(intent.payload_json, Mapping) else {}
    timeout_ms = _int_value(payload.get("timeout"))
    if timeout_ms is not None:
        return max(1, (max(0, timeout_ms) + 999) // 1000)

    return _DEFAULT_COMMAND_RESULT_TIMEOUT_SECONDS


def _required_rack_task_specs(payload_json: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_specs = payload_json.get("rack_tasks") or payload_json.get("task_specs")
    if not isinstance(raw_specs, list) or not raw_specs:
        raise ValueError("RACK_OPERATION_REQUEST intent requires payload.rack_tasks")

    specs: list[dict[str, Any]] = []
    for index, raw_spec in enumerate(raw_specs, start=1):
        if not isinstance(raw_spec, Mapping):
            raise TypeError(f"RACK_OPERATION_REQUEST payload.rack_tasks[{index}] must be a mapping")
        specs.append(dict(cast("Mapping[str, Any]", raw_spec)))
    return specs


def _required_handling_move_specs(payload_json: Mapping[str, Any], kind: RuntimeIntentKind) -> list[dict[str, Any]]:
    raw_specs = payload_json.get("moves")
    if not isinstance(raw_specs, list) or not raw_specs:
        raise ValueError(f"{kind.value} intent requires payload.moves")

    specs: list[dict[str, Any]] = []
    for index, raw_spec in enumerate(raw_specs, start=1):
        if not isinstance(raw_spec, Mapping):
            raise TypeError(f"{kind.value} payload.moves[{index}] must be a mapping")
        specs.append(dict(cast("Mapping[str, Any]", raw_spec)))
    return specs


def _rack_task_sequence_no(task: Any) -> int | None:
    value = getattr(task, "sequence_no", None)
    return int(value) if value is not None else None


def _rack_task_type(task: Any) -> str | None:
    value = getattr(task, "task_type", None)
    raw_value = getattr(value, "value", value)
    return str(raw_value) if raw_value is not None else None


def _rack_task_dispatch_key(task: Any) -> str | None:
    return _non_empty_text(getattr(task, "dispatch_key", None))


def _rack_task_rack_code(task: Any) -> str | None:
    return _non_empty_text(getattr(task, "rack_code", None))


def _rack_task_required(task: Any) -> bool:
    actions_json = getattr(task, "actions_json", None)
    if isinstance(actions_json, Mapping) and "required" in actions_json:
        return bool(actions_json["required"])
    return True


def _rack_task_is_releasing_source(task: Any) -> bool:
    return (
        _rack_task_required(task)
        and _rack_task_type(task) == "MOVE_RACK"
        and _non_empty_text(getattr(task, "source_position_code", None)) is not None
        and _non_empty_text(getattr(task, "target_position_code", None)) is None
    )


def _rack_operation_target_position_code(tasks: list[Any]) -> str | None:
    target_position_codes = {
        target_position_code
        for task in tasks
        if (target_position_code := _non_empty_text(getattr(task, "target_position_code", None))) is not None
    }
    if len(target_position_codes) != 1:
        return None
    return next(iter(target_position_codes))


def _result_status_value(result: Any) -> str | None:
    status = getattr(result, "status", None)
    raw = getattr(status, "value", status)
    return raw if isinstance(raw, str) else None


def _is_reconciling_result(result: Any) -> bool:
    return _result_status_value(result) == "RECONCILING"


def _resolve_target_device(ctx: Any, intent: RuntimeIntent) -> Any:
    if intent.target_device_id is not None:
        destination = Destination.device(intent.target_device_id)
    elif intent.destination is None and intent.device_role:
        destination = Destination.role(intent.device_role)
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
        rack_operation_service: Any | None = None,
        handling_operation_service: Any | None = None,
        inbox_service: Any | None = None,
        resource_projection_service: Any | None = None,
        bin_cell_reservation_service: Any | None = None,
    ) -> None:
        self._rack_operation_service = rack_operation_service
        self._handling_operation_service = handling_operation_service
        self._inbox_service = inbox_service
        self._resource_projection_service = resource_projection_service
        self._bin_cell_reservation_service = bin_cell_reservation_service

    async def apply(self, ctx: Any, intents: list[RuntimeIntent]) -> None:
        _validate_runtime_intents(intents)

        from src.app.workline.services import write_back_service as workline_effects

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

            if intent.kind == RuntimeIntentKind.RACK_OPERATION_REQUEST:
                await self._apply_rack_operation_request(ctx, intent)
                continue

            if intent.kind in {RuntimeIntentKind.BIN_OPERATION_REQUEST, RuntimeIntentKind.RACK_BIN_EXCHANGE_REQUEST}:
                await self._apply_handling_operation_request(ctx, intent)
                continue

            if intent.kind == RuntimeIntentKind.DEVICE_EVENT:
                await self._apply_device_event(ctx, intent)
                continue

            if intent.kind == RuntimeIntentKind.RESOURCE_FACT:
                result = await self._apply_resource_fact(ctx, intent)
                if _is_reconciling_result(result):
                    return
                continue

            if intent.kind == RuntimeIntentKind.RESOURCE_RESERVATION:
                result = await self._apply_resource_reservation(ctx, intent)
                if _is_reconciling_result(result):
                    return
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
        from src.app.workline.repositories.session_repository import WorklineSessionRepository
        from src.app.workline.services import write_back_service as workline_effects

        session = ctx["session"]
        if (
            getattr(session, "status", None) != "NEW"
            or getattr(session, "current_wait_type", None) is not None
            or getattr(session, "awaiting_command_id", None) is not None
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
        from src.app.workline.models.timeline import (
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
        from src.app.workline.services import write_back_service as workline_effects

        try:
            target_device = _resolve_target_device(ctx, intent)
        except ValueError as exc:
            await self._apply_destination_failure(ctx, exc)
            return
        target_device_id = resolve_required_pk(target_device, "target_device")
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
        resolved_command_code = string_value(vendor_payload.get("command_code"), generated_command_code)
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
        await self._apply_command_wait(ctx, intent)

    async def _apply_external_request(self, ctx: Any, intent: RuntimeIntent) -> None:
        from src.app.workline.models.timeline import (
            TimelineActionType,
            TimelineActorType,
            TimelineStage,
            TimelineStatus,
        )
        from src.app.workline.services import write_back_service as workline_effects

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
        workline_effects.workline_session_lifecycle_service.start_wait(
            session,
            wait_type="EXTERNAL_HTTP",
            occurred_at=ctx["now"],
            deadline_seconds=timeout_seconds,
        )
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

    async def _apply_rack_operation_request(self, ctx: Any, intent: RuntimeIntent) -> None:
        from src.app.workline.models.timeline import (
            TimelineActionType,
            TimelineActorType,
            TimelineStage,
            TimelineStatus,
        )
        from src.app.workline.services import write_back_service as workline_effects

        payload_json = dict(intent.payload_json)
        operation_type = str(intent.action)
        operation_key = str(intent.idempotency_key)
        target_code = _normalize_rack_operation_target_code(intent.target_code)
        timeout_seconds = int(intent.timeout_seconds or 0)
        session = ctx["session"]

        service = self._rack_operation_service
        if service is None:
            from src.app.rack.services import rack_operation_service

            service = rack_operation_service

        ctx_map = cast("Mapping[str, Any]", ctx)
        trace_id = _non_empty_text(payload_json.get("trace_id")) or _non_empty_text(_ctx_trace_id(ctx_map))
        if trace_id is None:
            raise ValueError("RACK_OPERATION_REQUEST intent requires trace_id")
        task_specs = _required_rack_task_specs(payload_json)
        tasks = await service.request_operation_tasks(
            ctx_map["db"],
            operation_key=operation_key,
            operation_type=operation_type,
            workline=ctx_map["workline"],
            session=session,
            target_code=target_code,
            trace_id=trace_id,
            task_specs=task_specs,
            timeout_seconds=timeout_seconds,
        )

        self._mark_session_waiting_for_rack_operation(
            ctx,
            operation_key=operation_key,
            operation_type=operation_type,
            tasks=list(tasks),
            timeout_seconds=timeout_seconds,
        )
        await self._persist_session_waiting_for_rack_operation(ctx, timeout_seconds=timeout_seconds)

        workline_effects._clear_session_failure(session)
        await workline_effects._emit_timeline(
            ctx,
            stage=TimelineStage.WAITING,
            action_type=TimelineActionType.WAIT_STARTED,
            payload=workline_effects._wait_timeline_payload(
                ctx,
                wait_type="RACK_OPERATION",
                wait_token=operation_key,
                deadline_seconds=timeout_seconds,
            ),
            from_status=ctx["current_status"],
            to_status=session.status,
            actor_type=TimelineActorType.EXTERNAL_SYSTEM,
            actor_code="WMS_RCS",
            related_inbox_id=workline_effects._timeline_inbox_id(ctx),
            status=TimelineStatus.PENDING,
        )

    def _mark_session_waiting_for_rack_operation(
        self,
        ctx: Any,
        *,
        operation_key: str,
        operation_type: str,
        tasks: list[Any],
        timeout_seconds: int,
    ) -> None:
        from src.app.workline.services import write_back_service as workline_effects

        session = ctx["session"]
        context_json = dict(getattr(session, "context_json", None) or {})
        existing_operation = context_json.get("rack_operation")
        rack_operation = dict(existing_operation) if isinstance(existing_operation, Mapping) else {}
        required_tasks = [task for task in tasks if _rack_task_required(task)]
        task_dispatch_keys = [_rack_task_dispatch_key(task) for task in tasks]
        required_task_dispatch_keys = [_rack_task_dispatch_key(task) for task in required_tasks]
        released_rack_codes = [
            rack_code
            for task in tasks
            if _rack_task_is_releasing_source(task) and (rack_code := _rack_task_rack_code(task)) is not None
        ]
        target_position_code = _rack_operation_target_position_code(tasks)
        rack_operation.update(
            {
                "operation_key": operation_key,
                "operation_type": operation_type,
                "status": "PENDING",
                "task_count": len(tasks),
                "required_task_count": len(required_tasks),
                "task_sequences": [_rack_task_sequence_no(task) for task in tasks],
                "task_dispatch_keys": task_dispatch_keys,
                "required_task_dispatch_keys": required_task_dispatch_keys,
                "released_rack_codes": released_rack_codes,
            }
        )
        if target_position_code is not None:
            rack_operation["target_position_code"] = target_position_code
            rack_operation["work_position_code"] = target_position_code
        context_json["waiting_rack_operation_key"] = operation_key
        context_json["rack_operation"] = rack_operation
        session.context_json = context_json
        ctx["session_ctx"] = dict(context_json)
        workline_effects.workline_session_lifecycle_service.start_wait(
            session,
            wait_type="RACK_OPERATION",
            occurred_at=ctx["now"],
            deadline_seconds=timeout_seconds,
        )

    async def _persist_session_waiting_for_rack_operation(self, ctx: Any, *, timeout_seconds: int) -> None:
        from src.app.workline.repositories.session_repository import WorklineSessionRepository

        session = ctx["session"]
        await WorklineSessionRepository().persist_external_wait(
            ctx["db"],
            session_id=resolve_required_pk(session, "session"),
            wait_type="RACK_OPERATION",
            occurred_at=ctx["now"],
            timeout_seconds=timeout_seconds,
            context_json=getattr(session, "context_json", None),
        )

    async def _apply_handling_operation_request(self, ctx: Any, intent: RuntimeIntent) -> None:
        from src.app.workline.models.timeline import (
            TimelineActionType,
            TimelineActorType,
            TimelineStage,
            TimelineStatus,
        )
        from src.app.workline.services import write_back_service as workline_effects

        payload_json = dict(intent.payload_json)
        operation_type = str(intent.action)
        operation_key = str(intent.idempotency_key)
        timeout_seconds = int(intent.timeout_seconds or 0)
        session = ctx["session"]

        service = self._handling_operation_service
        if service is None:
            from src.app.handling.services import handling_operation_service

            service = handling_operation_service

        ctx_map = cast("Mapping[str, Any]", ctx)
        trace_id = _non_empty_text(payload_json.get("trace_id")) or _non_empty_text(_ctx_trace_id(ctx_map))
        if trace_id is None:
            raise ValueError(f"{intent.kind.value} intent requires trace_id")
        moves = _required_handling_move_specs(payload_json, intent.kind)

        _ = await service.request_bin_operation(
            ctx_map["db"],
            operation_key=operation_key,
            operation_type=operation_type,
            workline_id=optional_int(getattr(ctx_map["workline"], "id", None)),
            workline_code=_non_empty_text(
                getattr(ctx_map["workline"], "line_code", None) or getattr(ctx_map["workline"], "workline_code", None)
            ),
            material_session_id=optional_int(getattr(session, "id", None)),
            trace_id=trace_id,
            moves=moves,
            carrier_type=str(payload_json["carrier_type"]),
            carrier_code=_non_empty_text(payload_json.get("carrier_code")),
            timeout_seconds=timeout_seconds,
        )

        self._mark_session_waiting_for_handling_operation(
            ctx,
            operation_key=operation_key,
            operation_type=operation_type,
            moves=moves,
            rack_code=_non_empty_text(payload_json.get("rack_code")),
            timeout_seconds=timeout_seconds,
        )

        workline_effects._clear_session_failure(session)
        await workline_effects._emit_timeline(
            ctx,
            stage=TimelineStage.WAITING,
            action_type=TimelineActionType.WAIT_STARTED,
            payload=workline_effects._wait_timeline_payload(
                ctx,
                wait_type="HANDLING_OPERATION",
                wait_token=operation_key,
                deadline_seconds=timeout_seconds,
            ),
            from_status=ctx["current_status"],
            to_status=session.status,
            actor_type=TimelineActorType.EXTERNAL_SYSTEM,
            actor_code="WMS_RCS",
            related_inbox_id=workline_effects._timeline_inbox_id(ctx),
            status=TimelineStatus.PENDING,
        )

    def _mark_session_waiting_for_handling_operation(
        self,
        ctx: Any,
        *,
        operation_key: str,
        operation_type: str,
        moves: list[dict[str, Any]],
        rack_code: str | None,
        timeout_seconds: int,
    ) -> None:
        from src.app.workline.services import write_back_service as workline_effects

        session = ctx["session"]
        context_json = dict(getattr(session, "context_json", None) or {})
        existing_operation = context_json.get("handling_operation")
        handling_operation = dict(existing_operation) if isinstance(existing_operation, Mapping) else {}
        handling_operation.update(
            {
                "operation_key": operation_key,
                "operation_type": operation_type,
                "status": "PENDING",
                "move_count": len(moves),
                "move_sequences": [move.get("sequence_no") for move in moves],
            }
        )
        if rack_code is not None:
            handling_operation["rack_code"] = rack_code
        context_json["waiting_handling_operation_key"] = operation_key
        context_json["handling_operation"] = handling_operation
        session.context_json = context_json
        ctx["session_ctx"] = dict(context_json)
        workline_effects.workline_session_lifecycle_service.start_wait(
            session,
            wait_type="HANDLING_OPERATION",
            occurred_at=ctx["now"],
            deadline_seconds=timeout_seconds,
        )

    async def _apply_device_event(self, ctx: Any, intent: RuntimeIntent) -> None:
        service = self._inbox_service
        if service is None:
            from src.app.workline.services import inbox_service

            service = inbox_service

        ctx_map = cast("Mapping[str, Any]", ctx)
        payload = dict(intent.payload_json)
        from src.app.workline.services.inbox_service import DuplicateInboxError

        try:
            _ = await service.create_device_event_inbox(
                db=ctx_map["db"],
                device_code=str(payload["device_code"]),
                event_type=str(payload["event_type"]),
                timestamp=int(payload["timestamp"]),
                data=dict(payload["data"]),
                trace_id=_ctx_trace_id(ctx_map),
                event_id=payload.get("event_id"),
                causation_id=payload.get("causation_id"),
                canonical_event_type=payload.get("canonical_event_type"),
                auto_commit=False,
            )
        except DuplicateInboxError:
            return

    async def _apply_resource_fact(self, ctx: Any, intent: RuntimeIntent) -> Any:
        service = self._resource_projection_service
        if service is None:
            from src.app.resource.services import resource_projection_service

            service = resource_projection_service

        ctx_map = cast("Mapping[str, Any]", ctx)
        return await service.record_resource_fact(
            db=ctx_map["db"],
            session=ctx_map["session"],
            workline=ctx_map["workline"],
            fact_type=str(intent.action),
            payload_json=dict(intent.payload_json),
            idempotency_key=intent.idempotency_key,
            trace_id=_ctx_trace_id(ctx_map),
        )

    async def _apply_resource_reservation(self, ctx: Any, intent: RuntimeIntent) -> Any:
        service = self._bin_cell_reservation_service
        if service is None:
            from src.app.workline.services import workline_bin_cell_reservation_service

            service = workline_bin_cell_reservation_service

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

    async def _apply_command_wait(self, ctx: Any, intent: RuntimeIntent) -> None:
        from src.app.workline.models.timeline import (
            TimelineActionType,
            TimelineActorType,
            TimelineStage,
            TimelineStatus,
        )
        from src.app.workline.repositories.session_repository import WorklineSessionRepository
        from src.app.workline.services import write_back_service as workline_effects

        session = ctx["session"]
        timeout_seconds = _resolve_command_result_timeout_seconds(intent)
        workline_effects.workline_session_lifecycle_service.start_wait(
            session,
            wait_type="COMMAND_RESULT",
            occurred_at=ctx["now"],
            awaiting_command_id=ctx["awaiting_command_id"],
            deadline_seconds=timeout_seconds,
        )
        await WorklineSessionRepository().persist_command_result_wait(
            ctx["db"],
            session_id=resolve_required_pk(session, "session"),
            occurred_at=ctx["now"],
            command_id=ctx["awaiting_command_id"],
            timeout_seconds=timeout_seconds,
        )
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
        from src.app.workline.services import write_back_service as workline_effects

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
        from src.app.workline.repositories.session_repository import WorklineSessionRepository
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

        if intent.action:
            await self._apply_command(ctx, _command_producing_intent_to_command_intent(intent))
            return

        session = ctx["session"]
        workline_effects.workline_session_lifecycle_service.running(session)
        workline_effects._clear_session_wait(session)
        workline_effects._clear_session_failure(session)


__all__ = ["RuntimeIntentEffectApplier"]
