"""
OrchestratorService - 编排器核心服务

负责协调 Session 的处理流程:
1. 获取分布式锁
2. 接收目标态 RuntimeCapability 输出的 RuntimeIntent
3. 校验 RuntimeIntent
4. 交给 Runtime effect 层落地命令、等待、状态和 Timeline

Runtime lock simplification:
- 两阶段锁合并为单阶段锁

设计参考: runtime-orchestration 设计文档
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.app.runtime.capabilities.material_flow.contracts.rough_sorter import (
    ACTION_MOVE_TO_NG,
    ACTION_PICK_AND_PUT,
    ACTION_TARGET_ROLES,
    EVENT_SCAN_COMPLETED,
    PHASE_NG_MOVING,
    PHASE_PICK_TO_PIPELINE,
    ROUGH_SORTER_PLUGIN_KEY,
    build_move_to_ng_payload,
    build_pick_and_put_payload,
    normalize_six_in_one_payload,
)
from src.app.runtime.capabilities.material_flow.contracts.smt_sorting_inbound import (
    COMMAND_SOURCE_PICK,
    EVENT_SOURCE_PICK_REQUESTED,
    ROLE_SORTING_SOURCE_ARM,
    SMT_SORTING_INBOUND_PLUGIN_KEY,
)
from src.app.runtime.normalization.contracts import (
    NormalizedCommandResult,
    NormalizedDeviceEvent,
    NormalizedExternalCallback,
)
from src.app.runtime.normalization.normalizers import normalize_inbox_input
from src.app.runtime.orchestration.diagnostics import ErrorCode, error_domain_for
from src.app.runtime.orchestration.lock_bridge import LockAcquireError
from src.app.runtime.orchestration.runtime_intent import BlockScope, RuntimeIntent
from src.app.runtime.runtime_capability_catalog import (
    resolve_runtime_capability_profile,
    runtime_capability_dispatcher,
)
from src.app.workline.domain.models import BarcodeDecisionType
from src.app.workline.trace_context import TraceContext
from src.core.logger import logger
from src.utils.value_normalization import mapping_copy

# 类型注解用（运行时需要这些类型作为函数签名）
if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from contextlib import AbstractAsyncContextManager

    from src.app.runtime.orchestration.models.session import WorklineSession
    from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
    from src.app.workline.models import WorkLine
    from src.app.workline.runtime_services import WorklineRuntimeServices


_ALLOW_NULL_PLUGIN = False


def set_allow_null_plugin(allow: bool) -> None:
    """重构完成后保留测试兼容入口；旧 NullPlugin 不再参与运行时 fallback。"""
    global _ALLOW_NULL_PLUGIN
    _ALLOW_NULL_PLUGIN = allow


_INBOX_KIND_TO_PLUGIN_TYPE = {
    "COMMAND_RESULT": "COMMAND_RESULT",
    "DEVICE_EVENT": "DEVICE_EVENT",
    "EXTERNAL_HTTP": "EXTERNAL_HTTP",
    "INTERNAL_EVENT": "DEVICE_EVENT",
    "TIMER_TIMEOUT": "TIMEOUT",
    "MANUAL_HOLD": "MANUAL_OPERATION",
    "MANUAL_RESUME": "MANUAL_OPERATION",
    "MANUAL_CANCEL": "MANUAL_OPERATION",
}
_MANUAL_OPERATION_KINDS = {"MANUAL_HOLD", "MANUAL_RESUME", "MANUAL_CANCEL"}
_MANUAL_OPERATION_TO_KIND = {
    "HOLD": "MANUAL_HOLD",
    "RESUME": "MANUAL_RESUME",
    "CANCEL": "MANUAL_CANCEL",
}
_DEFAULT_NG_LOCATION = "NG-01"
_DEFAULT_PIPELINE_INPUT_LOCATION = "PIPELINE-IN-01"
_RESERVED_CONTEXT_KEYS = frozenset(
    {
        "awaiting_device_command_code",
        "current_device_id",
        "current_device_role",
        "current_wait_type",
        "deadline_at",
        "failure_code",
        "failure_domain",
        "status",
    }
)


def _ensure_non_empty_str(value: Any) -> str | None:
    """Return value if it's a non-empty string, otherwise None."""
    return value if isinstance(value, str) and value else None


