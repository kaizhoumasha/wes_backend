"""WMS EFFECT 与 T8 双账本之间的共享事务写入边界。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from src.app.runtime.orchestration.repositories.runtime_intent_log_repository import (
    RuntimeIntentLogRepository,
    runtime_intent_log_repository,
)
from src.app.runtime.orchestration.services.wms_effect_status_service import freeze_wms_effect_status_binding
from src.app.sys.models import SystemOutbox
from src.app.wms_integration.operation_contract import WmsOperationDefinition, WmsOperationMode

if TYPE_CHECKING:
    from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog
    from src.app.sys.models import DispatchEnvelope


class WmsEffectEnvelopeAdapter(Protocol):
    """operation typed request 到通用 DispatchEnvelope 的最小边界。"""

    def build_envelope(self, request: Any, *, idempotency_key: str) -> DispatchEnvelope: ...


class WmsEffectPreparationService:
    """在调用方事务内复用唯一 RuntimeIntentLog/SystemOutbox 1:1 写入口。"""

    def __init__(
        self,
        *,
        intent_repository: RuntimeIntentLogRepository = runtime_intent_log_repository,
    ) -> None:
        self._intent_repository = intent_repository

    async def prepare(
        self,
        db: Any,
        *,
        operation: WmsOperationDefinition,
        request: Any,
        intent_log: RuntimeIntentLog,
        adapter: WmsEffectEnvelopeAdapter,
    ) -> SystemOutbox:
        operation_name = operation.identity.rsplit(".", maxsplit=1)[-1].split("@", maxsplit=1)[0]
        if operation.mode is not WmsOperationMode.EFFECT:
            raise ValueError(f"{operation_name} preparation requires EFFECT operation")
        if not isinstance(request, operation.request_model):
            raise TypeError(f"{operation_name} preparation requires its typed request")
        if intent_log.dispatch_key != request.dispatch_key:
            raise ValueError(f"{operation_name} intent/outbox dispatch_key mismatch")
        idempotency_key = getattr(intent_log, "idempotency_key", None)
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise ValueError(f"{operation_name} intent requires persisted idempotency_key")

        envelope = adapter.build_envelope(request, idempotency_key=idempotency_key)
        if envelope.dispatch_key != request.dispatch_key:
            raise ValueError(f"{operation_name} envelope dispatch_key mismatch")
        if envelope.idempotency_key != idempotency_key:
            raise ValueError(f"{operation_name} envelope idempotency_key mismatch")
        if envelope.operation_identity != operation.identity:
            raise ValueError(f"{operation_name} envelope operation identity mismatch")
        if envelope.target_code != operation.target_code:
            raise ValueError(f"{operation_name} envelope target code mismatch")
        frozen_binding = envelope.frozen_binding
        if frozen_binding is None:
            raise ValueError(f"{operation_name} requires frozen EXTERNAL_HTTP binding")

        outbox = SystemOutbox(
            session_id=envelope.session_id,
            workline_id=envelope.workline_id,
            device_id=envelope.device_id,
            operation_domain=envelope.operation_domain,
            operation_key=envelope.operation_key,
            dispatch_type=envelope.dispatch_type,
            dispatch_key=envelope.dispatch_key,
            idempotency_key=envelope.idempotency_key,
            target_type=envelope.target_type,
            target_code=frozen_binding.target_snapshot.code,
            provider_profile_identity=frozen_binding.provider_profile_identity,
            operation_identity=frozen_binding.operation_identity,
            provider_profile_hash=frozen_binding.provider_profile_hash,
            binding_revision=frozen_binding.binding_revision,
            target_snapshot_json=frozen_binding.target_snapshot.as_json(),
            target_snapshot_hash=frozen_binding.target_snapshot_hash,
            auth_scheme=frozen_binding.auth_scheme,
            credential_reference=frozen_binding.credential_reference,
            payload_json=envelope.payload_json,
            canonical_payload_bytes=envelope.canonical_payload_bytes,
            payload_hash=envelope.payload_hash,
            trace_id=envelope.trace_id,
        )
        if operation.supports_status_query:
            freeze_wms_effect_status_binding(intent_log=intent_log, outbox=outbox)
        await self._intent_repository.add_proposed_pair(db, intent_log=intent_log, outbox=outbox)
        return outbox


wms_effect_preparation_service = WmsEffectPreparationService()

__all__ = [
    "WmsEffectEnvelopeAdapter",
    "WmsEffectPreparationService",
    "wms_effect_preparation_service",
]
