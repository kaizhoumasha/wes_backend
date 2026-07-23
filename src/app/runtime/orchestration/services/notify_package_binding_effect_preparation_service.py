"""`notify_pkg_binding` EFFECT 与 T8 双账本之间的事务写入边界。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.app.runtime.orchestration.repositories.runtime_intent_log_repository import (
    RuntimeIntentLogRepository,
    runtime_intent_log_repository,
)

if TYPE_CHECKING:
    from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog
    from src.app.runtime.system_capabilities.wms.fulfillment.notify_pkg_binding.effect_adapter import (
        NotifyPackageBindingEffectAdapter,
    )
    from src.app.sys.models import SystemOutbox
    from src.app.wms_integration.ports.notify_pkg_binding_operation import NotifyPackageBindingOperationRequest


class NotifyPackageBindingEffectPreparationService:
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
        request: NotifyPackageBindingOperationRequest,
        intent_log: RuntimeIntentLog,
        adapter: NotifyPackageBindingEffectAdapter,
    ) -> SystemOutbox:
        if intent_log.dispatch_key != request.dispatch_key:
            raise ValueError("notify_pkg_binding intent/outbox dispatch_key mismatch")
        outbox = adapter.build_outbox(request)
        await self._intent_repository.add_proposed_pair(db, intent_log=intent_log, outbox=outbox)
        return outbox


notify_package_binding_effect_preparation_service = NotifyPackageBindingEffectPreparationService()

__all__ = [
    "NotifyPackageBindingEffectPreparationService",
    "notify_package_binding_effect_preparation_service",
]