def _inbox_kind_value(inbox: Any) -> str | None:
    kind = getattr(inbox, "kind", None)
    value = getattr(kind, "value", kind)
    return value if isinstance(value, str) and value else None


def _context_patch_has_reserved_key(context_patch: dict[str, Any] | None) -> bool:
    if not context_patch:
        return False
    return any(key in _RESERVED_CONTEXT_KEYS for key in context_patch)


def _payload_text(payload_json: Mapping[str, Any], data: Mapping[str, Any], *field_names: str) -> str | None:
    for field_name in field_names:
        data_value = _ensure_non_empty_str(data.get(field_name))
        if data_value is not None:
            return data_value
        payload_value = _ensure_non_empty_str(payload_json.get(field_name))
        if payload_value is not None:
            return payload_value
    return None


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.isdigit():
        parsed = int(value)
        return parsed if parsed > 0 else None
    return None


def _workline_config(workline: Any) -> dict[str, Any]:
    config: dict[str, Any] = {}
    for source in (getattr(workline, "config", None), getattr(workline, "runtime_config_json", None)):
        if isinstance(source, Mapping):
            config.update(source)
    return config


def _rough_sorter_scan_source_location(event: NormalizedDeviceEvent) -> str:
    payload_location = _ensure_non_empty_str(event.data.get("location"))
    if payload_location is not None:
        return payload_location
    return event.device_code or "UNKNOWN"


def _rough_sorter_pipeline_input_location(workline: Any) -> str:
    return (
        _ensure_non_empty_str(_workline_config(workline).get("pipeline_input_location"))
        or _DEFAULT_PIPELINE_INPUT_LOCATION
    )


def _rough_sorter_ng_location(workline: Any) -> str:
    return _ensure_non_empty_str(_workline_config(workline).get("ng_location")) or _DEFAULT_NG_LOCATION


def _rough_sorter_material_identity_key(six_in_one_payload: Mapping[str, Any]) -> str | None:
    material_code = _ensure_non_empty_str(six_in_one_payload.get("HHPN"))
    vendor_code = _ensure_non_empty_str(six_in_one_payload.get("MfrPN"))
    date_code = _ensure_non_empty_str(six_in_one_payload.get("DateCode"))
    lot_code = _ensure_non_empty_str(six_in_one_payload.get("LotCode"))
    if material_code or date_code or lot_code:
        return f"MAT:{material_code or ''}:{vendor_code or ''}:{date_code or ''}:{lot_code or ''}"
    return None


def _block_intent(
    *,
    scope: BlockScope,
    reason_code: str,
    message: str,
    payload: dict[str, Any] | None = None,
    suggested_action: str | None = None,
) -> list[RuntimeIntent]:
    return [
        RuntimeIntent.block(
            scope=scope,
            reason_code=reason_code,
            message=message,
            suggested_action=suggested_action,
            payload=payload,
        )
    ]


