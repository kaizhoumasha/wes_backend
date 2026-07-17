"""RuntimeInboxProcessorService composition (Task 5 三阶段 Processor 拆分).

组合 Validation → Orchestration → Write-back 三阶段.
提供 RuntimeInbox 唯一生产入口 `process_claimed(db, claim)`，由 Celery task 调用。

行为对齐:
- claim 阶段: 由唯一 RuntimeInboxRepository.claim_received_with_token 持有
  (调用方负责).
- validation 阶段: RuntimeInboxValidationService.
- orchestration 阶段: RuntimeInboxProcessorService 委托 OrchestratorService.
- write-back 阶段: RuntimeInboxWriteBackService.
- ESTOP / TIMER_TIMEOUT / duplicate entry / late command / missing context
  均由 RuntimeInbox-owned 三阶段服务承载。
- 写终态: mark_processed / mark_failed / mark_dead_letter 走
  RuntimeInboxService (processor_token 作为 lease_token fencing, 作用于
  RuntimeInbox 表，不存在跨表 fallback)。
- 失败重试与超时不直接 raise, 全部转换成 ProcessResult 统计.
"""

from __future__ import annotations

import uuid
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, Any, Self, TypedDict

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, ValidationError, model_validator

from src.app.runtime.capability_catalog import parse_workline_six_in_one
from src.app.runtime.capability_port_registry import CapabilityPortRegistry, RuntimeCapabilityContext
from src.app.runtime.extension_identity import sha256_digest
from src.app.runtime.orchestration.diagnostics import (
    ErrorCode,
    ErrorDomain,
    ProblemClass,
    map_failure_to_diagnostic,
)
from src.app.runtime.orchestration.effect_result import WriteBackDisposition
from src.app.runtime.orchestration.material_fact_version import (
    material_unit_fact_version as _material_unit_fact_version,
)
from src.app.runtime.orchestration.repositories.runtime_inbox_repository import (
    RuntimeInboxRepository,
    runtime_inbox_repository,
)
from src.app.runtime.orchestration.runtime_intent import (
    RuntimeIntent,
    RuntimeIntentKind,
)
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_context_loader import (
    _canonical_workline_session_id,
)
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_processor_service import (
    RuntimeInboxOrchestratorDelegate,
)
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_service import (
    RuntimeInboxReplayNotAllowed,
    RuntimeInboxReplaySourceValidation,
    RuntimeInboxReplaySourceValidator,
    RuntimeInboxService,
    runtime_inbox_service,
)
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_validation_service import (
    RuntimeInboxValidationService,
    _entry_event_types_for_workline,
)
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_writeback_service import (
    RuntimeInboxWriteBackService,
    WriteBackState,
    _is_late_or_duplicate_command_result_for_session,
    _payload_for_inbox,
    _record_duplicate_entry_archive_timeline,
    _record_late_command_result_archive_timeline,
    _require_fenced_update,
    _session_status_value,
    _session_write_snapshot,
)
from src.app.runtime.orchestration.services.session.session_resolver import SessionResolveError
from src.app.runtime.system_capabilities.gateway import AttemptCloseReport, SystemCapabilityGateway
from src.app.runtime.system_capabilities.outcomes import ContractViolation
from src.app.runtime.system_capabilities.replay import (
    RecordedReplayResolution,
    TimelineRecordedReplayService,
)
from src.app.runtime.workline_plugins.attempt_coordinator import (
    AttemptSnapshot,
    AttemptWriteSet,
    PluginAttemptContext,
    PluginAttemptRunner,
    PluginWriteSetLimits,
    WriteDisposition,
    bound_attempt_write_set,
)
from src.app.runtime.workline_plugins.contracts import MAX_PLUGIN_DECISION_INTENTS, PluginDecision
from src.app.runtime.workline_plugins.dispatcher import (
    PinnedPluginSnapshot,
    PluginDispatchRequest,
    WorklinePluginDispatcher,
)
from src.app.workline.constants import (
    INBOX_PROCESS_TIMEOUT_SECONDS,
    WORKLINE_INBOX_PROCESSING_STALE_SECONDS,
)
from src.app.workline.diagnostic_support import _record_diagnostic
from src.app.workline.services.safety_service import WorkLineSafetyBlocked
from src.app.workline.utils import payload_dict
from src.utils.value_normalization import (
    optional_int,
    optional_str,
    resolve_entity_id,
    resolve_required_pk,
    string_value,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.app.runtime.orchestration.orchestrator_bridge import OrchestratorResult
    from src.app.runtime.system_capabilities.definition import SystemCapabilityDefinition


class ProcessResult(TypedDict):
    """RuntimeInbox 处理结果统计。"""

    processed: int
    success: int
    failed: int
    skipped: int
    resource_wait: int


class GeneratedPluginAttemptRunner:
    """把 Stage 1 固定请求交给 generated dispatcher，禁止回落 legacy。"""

    def __init__(self, *, dispatcher: Any | None = None) -> None:
        self._dispatcher = dispatcher or WorklinePluginDispatcher()

    async def run(self, context: PluginAttemptContext) -> AttemptWriteSet:
        if context.dispatch_request is None:
            return _plugin_hold_write_set(context, "PLUGIN_DISPATCH_REQUEST_MISSING")
        gateway = _EvidenceCollectingGateway(context.runtime.gateway)
        result = await self._dispatcher.dispatch(
            request=context.dispatch_request,
            gateway=gateway,
        )
        if isinstance(result, ContractViolation):
            return _plugin_hold_write_set(context, result.error_code)
        if not isinstance(result, PluginDecision):
            return _plugin_hold_write_set(context, "PLUGIN_DECISION_INVALID")
        try:
            intents = _system_capability_intents(context, tuple(result.intents))
        except (TypeError, ValueError):
            return _plugin_hold_write_set(context, "PLUGIN_EFFECT_CONVERSION_INVALID")
        return AttemptWriteSet(
            evidence=tuple(gateway.evidence),
            next_state=result.next_state,
            intents=intents,
            outcome_code=result.outcome_code,
        )


class _EvidenceCollectingGateway:
    """只收集本次 dispatcher 实际读取的 QUERY evidence。"""

    def __init__(self, gateway: Any) -> None:
        self._gateway = gateway
        self.evidence: list[Any] = []

    async def execute(self, capability_key: str, contract_version: str, input_data: Any) -> Any:
        result = await self._gateway.execute(capability_key, contract_version, input_data)
        if result.evidence is not None and result.evidence not in self.evidence:
            self.evidence.append(result.evidence)
        return result


def _system_capability_intents(
    context: PluginAttemptContext,
    intents: tuple[RuntimeIntent, ...],
) -> tuple[RuntimeIntent, ...]:
    """把插件领域意图收敛为 generated EFFECT 的唯一通用包络。"""

    binding_id = context.snapshot.binding_id
    binding_version = context.snapshot.binding_version
    if binding_id is None or binding_version is None:
        if intents:
            raise ValueError("effect conversion requires binding snapshot")
        return ()
    mark_ng = any(intent.kind is RuntimeIntentKind.MARK_NG for intent in intents)
    creates_material = any(intent.kind is RuntimeIntentKind.CREATE_MATERIAL_UNIT for intent in intents)
    converted: list[RuntimeIntent] = []
    for index, intent in enumerate(intents):
        if intent.kind is RuntimeIntentKind.SYSTEM_CAPABILITY:
            converted.append(intent)
            continue
        if intent.kind is RuntimeIntentKind.UPDATE_CONTEXT:
            # PluginState 由 next_state 原子写入；同决策 MARK_NG 合并进 MaterialUnit CREATE。
            continue
        capability_key: str
        payload: dict[str, Any]
        precondition: dict[str, Any]
        fact_version: str | int
        if intent.kind is RuntimeIntentKind.MARK_NG:
            if creates_material:
                continue
            if context.snapshot.material_unit_id is None or context.snapshot.material_unit_version is None:
                raise ValueError("MARK_NG requires pinned material unit facts")
            capability_key = "material_flow.material_unit_write"
            payload = {
                "operation": "MARK_NG",
                "material_unit_id": context.snapshot.material_unit_id,
                "status": "NG",
            }
            precondition = {"expected_absent": None}
            fact_version = context.snapshot.material_unit_version
        elif intent.kind is RuntimeIntentKind.COMMAND:
            capability_key = "device.device_command_write"
            payload = {
                "device_role": intent.device_role,
                "target_device_id": intent.target_device_id,
                "action": intent.action,
                "payload": deepcopy(intent.payload_json),
                "timeout_ms": (intent.timeout_seconds or 30) * 1000,
            }
            precondition = {"expected_available": True}
            fact_version = "device:v1"
        elif intent.kind is RuntimeIntentKind.CREATE_MATERIAL_UNIT:
            capability_key = "material_flow.material_unit_write"
            payload = {"operation": "CREATE", **deepcopy(intent.payload_json)}
            if mark_ng:
                payload["status"] = "NG"
            precondition = {"expected_absent": True}
            fact_version = 0
        elif intent.kind is RuntimeIntentKind.UPDATE_MATERIAL_UNIT_STATUS:
            capability_key = "material_flow.material_unit_write"
            payload = {"operation": "UPDATE_STATUS", **deepcopy(intent.payload_json)}
            precondition = {"expected_absent": None}
            fact_version = context.snapshot.session_version
        elif intent.kind is RuntimeIntentKind.BLOCK:
            if context.snapshot.session_status is None:
                raise ValueError("BLOCK requires pinned session status")
            capability_key = "runtime.session_hold"
            payload = {
                "failure_domain": intent.block_scope.value if intent.block_scope is not None else "PLUGIN",
                "reason_code": intent.reason_code or "PLUGIN_HOLD",
                "message": intent.message or "Workline Plugin requested Hold",
            }
            precondition = {"expected_status": context.snapshot.session_status}
            fact_version = f"session:{context.snapshot.session_version}"
        else:
            raise ValueError(f"unsupported plugin effect intent: {intent.kind.value}")
        converted.append(
            RuntimeIntent.system_capability(
                capability_key=capability_key,
                contract_version="v1",
                operation_key=f"inbox:{context.inbox_id}:{index}:{intent.kind.value.lower()}",
                payload=payload,
                precondition=precondition,
                fact_version=fact_version,
                timeout_seconds=5,
                creator_authority="WORKLINE_PLUGIN",
                authorization_policy="PLUGIN_DECLARED_CAPABILITY",
                binding_snapshot={"binding_id": binding_id, "binding_version": binding_version},
                provider_snapshot={"provider_code": "RUNTIME", "profile": "runtime"},
            )
        )
    return tuple(converted)


def _plugin_hold_write_set(context: PluginAttemptContext, reason: str) -> AttemptWriteSet:
    return AttemptWriteSet(
        evidence=(),
        next_state=context.plugin_state,
        intents=(),
        outcome_code="HOLD",
        hold_reason=reason,
    )


def _canonical_plugin_input(inbox: Any) -> tuple[str, dict[str, Any]]:
    """把 transport kind 收敛为 Definition 声明的 logical route/input。"""

    payload = deepcopy(_payload_for_inbox(inbox))
    kind = string_value(getattr(inbox, "kind", None))
    event_type = string_value(getattr(inbox, "event_type", None))
    declared_route = optional_str(payload.get("logical_route")) or optional_str(payload.get("callback_type"))
    callback_route = declared_route or event_type
    if kind == "COMMAND_RESULT" or (
        kind in {"INTERNAL_EVENT", "EXTERNAL_HTTP"} and callback_route == "PICK_AND_PUT_RESULT"
    ):
        command_code = optional_str(payload.get("command_code"))
        if command_code is None:
            raise ValueError("command correlation is required")
        return "PICK_AND_PUT_RESULT", {
            "route": "PICK_AND_PUT_RESULT",
            "command_code": command_code,
            "command_type": optional_str(payload.get("command_type"))
            or optional_str(payload.get("task_type"))
            or "PICK_AND_PUT",
            "result": string_value(payload.get("result") or "ERROR").upper(),
            "data": payload_dict(payload.get("data")),
            "error_detail": payload_dict(payload.get("error_detail")),
        }
    if kind == "TIMER_TIMEOUT" or event_type == "TIMER_TIMEOUT":
        data = payload_dict(payload.get("data")) or payload
        command_code = optional_str(data.get("command_code"))
        if command_code is None:
            raise ValueError("command correlation is required")
        return "BUSINESS_TIMEOUT", {
            "route": "BUSINESS_TIMEOUT",
            "command_code": command_code,
            "wait_type": optional_str(data.get("wait_type")) or "COMMAND_RESULT",
        }
    if kind == "REPLAY_REQUEST":
        return "REPLAY_REQUEST", {
            "route": "REPLAY_REQUEST",
            "idempotency_key": string_value(payload.get("idempotency_key")),
            "payload_digest": string_value(payload.get("payload_digest")),
        }
    route = callback_route
    if route == "SYSTEM_CAPABILITY_RESULT":
        raise ValueError("raw SYSTEM_CAPABILITY_RESULT is not a plugin route")
    if route is None:
        raise ValueError("plugin logical route is required")
    return route, payload


@dataclass(frozen=True, slots=True)
class _InboxDiagnosticSnapshot:
    """Inbox 诊断快照, 避免 rollback 后访问已过期 ORM 字段."""

    id: int | None
    kind: Any | None
    trace_id: str | None
    event_id: str | None
    causation_id: str | None
    workline_id: int | None
    session_id: int | None
    device_id: int | None
    command_id: int | None
    attempt_count: int
    payload_json: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RuntimeInboxAttemptRuntime:
    """仅在单次 claim 内有效的 Port、Context、Gateway 容器。"""

    attempt_id: str
    port_registry: CapabilityPortRegistry
    context: RuntimeCapabilityContext
    gateway: SystemCapabilityGateway
    _closed: bool = field(default=False, init=False, compare=False, repr=False)
    _close_report: AttemptCloseReport | None = field(default=None, init=False, compare=False, repr=False)

    async def aclose(self) -> AttemptCloseReport | None:
        """幂等释放 attempt-scoped gateway；waiter 取消不等于 owner 关闭。"""

        if self._closed:
            return self._close_report
        object.__setattr__(self, "_closed", True)
        report = await self.gateway.aclose()
        object.__setattr__(self, "_close_report", report)
        return report


class _ReplayProjectedInbox:
    """仅覆盖 replay 路由字段，其余 claim/终态证据委托原 RuntimeInbox。"""

    def __init__(self, source: Any, envelope: dict[str, Any]) -> None:
        self._source = source
        self._replay_immediate_source_inbox_id = envelope["immediate_source_inbox_id"]
        self._replay_root_source_inbox_id = envelope["root_source_inbox_id"]
        self.kind = envelope["original_kind"]
        self.payload_json = deepcopy(envelope["original_payload"])
        self.provider_code = envelope["original_provider_code"]
        self.event_type = envelope["original_event_type"]
        self.source_event_id = envelope["original_source_event_id"]
        self.payload_hash = envelope["original_payload_hash"]
        self.workline_id = envelope["original_workline_id"]
        self.device_id = envelope["original_device_id"]
        self.command_id = envelope["original_command_id"]
        self.workline_session_id = envelope["original_workline_session_id"]
        self.execution_session_id = envelope["original_execution_session_id"]
        self.correlation_id = envelope["original_correlation_id"]
        self.trace_id = envelope["original_trace_id"]
        self.event_id = envelope["original_event_id"]
        self.causation_id = envelope["original_causation_id"]

    @property
    def is_manual_replay(self) -> bool:
        """标记该投影来自 canonical REPLAY_REQUEST，不写入原业务 payload。"""

        return True

    @property
    def replay_immediate_source_inbox_id(self) -> int:
        """返回已校验的直接 replay 来源 Inbox 主键。"""

        return self._replay_immediate_source_inbox_id

    @property
    def replay_root_source_inbox_id(self) -> int:
        """返回已校验的 replay 根来源 Inbox 主键。"""

        return self._replay_root_source_inbox_id

    def __getattr__(self, name: str) -> Any:
        return getattr(self._source, name)


def _project_replay_request(
    inbox: Any,
    *,
    validated_source: RuntimeInboxReplaySourceValidation | None = None,
) -> Any:
    """将 REPLAY_REQUEST 单层投影为原业务语义，不修改持久化行。"""

    if _kind_value(inbox) != "REPLAY_REQUEST":
        return inbox
    if validated_source is None:
        raise RuntimeError("UNVERIFIED_REPLAY_SOURCE")
    return _ReplayProjectedInbox(inbox, validated_source.envelope)


def _snapshot_inbox_for_diagnostic(inbox: Any) -> _InboxDiagnosticSnapshot:
    """在事务回滚前提取诊断需要的 Inbox 字段."""
    return _InboxDiagnosticSnapshot(
        id=resolve_entity_id(inbox),
        kind=getattr(inbox, "kind", None),
        trace_id=getattr(inbox, "trace_id", None),
        event_id=getattr(inbox, "event_id", None),
        causation_id=getattr(inbox, "causation_id", None),
        workline_id=optional_int(getattr(inbox, "workline_id", None)),
        session_id=_canonical_workline_session_id(inbox),
        device_id=optional_int(getattr(inbox, "device_id", None)),
        command_id=optional_int(getattr(inbox, "command_id", None)),
        attempt_count=int(getattr(inbox, "attempt_count", 0)),
        payload_json=dict(_payload_for_inbox(inbox)),
    )


def _empty_result() -> ProcessResult:
    return {
        "processed": 0,
        "success": 0,
        "failed": 0,
        "skipped": 0,
        "resource_wait": 0,
    }


def _is_lifecycle_only_external_callback(inbox: Any, payload: dict[str, Any]) -> bool:
    """识别已在 ingress 完成副作用、无需运行时能力编排的外部回调。"""
    if _kind_value(inbox) != "EXTERNAL_HTTP":
        return False
    callback_route = optional_str(payload.get("logical_route")) or optional_str(payload.get("callback_type"))
    if callback_route == "PICK_AND_PUT_RESULT":
        return False
    attributes = payload_dict(payload.get("attributes"))
    return (
        optional_str(payload.get("runtime_capability")) is None
        and optional_str(attributes.get("runtime_capability")) is None
    )


def _merge_result(target: ProcessResult, source: ProcessResult) -> None:
    target["processed"] += source.get("processed", 0)
    target["success"] += source.get("success", 0)
    target["failed"] += source.get("failed", 0)
    target["skipped"] += source.get("skipped", 0)
    target["resource_wait"] += source.get("resource_wait", 0)


def _problem_class_for_error_domain(error_domain: ErrorDomain | None) -> ProblemClass | None:
    """为 UNKNOWN 等兜底码补充更接近现场语义的问题大类."""
    if error_domain in {ErrorDomain.DEVICE, ErrorDomain.NETWORK}:
        return ProblemClass.HARDWARE
    return None


class RuntimeInboxProcessorBridge:
    """RuntimeInbox 三阶段 processor composition.

    内部按 validation → orchestration → write-back 三阶段拆分，
    process_runtime_inbox_batch Celery task 只通过本类调用。
    """

    def __init__(
        self,
        *,
        validation_service: RuntimeInboxValidationService | None = None,
        processor_service: RuntimeInboxOrchestratorDelegate | None = None,
        writeback_service: RuntimeInboxWriteBackService | None = None,
        inbox_service: RuntimeInboxService | None = None,
        inbox_repository: RuntimeInboxRepository | None = None,
        replay_source_validator: RuntimeInboxReplaySourceValidator | None = None,
        plugin_attempt_runner: PluginAttemptRunner | None = None,
        recorded_replay_service: TimelineRecordedReplayService | None = None,
        plugin_write_set_limits: PluginWriteSetLimits | None = None,
    ) -> None:
        self._validation_service = validation_service or RuntimeInboxValidationService()
        self._processor_service = processor_service or RuntimeInboxOrchestratorDelegate()
        self._inbox_service = inbox_service or runtime_inbox_service
        self._writeback_service = writeback_service or RuntimeInboxWriteBackService(inbox_service=self._inbox_service)
        self._inbox_repository = inbox_repository or runtime_inbox_repository
        self._replay_source_validator = replay_source_validator or RuntimeInboxReplaySourceValidator(
            self._inbox_repository
        )
        # 平台 binding 只进入 generated dispatcher；legacy 仅服务从未绑定的迁移路径。
        self._plugin_attempt_runner = plugin_attempt_runner or GeneratedPluginAttemptRunner()
        self._recorded_replay_service = recorded_replay_service or TimelineRecordedReplayService()
        self._plugin_write_set_limits = plugin_write_set_limits or PluginWriteSetLimits()

    @property
    def inbox_service(self) -> RuntimeInboxService:
        """RuntimeInboxService 实例。

        终态写回（mark_processed / mark_failed / mark_dead_letter）一律走
        RuntimeInboxService，作用于 RuntimeInbox 表的 processor_token fencing。
        处理状态始终写回 RuntimeInboxService。
        """
        return self._inbox_service

    @property
    def inbox_repository(self) -> RuntimeInboxRepository:
        """RuntimeInboxRepository 实例。

        加载 RuntimeInbox ORM（get_by_id）走 RuntimeInboxRepository，
        claim 与上下文加载始终使用 RuntimeInboxRepository。
        """
        return self._inbox_repository

    def create_attempt_runtime(
        self,
        processor_token: str,
        *,
        base_registry: CapabilityPortRegistry | None = None,
        definitions: dict[tuple[str, str], SystemCapabilityDefinition] | None = None,
        allowed_capabilities: frozenset[tuple[str, str]] | None = None,
        admission_profile: str | None = None,
    ) -> RuntimeInboxAttemptRuntime:
        """新建 attempt-scoped runtime；绝不复用 Port 实例或 QUERY cache。"""

        registry = base_registry.fork_attempt() if base_registry is not None else CapabilityPortRegistry()
        context = RuntimeCapabilityContext(registry)
        if definitions is None:
            from src.app.runtime.system_capabilities.generated_index import SYSTEM_CAPABILITY_INDEX

            definitions = SYSTEM_CAPABILITY_INDEX
        resolved_definitions = dict(definitions)
        if admission_profile is None:
            from src.app.runtime.system_capabilities.wms.rough_sorter_inventory_admission.contracts import (
                PROFILE_IDENTITY,
            )

            admission_profile = PROFILE_IDENTITY
        gateway = SystemCapabilityGateway(
            attempt_id=processor_token,
            definitions=resolved_definitions,
            allowed_capabilities=(
                frozenset(resolved_definitions) if allowed_capabilities is None else allowed_capabilities
            ),
            context=context,
            admission_profile=admission_profile,
        )
        return RuntimeInboxAttemptRuntime(
            attempt_id=processor_token,
            port_registry=registry,
            context=context,
            gateway=gateway,
        )

    async def claim_and_process_batch(
        self,
        db: AsyncSession,
        *,
        limit: int,
        processor_token_prefix: str = "runtime-inbox-worker",  # noqa: S107
    ) -> ProcessResult:
        """顺序 claim 并处理 RuntimeInbox。

        单个 worker 每轮 claim 1 条; 跨 worker 并发由数据库 token claim 和
        claim_bucket_key 队首围栏承载.
        """
        if limit <= 0:
            return _empty_result()

        result: ProcessResult = _empty_result()
        remaining = limit
        while remaining > 0:
            processor_token = f"{processor_token_prefix}-{uuid.uuid4()}"
            claim = await self._claim_one(db, processor_token=processor_token)
            if claim is None:
                break
            message_result = await self.process_claimed(db, claim=claim)
            _merge_result(result, message_result)
            remaining -= 1
            if remaining <= 0:
                break
        return result

    async def _claim_one(
        self,
        db: AsyncSession,
        *,
        processor_token: str,
    ) -> dict[str, Any] | None:
        """claim 1 条 RuntimeInbox 行.

        使用唯一 RuntimeInboxRepository.claim_received_with_token。
        """
        claims = await self.inbox_repository.claim_received_with_token(
            db,
            limit=1,
            processor_token=processor_token,
            stale_after_seconds=WORKLINE_INBOX_PROCESSING_STALE_SECONDS,
        )
        return claims[0] if claims else None

    async def process_claimed(  # noqa: PLR0911, PLR0912
        self,
        db: AsyncSession,
        *,
        claim: dict[str, Any] | Any,
    ) -> ProcessResult:
        """处理已被当前 worker claim 的单条 RuntimeInbox 消息.

        内部委托 Validation / Processor / Write-back 三阶段。
        """
        result: ProcessResult = _empty_result()
        inbox = await self.inbox_repository.get_by_id(db, claim["id"] if isinstance(claim, dict) else claim.id)
        if inbox is None:
            result["skipped"] += 1
            return result

        processor_token = (
            claim.get("processor_token") if isinstance(claim, dict) else getattr(claim, "processor_token", None)
        )
        if not processor_token:
            processor_token = f"runtime-inbox-worker-{uuid.uuid4()}"

        # 每次 claim 都建立全新的 Port/Context/Gateway；即使 replay 验真失败，
        # 也不允许复用上一个 attempt 的 handler 或 evidence cache。
        attempt_runtime = self.create_attempt_runtime(processor_token)

        diagnostic_inbox = _snapshot_inbox_for_diagnostic(inbox)
        inbox_pk_text = str(diagnostic_inbox.id or getattr(inbox, "id", "unknown"))
        inbox_pk: int | None = None
        try:
            inbox_pk = resolve_required_pk(inbox, "inbox", "id", "inbox_id")
            if isinstance(claim, dict) and claim.get("id") and claim["id"] != inbox_pk:
                result["skipped"] += 1
                return result

            validated_replay_source = None
            if _kind_value(inbox) == "REPLAY_REQUEST":
                validated_replay_source = await self._replay_source_validator.validate_for_consumption(db, source=inbox)
            inbox = _project_replay_request(inbox, validated_source=validated_replay_source)
            payload = _payload_for_inbox(inbox)
            resolved_event_type = optional_str(getattr(inbox, "event_type", None))
            if resolved_event_type is None:
                raise ValueError("RuntimeInbox event_type is required")

            if _is_lifecycle_only_external_callback(inbox, payload):
                # ingress 已在同一事务完成 lifecycle 副作用；processor 只负责
                # 以 lease fencing 收束 RuntimeInbox 终态，不重复加载会话或执行安全门禁。
                _require_fenced_update(
                    await self.inbox_service.mark_processed(
                        db,
                        inbox_id=inbox_pk,
                        lease_token=processor_token,
                    ),
                    action="mark_processed",
                    inbox_id=inbox_pk,
                )
                await db.commit()
                result["success"] += 1
                result["processed"] += 1
                return result

            # ========== Stage 1: Validation (SCAN gate) ==========
            (
                session,
                workline,
                device,
                command,
                devices_by_role,
                services,
                safety_checked,
            ) = await _load_related_entities(db, inbox, resolved_event_type=resolved_event_type)

            validation_outcome = await self._validation_service.pre_gate(
                db,
                inbox=inbox,
                resolved_event_type=resolved_event_type,
                workline=workline,
            )
            if not validation_outcome.proceed_to_orchestrator:
                # SCAN gate 失败 -> 终态 FAILED.
                _require_fenced_update(
                    await self.inbox_service.mark_failed(
                        db,
                        inbox_id=inbox_pk,
                        lease_token=processor_token,
                        error_code=(validation_outcome.error_code or ErrorCode.UNKNOWN).value,
                        error_message=validation_outcome.error_message or "validation failed",
                        retryable=False,
                    ),
                    action="mark_failed",
                    inbox_id=inbox_pk,
                )
                await db.commit()
                result["failed"] += 1
                result["processed"] += 1
                return result

            if (
                bool(getattr(inbox, "is_manual_replay", False))
                and session is not None
                and workline is not None
                and isinstance(getattr(session, "plugin_binding_id", None), int)
            ):
                # Recorded replay 复用已持久化 decision/evidence；必须先于源事件的
                # TIMER/late callback 路由，避免重放再次触发 provider 或新 EFFECT。
                _configure_attempt_runtime_ports(attempt_runtime, services=services)
                return await self._process_platform_plugin_attempt(
                    db,
                    inbox=inbox,
                    session=session,
                    workline=workline,
                    resolved_event_type=resolved_event_type,
                    processor_token=processor_token,
                    attempt_runtime=attempt_runtime,
                    devices_by_role=devices_by_role,
                )

            # ========== Stage 1b: ESTOP / TIMER 专用路由 ==========
            inbox_kind_value = _kind_value(inbox)
            routing_outcome = self._validation_service.classify_estop_or_timer(
                resolved_event_type=resolved_event_type,
                inbox_kind=inbox_kind_value,
            )
            if routing_outcome.estop_event:
                estop_processed = await _handle_estop(
                    db,
                    inbox=inbox,
                    inbox_pk=inbox_pk,
                    payload=payload,
                    session=session,
                    workline=workline,
                    device=device,
                    command=command,
                    processor_token=processor_token,
                    inbox_service=self.inbox_service,
                )
                await db.commit()
                if estop_processed:
                    result["success"] += 1
                else:
                    result["failed"] += 1
                result["processed"] += 1
                return result
            if routing_outcome.timer_timeout_event:
                _ = await _handle_timer_timeout(
                    db,
                    inbox=inbox,
                    inbox_pk=inbox_pk,
                    payload=payload,
                    processor_token=processor_token,
                    inbox_service=self.inbox_service,
                )
                await db.commit()
                result["success"] += 1
                result["processed"] += 1
                return result

            if session is None or workline is None:
                error_msg = "Inbox processing missing session/workline context"
                await _record_diagnostic(
                    db,
                    inbox=inbox,
                    error_code=ErrorCode.SESSION_CONTEXT_MISSING,
                    message=error_msg,
                    session=session,
                    workline=workline,
                    device=device,
                    command=command,
                )
                _require_fenced_update(
                    await self.inbox_service.mark_failed(
                        db,
                        inbox_id=inbox_pk,
                        lease_token=processor_token,
                        error_code=ErrorCode.SESSION_CONTEXT_MISSING.value,
                        error_message=error_msg,
                        retryable=False,
                    ),
                    action="mark_failed",
                    inbox_id=inbox_pk,
                )
                await db.commit()
                result["failed"] += 1
                result["processed"] += 1
                return result

            if not safety_checked:
                from src.app.workline.services.safety_service import workline_safety_service

                _ = await workline_safety_service.assert_accepting_work(
                    db,
                    workline_id=resolve_required_pk(workline, "workline", "id", "workline_id"),
                )

            # ========== Stage 1c: duplicate / late detection ==========
            if await _is_duplicate_entry_event(
                db,
                inbox=inbox,
                payload=payload,
                resolved_event_type=resolved_event_type,
                session=session,
                workline=workline,
                validation_service=self._validation_service,
            ) and not _is_resource_wait_retry_for_same_inbox(session, inbox_pk):
                material_conflict = _duplicate_entry_material_conflict(
                    session=session,
                    workline=workline,
                    payload=payload,
                )
                if material_conflict is not None:
                    conflict_message, conflict_details = material_conflict
                    await _record_diagnostic(
                        db,
                        inbox=inbox,
                        error_code=ErrorCode.CALLBACK_SCHEMA_INVALID,
                        message=conflict_message,
                        session=session,
                        workline=workline,
                        device=device,
                        command=command,
                        extra=conflict_details,
                    )
                    _require_fenced_update(
                        await self.inbox_service.mark_dead_letter(
                            db,
                            inbox_id=inbox_pk,
                            lease_token=processor_token,
                            error_code=ErrorCode.CALLBACK_SCHEMA_INVALID.value,
                            error_message=conflict_message,
                        ),
                        action="mark_dead_letter",
                        inbox_id=inbox_pk,
                    )
                    await db.commit()
                    result["failed"] += 1
                    result["processed"] += 1
                    logger.warning(
                        f"Inbox {inbox_pk} rejected conflicting duplicate entry event: "
                        f"session_id={resolve_entity_id(session)}, conflicts={conflict_details['conflicts']}"
                    )
                    return result

                await _record_duplicate_entry_archive_timeline(
                    db,
                    session=session,
                    workline=workline,
                    inbox=inbox,
                    payload=payload,
                    reason="SESSION_ALREADY_IN_PROGRESS_OR_TERMINAL",
                )
                _require_fenced_update(
                    await self.inbox_service.mark_processed(
                        db,
                        inbox_id=inbox_pk,
                        lease_token=processor_token,
                    ),
                    action="mark_processed",
                    inbox_id=inbox_pk,
                )
                await db.commit()
                result["success"] += 1
                result["processed"] += 1
                return result

            if _is_late_or_duplicate_command_result_for_session(
                inbox=inbox,
                payload=payload,
                session=session,
                command=command,
            ):
                await _record_late_command_result_archive_timeline(
                    db,
                    session=session,
                    workline=workline,
                    inbox=inbox,
                    command=command,
                    payload=payload,
                    reason="COMMAND_RESULT_NO_LONGER_MATCHES_SESSION_WAIT",
                )
                _require_fenced_update(
                    await self.inbox_service.mark_processed(
                        db,
                        inbox_id=inbox_pk,
                        lease_token=processor_token,
                    ),
                    action="mark_processed",
                    inbox_id=inbox_pk,
                )
                await db.commit()
                result["success"] += 1
                result["processed"] += 1
                return result

            if isinstance(getattr(session, "plugin_binding_id", None), int):
                _configure_attempt_runtime_ports(attempt_runtime, services=services)
                return await self._process_platform_plugin_attempt(
                    db,
                    inbox=inbox,
                    session=session,
                    workline=workline,
                    resolved_event_type=resolved_event_type,
                    processor_token=processor_token,
                    attempt_runtime=attempt_runtime,
                    devices_by_role=devices_by_role,
                )

            # ========== Stage 2: Orchestration (delegated) ==========
            write_state = WriteBackState()
            session_snapshot = _session_write_snapshot(session)
            sse_workline_id = resolve_entity_id(workline)
            sse_session_id = resolve_entity_id(session)
            write_callback = self._writeback_service.build_write_callback(
                db,
                session=session,
                workline=workline,
                inbox=inbox,
                devices_by_role=devices_by_role,
                device=device,
                command=command,
                inbox_pk=inbox_pk,
                session_snapshot=session_snapshot,
                sse_workline_id=sse_workline_id,
                sse_session_id=sse_session_id,
                processor_token=processor_token,
                state=write_state,
            )

            orch_result: "OrchestratorResult" = await self._processor_service.process(  # noqa: UP037
                db,
                session=session,
                workline=workline,
                inbox=inbox,
                devices_by_role=devices_by_role,
                services=services,
                trace_id=getattr(inbox, "trace_id", None) or "",
                write_callback=write_callback,
            )

            # ========== Stage 3: Result dispatch ==========
            if orch_result.success:
                if not write_state.write_effects_applied:
                    raise RuntimeError("WRITE lock callback was not executed for successful orchestrator result")
                if write_state.disposition == WriteBackDisposition.RESOURCE_RETRY:
                    result["resource_wait"] += 1
                    logger.info(f"Inbox {inbox_pk} resource wait, parked for retry")
                else:
                    result["success"] += 1
                    logger.info(f"Inbox {inbox_pk} 处理成功")
                if write_state.enqueue_outbox_dispatch:
                    from src.core.task_queue_gateway import task_queue_gateway

                    task_queue_gateway.enqueue_outbox(limit=50)
            else:
                error_msg = orch_result.error or "Unknown error"
                mapped_error_code, mapped_error_domain = map_failure_to_diagnostic(
                    failure=None,
                    error_code=orch_result.error_code,
                )
                await _record_diagnostic(
                    db,
                    inbox=diagnostic_inbox,
                    error_code=mapped_error_code,
                    error_domain=mapped_error_domain,
                    problem_class=_problem_class_for_error_domain(mapped_error_domain),
                    message=error_msg,
                    session=session,
                    workline=workline,
                    device=device,
                    command=command,
                )
                _require_fenced_update(
                    await self.inbox_service.mark_failed(
                        db,
                        inbox_id=inbox_pk,
                        lease_token=processor_token,
                        error_code=mapped_error_code.value,
                        error_message=error_msg,
                        retryable=False,
                    ),
                    action="mark_failed",
                    inbox_id=inbox_pk,
                )
                await db.commit()
                result["failed"] += 1
                logger.warning(f"Inbox {inbox_pk} 处理失败: {error_msg}")

            result["processed"] += 1

        except SessionResolveError as e:
            logger.warning(f"Inbox {inbox_pk_text} session resolve failed: {e}")
            with suppress(Exception):
                await db.rollback()
            await _record_diagnostic(
                db,
                inbox=diagnostic_inbox,
                error_code=ErrorCode.SESSION_RESOLVE_FAILED,
                message=str(e),
            )
            try:
                if inbox_pk is not None:
                    _require_fenced_update(
                        await self.inbox_service.mark_failed(
                            db,
                            inbox_id=inbox_pk,
                            lease_token=processor_token,
                            error_code=ErrorCode.SESSION_RESOLVE_FAILED.value,
                            error_message=str(e),
                            retryable=False,
                        ),
                        action="mark_failed",
                        inbox_id=inbox_pk,
                    )
                    await db.commit()
            except Exception as mark_error:
                with suppress(Exception):
                    await db.rollback()
                logger.warning(f"Inbox {inbox_pk_text} session resolve 失败补记失败: {mark_error}")
            result["failed"] += 1
            result["processed"] += 1

        except WorkLineSafetyBlocked as e:
            logger.warning(f"Inbox {inbox_pk_text} blocked by WorkLine safety state: {e}")
            with suppress(Exception):
                await db.rollback()
            await _record_diagnostic(
                db,
                inbox=diagnostic_inbox,
                error_code=ErrorCode.UNKNOWN,
                error_domain=ErrorDomain.WORKFLOW,
                message=str(e),
            )
            try:
                if inbox_pk is not None:
                    # RuntimeInboxService.mark_failed(retryable=True) 内部按
                    # attempt_count 计算指数退避 next_retry_at, 等价 park_for_retry 语义。
                    _require_fenced_update(
                        await self.inbox_service.mark_failed(
                            db,
                            inbox_id=inbox_pk,
                            lease_token=processor_token,
                            error_code=ErrorCode.UNKNOWN.value,
                            error_message=str(e),
                            retryable=True,
                        ),
                        action="mark_failed",
                        inbox_id=inbox_pk,
                    )
                    await db.commit()
            except Exception as mark_error:
                with suppress(Exception):
                    await db.rollback()
                logger.warning(f"Inbox {inbox_pk_text} safety blocked 补记失败: {mark_error}")
            result["failed"] += 1
            result["processed"] += 1

        except TimeoutError:
            logger.error(f"Inbox {inbox_pk} 处理超时 (> {INBOX_PROCESS_TIMEOUT_SECONDS}s)")
            with suppress(Exception):
                await db.rollback()
            await _record_diagnostic(
                db,
                inbox=diagnostic_inbox,
                error_code=ErrorCode.INBOX_PROCESSING_TIMEOUT,
                message=f"Inbox processing timeout (> {INBOX_PROCESS_TIMEOUT_SECONDS}s)",
            )
            try:
                pk_to_mark = inbox_pk or diagnostic_inbox.id
                if pk_to_mark is not None:
                    _require_fenced_update(
                        await self.inbox_service.mark_failed(
                            db,
                            inbox_id=pk_to_mark,
                            lease_token=processor_token,
                            error_code=ErrorCode.INBOX_PROCESSING_TIMEOUT.value,
                            error_message=f"处理超时 (> {INBOX_PROCESS_TIMEOUT_SECONDS}s)",
                            retryable=False,
                        ),
                        action="mark_failed",
                        inbox_id=pk_to_mark,
                    )
                    await db.commit()
            except Exception as mark_error:
                with suppress(Exception):
                    await db.rollback()
                logger.warning(f"Inbox 超时标记失败: {mark_error}")
            result["failed"] += 1
            result["processed"] += 1

        except Exception as e:
            if isinstance(e, RuntimeInboxReplayNotAllowed):
                # replay 合同拒绝不可重试，只持久化稳定原因码，避免 traceback locals 泄露 payload。
                failure_error_code = e.reason_code
                failure_message = e.reason_code
                retryable = False
                logger.warning(f"Inbox {inbox_pk_text} replay 验真拒绝: reason={e.reason_code}")
            else:
                failure_error_code = ErrorCode.UNKNOWN.value
                failure_message = str(e)
                retryable = True
                logger.exception(f"Inbox {inbox_pk_text} 处理异常")
            with suppress(Exception):
                await db.rollback()
            await _record_diagnostic(
                db,
                inbox=diagnostic_inbox,
                error_code=ErrorCode.UNKNOWN,
                message=failure_message,
            )
            try:
                pk_to_mark = inbox_pk or diagnostic_inbox.id
                if pk_to_mark is not None:
                    _require_fenced_update(
                        await self.inbox_service.mark_failed(
                            db,
                            inbox_id=pk_to_mark,
                            lease_token=processor_token,
                            error_code=failure_error_code,
                            error_message=failure_message,
                            retryable=retryable,
                        ),
                        action="mark_failed",
                        inbox_id=pk_to_mark,
                    )
                    await db.commit()
            except Exception as mark_error:
                with suppress(Exception):
                    await db.rollback()
                logger.warning(f"Inbox {inbox_pk_text} 异常补记失败: {mark_error}")
            result["failed"] += 1
            result["processed"] += 1

        finally:
            # Attempt owner 始终负责排空共享 QUERY；关闭失败不得覆盖原处理结果或异常。
            try:
                close_report = await attempt_runtime.aclose()
            except Exception:
                logger.warning("Plugin attempt cleanup failed: code=ATTEMPT_CLOSE_FAILED")
            else:
                if close_report is not None and close_report.unterminated:
                    logger.warning(
                        "Plugin attempt cleanup incomplete: "
                        f"code={close_report.error_code}, unterminated={close_report.unterminated}"
                    )

        from src.app.sys.services.event_stream_service import publish_deferred_sse_events

        await publish_deferred_sse_events(db)
        return result

    async def _process_platform_plugin_attempt(
        self,
        db: AsyncSession,
        *,
        inbox: Any,
        session: Any,
        workline: Any,
        resolved_event_type: str,
        processor_token: str,
        attempt_runtime: RuntimeInboxAttemptRuntime,
        devices_by_role: dict[str, list[Any]] | None = None,
    ) -> ProcessResult:
        """平台插件三阶段：固定快照 → 无 DB 决策 → 锁后原子写回。"""

        inbox_id = resolve_required_pk(inbox, "inbox", "id", "inbox_id")
        session_id = resolve_required_pk(session, "session", "id", "session_id")
        workline_id = resolve_required_pk(workline, "workline", "id", "workline_id")
        material_unit = None
        material_unit_id = optional_int(getattr(session, "current_material_unit_id", None))
        if material_unit_id is not None:
            from src.app.runtime.orchestration.models.material_unit import MaterialUnit

            material_unit = await db.get(MaterialUnit, material_unit_id)
        snapshot = _plugin_attempt_snapshot(
            session,
            processor_token=processor_token,
            material_unit_version=_material_unit_fact_version(material_unit),
        )
        dispatch_request = None
        if isinstance(self._plugin_attempt_runner, GeneratedPluginAttemptRunner):
            dispatch_request = await _build_plugin_dispatch_request(
                db,
                inbox=inbox,
                session=session,
                workline=workline,
                snapshot=snapshot,
            )
        context = PluginAttemptContext(
            attempt_id=processor_token,
            inbox_id=inbox_id,
            session_id=session_id,
            workline_id=workline_id,
            event_type=resolved_event_type,
            payload=deepcopy(_payload_for_inbox(inbox)),
            plugin_state=deepcopy(dict(getattr(session, "plugin_state_json", {}) or {})),
            snapshot=snapshot,
            runtime=attempt_runtime,
            dispatch_request=dispatch_request,
        )

        replay_resolution: RecordedReplayResolution | None = None
        if bool(getattr(inbox, "is_manual_replay", False)):
            replay_resolution = await self._load_recorded_replay(db, inbox=inbox, snapshot=snapshot)

        # Stage 1 显式提交，释放事务、连接占用和可能持有的行锁。
        await db.commit()

        # Stage 2 不接收 db/session/repository。Recorded replay 直接解码，
        # 因此不会调用 runner 或 Gateway handler。
        if replay_resolution is None:
            write_set = await self._plugin_attempt_runner.run(context)
        else:
            write_set = _write_set_from_recorded_replay(replay_resolution, fallback_state=context.plugin_state)
        write_set = _bounded_plugin_write_set(write_set, limits=self._plugin_write_set_limits)

        # Stage 3 在新短事务内锁权威行、重校验并原子写回。
        disposition = await self._writeback_service.commit_plugin_attempt(
            db,
            expected_snapshot=snapshot,
            inbox_id=inbox_id,
            session_id=session_id,
            workline_id=workline_id,
            trace_id=getattr(inbox, "trace_id", None) or "",
            write_set=write_set,
            workline=workline,
            devices_by_role=devices_by_role,
        )
        result = _empty_result()
        result["processed"] = 1
        if disposition is WriteDisposition.SAFE_RETRY:
            _require_fenced_update(
                await self.inbox_service.mark_failed(
                    db,
                    inbox_id=inbox_id,
                    lease_token=processor_token,
                    error_code="PLUGIN_SNAPSHOT_STALE",
                    error_message="PLUGIN_SNAPSHOT_STALE",
                    retryable=True,
                    consume_attempt=False,
                ),
                action="mark_failed",
                inbox_id=inbox_id,
            )
            await db.commit()
            result["resource_wait"] = 1
        elif write_set.hold_reason is not None:
            result["failed"] = 1
        else:
            result["success"] = 1
        return result

    async def _load_recorded_replay(
        self,
        db: AsyncSession,
        *,
        inbox: Any,
        snapshot: AttemptSnapshot,
    ) -> RecordedReplayResolution:
        if snapshot.definition_identity is None or snapshot.binding_identity is None or snapshot.index_digest is None:
            return RecordedReplayResolution(hold_reason="RECORDED_REPLAY_PIN_MISSING")
        return await self._recorded_replay_service.load(
            db,
            source_inbox_id=int(inbox.replay_root_source_inbox_id),
            expected_definition_identity=snapshot.definition_identity,
            expected_binding_identity=snapshot.binding_identity,
            expected_index_digest=snapshot.index_digest,
        )


async def _build_plugin_dispatch_request(
    db: Any,
    *,
    inbox: Any,
    session: Any,
    workline: Any,
    snapshot: AttemptSnapshot,
) -> PluginDispatchRequest:
    """在 Stage 1 固定 binding/config/facts，Stage 2 只消费 immutable request。"""

    _ = workline
    from src.app.runtime.system_capabilities.wms.rough_sorter_inventory_admission.contracts import (
        RoughSorterBindingSnapshot,
    )
    from src.app.runtime.workline_plugins.rough_sorter.handlers import RoughSorterFacts
    from src.app.workline.services.plugin_binding_service import workline_plugin_binding_service

    binding_id = snapshot.binding_id
    if (
        binding_id is None
        or snapshot.binding_version is None
        or snapshot.plugin_config_hash is None
        or snapshot.index_digest is None
    ):
        raise ValueError("plugin binding snapshot is incomplete")
    binding = await workline_plugin_binding_service.get_pinned(db, binding_id=binding_id)
    raw_config = deepcopy(dict(getattr(binding, "typed_config_json", {}) or {}))
    config_hash = sha256_digest(raw_config)
    binding_config_hash = optional_str(getattr(binding, "typed_config_hash", None))
    if config_hash != snapshot.plugin_config_hash or binding_config_hash != snapshot.plugin_config_hash:
        raise ValueError("plugin config snapshot mismatch")
    profile_identity = optional_str(raw_config.get("provider_profile"))
    if profile_identity is None:
        raise ValueError("plugin provider profile is required")
    plugin_key = optional_str(getattr(binding, "plugin_key", None))
    contract_version = optional_str(getattr(binding, "contract_version", None))
    if plugin_key is None or contract_version is None:
        raise ValueError("plugin identity is required")
    route, raw_input = _canonical_plugin_input(inbox)
    state = deepcopy(dict(getattr(session, "plugin_state_json", {}) or {}))
    data = payload_dict(raw_input.get("data"))
    session_context = payload_dict(getattr(session, "context_json", None))
    six_in_one = payload_dict(session_context.get("six_in_one"))
    business_key = _first_plugin_fact(data, six_in_one, session_context, names=("PkgID", "pkg_code", "business_key"))
    hhpn = _first_plugin_fact(data, six_in_one, session_context, names=("HHPN", "hhpn"))
    lot_code = _first_plugin_fact(data, six_in_one, session_context, names=("LotCode", "lot_code"))
    command_code = optional_str(raw_input.get("command_code"))
    awaiting_code = optional_str(getattr(session, "awaiting_device_command_code", None))
    facts = RoughSorterFacts(
        business_key=business_key,
        hhpn=hhpn,
        lot_code=lot_code,
        correlation_matches=command_code is None or (awaiting_code is not None and command_code == awaiting_code),
        binding_snapshot=RoughSorterBindingSnapshot(
            binding_id=binding_id,
            binding_version=snapshot.binding_version,
            profile_identity=profile_identity,
            plugin_config_hash=snapshot.plugin_config_hash,
            generated_index_digest=snapshot.index_digest,
        ),
    )
    return PluginDispatchRequest(
        plugin_key=plugin_key,
        contract_version=contract_version,
        logical_route=route,
        raw_config=raw_config,
        raw_state=state,
        context_state=state,
        raw_input=raw_input,
        raw_facts=facts.model_dump(mode="json"),
        snapshot=PinnedPluginSnapshot(
            plugin_key=plugin_key,
            contract_version=contract_version,
            binding_identity=f"binding:{binding_id}:{snapshot.binding_version}",
            binding_id=binding_id,
            binding_version=snapshot.binding_version,
            config_hash=snapshot.plugin_config_hash,
            index_digest=snapshot.index_digest,
            profile_identity=profile_identity,
        ),
    )


def _first_plugin_fact(*sources: dict[str, Any], names: tuple[str, ...]) -> str | None:
    for source in sources:
        for name in names:
            value = optional_str(source.get(name))
            if value is not None:
                return value
    return None


def _configure_attempt_runtime_ports(attempt_runtime: RuntimeInboxAttemptRuntime, *, services: Any) -> None:
    """把当前 Inbox 的 typed client 包装为 attempt-scoped QUERY Port。"""

    client = getattr(services, "wms_inventory_client", None)
    if client is None:
        return
    from src.app.wms_integration.adapters import build_wms_inventory_query_port_factory
    from src.app.wms_integration.ports.inventory_query import WmsInventoryQueryPort

    attempt_runtime.port_registry.register(
        WmsInventoryQueryPort,
        build_wms_inventory_query_port_factory(
            client,
            request_id_factory=lambda: f"plugin-query-{attempt_runtime.attempt_id}",
        ),
    )


def _plugin_attempt_snapshot(
    session: Any,
    *,
    processor_token: str,
    material_unit_version: int | None = None,
) -> AttemptSnapshot:
    definition_identity = getattr(session, "plugin_identity", None)
    if definition_identity is None:
        plugin_key = getattr(session, "plugin_key", None)
        contract_version = getattr(session, "contract_version", None)
        if isinstance(plugin_key, str) and plugin_key and isinstance(contract_version, str) and contract_version:
            from src.app.runtime.workline_plugins.generated_index import WORKLINE_PLUGIN_INDEX

            definition = WORKLINE_PLUGIN_INDEX.get((plugin_key, contract_version))
            definition_identity = definition.identity if definition is not None else f"{plugin_key}@{contract_version}"
    return AttemptSnapshot(
        processor_token=processor_token,
        session_version=int(getattr(session, "version", 0)),
        plugin_state_version=int(getattr(session, "plugin_state_version", 0)),
        session_status=string_value(getattr(session, "status", None)),
        material_unit_id=optional_int(getattr(session, "current_material_unit_id", None)),
        material_unit_version=material_unit_version,
        definition_identity=definition_identity,
        binding_id=optional_int(getattr(session, "plugin_binding_id", None)),
        binding_version=optional_int(getattr(session, "plugin_binding_version", None)),
        plugin_config_hash=optional_str(getattr(session, "plugin_config_hash", None)),
        index_digest=optional_str(getattr(session, "plugin_index_digest", None)),
    )


def _write_set_from_recorded_replay(
    resolution: RecordedReplayResolution,
    *,
    fallback_state: dict[str, Any],
) -> AttemptWriteSet:
    if resolution.hold_reason is not None or resolution.decision is None:
        return AttemptWriteSet(
            evidence=(),
            next_state=fallback_state,
            intents=(),
            outcome_code="HOLD",
            hold_reason=resolution.hold_reason or "RECORDED_REPLAY_RECORD_MISSING",
        )
    try:
        decision = TypeAdapter(_RecordedPluginDecision).validate_python(resolution.decision)
    except (TypeError, ValidationError, ValueError):
        return _invalid_recorded_write_set(fallback_state)
    return AttemptWriteSet(
        evidence=resolution.evidence,
        next_state=decision.next_state,
        # Recorded replay 只恢复已审计 decision/state/evidence；原 intent 的
        # EFFECT 已在源 attempt 执行，不得再次产生物理副作用。
        intents=(),
        outcome_code=decision.outcome_code,
        hold_reason=decision.hold_reason,
    )


class _RecordedPluginDecision(BaseModel):
    """Timeline recorded decision 的完整、封闭解码合同。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome_code: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    hold_reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] | None = None
    next_state: dict[str, Any]
    intents: tuple[RuntimeIntent, ...] = Field(max_length=MAX_PLUGIN_DECISION_INTENTS)

    @model_validator(mode="after")
    def validate_hold_combination(self) -> Self:
        if self.outcome_code == "HOLD":
            if self.hold_reason is None or self.intents:
                raise ValueError("recorded HOLD requires a reason and zero intents")
        elif self.hold_reason is not None:
            raise ValueError("recorded non-HOLD decision cannot carry hold_reason")
        return self


def _invalid_recorded_write_set(fallback_state: dict[str, Any]) -> AttemptWriteSet:
    return AttemptWriteSet(
        evidence=(),
        next_state=fallback_state,
        intents=(),
        outcome_code="HOLD",
        hold_reason="RECORDED_REPLAY_RECORD_INVALID",
    )


def _bounded_plugin_write_set(write_set: AttemptWriteSet, *, limits: Any) -> AttemptWriteSet:
    """保留 processor 内部 seam，并委托 write-set 合同 owner。"""

    return bound_attempt_write_set(write_set, limits=limits)


def _kind_value(entity: Any) -> str | None:
    value = getattr(getattr(entity, "kind", None), "value", getattr(entity, "kind", None))
    return value if isinstance(value, str) and value else None


async def _is_duplicate_entry_event(
    db: Any,
    *,
    inbox: Any,
    payload: dict[str, Any],
    resolved_event_type: str,
    session: Any,
    workline: Any,
    validation_service: RuntimeInboxValidationService,
) -> bool:
    """识别已进入忙碌或终态会话的重复入口事件。"""
    if _kind_value(inbox) != "DEVICE_EVENT":
        return False
    if resolved_event_type not in _entry_event_types_for_workline(workline):
        return False
    if await validation_service.is_payload_invalid_entry_replay(db, inbox=inbox, session=session):
        return False
    terminal_statuses = {"COMPLETED", "FAILED", "CANCELLED"}
    busy_statuses = {"WAITING_DEVICE_RESULT", "WAITING_EXTERNAL", "MANUAL_HOLD"}
    status = _session_status_value(session)
    if status in terminal_statuses or status in busy_statuses:
        return True
    if getattr(session, "awaiting_device_command_code", None) is not None:
        return True
    current_wait_type = getattr(session, "current_wait_type", None)
    return bool(current_wait_type)


def _is_resource_wait_retry_for_same_inbox(session: Any, inbox_id: int) -> bool:
    """识别同一 inbox 从 RESOURCE_WAIT 唤醒后的重试。"""
    if getattr(session, "current_wait_type", None) != "RESOURCE_WAIT":
        return False
    resource_wait = payload_dict(payload_dict(getattr(session, "context_json", None)).get("resource_wait"))
    return optional_int(resource_wait.get("inbox_id")) == inbox_id


def _session_context(session: Any) -> dict[str, Any]:
    raw_context = getattr(session, "context_json", None)
    return dict(raw_context) if isinstance(raw_context, dict) else {}


def _normalized_entry_material_evidence(*, plugin_key: str | None, payload: dict[str, Any]) -> dict[str, str]:
    """提取 capability 拥有的入口物料证据。"""
    try:
        six_in_one = parse_workline_six_in_one(plugin_key, payload)
    except (TypeError, ValueError):
        return {}
    if six_in_one is None:
        return {}
    evidence: dict[str, str] = {}
    for field_name, raw_value in six_in_one.iter_business_fields():
        if not isinstance(field_name, str) or not field_name:
            continue
        if isinstance(raw_value, str):
            value = raw_value.strip()
            if value:
                evidence[field_name] = value
    return evidence


def _duplicate_entry_material_conflict(
    *,
    session: Any,
    workline: Any,
    payload: dict[str, Any],
) -> tuple[str, dict[str, Any]] | None:
    """判断重复入口是否与 session 初始物料证据冲突。"""
    plugin_key = string_value(getattr(session, "plugin_key", None)) or string_value(
        getattr(workline, "plugin_key", None)
    )
    if not plugin_key:
        return None
    session_context = _session_context(session)
    initial_payload = payload_dict(session_context.get("initial_payload") or session_context.get("source_payload"))
    if not initial_payload:
        return None
    expected = _normalized_entry_material_evidence(plugin_key=plugin_key, payload=initial_payload)
    actual = _normalized_entry_material_evidence(plugin_key=plugin_key, payload=payload)
    if not expected or not actual:
        return None
    conflicts = {
        field_name: {"expected": expected[field_name], "actual": actual[field_name]}
        for field_name in sorted(expected.keys() & actual.keys())
        if expected[field_name] != actual[field_name]
    }
    if not conflicts:
        return None
    details = {
        "reason": "ENTRY_MATERIAL_IDENTITY_CONFLICT",
        "conflicts": conflicts,
        "expected": expected,
        "actual": actual,
    }
    message = "ENTRY_MATERIAL_IDENTITY_CONFLICT: duplicate entry event conflicts with session initial material evidence"
    return message, details


async def _load_related_entities(
    db: Any,
    inbox: Any,
    *,
    resolved_event_type: str | None = None,
) -> tuple[
    Any | None,
    Any | None,
    Any | None,
    Any | None,
    dict[str, list[Any]],
    Any,
    bool,
]:
    """加载关联实体并固定 tuple 中 device/command 的位置。"""
    from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_context_loader import (
        load_related_entities,
    )

    loaded = await load_related_entities(db, inbox, resolved_event_type=resolved_event_type)
    return (
        loaded.get("session"),
        loaded.get("workline"),
        loaded.get("device"),
        loaded.get("command"),
        loaded.get("devices_by_role", {}),
        loaded.get("services"),
        loaded.get("safety_checked", True),
    )


async def _handle_estop(
    db: Any,
    *,
    inbox: Any,
    inbox_pk: int,
    payload: dict[str, Any],
    session: Any,
    workline: Any,
    device: Any,
    command: Any,
    processor_token: str,
    inbox_service: RuntimeInboxService,
) -> bool:
    """处理 ESTOP_PRESSED 急停并在安全 effect 后执行 fenced 终态。"""
    from src.utils.value_normalization import resolve_entity_id

    workline_pk = resolve_entity_id(workline)
    if workline_pk is None:
        error_msg = "ESTOP_PRESSED missing workline context"
        await _record_diagnostic(
            db,
            inbox=inbox,
            error_code=ErrorCode.SESSION_CONTEXT_MISSING,
            message=error_msg,
            session=session,
            workline=workline,
            device=device,
            command=command,
        )
        _require_fenced_update(
            await inbox_service.mark_failed(
                db,
                inbox_id=inbox_pk,
                lease_token=processor_token,
                error_code=ErrorCode.SESSION_CONTEXT_MISSING.value,
                error_message=error_msg,
                retryable=False,
            ),
            action="mark_failed",
            inbox_id=inbox_pk,
        )
        return False

    from src.app.workline.services.safety_service import workline_safety_service

    # Fail-safe fencing 特例：handle_estop 内部立即提交安全冻结/排空；
    # RuntimeInbox 终态 fencing 必须后置，绝不能把两者重构为同一可回滚事务。
    _ = await workline_safety_service.handle_estop(
        db,
        workline_id=workline_pk,
        source_inbox_id=inbox_pk,
        source_device_id=resolve_entity_id(device) or getattr(inbox, "device_id", None),
        source_command_id=resolve_entity_id(command) or getattr(inbox, "command_id", None),
        trigger_payload=payload,
    )
    _require_fenced_update(
        await inbox_service.mark_processed(
            db,
            inbox_id=inbox_pk,
            lease_token=processor_token,
        ),
        action="mark_processed",
        inbox_id=inbox_pk,
    )
    return True


async def _handle_timer_timeout(
    db: Any,
    *,
    inbox: Any,
    inbox_pk: int,
    payload: dict[str, Any],
    processor_token: str,
    inbox_service: RuntimeInboxService,
) -> None:
    """将 TIMER_TIMEOUT 路由到 reconciliation service。"""
    from src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl import (
        workline_runtime_reconciliation_service,
    )

    raw_data = payload.get("data")
    payload_data = raw_data if isinstance(raw_data, dict) else payload
    # WorklineSession 与 ExecutionSession 分属不同 ID 空间；
    # 业务路由只认 canonical legacy session_id。
    session_id = optional_int(payload_data.get("session_id"))
    _ = await workline_runtime_reconciliation_service.handle_timer_timeout(
        db,
        session_id=session_id,
        inbox_id=inbox_pk,
        payload=payload,
        source_inbox_id=inbox_pk,
        correlation_id=string_value(getattr(inbox, "correlation_id", None)) or None,
        trace_id=string_value(getattr(inbox, "trace_id", None)) or None,
    )
    _require_fenced_update(
        await inbox_service.mark_processed(
            db,
            inbox_id=inbox_pk,
            lease_token=processor_token,
        ),
        action="mark_processed",
        inbox_id=inbox_pk,
    )


# Public alias used by callers.
RuntimeInboxProcessorService = RuntimeInboxProcessorBridge


__all__ = [
    "ProcessResult",
    "RuntimeInboxProcessorBridge",
    "RuntimeInboxProcessorService",
]
