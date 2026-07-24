"""`full_box_exchange` OUTBOX_ASYNC System Capability handler。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.app.runtime.system_capabilities.outcomes import Success

from .effect_adapter import full_box_exchange_effect_adapter
from .effect_contract import FullBoxExchangeDispatchAccepted, FullBoxExchangeEffectAdmission

if TYPE_CHECKING:
    from src.app.wms_integration.ports.full_box_exchange_operation import FullBoxExchangeOperationRequest


class FullBoxExchangeEffectHandler:
    """只创建双账本；外部 I/O 由提交后的既有 dispatcher 执行。"""

    async def __call__(self, request: FullBoxExchangeOperationRequest, *, execution: object) -> object:
        from src.app.runtime.orchestration.services.full_box_exchange_effect_preparation_service import (
            full_box_exchange_effect_preparation_service,
        )

        admission = execution.admission  # type: ignore[attr-defined]
        if not isinstance(admission, FullBoxExchangeEffectAdmission):
            raise TypeError("full_box_exchange requires typed admission")
        expected = (
            admission.precondition.rack_id,
            admission.precondition.empty_box_id,
            admission.precondition.full_box_id,
        )
        actual = (request.rack_id, request.empty_box_id, request.full_box_id)
        if expected != actual:
            raise ValueError("full_box_exchange admission binding identity mismatch")
        intent_log = execution.intent_log  # type: ignore[attr-defined]
        if intent_log is None:
            raise RuntimeError("full_box_exchange OUTBOX_ASYNC claim row is missing")
        outbox = await full_box_exchange_effect_preparation_service.prepare(
            execution.db,  # type: ignore[attr-defined]
            request=request,
            intent_log=intent_log,
            adapter=full_box_exchange_effect_adapter,
        )
        return Success(payload=FullBoxExchangeDispatchAccepted(dispatch_key=outbox.dispatch_key))


__all__ = ["FullBoxExchangeEffectHandler"]
