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
from .shadow_service import (
    QueryShadowComparisonService,
    QueryShadowReadinessService,
    query_shadow_comparison_service,
    query_shadow_readiness_service,
)

__all__ = [
    "AttemptCloseReport",
    "BusinessReject",
    "ContractViolation",
    "EffectCompletionMode",
    "GatewayLimits",
    "GatewayQueryResult",
    "QueryEvidence",
    "QueryShadowComparisonService",
    "QueryShadowReadinessService",
    "RecordedReplayEnvelope",
    "RecordedReplayResolution",
    "RetryableFailure",
    "Success",
    "SystemCapabilityDefinition",
    "SystemCapabilityGateway",
    "SystemCapabilityMode",
    "TimelineRecordedReplayService",
    "parse_outcome",
    "query_shadow_comparison_service",
    "query_shadow_readiness_service",
    "resolve_recorded_replay",
]
