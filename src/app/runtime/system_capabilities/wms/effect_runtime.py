"""WMS registry EFFECT 的共享 Definition、Handler 与 preparation runtime。"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.app.runtime.system_capabilities.definition import (
    EffectCompletionMode,
    SystemCapabilityDefinition,
    SystemCapabilityMode,
)
from src.app.runtime.system_capabilities.wms.provider_catalog import freeze_wms_effect_binding
from src.app.sys.canonical_dispatch import CanonicalPayload
from src.app.sys.models import SystemOutbox, SystemOutboxDispatchType, SystemOutboxTargetType
from src.app.wms_integration.operation_contract import (
    WmsExecutionLane,
    WmsOperationDefinition,
    WmsOperationMode,
)
from src.app.wms_integration.operation_registry import EFFECT_OPERATIONS
from src.app.wms_integration.ports.effect_preparation import WmsEffectPreparationPort
from src.app.wms_integration.provider_profile import WMS_PROVIDER_CONTRACT_VERSION
from src.core.task_queue_gateway import OutboxDispatchTarget

if TYPE_CHECKING:
    from collections.abc import Callable

    from src.app.runtime.orchestration.services.wms_fulfillment_domain_projector import (
        WmsFulfillmentDomainProjector,
    )
    from src.app.runtime.system_capabilities.wms.provider_catalog import WmsProviderCatalog

_EFFECT_OPERATION_BY_REQUEST_MODEL = MappingProxyType(
    {operation.request_model: operation for operation in EFFECT_OPERATIONS}
)


class WmsEffectDispatchAccepted(BaseModel):
    """只表示 Intent/Outbox 已在调用方事务内完成 preparation。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    accepted: Literal[True] = True
    dispatch_key: str = Field(min_length=1, max_length=240)


@dataclass(frozen=True, slots=True)
class WmsEffectPreparationResult:
    """区分公开 accepted payload 与只在当前事务传播的唤醒 target。"""

    accepted_payload: WmsEffectDispatchAccepted
    dispatch_targets: frozenset[OutboxDispatchTarget]


class WmsRegistryEffectCapabilityHandler:
    """把 11 项 typed request 委托给同一个事务内 preparation Port。"""

    def __init__(self, preparation_port: WmsEffectPreparationPort) -> None:
        self._preparation_port = preparation_port

    async def __call__(self, request: BaseModel, *, execution: Any) -> object:
        try:
            operation = _EFFECT_OPERATION_BY_REQUEST_MODEL[type(request)]
        except KeyError as exc:
            raise ValueError("WMS EFFECT request model is absent from the static registry") from exc
        return await self._preparation_port.prepare(operation, request, execution=execution)


def build_wms_effect_capability_definition(
    operation: WmsOperationDefinition,
) -> SystemCapabilityDefinition:
    """把单项静态 EFFECT Definition 投影为独立 System Capability identity。"""

    if operation.mode is not WmsOperationMode.EFFECT:
        raise ValueError("WMS effect capability builder requires an EFFECT operation")
    capability_key, contract_version = operation.identity.rsplit("@", maxsplit=1)
    return SystemCapabilityDefinition(
        capability_key=capability_key,
        contract_version=contract_version,
        mode=SystemCapabilityMode.EFFECT,
        input_model=operation.request_model,
        output_model=WmsEffectDispatchAccepted,
        handler_factory=WmsRegistryEffectCapabilityHandler,
        required_ports=(WmsEffectPreparationPort,),
        admission=f"wms.{WMS_PROVIDER_CONTRACT_VERSION}",
        timeout_seconds=operation.budget.deadline_seconds,
        # SYNC_RESULT 描述 WMS 单次 submit 的完成语义；WES 调用方仍统一先写 Outbox，
        # 因而 11 项在 System Capability 层均使用现有 OUTBOX_ASYNC 事务边界。
        completion_mode=EffectCompletionMode.OUTBOX_ASYNC,
        audit_policy="metadata",
    )


