"""SMT 满箱交换插件。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from src.workline_plugins.smt_full_box_exchange.contract import (
    SINGLE_LAYER_RACK_RELEASED,
    SMT_FORCE_EXCHANGE_RELEASE_REASON_CODES,
    WMS_FULL_BOX_EXCHANGE_CALLBACK,
    resolve_smt_full_box_exchange_business_key,
)
from src.workline_runtime.plugin_base import WorklinePlugin, build_payload_invalid_block, on_event
from src.workline_runtime.plugin_manifest import DeviceRoleRequirement, WorklinePluginManifest
from src.workline_runtime.plugin_sdk.contracts import NormalizedExternalCallback
from src.workline_runtime.runtime_intent import BlockScope, RuntimeIntent

_PROGRESS_STATUSES = {"ACCEPTED", "QUEUED", "IN_PROGRESS", "PHYSICAL_COMPLETED", "RESOURCE_PROJECTED"}
_RESOURCE_REJECTION_STATUSES = {"REJECTED_EXCHANGE_AREA_FULL", "REJECTED_EMPTY_BIN_UNAVAILABLE"}
_EXECUTION_FAILURE_STATUSES = {"FAILED_AGV", "FAILED_CTU"}
_UNKNOWN_FAILURE_STATUSES = {"UNKNOWN"}
_RESOURCE_UNAVAILABLE_STATUSES = {"REJECTED"} | _RESOURCE_REJECTION_STATUSES
_EXECUTION_FAILED_STATUSES = {"FAILED"} | _EXECUTION_FAILURE_STATUSES
_FAILURE_STATUSES = (
    {"REJECTED", "WMS_REJECTED", "FAILED", "CANCELLED", "RECONCILING"}
    | _RESOURCE_REJECTION_STATUSES
    | _EXECUTION_FAILURE_STATUSES
    | _UNKNOWN_FAILURE_STATUSES
)
_WMS_CONFIRMATION_STATUSES = {"WMS_CONFIRMED", "BUSINESS_COMPLETED"}
_CONTEXT_UPDATE_STATUSES = _PROGRESS_STATUSES | {"WMS_CONFIRMED"}
_POST_EXCHANGE_RELATION_STATUSES = {"PHYSICAL_COMPLETED", "RESOURCE_PROJECTED"}
_ALLOWED_BIN_STATUSES = {
    "EMPTY_VERIFIED",
    "IN_USE",
    "LOCKED",
    "FULL_SNAPSHOT",
    "EXCEPTION",
    "DISABLED",
    "UNKNOWN",
    "CLOSED",
    "FULL",
}


class SmtFullBoxExchangePlugin(WorklinePlugin):
    """SMT 单层货架满箱交换插件。"""

    plugin_key = "smt_full_box_exchange"
    contract_version = "1.0"
    manifest = WorklinePluginManifest(
        plugin_key=plugin_key,
        contract_version=contract_version,
        required_device_roles=(
            DeviceRoleRequirement(
                "RACK_RELEASE_SOURCE",
                min_count=1,
                max_count=1,
                capabilities=frozenset({SINGLE_LAYER_RACK_RELEASED}),
            ),
        ),
        business_key_resolver=resolve_smt_full_box_exchange_business_key,
        event_source_roles={SINGLE_LAYER_RACK_RELEASED: "RACK_RELEASE_SOURCE"},
        supported_events=frozenset({SINGLE_LAYER_RACK_RELEASED}),
    )

    @on_event(SINGLE_LAYER_RACK_RELEASED)
    async def handle_rack_released(self, ctx: Any, inbox: Any) -> list[RuntimeIntent]:
        """处理单层货架释放事件。"""

        payload = getattr(inbox, "payload_json", None)
        data = _payload_data(payload)
        invalid_message = _validate_release_data(data)
        if invalid_message is not None:
            return [build_payload_invalid_block(invalid_message)]

        config = _ctx_config(ctx)
        rack_identifier = _single_layer_rack_identifier(data)
        snapshots = _snapshot_list(data)
        exchange_policy = _exchange_policy(config)
        forced_exchange_reason = _is_smt_force_exchange_reason(data.get("release_reason_code"))
        exchange_bins = (
            _smt_release_exchange_bins(snapshots)
            if forced_exchange_reason
            else _exchange_bins(snapshots, exchange_policy)
        )
        evaluation_context: dict[str, Any] = {
            "exchange_policy": exchange_policy,
            "exchange_policy_version": exchange_policy["policy_version"],
            "evaluated_bins": snapshots,
            "qualified_bin_count": len(exchange_bins),
            "release_reason_code": _optional_text(data.get("release_reason_code")),
            "forced_exchange_reason": forced_exchange_reason,
        }
        if not exchange_bins:
            return [
                ctx.next.complete(
                    {
                        "rack_release_id": data["rack_release_id"],
                        "single_layer_rack_id": rack_identifier,
                        "single_layer_rack_code": rack_identifier,
                        "exchange_required": False,
                        "exchange_status": "NOT_REQUIRED",
                        **evaluation_context,
                    }
                )
            ]

        target_code = _target_code(config)
        if target_code is None:
            return [
                ctx.next.block(
                    scope=BlockScope.WORKLINE,
                    reason_code="FULL_BOX_EXCHANGE_TARGET_MISSING",
                    message="SMT 满箱交换缺少 WMS/RCS 目标地址配置",
                    suggested_action="配置 WorkLine external_endpoints.wms_rcs_full_box_exchange_url",
                )
            ]

        timeout_seconds = _timeout_seconds(config)
        if timeout_seconds is None:
            return [
                ctx.next.block(
                    scope=BlockScope.WORKLINE,
                    reason_code="FULL_BOX_EXCHANGE_TIMEOUT_MISSING",
                    message="SMT 满箱交换缺少外部等待超时配置",
                    suggested_action="配置 WorkLine timeouts.external_exchange_seconds",
                )
            ]

        rack_release_id = str(data["rack_release_id"])
        dispatch_key = f"external:{self.plugin_key}:{rack_release_id}:FULL_BIN_EXCHANGE"
        exchange_request_code = dispatch_key
        request_payload: dict[str, Any] = {
            "request_type": "SMT_FULL_BOX_EXCHANGE",
            "request_code": exchange_request_code,
            "exchange_request_code": exchange_request_code,
            "dispatch_key": dispatch_key,
            "trace_id": _trace_id(ctx, inbox),
            "rack_release_id": rack_release_id,
            "single_layer_rack_code": rack_identifier,
            "single_layer_rack_id": rack_identifier,
            "source_workline_code": _workline_code(ctx),
            "source_classifier_line_code": _optional_text(data.get("source_classifier_line_code")),
            "source_task_batch_id": _optional_text(data.get("source_task_batch_id")),
            "release_reason_code": _optional_text(data.get("release_reason_code")),
            "exchange_area_code": _optional_text(config.get("exchange_area_code")),
            "callback_url": _callback_url(config),
            "release_cycle_seq": data.get("release_cycle_seq"),
            "snapshot_hash": data.get("snapshot_hash"),
            "exchange_policy": exchange_policy,
            "exchange_bins": exchange_bins,
            "requested_bins": exchange_bins,
            "bins": snapshots,
            "bin_snapshots": snapshots,
        }
        context_patch: dict[str, Any] = {
            "rack_release_id": rack_release_id,
            "single_layer_rack_id": rack_identifier,
            "single_layer_rack_code": rack_identifier,
            "exchange_required": True,
            "exchange_status": "REQUESTED",
            "exchange_request_code": exchange_request_code,
            **evaluation_context,
            "full_box_exchange": {
                "dispatch_key": dispatch_key,
                "exchange_request_code": exchange_request_code,
                "exchange_status": "REQUESTED",
                "exchange_bins": exchange_bins,
            },
        }
        return [
            ctx.next.update_context(context_patch),
            ctx.next.external_request(
                dispatch_key=dispatch_key,
                target_code=target_code,
                payload=request_payload,
                timeout_seconds=timeout_seconds,
                source_system="WMS_RCS",
            ),
        ]

    async def on_external_http(self, ctx: Any, inbox: Any) -> list[RuntimeIntent]:
        """处理 WMS/RCS 满箱交换回调。"""

        raw_payload = _external_callback_payload(ctx, inbox)
        if not isinstance(raw_payload, Mapping):
            return [build_payload_invalid_block("SMT 满箱交换回调 payload 非法")]
        payload = cast("Mapping[str, Any]", raw_payload)
        if payload.get("callback_type") != WMS_FULL_BOX_EXCHANGE_CALLBACK:
            return [build_payload_invalid_block("SMT 满箱交换回调 callback_type 不支持")]

        status = _non_empty_upper(payload.get("exchange_status"))
        if status is None:
            return [build_payload_invalid_block("SMT 满箱交换回调缺少 exchange_status")]

        identity_block = _callback_identity_block(ctx, payload)
        if identity_block is not None:
            return [identity_block]

        exchange_context = _exchange_context(ctx)
        expected_dispatch_key = _optional_text(exchange_context.get("dispatch_key"))
        callback_dispatch_key = _optional_text(payload.get("dispatch_key"))
        if expected_dispatch_key is not None and callback_dispatch_key != expected_dispatch_key:
            return [
                ctx.next.block(
                    scope=BlockScope.MATERIAL,
                    reason_code="EXCHANGE_DISPATCH_KEY_MISMATCH",
                    message="SMT 满箱交换回调 dispatch_key 与当前请求不匹配",
                    suggested_action="核对 WMS/RCS 回调 dispatch_key 与当前 Session context",
                )
            ]

        if status in _POST_EXCHANGE_RELATION_STATUSES and not isinstance(
            payload.get("post_exchange_relations"), Mapping
        ):
            return [
                ctx.next.block(
                    scope=BlockScope.MATERIAL,
                    reason_code="EXCHANGE_RECONCILING",
                    message="SMT 满箱交换回调缺少交换后关系证据",
                    suggested_action="要求 WMS/RCS 补传 post_exchange_relations 后重放回调",
                )
            ]

        if status in _WMS_CONFIRMATION_STATUSES and not isinstance(payload.get("wms_confirmation"), Mapping):
            return [
                ctx.next.block(
                    scope=BlockScope.MATERIAL,
                    reason_code="EXCHANGE_WMS_CONFIRMATION_INVALID",
                    message="SMT 满箱交换 WMS 确认回调缺少 wms_confirmation",
                    suggested_action="要求 WMS 补传 wms_confirmation 后重放回调",
                )
            ]

        context_patch = {"full_box_exchange": _callback_context(exchange_context, payload, status)}
        return _callback_intents(ctx, context_patch=context_patch, status=status)


def _callback_intents(ctx: Any, *, context_patch: dict[str, Any], status: str) -> list[RuntimeIntent]:
    if status == "BUSINESS_COMPLETED":
        return [ctx.next.complete(context_patch)]
    if status in _CONTEXT_UPDATE_STATUSES:
        return [ctx.next.update_context(context_patch)]
    if status in _FAILURE_STATUSES:
        return [
            ctx.next.block(
                scope=BlockScope.MATERIAL,
                reason_code=_failure_reason(status),
                message="SMT 满箱交换外部执行失败",
                suggested_action=_failure_suggested_action(status),
            )
        ]
    return [build_payload_invalid_block(f"SMT 满箱交换回调状态不支持: {status}")]


def _callback_identity_block(ctx: Any, payload: Mapping[str, Any]) -> RuntimeIntent | None:
    expected_trace_id = _optional_text(getattr(ctx, "trace_id", None))
    callback_trace_id = _optional_text(payload.get("trace_id"))
    if expected_trace_id is not None and callback_trace_id != expected_trace_id:
        return ctx.next.block(
            scope=BlockScope.MATERIAL,
            reason_code="EXCHANGE_TRACE_ID_MISMATCH",
            message="SMT 满箱交换回调 trace_id 与当前会话不匹配",
            suggested_action="核对 WMS/RCS 回调 trace_id 与当前 Session",
        )

    expected_rack_release_id = _expected_rack_release_id(ctx)
    callback_rack_release_id = _optional_text(payload.get("rack_release_id"))
    if expected_rack_release_id is not None and callback_rack_release_id != expected_rack_release_id:
        return ctx.next.block(
            scope=BlockScope.MATERIAL,
            reason_code="EXCHANGE_RACK_RELEASE_MISMATCH",
            message="SMT 满箱交换回调 rack_release_id 与当前会话不匹配",
            suggested_action="核对 WMS/RCS 回调 rack_release_id 与当前释放周期",
        )

    exchange_context = _exchange_context(ctx)
    expected_exchange_request_code = _optional_text(exchange_context.get("exchange_request_code")) or _optional_text(
        exchange_context.get("dispatch_key")
    )
    callback_exchange_request_code = _optional_text(payload.get("exchange_request_code"))
    if expected_exchange_request_code is not None and callback_exchange_request_code != expected_exchange_request_code:
        return ctx.next.block(
            scope=BlockScope.MATERIAL,
            reason_code="EXCHANGE_REQUEST_CODE_MISMATCH",
            message="SMT 满箱交换回调 exchange_request_code 与当前请求不匹配",
            suggested_action="核对 WMS/RCS 回调 exchange_request_code 与当前 Session context",
        )
    return None


def _payload_data(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {}
    payload_map = cast("Mapping[str, Any]", payload)
    data = payload_map.get("data")
    return dict(cast("Mapping[str, Any]", data)) if isinstance(data, Mapping) else dict(payload_map)


def _external_callback_payload(ctx: Any, inbox: Any) -> Any:
    normalized_input = getattr(ctx, "normalized_input", None)
    if isinstance(normalized_input, NormalizedExternalCallback):
        payload = dict(normalized_input.payload)
        payload.setdefault("callback_type", normalized_input.callback_type)
        if normalized_input.trace_id is not None:
            payload.setdefault("trace_id", normalized_input.trace_id)
        if normalized_input.source_system is not None:
            payload.setdefault("source_system", normalized_input.source_system)
        return payload
    return getattr(inbox, "payload_json", None)


def _validate_release_data(data: Mapping[str, Any]) -> str | None:
    if not _optional_text(data.get("rack_release_id")):
        return "SINGLE_LAYER_RACK_RELEASED 缺少 rack_release_id"
    if _single_layer_rack_identifier(data) is None:
        return "SINGLE_LAYER_RACK_RELEASED 缺少 single_layer_rack_id"

    snapshots = _snapshot_items(data)
    if not isinstance(snapshots, Sequence) or isinstance(snapshots, (str, bytes)):
        return "SINGLE_LAYER_RACK_RELEASED 必须携带 4 个料箱快照"
    snapshot_sequence = cast("Sequence[Any]", snapshots)
    if len(snapshot_sequence) != 4:
        return "SINGLE_LAYER_RACK_RELEASED 必须携带 4 个料箱快照"

    return _validate_snapshot_items(snapshot_sequence)


def _validate_snapshot_items(snapshots: Sequence[Any]) -> str | None:
    slot_codes: list[str] = []
    bin_ids: list[str] = []
    for index, snapshot in enumerate(snapshots, start=1):
        item_error = _validate_snapshot_item(index, snapshot)
        if item_error is not None:
            return item_error
        snapshot_map = cast("Mapping[str, Any]", snapshot)
        slot_codes.append(_optional_text(snapshot_map.get("slot_code")) or "")
        bin_ids.append(_snapshot_bin_id(snapshot_map) or "")

    if len(set(slot_codes)) != len(slot_codes):
        return "SINGLE_LAYER_RACK_RELEASED 料箱快照存在重复槽位"
    if len(set(bin_ids)) != len(bin_ids):
        return "SINGLE_LAYER_RACK_RELEASED 料箱快照存在重复料箱"
    return None


def _validate_snapshot_item(index: int, snapshot: Any) -> str | None:
    if not isinstance(snapshot, Mapping):
        return f"SINGLE_LAYER_RACK_RELEASED 第 {index} 个料箱快照非法"

    snapshot_map = cast("Mapping[str, Any]", snapshot)
    if not _optional_text(snapshot_map.get("slot_code")):
        return "SINGLE_LAYER_RACK_RELEASED 料箱快照缺少 slot_code"
    if _snapshot_bin_id(snapshot_map) is None:
        return "SINGLE_LAYER_RACK_RELEASED 料箱快照缺少料箱编码"

    status = _non_empty_upper(snapshot_map.get("status") or snapshot_map.get("bin_execution_status"))
    if status is None:
        return "SINGLE_LAYER_RACK_RELEASED 料箱快照缺少 status"
    if status not in _ALLOWED_BIN_STATUSES:
        return f"SINGLE_LAYER_RACK_RELEASED 料箱快照 status 不支持: {status}"

    usage = _snapshot_usage(snapshot_map)
    if usage is None:
        return "SINGLE_LAYER_RACK_RELEASED 料箱快照缺少 usage"
    if usage < 0 or usage > 1:
        return "SINGLE_LAYER_RACK_RELEASED 料箱快照 usage 必须在 0 到 1 之间"
    return None


def _snapshot_list(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    snapshots = _snapshot_items(data)
    if not isinstance(snapshots, Sequence) or isinstance(snapshots, (str, bytes)):
        return []
    return [
        _normalize_snapshot(cast("Mapping[str, Any]", item))
        for item in cast("Sequence[Any]", snapshots)
        if isinstance(item, Mapping)
    ]


def _single_layer_rack_identifier(data: Mapping[str, Any]) -> str | None:
    return _optional_text(data.get("single_layer_rack_id")) or _optional_text(data.get("single_layer_rack_code"))


def _snapshot_items(data: Mapping[str, Any]) -> Any:
    return data.get("bins") if "bins" in data else data.get("bin_snapshots")


def _normalize_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(snapshot)
    bin_id = _snapshot_bin_id(snapshot)
    status = _non_empty_upper(snapshot.get("status") or snapshot.get("bin_execution_status"))
    usage = _snapshot_usage(snapshot)
    normalized["bin_id"] = bin_id
    normalized["bin_code"] = bin_id
    normalized["status"] = status
    normalized["bin_execution_status"] = status
    normalized["usage"] = usage
    normalized["usage_snapshot"] = usage
    return normalized


def _snapshot_bin_id(snapshot: Mapping[str, Any]) -> str | None:
    return _optional_text(snapshot.get("bin_id")) or _optional_text(snapshot.get("bin_code"))


def _snapshot_usage(snapshot: Mapping[str, Any]) -> float | None:
    value = snapshot.get("usage") if "usage" in snapshot else snapshot.get("usage_snapshot")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ctx_config(ctx: Any) -> dict[str, Any]:
    config = getattr(ctx, "config", None)
    return dict(cast("Mapping[str, Any]", config)) if isinstance(config, Mapping) else {}


def _exchange_policy(config: Mapping[str, Any]) -> dict[str, Any]:
    policy = config.get("exchange_policy")
    raw_policy: dict[str, Any] = dict(cast("Mapping[str, Any]", policy)) if isinstance(policy, Mapping) else {}
    full_statuses = sorted(_string_set(raw_policy.get("full_statuses")) or {"CLOSED", "FULL"})
    return {
        **raw_policy,
        "policy_version": _optional_text(raw_policy.get("policy_version"))
        or _optional_text(raw_policy.get("version"))
        or "default",
        "expected_bin_count": _int_value(raw_policy.get("expected_bin_count"), default=4),
        "full_statuses": full_statuses,
        "full_usage_threshold": _float_value(raw_policy.get("full_usage_threshold"), default=0.8),
        "min_exchange_bin_count": _int_value(raw_policy.get("min_exchange_bin_count"), default=1),
        "require_all_bins": bool(raw_policy.get("require_all_bins", False)),
    }


def _exchange_bins(snapshots: list[dict[str, Any]], policy: Mapping[str, Any]) -> list[dict[str, Any]]:
    full_statuses = _string_set(policy.get("full_statuses")) or {"CLOSED", "FULL"}
    threshold = _float_value(policy.get("full_usage_threshold"), default=0.8)
    min_count = _int_value(policy.get("min_exchange_bin_count"), default=1)
    require_all = bool(policy.get("require_all_bins", False))
    selected = [snapshot for snapshot in snapshots if _is_exchange_bin(snapshot, full_statuses, threshold)]
    if require_all and len(selected) != len(snapshots):
        return []
    if len(selected) < min_count:
        return []
    return [_exchange_bin_payload(snapshot) for snapshot in selected]


def _smt_release_exchange_bins(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_exchange_bin_payload(snapshot) for snapshot in snapshots]


def _exchange_bin_payload(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "slot_code": snapshot.get("slot_code"),
        "bin_id": snapshot.get("bin_id"),
        "bin_code": snapshot.get("bin_code"),
        "bin_type_code": snapshot.get("bin_type_code"),
        "status": snapshot.get("status"),
        "usage_snapshot": snapshot.get("usage_snapshot"),
        "usage": snapshot.get("usage"),
    }


def _is_smt_force_exchange_reason(value: Any) -> bool:
    text = _non_empty_upper(value)
    return text in SMT_FORCE_EXCHANGE_RELEASE_REASON_CODES


def _is_exchange_bin(snapshot: Mapping[str, Any], full_statuses: set[str], threshold: float) -> bool:
    status = _non_empty_upper(snapshot.get("bin_execution_status"))
    usage = _float_value(snapshot.get("usage_snapshot"), default=0.0)
    return status in full_statuses or usage >= threshold


def _target_code(config: Mapping[str, Any]) -> str | None:
    endpoints = config.get("external_endpoints")
    if isinstance(endpoints, Mapping):
        endpoint_map = cast("Mapping[str, Any]", endpoints)
        value = _optional_text(endpoint_map.get("wms_rcs_full_box_exchange_url"))
        if value is not None:
            return value
    return _optional_text(config.get("wms_rcs_full_box_exchange_url"))


def _timeout_seconds(config: Mapping[str, Any]) -> int | None:
    timeouts = config.get("timeouts")
    if isinstance(timeouts, Mapping):
        timeout_map = cast("Mapping[str, Any]", timeouts)
        value = timeout_map.get("external_exchange_seconds")
        if value is not None:
            seconds = _int_value(value, default=0)
            return seconds if seconds > 0 else None
    return None


def _callback_url(config: Mapping[str, Any]) -> str | None:
    return _optional_text(config.get("callback_url")) or _optional_text(config.get("external_callback_url"))


def _trace_id(ctx: Any, inbox: Any) -> str | None:
    return _optional_text(getattr(ctx, "trace_id", None)) or _optional_text(getattr(inbox, "trace_id", None))


def _workline_code(ctx: Any) -> str | None:
    return _optional_text(getattr(getattr(ctx, "workline", None), "line_code", None))


def _exchange_context(ctx: Any) -> dict[str, Any]:
    context = getattr(getattr(ctx, "session", None), "context_json", None)
    if not isinstance(context, Mapping):
        return {}
    context_map = cast("Mapping[str, Any]", context)
    exchange = context_map.get("full_box_exchange")
    return dict(cast("Mapping[str, Any]", exchange)) if isinstance(exchange, Mapping) else {}


def _expected_rack_release_id(ctx: Any) -> str | None:
    context = getattr(getattr(ctx, "session", None), "context_json", None)
    if not isinstance(context, Mapping):
        return None
    context_map = cast("Mapping[str, Any]", context)
    return _optional_text(context_map.get("rack_release_id")) or _optional_text(
        _exchange_context(ctx).get("rack_release_id")
    )


def _callback_context(existing: Mapping[str, Any], payload: Mapping[str, Any], status: str) -> dict[str, Any]:
    updated = dict(existing)
    updated["exchange_status"] = status
    for key in ("queue_position", "eta_seconds", "wms_rcs_task_id", "exchange_request_code", "dispatch_key"):
        if key in payload:
            updated[key] = payload[key]
    post_exchange_relations = payload.get("post_exchange_relations")
    if isinstance(post_exchange_relations, Mapping):
        updated["post_exchange_relations"] = dict(cast("Mapping[str, Any]", post_exchange_relations))
    wms_confirmation = payload.get("wms_confirmation")
    if isinstance(wms_confirmation, Mapping):
        updated["wms_confirmation"] = dict(cast("Mapping[str, Any]", wms_confirmation))
    return updated


def _failure_reason(status: str) -> str:
    if status == "WMS_REJECTED":
        return "EXCHANGE_WMS_REJECTED"
    if status in _RESOURCE_UNAVAILABLE_STATUSES:
        return "EXCHANGE_RESOURCE_UNAVAILABLE"
    if status in _UNKNOWN_FAILURE_STATUSES:
        return "EXCHANGE_STATUS_UNKNOWN"
    if status == "CANCELLED":
        return "EXCHANGE_CANCELLED"
    if status == "RECONCILING":
        return "EXCHANGE_RECONCILING"
    return "EXCHANGE_EXECUTION_FAILED"


def _failure_suggested_action(status: str) -> str:
    if status == "WMS_REJECTED":
        return "人工核对 WMS 库存或单据确认结果"
    if status in _RESOURCE_UNAVAILABLE_STATUSES:
        return "等待交换区或空箱资源恢复后重试"
    if status in _EXECUTION_FAILED_STATUSES:
        return "联系 WMS/RCS 排查 AGV/CTU 执行失败"
    if status == "CANCELLED":
        return "人工确认现场实物状态后决定重试或取消"
    return "人工核对现场实物状态并处理对账"


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _non_empty_upper(value: Any) -> str | None:
    text = _optional_text(value)
    return text.upper() if text is not None else None


def _string_set(value: Any) -> set[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return set()

    values: set[str] = set()
    for item in cast("Sequence[Any]", value):
        text = _non_empty_upper(item)
        if text is not None:
            values.add(text)
    return values


def _float_value(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int_value(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


smt_full_box_exchange_plugin = SmtFullBoxExchangePlugin()


__all__ = ["SmtFullBoxExchangePlugin", "smt_full_box_exchange_plugin"]
