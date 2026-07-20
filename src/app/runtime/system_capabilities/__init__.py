"""系统能力 Definition 与 outcome 公共合同。"""

from .definition import EffectCompletionMode, SystemCapabilityDefinition, SystemCapabilityMode
from .evidence import QueryEvidence
from .gateway import AttemptCloseReport, GatewayLimits, GatewayQueryResult, SystemCapabilityGateway
from .outcomes import BusinessReject, ContractViolation, RetryableFailure, Success, parse_outcome
from .replay import (
    RecordedReplayEnvelope,
    RecordedReplayResolution,
    TimelineRecordedReplayService,
    resolve_recorded_replay,
)

__all__ = [
    "AttemptCloseReport",
    "BusinessReject",
    "ContractViolation",
    "EffectCompletionMode",
    "GatewayLimits",
    "GatewayQueryResult",
    "QueryEvidence",
    "RecordedReplayEnvelope",
    "RecordedReplayResolution",
    "RetryableFailure",
    "Success",
    "SystemCapabilityDefinition",
    "SystemCapabilityGateway",
    "SystemCapabilityMode",
    "TimelineRecordedReplayService",
    "parse_outcome",
    "resolve_recorded_replay",
]