class WmsEffectPreparationRuntime:
    """在现有事务中构造唯一 SystemOutbox；不执行 HTTP，也不拥有 client。"""

    def __init__(
        self,
        *,
        catalog: WmsProviderCatalog,
        allow_new_claim: Callable[[SystemCapabilityDefinition], bool],
        domain_projector: WmsFulfillmentDomainProjector | None = None,
    ) -> None:
        self._catalog = catalog
        self._domain_projector = domain_projector
        self._allow_new_claim = allow_new_claim

    @property
    def allow_new_claim(self) -> Callable[[SystemCapabilityDefinition], bool]:
        """返回 composition root 冻结的 EFFECT 新 claim 准入策略。"""

        return self._allow_new_claim

    async def prepare(
        self,
        operation: WmsOperationDefinition,
        request: BaseModel,
        *,
        execution: Any,
    ) -> WmsEffectPreparationResult:
        if operation.mode is not WmsOperationMode.EFFECT:
            raise ValueError("WMS preparation requires an EFFECT operation")
        if type(request) is not operation.request_model:
            raise TypeError("WMS preparation request does not match the operation definition")
        intent_log = getattr(execution, "intent_log", None)
        if intent_log is None:
            raise RuntimeError("WMS EFFECT preparation requires a claimed RuntimeIntentLog")
        dispatch_key = getattr(request, "dispatch_key", None)
        if getattr(intent_log, "dispatch_key", None) != dispatch_key:
            raise ValueError("WMS EFFECT intent/outbox dispatch_key mismatch")
        idempotency_key = getattr(execution, "idempotency_key", None)
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise ValueError("WMS EFFECT preparation requires the persisted idempotency key")

        frozen_binding = freeze_wms_effect_binding(
            catalog=self._catalog,
            profile_identity=self._catalog.profile_identity,
            operation_identity=operation.identity,
            target_code=operation.target_code,
        )
        payload_json = request.model_dump(mode="json")
        canonical = CanonicalPayload.from_projection(payload_json)
        ctx = getattr(execution, "ctx", None)
        if not isinstance(ctx, dict):
            raise TypeError("WMS EFFECT preparation requires the runtime execution context")
        if operation.domain_projection_kind is not None:
            if self._domain_projector is None:
                raise RuntimeError("WMS EFFECT domain projector binding is missing")
            await self._domain_projector.prepare_effect(
                execution.db,
                operation=operation,
                request=request,
                execution=execution,
            )
        intent = getattr(execution, "intent", None)
        outbox = SystemOutbox(
            session_id=getattr(ctx.get("session"), "id", None),
            workline_id=getattr(ctx.get("workline"), "id", None),
            operation_domain="WMS",
            operation_key=getattr(intent, "operation_key", None),
            dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
            dispatch_key=dispatch_key,
            idempotency_key=idempotency_key,
            target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
            target_code=frozen_binding.target_snapshot.code,
            provider_profile_identity=frozen_binding.provider_profile_identity,
            operation_identity=frozen_binding.operation_identity,
            provider_profile_hash=frozen_binding.provider_profile_hash,
            binding_revision=frozen_binding.binding_revision,
            target_snapshot_json=frozen_binding.target_snapshot.as_json(),
            target_snapshot_hash=frozen_binding.target_snapshot_hash,
            auth_scheme=frozen_binding.auth_scheme,
            network_trust_mode=frozen_binding.network_trust_mode,
            credential_reference=frozen_binding.credential_reference,
            payload_json=payload_json,
            canonical_payload_bytes=canonical.body,
            payload_hash=canonical.sha256,
            trace_id=ctx.get("trace_id"),
        )
        db = execution.db
        db.add(outbox)
        await db.flush()
        dispatch_target = (
            OutboxDispatchTarget.WMS_DATA
            if operation.execution_lane is WmsExecutionLane.WMS_DATA
            else OutboxDispatchTarget.WMS_FULFILLMENT
        )
        return WmsEffectPreparationResult(
            accepted_payload=WmsEffectDispatchAccepted(dispatch_key=dispatch_key),
            dispatch_targets=frozenset({dispatch_target}),
        )


__all__ = [
    "WmsEffectDispatchAccepted",
    "WmsEffectPreparationResult",
    "WmsEffectPreparationRuntime",
    "WmsRegistryEffectCapabilityHandler",
    "build_wms_effect_capability_definition",
]
