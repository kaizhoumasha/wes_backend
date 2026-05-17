"""SMT 满箱交换插件。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from src.workline_plugins.smt_full_box_exchange.contract import (
    SINGLE_LAYER_RACK_RELEASED,
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
_FAILURE_STATUSES = (
    {"REJECTED", "WMS_REJECTED", "FAILED", "CANCELLED", "RECONCILING"}
    | _RESOURCE_REJECTION_STATUSES
    | _EXECUTION_FAILURE_STATUSES
    | _UNKNOWN_FAILURE_STATUSES
)
_WMS_CONFIRMATION_STATUSES = {"WMS_CONFIRMED", "BUSINESS_COMPLETED"}
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
        exchange_bins = _exchange_bins(snapshots, _exchange_policy(config))
        if not exchange_bins:
            return [
                ctx.next.complete(
                    {
                        "rack_release_id": data["rack_release_id"],
                        "single_layer_rack_id": rack_identifier,
                        "single_layer_rack_code": rack_identifier,
                        "exchange_required": False,
                        "exchange_status": "NOT_REQUIRED",
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
        request_payload = {
            "request_type": "SMT_FULL_BOX_EXCHANGE",
            "request_code": exchange_request_code,
            "exchange_request_code": exchange_request_code,
            "dispatch_key": dispatch_key,
            "trace_id": _trace_id(ctx, inbox),
            "rack_release_id": rack_release_id,
            "single_layer_rack_code": rack_identifier,
            "single_layer_rack_id": rack_identifier,
            "source_workline_code": _workline_code(ctx),
            "exchange_area_code": _optional_text(config.get("exchange_area_code")),
            "callback_url": _callback_url(config),
            "release_cycle_seq": data.get("release_cycle_seq"),
            "snapshot_hash": data.get("snapshot_hash"),
            "exchange_bins": exchange_bins,
            "requested_bins": exchange_bins,
            "bins": snapshots,
            "bin_snapshots": snapshots,
        }
        context_patch = {
            "rack_release_id": rack_release_id,
            "single_layer_rack_id": rack_identifier,
            "single_layer_rack_code": rack_identifier,
            "exchange_required": True,
            "exchange_status": "REQUESTED",
            "exchange_request_code": exchange_request_code,
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

        payload = _external_callback_payload(ctx, inbox)
        if not isinstance(payload, Mapping):
            return [build_payload_invalid_block("SMT 满箱交换回调 payload 非法")]
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
                )
            ]

        if status in _WMS_CONFIRMATION_STATUSES and not isinstance(payload.get("wms_confirmation"), Mapping):
            return [
                ctx.next.block(
                    scope=BlockScope.MATERIAL,
                    reason_code="EXCHANGE_WMS_CONFIRMATION_INVALID",
                    message="SMT 满箱交换 WMS 确认回调缺少 wms_confirmation",
                )
            ]

        context_patch = {"full_box_exchange": _callback_context(exchange_context, payload, status)}
        return _callback_intents(ctx, context_patch=context_patch, status=status)


def _callback_intents(ctx: Any, *, context_patch: dict[str, Any], status: str) -> list[RuntimeIntent]:
    if status == "BUSINESS_COMPLETED":
        return [ctx.next.complete(context_patch)]
    if status in _PROGRESS_STATUSES or status == "WMS_CONFIRMED":
        return [ctx.next.update_context(context_patch)]
    if status in _FAILURE_STATUSES:
        return [
            ctx.next.block(
                scope=BlockScope.MATERIAL,
                reason_code=_failure_reason(status),
                message="SMT 满箱交换外部执行失败",
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
        )

    expected_rack_release_id = _expected_rack_release_id(ctx)
    callback_rack_release_id = _optional_text(payload.get("rack_release_id"))
    if expected_rack_release_id is not None and callback_rack_release_id != expected_rack_release_id:
        return ctx.next.block(
            scope=BlockScope.MATERIAL,
            reason_code="EXCHANGE_RACK_RELEASE_MISMATCH",
            message="SMT 满箱交换回调 rack_release_id 与当前会话不匹配",
        )
    return None


def _payload_data(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {}
    data = payload.get("data")
    return dict(data) if isinstance(data, Mapping) else dict(payload)


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
    if not isinstance(snapshots, Sequence) or isinstance(snapshots, (str, bytes)) or len(snapshots) != 4:
        return "SINGLE_LAYER_RACK_RELEASED 必须携带 4 个料箱快照"

    return _validate_snapshot_items(snapshots)


def _validate_snapshot_items(snapshots: Sequence[Any]) -> str | None:
    slot_codes: list[str] = []
    for index, snapshot in enumerate(snapshots, start=1):
        item_error = _validate_snapshot_item(index, snapshot)
        if item_error is not None:
            return item_error
        slot_codes.append(_optional_text(snapshot.get("slot_code")) or "")

    if len(set(slot_codes)) != len(slot_codes):
        return "SINGLE_LAYER_RACK_RELEASED 料箱快照存在重复槽位"
    return None


def _validate_snapshot_item(index: int, snapshot: Any) -> str | None:
    if not isinstance(snapshot, Mapping):
        return f"SINGLE_LAYER_RACK_RELEASED 第 {index} 个料箱快照非法"

    if not _optional_text(snapshot.get("slot_code")):
        return "SINGLE_LAYER_RACK_RELEASED 料箱快照缺少 slot_code"
    if _snapshot_bin_id(snapshot) is None:
        return "SINGLE_LAYER_RACK_RELEASED 料箱快照缺少料箱编码"

    status = _non_empty_upper(snapshot.get("status") or snapshot.get("bin_execution_status"))
    if status is None:
        return "SINGLE_LAYER_RACK_RELEASED 料箱快照缺少 status"
    if status not in _ALLOWED_BIN_STATUSES:
        return f"SINGLE_LAYER_RACK_RELEASED 料箱快照 status 不支持: {status}"

    usage = _snapshot_usage(snapshot)
    if usage is None:
        return "SINGLE_LAYER_RACK_RELEASED 料箱快照缺少 usage"
    if usage < 0 or usage > 1:
        return "SINGLE_LAYER_RACK_RELEASED 料箱快照 usage 必须在 0 到 1 之间"
    return None


def _snapshot_list(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    snapshots = _snapshot_items(data)
    if not isinstance(snapshots, Sequence) or isinstance(snapshots, (str, bytes)):
        return []
    return [_normalize_snapshot(item) for item in snapshots if isinstance(item, Mapping)]


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
    return dict(config) if isinstance(config, Mapping) else {}


def _exchange_policy(config: Mapping[str, Any]) -> dict[str, Any]:
    policy = config.get("exchange_policy")
    return dict(policy) if isinstance(policy, Mapping) else {}


def _exchange_bins(snapshots: list[dict[str, Any]], policy: Mapping[str, Any]) -> list[dict[str, Any]]:
    full_statuses = _string_set(policy.get("full_statuses")) or {"CLOSED", "FULL"}
    threshold = _float_value(policy.get("full_usage_threshold"), default=0.8)
    min_count = _int_value(policy.get("min_exchange_bin_count"), default=1)
    require_all = bool(policy.get("require_all_bins", False))
    selected = [
        snapshot
        for snapshot in snapshots
        if _non_empty_upper(snapshot.get("bin_execution_status")) in full_statuses
        or _float_value(snapshot.get("usage_snapshot"), default=0.0) >= threshold
    ]
    if require_all and len(selected) != len(snapshots):
        return []
    if len(selected) < min_count:
        return []
    return [
        {
            "slot_code": snapshot.get("slot_code"),
            "bin_id": snapshot.get("bin_id"),
            "bin_code": snapshot.get("bin_code"),
            "bin_type_code": snapshot.get("bin_type_code"),
            "status": snapshot.get("status"),
            "usage_snapshot": snapshot.get("usage_snapshot"),
            "usage": snapshot.get("usage"),
        }
        for snapshot in selected
    ]


def _target_code(config: Mapping[str, Any]) -> str | None:
    endpoints = config.get("external_endpoints")
    if isinstance(endpoints, Mapping):
        value = _optional_text(endpoints.get("wms_rcs_full_box_exchange_url"))
        if value is not None:
            return value
    return _optional_text(config.get("wms_rcs_full_box_exchange_url"))


def _timeout_seconds(config: Mapping[str, Any]) -> int | None:
    timeouts = config.get("timeouts")
    if isinstance(timeouts, Mapping):
        value = timeouts.get("external_exchange_seconds")
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
    exchange = context.get("full_box_exchange")
    return dict(exchange) if isinstance(exchange, Mapping) else {}


def _expected_rack_release_id(ctx: Any) -> str | None:
    context = getattr(getattr(ctx, "session", None), "context_json", None)
    if not isinstance(context, Mapping):
        return None
    return _optional_text(context.get("rack_release_id")) or _optional_text(
        _exchange_context(ctx).get("rack_release_id")
    )


def _callback_context(existing: Mapping[str, Any], payload: Mapping[str, Any], status: str) -> dict[str, Any]:
    updated = dict(existing)
    updated["exchange_status"] = status
    for key in ("queue_position", "eta_seconds", "wms_rcs_task_id", "exchange_request_code", "dispatch_key"):
        if key in payload:
            updated[key] = payload[key]
    if isinstance(payload.get("wms_confirmation"), Mapping):
        updated["wms_confirmation"] = dict(payload["wms_confirmation"])
    return updated


def _failure_reason(status: str) -> str:
    if status == "WMS_REJECTED":
        return "EXCHANGE_WMS_REJECTED"
    if status == "REJECTED" or status in _RESOURCE_REJECTION_STATUSES:
        return "EXCHANGE_RESOURCE_UNAVAILABLE"
    if status in _UNKNOWN_FAILURE_STATUSES:
        return "EXCHANGE_STATUS_UNKNOWN"
    if status == "CANCELLED":
        return "EXCHANGE_CANCELLED"
    if status == "RECONCILING":
        return "EXCHANGE_RECONCILING"
    return "EXCHANGE_EXECUTION_FAILED"


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
    return {_non_empty_upper(item) for item in value if _non_empty_upper(item) is not None}


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