def _rough_sorter_scan_completed_intents(
    event: NormalizedDeviceEvent,
    *,
    workline: Any,
    trace_id: str,
) -> list[RuntimeIntent]:
    from src.app.workline.domain.services.barcode_decision_service import barcode_decision_service

    six_in_one = normalize_six_in_one_payload(event.payload)
    decision = barcode_decision_service.evaluate(six_in_one)
    six_in_one_payload = {
        field_name: value
        for field_name, value in six_in_one.model_dump().items()
        if field_name in six_in_one.BUSINESS_FIELD_NAMES and value is not None
    }

    if decision.decision == BarcodeDecisionType.OK:
        pkg_code = _ensure_non_empty_str(six_in_one_payload.get("PkgID"))
        if pkg_code is None:
            return _block_intent(
                scope=BlockScope.MATERIAL,
                reason_code="ROUGH_SORTER_CONTEXT_MISSING",
                message="粗分机扫码成功但缺少 PkgID，无法建立料盘实体",
            )
        material_identity_key = _rough_sorter_material_identity_key(six_in_one_payload)
        if material_identity_key is None:
            return _block_intent(
                scope=BlockScope.MATERIAL,
                reason_code="ROUGH_SORTER_CONTEXT_MISSING",
                message="粗分机扫码成功但缺少物料身份键，无法建立料盘实体",
            )
        return [
            RuntimeIntent.create_material_unit(
                pkg_code=pkg_code,
                material_identity_key=material_identity_key,
                six_in_one=six_in_one_payload,
                status="IN_TRANSIT",
            ),
            RuntimeIntent.update_context(
                {
                    "six_in_one": six_in_one_payload,
                    "business_key": decision.business_key,
                    "phase": PHASE_PICK_TO_PIPELINE,
                }
            ),
            RuntimeIntent.command(
                device_role=ACTION_TARGET_ROLES[ACTION_PICK_AND_PUT],
                action=ACTION_PICK_AND_PUT,
                payload=build_pick_and_put_payload(
                    business_key=decision.business_key,
                    source_location=_rough_sorter_scan_source_location(event),
                    target_location=_rough_sorter_pipeline_input_location(workline),
                    six_in_one=six_in_one,
                    trace_id=trace_id or event.trace_id,
                ),
            ),
        ]

    reason_code = decision.reason_code or "BARCODE_INVALID"
    reason_message = decision.reason_message or "扫码业务判定 NG"
    context_patch = {
        "six_in_one": six_in_one_payload,
        "business_key": decision.business_key,
        "ng_reason": {
            "reason_code": reason_code,
            "reason_message": reason_message,
        },
        "phase": PHASE_NG_MOVING,
    }
    intents: list[RuntimeIntent] = []
    pkg_code = _ensure_non_empty_str(six_in_one_payload.get("PkgID"))
    material_identity_key = _rough_sorter_material_identity_key(six_in_one_payload)
    if material_identity_key is not None and pkg_code is not None:
        intents.append(
            RuntimeIntent.create_material_unit(
                pkg_code=pkg_code,
                material_identity_key=material_identity_key,
                six_in_one=six_in_one_payload,
                status="NG",
            )
        )
    intents.extend(
        [
            RuntimeIntent.update_context(context_patch),
            RuntimeIntent.mark_ng(
                reason_code=reason_code,
                message=reason_message,
                payload={"six_in_one": six_in_one_payload},
            ),
            RuntimeIntent.command(
                device_role=ACTION_TARGET_ROLES[ACTION_MOVE_TO_NG],
                action=ACTION_MOVE_TO_NG,
                payload=build_move_to_ng_payload(
                    business_key=decision.business_key,
                    source_location=_rough_sorter_scan_source_location(event),
                    ng_location=_rough_sorter_ng_location(workline),
                    reason_code=reason_code,
                ),
            ),
        ]
    )
    return intents


