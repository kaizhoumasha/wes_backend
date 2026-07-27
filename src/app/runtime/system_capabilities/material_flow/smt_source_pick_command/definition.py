"""SMT source-pick command capability definition。"""

from src.app.runtime.system_capabilities.definition import (
    EffectCompletionMode,
    SystemCapabilityDefinition,
    SystemCapabilityMode,
)

from .contracts import SmtSourcePickCommandAdmission, SmtSourcePickCommandInput, SmtSourcePickCommandOutput
from .handler import SmtSourcePickCommandHandler

DEFINITION = SystemCapabilityDefinition(
    capability_key="material_flow.smt_source_pick_command",
    contract_version="v1",
    mode=SystemCapabilityMode.EFFECT,
    input_model=SmtSourcePickCommandInput,
    output_model=SmtSourcePickCommandOutput,
    handler_factory=SmtSourcePickCommandHandler,
    required_ports=(),
    admission="runtime",
    timeout_seconds=5,
    completion_mode=EffectCompletionMode.OUTBOX_ASYNC,
    audit_policy="metadata",
    admission_model=SmtSourcePickCommandAdmission,
)

__all__ = ["DEFINITION"]
