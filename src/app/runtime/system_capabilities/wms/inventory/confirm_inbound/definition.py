"""`confirm_inbound` OUTBOX_ASYNC System Capability Definition。"""

from src.app.runtime.system_capabilities.definition import (
    EffectCompletionMode,
    SystemCapabilityDefinition,
    SystemCapabilityMode,
)
from src.app.wms_integration.ports.confirm_inbound_operation import (
    OPERATION_IDENTITY,
    ConfirmInboundOperationRequest,
)

from .contract import CONTRACT
from .effect_contract import ConfirmInboundDispatchAccepted, ConfirmInboundEffectAdmission
from .handler import ConfirmInboundEffectHandler

CAPABILITY_KEY, CONTRACT_VERSION = OPERATION_IDENTITY.rsplit("@", maxsplit=1)

DEFINITION = SystemCapabilityDefinition(
    capability_key=CAPABILITY_KEY,
    contract_version=CONTRACT_VERSION,
    mode=SystemCapabilityMode.EFFECT,
    input_model=ConfirmInboundOperationRequest,
    output_model=ConfirmInboundDispatchAccepted,
    handler_factory=ConfirmInboundEffectHandler,
    required_ports=(),
    admission="runtime",
    timeout_seconds=CONTRACT.budget.timeout_seconds,
    completion_mode=EffectCompletionMode.OUTBOX_ASYNC,
    audit_policy="metadata",
    admission_model=ConfirmInboundEffectAdmission,
)

__all__ = ["CAPABILITY_KEY", "CONTRACT_VERSION", "DEFINITION"]
