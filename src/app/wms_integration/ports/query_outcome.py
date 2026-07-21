"""所有 WMS QUERY operation 共用的封闭领域 outcome。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class QuerySuccess[QueryResultT]:
    """Provider 明确返回的有效 authority snapshot，包括显式空结果。"""

    value: QueryResultT
    evidence_key: str | None = None


@dataclass(frozen=True, slots=True)
class QueryBusinessReject:
    """Provider 明确拒绝当前查询。"""

    reason_code: str
    message: str
    evidence_key: str | None = None


@dataclass(frozen=True, slots=True)
class QueryTechnicalFailure:
    """仅此 outcome 可通过 retryable 显式授权消耗重试预算。"""

    reason_code: str
    message: str
    retryable: bool
    retry_after_seconds: float | None = None
    evidence_key: str | None = None


@dataclass(frozen=True, slots=True)
class QueryContractFailure:
    """Provider 或内部合同不满足，必须 fail closed 且默认不重试。"""

    reason_code: str
    message: str
    evidence_key: str | None = None


type WmsQueryOutcome[QueryResultT] = (
    QuerySuccess[QueryResultT] | QueryBusinessReject | QueryTechnicalFailure | QueryContractFailure
)


__all__ = [
    "QueryBusinessReject",
    "QueryContractFailure",
    "QuerySuccess",
    "QueryTechnicalFailure",
    "WmsQueryOutcome",
]