def _smt_source_pick_requested_intents(event: NormalizedDeviceEvent, *, inbox: Any) -> list[RuntimeIntent]:
    payload_json = event.payload
    data = event.data
    bin_cell_index = _positive_int(data.get("bin_cell_index")) or _positive_int(data.get("source_cell_index"))
    bin_code = _payload_text(payload_json, data, "bin_code", "source_bin_code")
    bin_cell_code = _payload_text(payload_json, data, "bin_cell_code", "source_cell_code")
    reel_thickness = _payload_text(payload_json, data, "reel_thickness", "reel_thickness_mm")
    command_payload = {
        "handoff_demand_id": _positive_int(data.get("handoff_demand_id")),
        "handoff_source_item_id": _positive_int(data.get("handoff_source_item_id")),
        "claim_attempt_no": _positive_int(data.get("claim_attempt_no")),
        "source_pick_inbox_id": _positive_int(getattr(inbox, "id", None)),
        "source_pick_request_event_id": _payload_text(payload_json, data, "event_id"),
        "rack_release_id": _payload_text(payload_json, data, "rack_release_id"),
        "single_layer_rack_code": _payload_text(payload_json, data, "single_layer_rack_code"),
        "bin_code": bin_code,
        "source_bin_code": bin_code,
        "bin_cell_index": bin_cell_index,
        "bin_cell_code": bin_cell_code,
        "source_cell_code": bin_cell_code or (str(bin_cell_index) if bin_cell_index is not None else None),
        "material_identity_key": _payload_text(payload_json, data, "material_identity_key"),
        "pkg_code": _payload_text(payload_json, data, "pkg_code", "PkgID"),
        "reel_thickness": reel_thickness,
        "reel_thickness_mm": reel_thickness,
        "route_evidence": mapping_copy(data.get("route_evidence")),
    }
    missing_fields = [
        field_name
        for field_name in (
            "handoff_demand_id",
            "handoff_source_item_id",
            "claim_attempt_no",
            "source_pick_inbox_id",
            "source_pick_request_event_id",
            "bin_code",
            "bin_cell_index",
            "material_identity_key",
            "pkg_code",
            "reel_thickness",
        )
        if command_payload.get(field_name) is None
    ]
    if missing_fields:
        return _block_intent(
            scope=BlockScope.MATERIAL,
            reason_code="PLUGIN_CONTRACT_INVALID",
            message="SORTING_SOURCE_PICK_REQUESTED payload 缺少生成首盘取盘命令所需字段",
            payload={
                "missing_fields": missing_fields,
                "event_id": event.payload.get("event_id"),
                "inbox_id": getattr(inbox, "id", None),
            },
            suggested_action="检查 SMT 分拣入库 handoff source-pick 内部事件 payload",
        )

    return [
        RuntimeIntent.command(
            device_role=ROLE_SORTING_SOURCE_ARM,
            action=COMMAND_SOURCE_PICK,
            payload=command_payload,
        )
    ]


def _unsupported_device_event_intents(event: NormalizedDeviceEvent, *, workline: Any) -> list[RuntimeIntent]:
    plugin_key = _ensure_non_empty_str(getattr(workline, "plugin_key", None))
    return _block_intent(
        scope=BlockScope.MATERIAL,
        reason_code="TARGET_STATE_DEVICE_EVENT_HANDLER_MISSING",
        message=f"目标态 runtime event handler 未注册: {event.canonical_event_type}",
        payload={
            "plugin_key": plugin_key,
            "source_event_type": event.source_event_type,
            "canonical_event_type": event.canonical_event_type,
            "device_code": event.device_code,
            "business_key": event.business_key,
        },
        suggested_action="为该 WorkLine capability 补齐目标态事件 handler 或改为声明 runtime_capability",
    )


def _device_event_intents(
    event: NormalizedDeviceEvent,
    *,
    inbox: Any,
    workline: Any,
    trace_id: str,
) -> list[RuntimeIntent]:
    plugin_key = _ensure_non_empty_str(getattr(workline, "plugin_key", None))
    event_type = event.canonical_event_type or event.source_event_type
    if plugin_key == ROUGH_SORTER_PLUGIN_KEY and event_type == EVENT_SCAN_COMPLETED:
        return _rough_sorter_scan_completed_intents(event, workline=workline, trace_id=trace_id)
    if plugin_key == SMT_SORTING_INBOUND_PLUGIN_KEY and event_type == EVENT_SOURCE_PICK_REQUESTED:
        return _smt_source_pick_requested_intents(event, inbox=inbox)
    return _unsupported_device_event_intents(event, workline=workline)


