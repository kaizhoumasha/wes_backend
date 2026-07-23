"""Material-flow request 到 `full_box_exchange` SYSTEM_CAPABILITY intent 的唯一映射。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.app.runtime.orchestration.runtime_intent import RuntimeIntent
from src.app.wms_integration.ports.full_box_exchange_operation import OPERATION_IDENTITY

if TYPE_CHECKING:
    from src.app.wms_integration.ports.full_box_exchange_operation import FullBoxExchangeOperationRequest

    from .effect_contract import FullBoxExchangeEffectAdmission

CAPABILITY_KEY, CONTRACT_VERSION = OPERATION_IDENTITY.rsplit("@", maxsplit=1)


class FullBoxExchangeIntentAdapter:
    """冻结稳定业务 identity；外部 binding 由 outbox adapter 在同事务冻结。"""

    def build_intent(
        self,
        request: FullBoxExchangeOperationRequest,
        *,
        admission: FullBoxExchangeEffectAdmission,
        binding_id: int,
        binding_version: int,
    ) -> RuntimeIntent:
        if binding_id <= 0 or binding_version <= 0:
            raise ValueError("full_box_exchange requires positive plugin binding identity")
        expected = (
            admission.precondition.rack_id,
            admission.precondition.empty_box_id,
            admission.precondition.full_box_id,
        )
        actual = (request.rack_id, request.empty_box_id, request.full_box_id)
        if expected != actual:
            raise ValueError("full_box_exchange admission binding identity mismatch")
        operation_key = f"{request.provider_code}:{request.rack_id}:{request.empty_box_id}:{request.full_box_id}"
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


full_box_exchange_intent_adapter = FullBoxExchangeIntentAdapter()

__all__ = ["FullBoxExchangeIntentAdapter", "full_box_exchange_intent_adapter"]
