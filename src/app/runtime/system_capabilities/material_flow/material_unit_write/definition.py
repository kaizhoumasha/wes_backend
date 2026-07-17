"""MaterialUnit LOCAL_TRANSACTIONAL capability Definition。"""

from src.app.runtime.system_capabilities.definition import (
    EffectCompletionMode,
    SystemCapabilityDefinition,
    SystemCapabilityMode,
)

from .contracts import MaterialUnitWriteInput, MaterialUnitWriteOutput
from .handler import MaterialUnitWriteHandler

DEFINITION = SystemCapabilityDefinition(
    capability_key="material_flow.material_unit_write",
    contract_version="v1",
    mode=SystemCapabilityMode.EFFECT,
    input_model=MaterialUnitWriteInput,
    output_model=MaterialUnitWriteOutput,
    handler_factory=MaterialUnitWriteHandler,
    required_ports=(),
    admission="runtime",
    timeout_seconds=5,
    completion_mode=EffectCompletionMode.LOCAL_TRANSACTIONAL,
    audit_policy="metadata",
)

__all__ = ["DEFINITION"]
