"""SMT source-pick ledger capability definition。"""

from src.app.runtime.system_capabilities.definition import (
    EffectCompletionMode,
    SystemCapabilityDefinition,
    SystemCapabilityMode,
)

from .contracts import (
    SmtSourcePickLedgerAdmission,
    SmtSourcePickLedgerInput,
    SmtSourcePickLedgerOutput,
)
from .handler import SmtSourcePickLedgerHandler

DEFINITION = SystemCapabilityDefinition(
    capability_key="material_flow.smt_source_pick_ledger",
    contract_version="v1",
    mode=SystemCapabilityMode.EFFECT,
    input_model=SmtSourcePickLedgerInput,
    output_model=SmtSourcePickLedgerOutput,
    handler_factory=SmtSourcePickLedgerHandler,
    required_ports=(),
    admission="runtime",
    timeout_seconds=5,
    completion_mode=EffectCompletionMode.LOCAL_TRANSACTIONAL,
    audit_policy="metadata",
    admission_model=SmtSourcePickLedgerAdmission,
)

__all__ = ["DEFINITION"]
