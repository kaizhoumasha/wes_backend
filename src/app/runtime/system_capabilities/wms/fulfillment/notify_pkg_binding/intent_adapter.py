"""Material-flow request 到 `notify_pkg_binding` SYSTEM_CAPABILITY intent 的唯一映射。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.app.runtime.orchestration.runtime_intent import RuntimeIntent
from src.app.wms_integration.ports.notify_pkg_binding_operation import OPERATION_IDENTITY

if TYPE_CHECKING:
    from src.app.wms_integration.ports.notify_pkg_binding_operation import NotifyPackageBindingOperationRequest

    from .effect_contract import NotifyPackageBindingEffectAdmission

CAPABILITY_KEY, CONTRACT_VERSION = OPERATION_IDENTITY.rsplit("@", maxsplit=1)


class NotifyPackageBindingIntentAdapter:
    """冻结稳定绑定 identity；外部 endpoint binding 由 outbox adapter 在同事务冻结。"""

    def build_intent(
        self,
        request: NotifyPackageBindingOperationRequest,
        *,
        admission: NotifyPackageBindingEffectAdmission,
        binding_id: int,
        binding_version: int,
    ) -> RuntimeIntent:
        if binding_id <= 0 or binding_version <= 0:
            raise ValueError("notify_pkg_binding requires positive plugin binding identity")
        if (
            admission.precondition.package_id != request.package_id
            or admission.precondition.pallet_id != request.pallet_id
        ):
            raise ValueError("notify_pkg_binding admission binding identity mismatch")
        operation_key = f"{request.provider_code}:{request.package_id}:{request.pallet_id}"
        return RuntimeIntent.system_capability(
            capability_key=CAPABILITY_KEY,
            contract_version=CONTRACT_VERSION,
            operation_key=operation_key,
            dispatch_key=request.dispatch_key,
            payload=request,
            precondition=admission.precondition,
            fact_version=admission.fact_version,
            timeout_seconds=30,
            creator_authority="WORKLINE_PLUGIN",
            authorization_policy="PLUGIN_DECLARED_CAPABILITY",
            binding_snapshot={"binding_id": binding_id, "binding_version": binding_version},
            provider_snapshot={"provider_code": "RUNTIME", "profile": "runtime"},
        )


notify_package_binding_intent_adapter = NotifyPackageBindingIntentAdapter()

__all__ = [
    "NotifyPackageBindingIntentAdapter",
    "notify_package_binding_intent_adapter",
]
