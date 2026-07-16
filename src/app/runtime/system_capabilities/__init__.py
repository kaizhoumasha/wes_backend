"""系统能力 Definition 与 outcome 公共合同。"""

from .definition import EffectCompletionMode, SystemCapabilityDefinition, SystemCapabilityMode
from .outcomes import BusinessReject, ContractViolation, RetryableFailure, Success, parse_outcome

__all__ = [
    "BusinessReject",
    "ContractViolation",
    "EffectCompletionMode",
    "RetryableFailure",
    "Success",
    "SystemCapabilityDefinition",
    "SystemCapabilityMode",
    "parse_outcome",
]