def _command_result_reason_code(result: NormalizedCommandResult, error_detail: Mapping[str, Any]) -> str:
    error_code = _ensure_non_empty_str(error_detail.get("error_code"))
    if error_code is not None:
        return error_code.upper()
    normalized_result = _ensure_non_empty_str(result.normalized_result)
    if normalized_result in {"TERMINAL_FAILURE", "RETRYABLE_FAILURE"}:
        return normalized_result.upper()
    result_classification = _ensure_non_empty_str(result.result_classification)
    if result_classification is not None:
        return result_classification.upper()
    if normalized_result is not None and normalized_result != "UNKNOWN":
        return normalized_result.upper()
    return "COMMAND_RESULT_FAILED"


def _command_result_failure_intent(result: NormalizedCommandResult) -> RuntimeIntent:
    error_detail = dict(result.error_detail)
    reason_code = _command_result_reason_code(result, error_detail)
    message = (
        _ensure_non_empty_str(error_detail.get("error_message"))
        or _ensure_non_empty_str(error_detail.get("message"))
        or "设备命令执行失败，需人工确认"
    )
    return RuntimeIntent.block(
        scope=BlockScope.COMMAND,
        reason_code=reason_code,
        message=message,
        suggested_action="检查设备命令结果并确认是否需要重试或人工处理",
        payload={
            "command_code": result.command_code,
            "command_type": result.command_type,
            "source_result": result.source_result,
            "normalized_result": result.normalized_result,
            "result_classification": result.result_classification,
            "device_code": result.device_code,
            "error_detail": error_detail,
        },
    )


def _command_result_intents(result: NormalizedCommandResult) -> list[RuntimeIntent]:
    normalized_result = _ensure_non_empty_str(result.normalized_result)
    if normalized_result == "SUCCESS" and result.result_classification is None:
        return [RuntimeIntent.continue_next()]
    return [_command_result_failure_intent(result)]


def _manual_operation_kind(normalized_input: Any, *, inbox: Any) -> str | None:
    kind = _inbox_kind_value(inbox)
    if kind in _MANUAL_OPERATION_KINDS:
        return kind
    if not isinstance(normalized_input, NormalizedDeviceEvent):
        return None
    if _ensure_non_empty_str(normalized_input.payload.get("message_type")) != "MANUAL_OPERATION":
        return None
    operation = _ensure_non_empty_str(normalized_input.data.get("operation")) or _ensure_non_empty_str(
        normalized_input.payload.get("operation")
    )
    if operation is None:
        return None
    return _MANUAL_OPERATION_TO_KIND.get(operation.upper())


def _manual_operation_payload(
    normalized_input: Any,
    *,
    inbox: Any,
    manual_kind: str,
) -> dict[str, Any]:
    payload_json = normalized_input.payload if isinstance(normalized_input, NormalizedDeviceEvent) else {}
    data = normalized_input.data if isinstance(normalized_input, NormalizedDeviceEvent) else {}
    operation = _payload_text(payload_json, data, "operation")
    if operation is None:
        operation = manual_kind.removeprefix("MANUAL_")
    return {
        "operation": operation.upper(),
        "operator_id": _payload_text(payload_json, data, "operator_id"),
        "reason": _payload_text(payload_json, data, "reason"),
        "session_id": _positive_int(data.get("session_id")) or _positive_int(payload_json.get("session_id")),
        "inbox_id": _positive_int(getattr(inbox, "id", None)),
    }


def _manual_operation_message(payload: Mapping[str, Any], fallback: str) -> str:
    reason = _ensure_non_empty_str(payload.get("reason"))
    if reason is not None:
        return reason
    return fallback


