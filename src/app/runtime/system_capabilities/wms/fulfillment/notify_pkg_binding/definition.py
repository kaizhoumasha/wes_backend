"""`notify_pkg_binding` OUTBOX_ASYNC System Capability Definition。"""

from src.app.runtime.system_capabilities.definition import (
    EffectCompletionMode,
    SystemCapabilityDefinition,
    SystemCapabilityMode,
)
from src.app.wms_integration.ports.notify_pkg_binding_operation import (
    OPERATION_IDENTITY,
    NotifyPackageBindingOperationRequest,
)

from .contract import CONTRACT
from .effect_contract import NotifyPackageBindingDispatchAccepted, NotifyPackageBindingEffectAdmission
from .handler import NotifyPackageBindingEffectHandler

CAPABILITY_KEY, CONTRACT_VERSION = OPERATION_IDENTITY.rsplit("@", maxsplit=1)

DEFINITION = SystemCapabilityDefinition(
    capability_key=CAPABILITY_KEY,
    contract_version=CONTRACT_VERSION,
    mode=SystemCapabilityMode.EFFECT,
    input_model=NotifyPackageBindingOperationRequest,
    output_model=NotifyPackageBindingDispatchAccepted,
    handler_factory=NotifyPackageBindingEffectHandler,
    required_ports=(),
    admission="runtime",
    timeout_seconds=CONTRACT.budget.timeout_seconds,
    completion_mode=EffectCompletionMode.OUTBOX_ASYNC,
    audit_policy="metadata",
    admission_model=NotifyPackageBindingEffectAdmission,
)

__all__ = ["CAPABILITY_KEY", "CONTRACT_VERSION", "DEFINITION"]
