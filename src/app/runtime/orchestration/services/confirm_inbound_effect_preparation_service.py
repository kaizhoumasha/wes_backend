"""`confirm_inbound` EFFECT 与 T8 双账本之间的事务写入边界。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.app.runtime.orchestration.repositories.runtime_intent_log_repository import (
    RuntimeIntentLogRepository,
    runtime_intent_log_repository,
)

if TYPE_CHECKING:
    from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog
    from src.app.runtime.system_capabilities.wms.inventory.confirm_inbound.effect_adapter import (
        ConfirmInboundEffectAdapter,
    )
    from src.app.sys.models import SystemOutbox
    from src.app.wms_integration.ports.confirm_inbound_operation import ConfirmInboundOperationRequest


class ConfirmInboundEffectPreparationService:
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
        request: ConfirmInboundOperationRequest,
        intent_log: RuntimeIntentLog,
        adapter: ConfirmInboundEffectAdapter,
    ) -> SystemOutbox:
        if intent_log.dispatch_key != request.dispatch_key:
            raise ValueError("confirm_inbound intent/outbox dispatch_key mismatch")
        idempotency_key = getattr(intent_log, "idempotency_key", None)
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise ValueError("confirm_inbound intent requires persisted idempotency_key")
        outbox = adapter.build_outbox(request, idempotency_key=idempotency_key)
        await self._intent_repository.add_proposed_pair(db, intent_log=intent_log, outbox=outbox)
        return outbox


confirm_inbound_effect_preparation_service = ConfirmInboundEffectPreparationService()

__all__ = [
    "ConfirmInboundEffectPreparationService",
    "confirm_inbound_effect_preparation_service",
]
