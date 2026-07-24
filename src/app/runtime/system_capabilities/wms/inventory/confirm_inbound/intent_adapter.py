"""Material-flow request 到 `confirm_inbound` SYSTEM_CAPABILITY intent 的唯一映射。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.app.runtime.orchestration.runtime_intent import RuntimeIntent
from src.app.wms_integration.ports.confirm_inbound_operation import OPERATION_IDENTITY

if TYPE_CHECKING:
    from src.app.wms_integration.ports.confirm_inbound_operation import ConfirmInboundOperationRequest

    from .effect_contract import ConfirmInboundEffectAdmission

CAPABILITY_KEY, CONTRACT_VERSION = OPERATION_IDENTITY.rsplit("@", maxsplit=1)


class ConfirmInboundIntentAdapter:
    """只冻结 Runtime claim identity；外部 binding 由 outbox adapter 在同事务冻结。"""

    def build_intent(
        self,
        request: ConfirmInboundOperationRequest,
        *,
        admission: ConfirmInboundEffectAdmission,
        binding_id: int,
        binding_version: int,
    ) -> RuntimeIntent:
        if binding_id <= 0 or binding_version <= 0:
            raise ValueError("confirm_inbound requires positive plugin binding identity")
        if admission.precondition.inbound_key != request.inbound_key:
            raise ValueError("confirm_inbound admission inbound_key mismatch")
        return RuntimeIntent.system_capability(
            capability_key=CAPABILITY_KEY,
            contract_version=CONTRACT_VERSION,
            operation_key=request.inbound_key,
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


confirm_inbound_intent_adapter = ConfirmInboundIntentAdapter()

__all__ = [
    "ConfirmInboundIntentAdapter",
    "confirm_inbound_intent_adapter",
]
