"""DeviceCommand OUTBOX_ASYNC capability Definition。"""

from src.app.runtime.system_capabilities.definition import (
    EffectCompletionMode,
    SystemCapabilityDefinition,
    SystemCapabilityMode,
)

from .contracts import DeviceCommandWriteAdmission, DeviceCommandWriteInput, DeviceCommandWriteOutput
from .handler import DeviceCommandWriteHandler

DEFINITION = SystemCapabilityDefinition(
    capability_key="device.device_command_write",
    contract_version="v1",
    mode=SystemCapabilityMode.EFFECT,
    input_model=DeviceCommandWriteInput,
    output_model=DeviceCommandWriteOutput,
    handler_factory=DeviceCommandWriteHandler,
    required_ports=(),
    admission="runtime",
    timeout_seconds=5,
    completion_mode=EffectCompletionMode.OUTBOX_ASYNC,
    audit_policy="metadata",
    admission_model=DeviceCommandWriteAdmission,
)

__all__ = ["DEFINITION"]