def _manual_operation_intents(
    normalized_input: Any,
    *,
    inbox: Any,
    manual_kind: str,
) -> list[RuntimeIntent]:
    payload = _manual_operation_payload(normalized_input, inbox=inbox, manual_kind=manual_kind)
    if manual_kind == "MANUAL_HOLD":
        return _block_intent(
            scope=BlockScope.WORKLINE,
            reason_code="MANUAL_HOLD_REQUESTED",
            message=_manual_operation_message(payload, "人工暂停 Session"),
            payload=payload,
            suggested_action="等待人工恢复或取消该 Session",
        )
    if manual_kind == "MANUAL_RESUME":
        return [RuntimeIntent.continue_next(payload=payload)]
    if manual_kind == "MANUAL_CANCEL":
        return [
            RuntimeIntent.cancel(
                reason_code="MANUAL_CANCEL_REQUESTED",
                message=_manual_operation_message(payload, "人工取消 Session"),
                payload=payload,
            )
        ]
    raise ValueError(f"unsupported manual operation inbox kind: {manual_kind}")


def _standard_inbox_intents(
    normalized_input: Any,
    *,
    inbox: Any,
    workline: Any,
    trace_id: str,
) -> list[RuntimeIntent]:
    manual_kind = _manual_operation_kind(normalized_input, inbox=inbox)
    if manual_kind is not None:
        return _manual_operation_intents(normalized_input, inbox=inbox, manual_kind=manual_kind)
    if isinstance(normalized_input, NormalizedCommandResult):
        return _command_result_intents(normalized_input)
    if isinstance(normalized_input, NormalizedDeviceEvent):
        return _device_event_intents(normalized_input, inbox=inbox, workline=workline, trace_id=trace_id)
    if isinstance(normalized_input, NormalizedExternalCallback):
        # 无 runtime_capability 的 external callback 属于 lifecycle-only evidence。
        # lifecycle 已在 ingress 同事务完成，processor 只通过空 intents 触发 fenced PROCESSED 写回。
        return []
    raise ValueError(f"target-state runtime inbox handler is not registered for {type(normalized_input).__name__}")


def _system_error_result(message: str) -> OrchestratorResult:
    return _error_result(ErrorCode.UNKNOWN, message)


def _error_result(
    error_code: ErrorCode,
    message: str,
) -> OrchestratorResult:
    return OrchestratorResult(
        success=False,
        error=message,
        error_code=error_code.value,
        error_domain=error_domain_for(error_code).value,
    )


@dataclass
class OrchestratorResult:
    """编排器处理结果

    Attributes:
        success: 是否成功
        error: 错误信息（失败时）
        intents: RuntimeIntent 输出列表
    """

    success: bool
    error: str | None = None
    error_code: str | None = None
    error_domain: str | None = None
    intents: list[RuntimeIntent] | None = None


