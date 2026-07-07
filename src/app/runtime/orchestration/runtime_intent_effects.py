# 阶段 2 burn-down C5a 镜像:src.workline_runtime.runtime_intent_effects 的平级副本
# wlr 目录在阶段 3 整体删除时,本镜像改名为正式模块。
# 自引用 src.workline_runtime.{effect_result, material_target_resolver,
# resource_wait_evidence, runtime_intent} 已重定向到本目录 / 本目录 bridge。

"""RuntimeIntent 到 Session / Command / Outbox / Timeline 的 effect 落地。"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any, cast
from urllib.parse import urlparse

from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from src.app.runtime.capability_catalog import get_workline_capability_definition
from src.app.runtime.orchestration.effect_result import RuntimeIntentEffectResult
from src.app.runtime.orchestration.material_target_resolver import resolve_destination_device
from src.app.runtime.orchestration.models.session import SessionStatus
from src.app.runtime.orchestration.resource_wait_evidence_bridge import ResourceWaitEvidence
from src.app.runtime.orchestration.runtime_intent import (
    BlockScope,
    Destination,
    DestinationKind,
    RuntimeIntent,
    RuntimeIntentKind,
)
from src.app.workline.domain.contracts.smt_sorting_inbound import (
    SMT_SORTING_INBOUND_PLUGIN_KEY,
    SORTING_CONTEXT_SCHEMA_VERSION,
)
from src.utils.value_normalization import optional_int, resolve_required_pk, string_value

logger = logging.getLogger(__name__)

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
_DEFAULT_RACK_OPERATION_TARGET_CODE = "WMS_RCS_RACK_OPERATION"
_FULFILLMENT_EXTERNAL_TARGET_CODES = frozenset(
    {
        "WMS_FULFILLMENT",
        "WMS_RCS_BIN_OPERATION",
        "WMS_RCS_FULL_BOX_EXCHANGE",
        "WMS_RCS_RACK_OPERATION",
    }
)
_DEFAULT_COMMAND_RESULT_TIMEOUT_SECONDS = 300
_STATION_DISPATCH_LEASE_UNAVAILABLE = "station dispatch lease is not available"
_SMT_SOURCE_PICK_COMMAND = "SORTING_SOURCE_PICK"
_SMT_TERMINAL_RESULT_MARKER_KEY = "smt_inbound_handoff_terminal_result"
_RESOURCE_WAIT_SUBJECT_CONTRACT_INVALID = "RESOURCE_WAIT_SUBJECT_CONTRACT_INVALID"


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


def _rack_operation_key_from_resource_fact(ctx: Mapping[str, Any], payload_json: Mapping[str, Any]) -> str | None:
    session = ctx.get("session")
    context_json = getattr(session, "context_json", None)
    context = context_json if isinstance(context_json, Mapping) else {}
    waiting_operation_key = _non_empty_text(context.get("waiting_rack_operation_key"))
    payload_operation_key = _non_empty_text(payload_json.get("operation_key"))
    if waiting_operation_key is not None and (
        payload_operation_key is None or payload_operation_key == waiting_operation_key
    ):
        return waiting_operation_key

    rack_operation = context.get("rack_operation")
    if isinstance(rack_operation, Mapping):
        context_operation_key = _non_empty_text(rack_operation.get("operation_key"))
        if payload_operation_key is not None and payload_operation_key != context_operation_key:
            return None
        return context_operation_key
    return None


def _non_empty_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _mapping_text(value: Mapping[str, Any], *field_names: str) -> str | None:
    for field_name in field_names:
        if (text := _non_empty_text(value.get(field_name))) is not None:
            return text
    return None


def _is_fulfillment_external_request(*, target_code: str, payload_json: Mapping[str, Any]) -> bool:
    operation_kind = _mapping_text(payload_json, "operation_kind")
    if operation_kind is not None:
        from src.app.runtime.orchestration.services.idempotency_guard import get_idempotency_operation_spec

        return get_idempotency_operation_spec(operation_kind).operation_kind == "fulfillment"
    return target_code in _FULFILLMENT_EXTERNAL_TARGET_CODES


def _fulfillment_provider_code(intent: RuntimeIntent, *, target_code: str, payload_json: Mapping[str, Any]) -> str:
    return (
        _mapping_text(payload_json, "provider_code", "source_system", "provider")
        or _non_empty_text(intent.source_system)
        or ("WMS" if target_code.startswith("WMS") else target_code)
    )


def _canonical_payload_hash(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


async def _claim_external_fulfillment_idempotency_if_needed(
    ctx: Mapping[str, Any],
    intent: RuntimeIntent,
    *,
    dispatch_key: str,
    target_code: str,
    payload_json: Mapping[str, Any],
) -> Any | None:
    """在实际创建 fulfillment outbox 前 claim 幂等键；缺少 correlation 的 legacy 请求保持旧行为。"""

    if not _is_fulfillment_external_request(target_code=target_code, payload_json=payload_json):
        return None
    correlation_id = _mapping_text(payload_json, "correlation_id", "execution_correlation_id")
    if correlation_id is None:
        return None

    from src.app.runtime.orchestration.services.idempotency_guard import idempotency_guard
    from src.utils.timezone import timezone as timezone_utils

    idempotency_key = (
        _mapping_text(payload_json, "idempotency_key", "request_id", "dispatch_key", "exchange_request_code")
        or dispatch_key
    )
    return await idempotency_guard.claim_or_match(
        ctx["db"],
        provider_code=_fulfillment_provider_code(intent, target_code=target_code, payload_json=payload_json),
        operation_kind="fulfillment",
        idempotency_key=idempotency_key,
        request_hash=_canonical_payload_hash(payload_json),
        execution_correlation_id=correlation_id,
        now_ms=int(timezone_utils.now_utc().timestamp() * 1000),
        business_owner_key=f"fulfillment:{dispatch_key}",
    )


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

    material_context = payload_json.get("material")
    specs: list[dict[str, Any]] = []
    for index, raw_spec in enumerate(raw_specs, start=1):
        if not isinstance(raw_spec, Mapping):
            raise TypeError(f"RACK_OPERATION_REQUEST payload.rack_tasks[{index}] must be a mapping")
        spec = dict(cast("Mapping[str, Any]", raw_spec))
        if isinstance(material_context, Mapping):
            request_json = spec.get("request_json")
            merged_request_json = dict(request_json) if isinstance(request_json, Mapping) else {}
            merged_request_json.setdefault("material", dict(material_context))
            spec["request_json"] = merged_request_json
        specs.append(spec)
    return specs


def _station_position_code_from_mapping(value: Mapping[str, Any]) -> str | None:
    for key in ("target_position_code", "work_position_code", "position_code", "endpoint_code"):
        if (position_code := _non_empty_text(value.get(key))) is not None:
            return position_code

    station = value.get("station")
    if isinstance(station, Mapping):
        return _station_position_code_from_mapping(cast("Mapping[str, Any]", station))
    return None


def _station_position_code_from_rack_operation_request(
    payload_json: Mapping[str, Any],
    task_specs: list[dict[str, Any]],
) -> str | None:
    if (position_code := _station_position_code_from_mapping(payload_json)) is not None:
        return position_code

    for task_spec in task_specs:
        if (position_code := _station_position_code_from_mapping(task_spec)) is not None:
            return position_code
        request_json = task_spec.get("request_json")
        if (
            isinstance(request_json, Mapping)
            and (position_code := _station_position_code_from_mapping(cast("Mapping[str, Any]", request_json)))
            is not None
        ):
            return position_code
    return None


def _is_station_dispatch_lease_unavailable(exc: ValueError) -> bool:
    return str(exc) == _STATION_DISPATCH_LEASE_UNAVAILABLE


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


def _state_value(value: Any) -> str:
    raw = getattr(value, "value", value)
    return string_value(raw, "")


def _ctx_plugin_key(ctx: Mapping[str, Any]) -> str | None:
    for source_name in ("session", "workline"):
        plugin_key = string_value(getattr(ctx.get(source_name), "plugin_key", None), "")
        if plugin_key:
            return plugin_key
    return None


def _material_unit_status_transition_targets(
    ctx: Mapping[str, Any], *, from_state: str
) -> tuple[str, tuple[str, ...]] | None:
    plugin_key = _ctx_plugin_key(ctx)
    definition = get_workline_capability_definition(plugin_key)
    if definition is None:
        return None

    try:
        manifest = getattr(definition, "manifest", None)
    except (ImportError, AttributeError, TypeError, ValueError):
        logger.debug(
            "skip material unit status transition warning because plugin manifest is unavailable: plugin_key=%s",
            plugin_key,
            exc_info=True,
        )
        return None
    for state_machine in getattr(manifest, "state_machines", ()) or ():
        state_owner = getattr(state_machine, "state_owner", None)
        if getattr(state_owner, "model", None) != "MaterialUnit" or getattr(state_owner, "field", None) != "status":
            continue
        for transition in getattr(state_machine, "transitions", ()) or ():
            if _state_value(getattr(transition, "from_state", None)) == from_state:
                return plugin_key or "", tuple(_state_value(state) for state in getattr(transition, "to_states", ()))
    return None


def _warn_material_unit_status_transition_if_outside_manifest(
    ctx: Mapping[str, Any],
    material_unit: Any,
    *,
    from_state: str,
    to_state: str,
) -> None:
    transition_contract = _material_unit_status_transition_targets(ctx, from_state=from_state)
    if transition_contract is None:
        return

    plugin_key, allowed_to_states = transition_contract
    if to_state in allowed_to_states:
        return

    material_unit_id = getattr(material_unit, "id", None)
    pkg_code = string_value(getattr(material_unit, "pkg_code", None), "")
    logger.warning(
        "material unit status transition is outside manifest contract "
        "object_type=REEL object_id=%s from_state=%s to_state=%s pkg_code=%s plugin_key=%s suggestion=%s",
        material_unit_id,
        from_state,
        to_state,
        pkg_code,
        plugin_key,
        "check whether the plugin is missing a transition or the manifest transitions contract lacks this from->to move",
    )


def _apply_material_unit_status_write(
    ctx: Mapping[str, Any],
    material_unit: Any,
    *,
    from_state: str | None,
    to_status: Any,
) -> None:
    to_state = _state_value(to_status)
    if from_state is not None:
        _warn_material_unit_status_transition_if_outside_manifest(
            ctx,
            material_unit,
            from_state=from_state,
            to_state=to_state,
        )
        if to_state == "RECONCILING" and from_state and from_state != "RECONCILING":
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
    """create 路径所有权检查：白名单语义（仅当 owner 明确非终态才拒绝）。

    与 SMT handoff claim 路径（smt_inbound_handoff_service._link_claim_session_material_unit）
    的黑名单语义有意区分：
    - 本函数处理"扫码新建/补建"场景。owner_session 不存在（已硬删）= 孤儿料盘，放行回收，
      避免历史数据让 SCAN_COMPLETED 永远卡死。
    - claim 路径处理"跨线 handoff 接管"场景，owner 未知时风险更高（接管错的料盘）→ 保守拒绝。
    """
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


def _positive_int_payload(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.isdigit():
        parsed = int(value)
        return parsed if parsed > 0 else None
    return None


def _source_pick_command_correlation(intent: RuntimeIntent) -> dict[str, int] | None:
    if intent.action != _SMT_SOURCE_PICK_COMMAND:
        return None
    payload = intent.payload_json
    handoff_demand_id = _positive_int_payload(payload.get("handoff_demand_id"))
    source_item_id = _positive_int_payload(payload.get("handoff_source_item_id"))
    claim_attempt_no = _positive_int_payload(payload.get("claim_attempt_no"))
    source_pick_inbox_id = _positive_int_payload(payload.get("source_pick_inbox_id"))
    if None in {handoff_demand_id, source_item_id, claim_attempt_no, source_pick_inbox_id}:
        return None
    return {
        "handoff_demand_id": cast("int", handoff_demand_id),
        "source_item_id": cast("int", source_item_id),
        "claim_attempt_no": cast("int", claim_attempt_no),
        "source_pick_inbox_id": cast("int", source_pick_inbox_id),
    }


async def _record_source_pick_command_correlation(
    ctx: Any,
    intent: RuntimeIntent,
    *,
    command: Any,
    command_outbox: Any,
) -> None:
    correlation = _source_pick_command_correlation(intent)
    if correlation is None:
        return

    from src.app.runtime.orchestration.services.intent.smt_inbound_handoff_service import smt_inbound_handoff_service

    _ = await smt_inbound_handoff_service.record_source_pick_command_correlation(
        ctx["db"],
        command_id=resolve_required_pk(command, "source_pick_command"),
        command_code=string_value(getattr(command, "command_code", None), ""),
        dispatch_key=string_value(getattr(command_outbox, "dispatch_key", None), ""),
        trace_id=string_value(ctx.get("trace_id"), ""),
        **correlation,
    )


def _source_pick_success_command_id(ctx: Any) -> int | None:
    return optional_int(getattr(ctx["inbox"], "command_id", None)) or optional_int(
        ctx.get("awaiting_device_command_pk")
    )


def _has_smt_handoff_source_pick_evidence(ctx: Any, *, command_id: int | None) -> bool:
    if _smt_handoff_source_item_id(ctx) is not None:
        return True
    return command_id is not None


async def _record_source_pick_success(ctx: Any, *, command_id: int | None = None) -> None:
    if not _has_smt_handoff_source_pick_evidence(ctx, command_id=command_id):
        return

    from src.app.runtime.orchestration.services.intent.smt_inbound_handoff_service import smt_inbound_handoff_service

    try:
        _ = await smt_inbound_handoff_service.record_source_pick_success(
            ctx["db"],
            session=ctx["session"],
            command_id=command_id,
            trace_id=string_value(ctx.get("trace_id"), ""),
        )
    except ValueError as exc:
        if "source pick success 缺少 source_item_id" in str(exc):
            return
        raise


def _terminal_command_id(ctx: Any) -> int | None:
    return optional_int(getattr(ctx["inbox"], "command_id", None)) or optional_int(
        ctx.get("awaiting_device_command_pk")
    )


def _target_terminal_evidence(ctx: Any) -> dict[str, Any]:
    return {
        "target_command_payload": dict(getattr(ctx["inbox"], "payload_json", None) or {}),
    }


def _is_smt_sorting_inbound_session(ctx: Any) -> bool:
    plugin_keys = {
        string_value(getattr(ctx.get("workline"), "plugin_key", None), ""),
        string_value(getattr(ctx.get("session"), "plugin_key", None), ""),
        string_value(ctx.get("plugin_key"), ""),
    }
    if SMT_SORTING_INBOUND_PLUGIN_KEY in plugin_keys:
        return True
    context_json = getattr(ctx["session"], "context_json", None)
    if not isinstance(context_json, Mapping):
        return False
    sorting = context_json.get("sorting")
    return (
        isinstance(sorting, Mapping)
        and optional_int(sorting.get("context_schema_version")) == SORTING_CONTEXT_SCHEMA_VERSION
    )


def _smt_handoff_source_item_id(ctx: Any) -> int | None:
    context_json = getattr(ctx["session"], "context_json", None)
    if not isinstance(context_json, Mapping):
        return None
    sorting = context_json.get("sorting")
    if not isinstance(sorting, Mapping):
        return None
    source_pick_request = sorting.get("source_pick_request")
    if not isinstance(source_pick_request, Mapping):
        return None
    return optional_int(source_pick_request.get("handoff_source_item_id"))


def _can_record_smt_target_terminal_result(ctx: Any) -> bool:
    return _is_smt_sorting_inbound_session(ctx) and _smt_handoff_source_item_id(ctx) is not None


def _consume_terminal_result_marker(ctx: Any) -> dict[str, Any] | None:
    session = ctx["session"]
    context_json = getattr(session, "context_json", None)
    if not isinstance(context_json, Mapping):
        return None
    marker = context_json.get(_SMT_TERMINAL_RESULT_MARKER_KEY)
    if not isinstance(marker, Mapping):
        return None

    updated_context = dict(context_json)
    updated_context.pop(_SMT_TERMINAL_RESULT_MARKER_KEY, None)
    session.context_json = updated_context
    ctx["session_ctx"] = dict(updated_context)
    return dict(cast("Mapping[str, Any]", marker))


def _terminal_marker_evidence(marker: Mapping[str, Any]) -> dict[str, Any]:
    evidence = marker.get("terminal_evidence")
    return dict(cast("Mapping[str, Any]", evidence)) if isinstance(evidence, Mapping) else {}


async def _record_source_item_terminal_result(
    ctx: Any,
    *,
    terminal_status: str,
    command_id: int | None,
    terminal_evidence: Mapping[str, Any] | None,
) -> Any:
    from src.app.runtime.orchestration.services.intent.smt_inbound_handoff_service import smt_inbound_handoff_service

    result = await smt_inbound_handoff_service.record_source_item_terminal_result(
        ctx["db"],
        session=ctx["session"],
        terminal_status=terminal_status,
        command_id=command_id,
        trace_id=string_value(ctx.get("trace_id"), ""),
        terminal_evidence=dict(terminal_evidence or {}),
    )
    if bool(getattr(result, "advanced", False)):
        current_demand_id = optional_int(getattr(result, "current_demand_id", None))
        await smt_inbound_handoff_service.claim_next_source_item(
            ctx["db"],
            trace_id=string_value(ctx.get("trace_id"), ""),
            demand_id=current_demand_id,
        )
    return result


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

    async def apply(self, ctx: Any, intents: list[RuntimeIntent]) -> RuntimeIntentEffectResult:  # noqa: PLR0912
        _validate_runtime_intents(intents)

        from src.app.workline.services import write_back_service as workline_effects

        workline_effects._sync_effect_trace_fields(ctx)
        if not intents:
            await self._apply_noop_completion(ctx)
            return RuntimeIntentEffectResult.processed()

        terminal_state = SimpleNamespace(
            pending_source_pick_success=False,
            pending_source_pick_success_command_id=None,
            pending_target_terminal_success=False,
            pending_target_terminal_command_id=None,
            skip_next_material_unit_intent=False,
        )
        for intent in intents:
            skip_material_unit_intent = terminal_state.skip_next_material_unit_intent and intent.kind in {
                RuntimeIntentKind.CREATE_MATERIAL_UNIT,
                RuntimeIntentKind.UPDATE_MATERIAL_UNIT_STATUS,
            }
            if terminal_state.skip_next_material_unit_intent and not skip_material_unit_intent:
                terminal_state.skip_next_material_unit_intent = False

            if intent.kind == RuntimeIntentKind.UPDATE_CONTEXT:
                await self._apply_update_context(ctx, intent, workline_effects, terminal_state)
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
                result = await self._apply_rack_operation_request(ctx, intent)
                if result is not None:
                    return result
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
                    await self._apply_resource_reconciliation_hold(ctx, result)
                    return RuntimeIntentEffectResult.processed()
                terminal_state.skip_next_material_unit_intent = _is_duplicate_result(result)
                if str(intent.action) == "MATERIAL_UNMOUNTED":
                    terminal_state.pending_source_pick_success = True
                    terminal_state.pending_source_pick_success_command_id = _source_pick_success_command_id(ctx)
                if str(intent.action) == "MATERIAL_MOUNTED" and _can_record_smt_target_terminal_result(ctx):
                    terminal_state.pending_target_terminal_success = True
                    terminal_state.pending_target_terminal_command_id = _terminal_command_id(ctx)
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
                    terminal_state.skip_next_material_unit_intent = False
                    continue
                await self._apply_create_material_unit(ctx, intent)
                continue

            if intent.kind == RuntimeIntentKind.UPDATE_MATERIAL_UNIT_STATUS:
                if skip_material_unit_intent:
                    terminal_state.skip_next_material_unit_intent = False
                    continue
                await self._apply_update_material_unit_status(ctx, intent)
                continue

            if intent.kind == RuntimeIntentKind.COMPLETE:
                _merge_context_patch(ctx, intent.context_patch)
                workline_effects._apply_context_patch(ctx)
                if terminal_state.pending_target_terminal_success:
                    await _record_source_item_terminal_result(
                        ctx,
                        terminal_status="SORTED",
                        command_id=terminal_state.pending_target_terminal_command_id,
                        terminal_evidence=_target_terminal_evidence(ctx),
                    )
                    terminal_state.pending_target_terminal_success = False
                    terminal_state.pending_target_terminal_command_id = None
                terminal_marker = _consume_terminal_result_marker(ctx)
                if terminal_marker is not None:
                    await _record_source_item_terminal_result(
                        ctx,
                        terminal_status=string_value(terminal_marker.get("terminal_status"), ""),
                        command_id=optional_int(terminal_marker.get("command_id")) or _terminal_command_id(ctx),
                        terminal_evidence=_terminal_marker_evidence(terminal_marker),
                    )
                workline_effects._clear_session_failure(ctx["session"])
                ctx["orch_result"].complete = True
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

        return RuntimeIntentEffectResult.processed()

    async def _apply_create_material_unit(self, ctx: Any, intent: RuntimeIntent) -> None:
        from src.app.runtime.orchestration.models.material_unit import MaterialUnit, MaterialUnitStatus

        session = ctx["session"]
        db = ctx["db"]
        pkg_code = string_value(intent.payload_json.get("pkg_code"), "")
        material_identity_key = string_value(intent.payload_json.get("material_identity_key"), "")
        six_in_one = dict(cast("Mapping[str, Any]", intent.payload_json.get("six_in_one") or {}))
        status = MaterialUnitStatus(string_value(intent.payload_json.get("status"), ""))
        current_session_id = resolve_required_pk(session, "session")
        flush = getattr(db, "flush", None)

        async def get_material_unit_by_pkg_code() -> Any | None:
            # with_for_update 锁住已存在料盘行直到事务结束，消除复用路径的 TOCTOU 窗口：
            # 并发事务同时读到同一 material_unit 后盲目覆盖 current_session_id。
            # SQLite 静默忽略 FOR UPDATE；Postgres 行锁串行化所有权转移。
            result = await db.execute(
                select(MaterialUnit).where(MaterialUnit.pkg_code == pkg_code).limit(1).with_for_update()
            )
            material_units = list(result.scalars().all())
            if len(material_units) > 1:
                raise ValueError(f"multiple material units found for pkg_code: {pkg_code}")
            return material_units[0] if material_units else None

        material_unit = await get_material_unit_by_pkg_code()
        status_from_state = _state_value(getattr(material_unit, "status", None)) if material_unit is not None else None
        if material_unit is not None:
            # 复用已存在的料盘实体（跨 Session handoff 的正常路径）。
            # 若该实体仍被另一个非终态 Session 持有，说明出现跨线并发或重复 claim，
            # 拒绝静默窃取所有权，避免双 Session 指向同一料盘造成状态分裂。
            await _reject_reuse_when_owned_by_active_session(db, material_unit, current_session_id=current_session_id)
        if material_unit is None:
            begin_nested = getattr(db, "begin_nested", None)
            if callable(begin_nested):
                try:
                    # 唯一索引竞争回滚到 savepoint，避免污染外层事务。
                    async with cast("Any", begin_nested)():
                        material_unit = MaterialUnit(
                            pkg_code=pkg_code,
                            material_identity_key=material_identity_key,
                            six_in_one=six_in_one,
                            status=status,
                            current_session_id=current_session_id,
                        )
                        db.add(material_unit)
                        if flush is not None:
                            await flush()
                except IntegrityError:
                    material_unit = await get_material_unit_by_pkg_code()
                    if material_unit is None:
                        raise
                    await _reject_reuse_when_owned_by_active_session(
                        db, material_unit, current_session_id=current_session_id
                    )
                    status_from_state = _state_value(getattr(material_unit, "status", None))
            else:
                material_unit = MaterialUnit(
                    pkg_code=pkg_code,
                    material_identity_key=material_identity_key,
                    six_in_one=six_in_one,
                    status=status,
                    current_session_id=current_session_id,
                )
                db.add(material_unit)

        material_unit.material_identity_key = material_identity_key
        # six_in_one 合并而非覆盖：保留已有字段（如粗分机扫码的完整六合一码），
        # 仅用本次 payload 的非空值更新，避免 SMT 接管时用瘦构造 dict 丢数据。
        merged_six_in_one = {
            **dict(material_unit.six_in_one or {}),
            **{key: value for key, value in six_in_one.items() if value is not None},
        }
        material_unit.six_in_one = merged_six_in_one
        if "current_location" in intent.payload_json:
            material_unit.current_location = intent.payload_json.get("current_location")
        _apply_material_unit_status_write(ctx, material_unit, from_state=status_from_state, to_status=status)
        material_unit.current_session_id = current_session_id
        if flush is not None:
            await flush()
        session.current_material_unit_id = resolve_required_pk(material_unit, "material_unit")

    async def _apply_update_material_unit_status(self, ctx: Any, intent: RuntimeIntent) -> None:
        from src.app.runtime.orchestration.models.material_unit import MaterialUnit, MaterialUnitStatus

        material_unit_id = optional_int(intent.payload_json.get("material_unit_id"))
        if material_unit_id is None:
            raise ValueError(
                f"UPDATE_MATERIAL_UNIT_STATUS intent requires valid integer material_unit_id, "
                f"got: {intent.payload_json.get('material_unit_id')!r}"
            )
        material_unit = await ctx["db"].get(MaterialUnit, material_unit_id)
        if material_unit is None:
            raise ValueError(f"material unit not found: {material_unit_id}")

        session = ctx["session"]
        current_session_id = resolve_required_pk(session, "session")
        if intent.payload_json.get("clear_session_reference") is True and (
            getattr(material_unit, "current_session_id", None) != current_session_id
            or getattr(session, "current_material_unit_id", None) != material_unit_id
        ):
            return

        from_status = _state_value(getattr(material_unit, "status", None))
        to_status = MaterialUnitStatus(string_value(intent.payload_json.get("status"), ""))
        _apply_material_unit_status_write(ctx, material_unit, from_state=from_status, to_status=to_status)
        if "current_location" in intent.payload_json:
            material_unit.current_location = intent.payload_json.get("current_location")
        material_unit.current_session_id = current_session_id
        if intent.payload_json.get("clear_session_reference") is True:
            # 同批次清理登记在 ctx；同时持久化到 session.context_json，
            # 以便 NG 冲突进 MANUAL_HOLD 后跨 inbox 批次恢复到 COMPLETE 时仍能清理。
            cleanup_ids = ctx.setdefault("_runtime_material_unit_cleanup_ids", set())
            cleanup_ids.add(material_unit_id)
            _persist_pending_cleanup_ids(session, set(cleanup_ids))
            return
        session.current_material_unit_id = material_unit_id

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
        terminal_state: SimpleNamespace,
    ) -> None:
        _merge_context_patch(ctx, intent.context_patch)
        workline_effects._apply_context_patch(ctx)
        if terminal_state.pending_source_pick_success:
            await _record_source_pick_success(ctx, command_id=terminal_state.pending_source_pick_success_command_id)
            terminal_state.pending_source_pick_success = False
            terminal_state.pending_source_pick_success_command_id = None
        if terminal_state.pending_target_terminal_success:
            await _record_source_item_terminal_result(
                ctx,
                terminal_status="SORTED",
                command_id=terminal_state.pending_target_terminal_command_id,
                terminal_evidence=_target_terminal_evidence(ctx),
            )
            terminal_state.pending_target_terminal_success = False
            terminal_state.pending_target_terminal_command_id = None
        terminal_marker = _consume_terminal_result_marker(ctx)
        if terminal_marker is not None:
            await _record_source_item_terminal_result(
                ctx,
                terminal_status=string_value(terminal_marker.get("terminal_status"), ""),
                command_id=optional_int(terminal_marker.get("command_id")) or _terminal_command_id(ctx),
                terminal_evidence=_terminal_marker_evidence(terminal_marker),
            )

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
            actor_type=TimelineActorType.PLUGIN,
            actor_code=getattr(ctx["workline"], "plugin_key", None),
            message=intent.message,
            related_inbox_id=workline_effects._timeline_inbox_id(ctx),
            status=TimelineStatus.SUCCESS,
        )

    async def _apply_command(self, ctx: Any, intent: RuntimeIntent) -> None:
        from src.app.device.repositories.command_repository import DeviceCommandRepository
        from src.app.runtime.orchestration.models.timeline import (
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
            workline_effects._map_command_task_type(str(intent.action)),
            session_id=resolve_required_pk(ctx["session"], "session"),
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

        ctx["awaiting_device_command_pk"] = command.id
        ctx["awaiting_command_code"] = command.command_code

        command_outbox = workline_effects._build_command_outbox_model(ctx, command=command, device_code=device_code)
        ctx["db"].add(command_outbox)
        await _record_source_pick_command_correlation(ctx, intent, command=command, command_outbox=command_outbox)
        await workline_effects._emit_timeline(
            ctx,
            stage=TimelineStage.DISPATCH_PREPARE,
            action_type=TimelineActionType.COMMAND_SENT,
            payload=workline_effects._command_timeline_payload(
                ctx,
                command_code=command.command_code,
                task_type=str(intent.action),
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
        from src.app.runtime.orchestration.models.timeline import (
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

        claim_result = await _claim_external_fulfillment_idempotency_if_needed(
            ctx,
            intent,
            dispatch_key=dispatch_key,
            target_code=target_code,
            payload_json=payload_json,
        )
        if claim_result == "MATCH":
            return
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

    async def _apply_rack_operation_request(self, ctx: Any, intent: RuntimeIntent) -> RuntimeIntentEffectResult | None:
        from src.app.runtime.orchestration.models.timeline import (
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
        try:
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
        except ValueError as exc:
            if not _is_station_dispatch_lease_unavailable(exc):
                raise
            position_code = _station_position_code_from_rack_operation_request(payload_json, task_specs)
            if position_code is None:
                raise
            return await self._apply_resource_wait(
                ctx,
                RuntimeIntent.resource_wait(
                    subject_type=position_code,
                    subject_key=f"station:{position_code}",
                    projection_type="STATION_LEASE",
                    reason_code="STATION_LEASE_CLAIM_FAILED",
                    message="目标 Station dispatch lease 已被其它会话占用，等待资源释放后自动重试",
                    suggested_action="等待目标 Station dispatch lease 释放，或检查当前 rack operation/session 占用",
                    payload={
                        "position_code": position_code,
                        "operation_key": operation_key,
                        "operation_type": operation_type,
                        "target_code": target_code,
                        "trace_id": trace_id,
                    },
                ),
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
        return None

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
        from src.app.runtime.orchestration.repositories.session_repository import WorklineSessionRepository

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
        from src.app.runtime.orchestration.models.timeline import (
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
        from src.app.runtime.orchestration.services.inbox.inbox_service import DuplicateInboxError

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
        result = await service.record_resource_fact(
            db=ctx_map["db"],
            session=ctx_map["session"],
            workline=ctx_map["workline"],
            fact_type=fact_type,
            payload_json=dict(intent.payload_json),
            idempotency_key=intent.idempotency_key,
            trace_id=_ctx_trace_id(ctx_map),
        )
        await self._sync_rack_operation_after_resource_fact(ctx, dict(intent.payload_json))
        return result

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
            correlation_id=_non_empty_text(payload.get("correlation_id")),
            source_event_id=_non_empty_text(payload.get("source_event_id")),
            source_version=_non_empty_text(payload.get("source_version")),
            idempotency_key=intent.idempotency_key or _non_empty_text(payload.get("idempotency_key")),
            external_reference_type=_non_empty_text(payload.get("external_reference_type")),
            external_reference_value=_non_empty_text(payload.get("external_reference_value")),
            provider_code=_non_empty_text(payload.get("provider_code")),
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
                _non_empty_text(payload.get("reason_code"))
                or _non_empty_text(payload.get("scenario"))
                or "RECONCILIATION_EVIDENCE"
            ),
            business_step="RECONCILIATION_EVIDENCE",
            source="PHASE4_RECONCILIATION",
            evidence_json=payload,
            correlation_id=_non_empty_text(payload.get("correlation_id")),
            source_event_id=_non_empty_text(payload.get("source_event_id")),
            source_version=_non_empty_text(payload.get("source_version")),
            idempotency_key=intent.idempotency_key,
            external_reference_type=_non_empty_text(external_reference_map.get("type")),
            external_reference_value=_non_empty_text(external_reference_map.get("value")),
            provider_code=_non_empty_text(payload.get("provider_code")),
            auto_commit=False,
        )

    async def _sync_rack_operation_after_resource_fact(self, ctx: Any, payload_json: dict[str, Any]) -> None:
        operation_key = _rack_operation_key_from_resource_fact(ctx, payload_json)
        if operation_key is None:
            return

        service = self._rack_operation_service
        if service is None:
            from src.app.rack.services import rack_operation_service

            service = rack_operation_service

        _ = await service.sync_operation_status(ctx["db"], operation_key=operation_key)

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
        plugin_key = (
            _non_empty_text(getattr(workline, "plugin_key", None))
            or _non_empty_text(getattr(session, "plugin_key", None))
            or _non_empty_text(payload_json.get("plugin_key"))
        )
        plugin_definition = get_workline_capability_definition(plugin_key)
        if plugin_definition is None:
            return await self._reject_resource_wait_subject_contract(
                ctx,
                intent,
                subject_type=subject_type,
                subject_key=subject_key,
                projection_type=projection_type,
                plugin_key=plugin_key,
                contract_error="RESOURCE_WAIT manifest is missing or unknown",
            )
        try:
            plugin_definition.manifest.validate_resource_wait_subject(
                subject_type=subject_type,
                projection_type=projection_type,
            )
        except ValueError as exc:
            return await self._reject_resource_wait_subject_contract(
                ctx,
                intent,
                subject_type=subject_type,
                subject_key=subject_key,
                projection_type=projection_type,
                plugin_key=plugin_key,
                contract_error=str(exc),
            )
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
            trace_id=_non_empty_text(_ctx_trace_id(cast("Mapping[str, Any]", ctx))),
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

    async def _reject_resource_wait_subject_contract(
        self,
        ctx: Any,
        intent: RuntimeIntent,
        *,
        subject_type: str,
        subject_key: str,
        projection_type: str,
        plugin_key: str | None,
        contract_error: str,
    ) -> RuntimeIntentEffectResult:
        from src.app.runtime.orchestration.diagnostics import (
            ErrorCode,
            build_diagnostic_context,
            build_diagnostic_event,
        )
        from src.app.workline.services.diagnostic_service import workline_diagnostic_service

        session = ctx["session"]
        inbox = ctx["inbox"]
        workline = ctx["workline"]
        payload_json = dict(intent.payload_json)
        details = {
            key: value
            for key, value in payload_json.items()
            if key not in {"subject_type", "subject_key", "projection_type"}
        }
        details.update(
            {
                "contract_error": contract_error,
                "plugin_key": plugin_key,
                "original_reason_code": intent.reason_code,
                "original_message": intent.message,
                "suggested_action": intent.suggested_action,
            }
        )
        evidence = ResourceWaitEvidence.build(
            inbox_id=resolve_required_pk(inbox, "inbox"),
            subject_type=subject_type,
            subject_key=subject_key,
            projection_type=projection_type,
            reason_code=_RESOURCE_WAIT_SUBJECT_CONTRACT_INVALID,
            message=f"RESOURCE_WAIT subject contract invalid: {contract_error}",
            occurred_at=ctx["now"],
            session_id=optional_int(getattr(session, "id", None)),
            workline_id=optional_int(getattr(workline, "id", None))
            or optional_int(getattr(session, "workline_id", None)),
            trace_id=_non_empty_text(_ctx_trace_id(cast("Mapping[str, Any]", ctx))),
            details=details,
        )
        context = build_diagnostic_context(
            trace_id=evidence.trace_id,
            session=session,
            inbox=inbox,
            workline=workline,
            extra={
                "subject_type": subject_type,
                "subject_key": subject_key,
                "projection_type": projection_type,
                "reason_code": evidence.reason_code,
            },
        )
        event = build_diagnostic_event(
            error_code=ErrorCode.RESOURCE_WAIT,
            context=context,
            message=evidence.message,
            operator_action="检查 RESOURCE_WAIT subject 是否已声明在插件 manifest 中",
        )
        _ = await workline_diagnostic_service.record_event(
            ctx["db"],
            event=event,
            evidence=evidence.to_diagnostic_evidence(),
            diagnostic_key_override=f"{evidence.diagnostic_key}:SUBJECT_CONTRACT",
            auto_commit=False,
        )
        return RuntimeIntentEffectResult.processed()

    async def _apply_command_wait(self, ctx: Any, intent: RuntimeIntent) -> None:
        from src.app.runtime.orchestration.models.timeline import (
            TimelineActionType,
            TimelineActorType,
            TimelineStage,
            TimelineStatus,
        )
        from src.app.runtime.orchestration.repositories.session_repository import WorklineSessionRepository
        from src.app.workline.services import write_back_service as workline_effects

        session = ctx["session"]
        timeout_seconds = _resolve_command_result_timeout_seconds(intent)
        workline_effects.workline_session_lifecycle_service.start_wait(
            session,
            wait_type="COMMAND_RESULT",
            occurred_at=ctx["now"],
            awaiting_device_command_code=ctx["awaiting_command_code"],
            deadline_seconds=timeout_seconds,
        )
        await WorklineSessionRepository().persist_command_result_wait(
            ctx["db"],
            session_id=resolve_required_pk(session, "session"),
            occurred_at=ctx["now"],
            command_code=ctx["awaiting_command_code"],
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
            related_command_id=ctx["awaiting_device_command_pk"],
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

    async def _apply_cancel(self, ctx: Any, intent: RuntimeIntent) -> None:
        from src.app.runtime.orchestration.models.timeline import TimelineActionType, TimelineActorType, TimelineStage
        from src.app.runtime.orchestration.repositories.session_repository import WorklineSessionRepository
        from src.app.sys.repositories import SystemOutboxRepository
        from src.app.workline.services import write_back_service as workline_effects

        session = ctx["session"]
        session_id = resolve_required_pk(session, "session")
        workline_effects.workline_session_lifecycle_service.cancel(session, occurred_at=ctx["now"])
        await WorklineSessionRepository().persist_cancelled(
            ctx["db"],
            session_id=session_id,
            occurred_at=ctx["now"],
        )
        _ = await SystemOutboxRepository().cancel_active_by_session(
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

        if intent.action:
            await self._apply_command(ctx, _command_producing_intent_to_command_intent(intent))
            return

        session = ctx["session"]
        workline_effects.workline_session_lifecycle_service.running(session)
        workline_effects._clear_session_wait(session)
        workline_effects._clear_session_failure(session)


__all__ = ["RuntimeIntentEffectApplier"]
