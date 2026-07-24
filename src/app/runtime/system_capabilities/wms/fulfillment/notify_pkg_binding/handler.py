"""`notify_pkg_binding` OUTBOX_ASYNC System Capability handler。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.app.runtime.system_capabilities.outcomes import Success

from .effect_adapter import notify_package_binding_effect_adapter
from .effect_contract import NotifyPackageBindingDispatchAccepted, NotifyPackageBindingEffectAdmission

if TYPE_CHECKING:
    from src.app.wms_integration.ports.notify_pkg_binding_operation import NotifyPackageBindingOperationRequest


class NotifyPackageBindingEffectHandler:
    """只创建双账本；外部 I/O 由提交后的既有 dispatcher 执行。"""

    async def __call__(self, request: NotifyPackageBindingOperationRequest, *, execution: object) -> object:
        from src.app.runtime.orchestration.services.notify_package_binding_effect_preparation_service import (
            notify_package_binding_effect_preparation_service,
        )

        admission = execution.admission  # type: ignore[attr-defined]
        if not isinstance(admission, NotifyPackageBindingEffectAdmission):
            raise TypeError("notify_pkg_binding requires typed admission")
        if (
            admission.precondition.package_id != request.package_id
            or admission.precondition.pallet_id != request.pallet_id
        ):
            raise ValueError("notify_pkg_binding admission binding identity mismatch")
        intent_log = execution.intent_log  # type: ignore[attr-defined]
        if intent_log is None:
            raise RuntimeError("notify_pkg_binding OUTBOX_ASYNC claim row is missing")
        outbox = await notify_package_binding_effect_preparation_service.prepare(
            execution.db,  # type: ignore[attr-defined]
            request=request,
            intent_log=intent_log,
            adapter=notify_package_binding_effect_adapter,
        )
        return Success(payload=NotifyPackageBindingDispatchAccepted(dispatch_key=outbox.dispatch_key))


__all__ = ["NotifyPackageBindingEffectHandler"]