class OrchestratorService:
    """编排器服务

    核心职责:
    - 协调 Session 处理流程
    - 管理分布式锁
    - 调用插件并处理结果
    - 验证状态迁移

    Attributes:
        lock_provider: 锁提供者函数（用于依赖注入）
    """

    def __init__(
        self,
        lock_provider: Callable[[str], AbstractAsyncContextManager[None]] | None = None,
        runtime_dispatcher: Any | None = None,
        runtime_profile_resolver: Callable[[Any], Any] | None = None,
    ):
        """初始化编排器服务

        Args:
            lock_provider: 可选的锁提供者函数，用于测试注入。
                          接收锁 key，返回异步上下文管理器。
        """
        self._lock_provider = lock_provider
        self._runtime_dispatcher = runtime_dispatcher or runtime_capability_dispatcher
        self._runtime_profile_resolver = runtime_profile_resolver or resolve_runtime_capability_profile

    @staticmethod
    def _resolve_session_pk(session: Any) -> int | None:
        """提取 Session 的真实整型主键。"""
        value = getattr(session, "id", None)
        if isinstance(value, bool):
            return None
        return value if isinstance(value, int) else None

    def _get_lock(self, lock_key: str) -> AbstractAsyncContextManager[None]:
        """获取锁上下文管理器。

        单阶段锁，不再区分 READ/WRITE。

        Args:
            lock_key: 锁的 key

        Returns:
            异步上下文管理器
        """
        if self._lock_provider:
            return self._lock_provider(lock_key)

        logger.error(
            "No lock provider configured for OrchestratorService; "
            "production paths must inject a real lock provider explicitly"
        )
        raise LockAcquireError("No lock provider configured for OrchestratorService")

    async def process_inbox(
        self,
        session: WorklineSession | None,
        workline: WorkLine | None,
        inbox: RuntimeInbox | None,
        devices_by_role: dict[str, list[Any]],
        services: WorklineRuntimeServices,
        trace_id: str,
        write_callback: Callable[[OrchestratorResult], Awaitable[None]] | None = None,
    ) -> OrchestratorResult:
        """处理 Inbox 事件（单阶段互斥锁）

        两阶段锁合并为单阶段。
        stale-session guard 由 Celery worker 保留（workline.py:1646-1660）。

        注意:session 锁确保同一 session 的消息串行处理。
        真实的 session 刷新和 stale 防护在 worker callback 中完成。

        Args:
            session: WorklineSession 实体
            workline: WorkLine 实体
            inbox: RuntimeInbox 实体
            devices_by_role: 按角色分组的设备映射
            services: 运行时领域服务容器
            trace_id: Trace ID
            write_callback: 可选的写入回调。若提供，则在锁临界区内执行，
                由 Celery worker 负责完成真实持久化写入
                （session / command / outbox / timeline / inbox）。

        Returns:
            OrchestratorResult: 处理结果
        """
        session_id = self._resolve_session_pk(session)
        if session_id is None:
            return _error_result(ErrorCode.SESSION_CONTEXT_MISSING, "Session missing primary key")

        lock_key = f"session:{session_id}"
        inbox_id_for_log = getattr(inbox, "id", "unknown") if inbox else "unknown"

        # 单阶段锁:包含插件调用和结果处理
        try:
            async with self._get_lock(lock_key):
                # 加载插件、构建上下文、调用插件、处理结果
                result = await self._process_read_phase(
                    session=session,
                    workline=workline,
                    inbox=inbox,
                    devices_by_role=devices_by_role,
                    services=services,
                    trace_id=trace_id,
                )

                # 如果处理失败，直接返回
                if not result.success:
                    return result

                # 如果提供了 write_callback，执行持久化写入
                if write_callback is not None and result.success:
                    await write_callback(result)

                return result

        except LockAcquireError:
            logger.exception(f"Failed to acquire lock for session {session_id}")
            return _system_error_result("Lock acquire failed")
        except Exception as e:
            logger.exception(f"Unexpected error processing inbox {inbox_id_for_log}")
            return _system_error_result(str(e))

    async def _process_read_phase(
        self,
        session: Any,
        workline: Any,
        inbox: Any,
        devices_by_role: dict[str, list[Any]],
        services: WorklineRuntimeServices,
        trace_id: str,
    ) -> OrchestratorResult:
        """READ pass - 读取阶段（当前非共享读）

        执行:
        - 读取 RuntimeCapabilityDispatcher 写入的 RuntimeIntent
        - 校验 intent 不修改 runtime-owned context key

        Args:
            session: WorklineSession 实体
            workline: WorkLine 实体
            inbox: RuntimeInbox 实体
            devices_by_role: 按角色分组的设备映射
            services: 运行时领域服务容器
            trace_id: Trace ID

        Returns:
            OrchestratorResult: 处理结果
        """
        trace = TraceContext.from_runtime(
            session=session,
            workline=workline,
            inbox=inbox,
            trace_id=trace_id,
        )
        _ = devices_by_role, services, trace

        session_contract = _ensure_non_empty_str(getattr(session, "contract_version", None))
        workline_contract = _ensure_non_empty_str(getattr(workline, "contract_version", None))
        if session_contract and workline_contract and session_contract != workline_contract:
            return _error_result(
                ErrorCode.CONTRACT_MISMATCH,
                f"Session contract {session_contract!r} != workline {workline_contract!r}",
            )

        try:
            result = self._runtime_intents_from_dispatcher(
                inbox,
                workline=workline,
                trace_id=trace.trace_id or trace_id,
            )
        except Exception as e:
            logger.exception("Runtime capability intent extraction failed")
            return _error_result(ErrorCode.PLUGIN_EXECUTION_FAILED, str(e))

        return self._process_intents(result, session)

    async def _process_write_phase(
        self,
        session: Any,
        workline: Any,
        inbox: Any,
        devices_by_role: dict[str, list[Any]],
        services: WorklineRuntimeServices,
        trace_id: str,
        read_result: OrchestratorResult,
    ) -> OrchestratorResult:
        """WRITE pass - 写入阶段（独占）

        执行:
        - 状态迁移验证
        - 结果返回（供 Celery 任务使用）

        注意:实际的状态修改默认仍由 Celery 任务的 `_apply_orchestrator_effects` 完成；
        当 `process_inbox(..., write_callback=...)` 提供写回调时，worker 会在同一 WRITE 锁临界区内
        执行真实持久化写入，从而避免"锁住编排结果、放开真实写入"的并发窗口。

        Args:
            session: WorklineSession 实体
            workline: WorkLine 实体
            inbox: RuntimeInbox 实体
            devices_by_role: 按角色分组的设备映射
            services: 运行时领域服务容器
            trace_id: Trace ID
            read_result: 读取阶段的结果

        Returns:
            OrchestratorResult: 处理结果
        """
        session_id = self._resolve_session_pk(session)
        logger.debug(f"WRITE 阶段开始 for session {session_id}")

        # 当前实现:直接返回读取阶段的结果
        # 状态修改在 Celery 任务 _apply_orchestrator_effects 中完成（不在锁保护下）
        # 占位参数避免 IDE/ruff 警告
        _ = session, workline, inbox, devices_by_role, services, trace_id

        logger.debug(f"WRITE 阶段完成 for session {session_id}")
        return read_result

    def _runtime_intents_from_dispatcher(self, inbox: Any, *, workline: Any, trace_id: str) -> list[RuntimeIntent]:
        """Normalize RuntimeInbox payload and dispatch to material-flow runtime capability."""

        normalized_input = normalize_inbox_input(
            inbox,
            trace_id=trace_id,
            plugin_key=getattr(workline, "plugin_key", None),
        )
        if not _ensure_non_empty_str(getattr(normalized_input, "runtime_capability", None)):
            return _standard_inbox_intents(normalized_input, inbox=inbox, workline=workline, trace_id=trace_id)

        profile = self._runtime_profile_resolver(normalized_input)
        capability_result = self._runtime_dispatcher.dispatch(normalized_input, profile=profile)
        raw_intents = getattr(capability_result, "intents", None)
        if not isinstance(raw_intents, list) or not raw_intents:
            raise ValueError("Runtime capability dispatcher did not produce intents")
        return [
            intent if isinstance(intent, RuntimeIntent) else RuntimeIntent.model_validate(intent)
            for intent in raw_intents
        ]

    def _process_intents(self, intents: list[RuntimeIntent], session: Any) -> OrchestratorResult:
        _ = session
        for intent in intents:
            if intent.context_patch and _context_patch_has_reserved_key(intent.context_patch):
                logger.warning("Plugin attempted to write reserved runtime state")
                return _error_result(
                    ErrorCode.PLUGIN_TRANSITION_INVALID,
                    "context patch contains runtime-owned key",
                )

        return OrchestratorResult(success=True, intents=intents)


__all__ = ["OrchestratorResult", "OrchestratorService"]
