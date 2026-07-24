"""`full_box_exchange` OUTBOX_ASYNC System Capability Definition。"""

from src.app.runtime.system_capabilities.definition import (
    EffectCompletionMode,
    SystemCapabilityDefinition,
    SystemCapabilityMode,
)
from src.app.wms_integration.ports.full_box_exchange_operation import (
    OPERATION_IDENTITY,
    FullBoxExchangeOperationRequest,
)

from .contract import CONTRACT
from .effect_contract import FullBoxExchangeDispatchAccepted, FullBoxExchangeEffectAdmission
from .handler import FullBoxExchangeEffectHandler

CAPABILITY_KEY, CONTRACT_VERSION = OPERATION_IDENTITY.rsplit("@", maxsplit=1)

DEFINITION = SystemCapabilityDefinition(
    capability_key=CAPABILITY_KEY,
    contract_version=CONTRACT_VERSION,
    mode=SystemCapabilityMode.EFFECT,
    input_model=FullBoxExchangeOperationRequest,
    output_model=FullBoxExchangeDispatchAccepted,
    handler_factory=FullBoxExchangeEffectHandler,
    required_ports=(),
    admission="runtime",
    timeout_seconds=CONTRACT.budget.timeout_seconds,
    completion_mode=EffectCompletionMode.OUTBOX_ASYNC,
    audit_policy="metadata",
    admission_model=FullBoxExchangeEffectAdmission,
)

__all__ = ["CAPABILITY_KEY", "CONTRACT_VERSION", "DEFINITION"]
