"""普通 Session Hold LOCAL_TRANSACTIONAL capability Definition。"""

from src.app.runtime.system_capabilities.definition import (
    EffectCompletionMode,
    SystemCapabilityDefinition,
    SystemCapabilityMode,
)

from .contracts import SessionHoldInput, SessionHoldOutput
from .handler import SessionHoldHandler

DEFINITION = SystemCapabilityDefinition(
    capability_key="runtime.session_hold",
    contract_version="v1",
    mode=SystemCapabilityMode.EFFECT,
    input_model=SessionHoldInput,
    output_model=SessionHoldOutput,
    handler_factory=SessionHoldHandler,
    required_ports=(),
    admission="runtime",
    timeout_seconds=5,
    completion_mode=EffectCompletionMode.LOCAL_TRANSACTIONAL,
    audit_policy="metadata",
)

__all__ = ["DEFINITION"]
