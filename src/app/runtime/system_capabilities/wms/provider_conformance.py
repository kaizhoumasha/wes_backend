"""WMS Provider 合同测试使用的纯 conformance 题库与确定性报告评估器。"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

StableText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
CaseId = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[a-z][a-z0-9_]*$")]
ConformanceCode = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Z][A-Z0-9_]*$")]


class ConformanceOutcomeKind(str, Enum):
    """与 QUERY outcome 一一对应的封闭分类。"""

    SUCCESS = "SUCCESS"
    BUSINESS_REJECT = "BUSINESS_REJECT"
    TECHNICAL_FAILURE = "TECHNICAL_FAILURE"
    CONTRACT_FAILURE = "CONTRACT_FAILURE"


class ConformanceTarget(str, Enum):
    """仅保留本地合同测试需要的真实 adapter、simulator 与纯 replay。"""

    CI_ADAPTER = "CI_ADAPTER"
    SIMULATOR = "SIMULATOR"
    REPLAY = "REPLAY"
    REAL_TCP = "REAL_TCP"


class _ConformanceVerdict(BaseModel):
    """题目期望和执行观察共用的最小、脱敏 verdict。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: CaseId = Field(max_length=80)
    outcome_kind: ConformanceOutcomeKind
    reason_code: ConformanceCode | None = Field(default=None, max_length=120)
    retryable: bool | None = None
    retry_after_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    evidence_recorded: bool
    semantic_marker: ConformanceCode = Field(max_length=120)

    @model_validator(mode="after")
    def validate_closed_outcome(self) -> _ConformanceVerdict:
        if self.outcome_kind is ConformanceOutcomeKind.SUCCESS:
            if self.reason_code is not None or self.retryable is not None:
                raise ValueError("success conformance verdict cannot carry failure classification")
        elif self.outcome_kind is ConformanceOutcomeKind.TECHNICAL_FAILURE:
            if self.reason_code is None or self.retryable is None:
                raise ValueError("technical conformance verdict requires reason_code and retryable")
        elif self.reason_code is None or self.retryable is not None:
            raise ValueError("business/contract conformance verdict requires reason_code without retryable")
        if self.retry_after_seconds is not None and not (
            self.outcome_kind is ConformanceOutcomeKind.TECHNICAL_FAILURE and self.retryable
        ):
            raise ValueError("retry_after_seconds requires a retryable technical failure")
        return self


class ConformanceCaseExpectation(_ConformanceVerdict):
    """所有执行面都不可覆写的单道核心题。"""


class ConformanceObservation(_ConformanceVerdict):
    """执行面返回给纯 runner 的固定观察；不允许原始 payload/header。"""


QUERY_INVENTORY_CONFORMANCE_CASES = (
    ConformanceCaseExpectation(
        case_id="success",
        outcome_kind=ConformanceOutcomeKind.SUCCESS,
        evidence_recorded=True,
        semantic_marker="ONE_ITEM",
    ),
    ConformanceCaseExpectation(
        case_id="empty",
        outcome_kind=ConformanceOutcomeKind.SUCCESS,
        evidence_recorded=True,
        semantic_marker="EMPTY",
    ),
    ConformanceCaseExpectation(
        case_id="missing_field",
        outcome_kind=ConformanceOutcomeKind.CONTRACT_FAILURE,
        reason_code="WMS_MALFORMED_RESPONSE",
        evidence_recorded=True,
        semantic_marker="CONTRACT_FAILURE",
    ),
    ConformanceCaseExpectation(
        case_id="invalid_decimal",
        outcome_kind=ConformanceOutcomeKind.CONTRACT_FAILURE,
        reason_code="WMS_MALFORMED_RESPONSE",
        evidence_recorded=True,
        semantic_marker="CONTRACT_FAILURE",
    ),
    ConformanceCaseExpectation(
        case_id="reject",
        outcome_kind=ConformanceOutcomeKind.BUSINESS_REJECT,
        reason_code="INVALID_INVENTORY_FILTER",
        evidence_recorded=True,
        semantic_marker="BUSINESS_REJECT",
    ),
    ConformanceCaseExpectation(
        case_id="timeout",
        outcome_kind=ConformanceOutcomeKind.TECHNICAL_FAILURE,
        reason_code="WMS_PROVIDER_TIMEOUT",
        retryable=True,
        evidence_recorded=True,
        semantic_marker="TECHNICAL_FAILURE",
    ),
    ConformanceCaseExpectation(
        case_id="rate_limit",
        outcome_kind=ConformanceOutcomeKind.TECHNICAL_FAILURE,
        reason_code="WMS_RATE_LIMITED",
        retryable=True,
        retry_after_seconds=3,
        evidence_recorded=True,
        semantic_marker="TECHNICAL_FAILURE",
    ),
    ConformanceCaseExpectation(
        case_id="unavailable",
        outcome_kind=ConformanceOutcomeKind.TECHNICAL_FAILURE,
        reason_code="WMS_UNAVAILABLE",
        retryable=True,
        evidence_recorded=True,
        semantic_marker="TECHNICAL_FAILURE",
    ),
    ConformanceCaseExpectation(
        case_id="malformed",
        outcome_kind=ConformanceOutcomeKind.CONTRACT_FAILURE,
        reason_code="WMS_MALFORMED_RESPONSE",
        evidence_recorded=True,
        semantic_marker="CONTRACT_FAILURE",
    ),
    ConformanceCaseExpectation(
        case_id="pagination",
        outcome_kind=ConformanceOutcomeKind.SUCCESS,
        evidence_recorded=True,
        semantic_marker="TWO_ITEMS",
    ),
    ConformanceCaseExpectation(
        case_id="precision",
        outcome_kind=ConformanceOutcomeKind.SUCCESS,
        evidence_recorded=True,
        semantic_marker="DECIMAL_EXACT",
    ),
    ConformanceCaseExpectation(
        case_id="budget",
        outcome_kind=ConformanceOutcomeKind.CONTRACT_FAILURE,
        reason_code="WMS_WIRE_BUDGET_EXCEEDED",
        evidence_recorded=True,
        semantic_marker="CONTRACT_FAILURE",
    ),
    ConformanceCaseExpectation(
        case_id="evidence_failure",
        outcome_kind=ConformanceOutcomeKind.CONTRACT_FAILURE,
        reason_code="WMS_EVIDENCE_WRITE_FAILED",
        evidence_recorded=False,
        semantic_marker="CONTRACT_FAILURE",
    ),
)


def _digest(payload: object) -> str:
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def _canonical_json_bytes(payload: object) -> bytes:
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return canonical.encode("utf-8")


QUERY_INVENTORY_CONFORMANCE_SUITE_DIGEST = _digest(
    [case.model_dump(mode="json") for case in QUERY_INVENTORY_CONFORMANCE_CASES]
)

from src.app.runtime.system_capabilities.wms.conformance_matrix import (  # noqa: E402 - Q14 题库先完成定义。
    WMS_PROVIDER_CONFORMANCE_CASES,
    WMS_PROVIDER_CONFORMANCE_SUITE_DIGEST,
    WMS_PROVIDER_CONFORMANCE_SUITE_VERSION,
    OperationConformanceCaseResult,
    OperationConformanceExpectation,
    OperationConformanceObservation,
    WmsConformanceReport,
    build_wms_conformance_report,
    build_wms_release_conformance_report,
    conformance_endpoint_digest,
    verify_wms_conformance_report,
    verify_wms_release_conformance_report,
)

__all__ = [
    "QUERY_INVENTORY_CONFORMANCE_CASES",
    "QUERY_INVENTORY_CONFORMANCE_SUITE_DIGEST",
    "WMS_PROVIDER_CONFORMANCE_CASES",
    "WMS_PROVIDER_CONFORMANCE_SUITE_DIGEST",
    "WMS_PROVIDER_CONFORMANCE_SUITE_VERSION",
    "ConformanceCaseExpectation",
    "ConformanceObservation",
    "ConformanceOutcomeKind",
    "ConformanceTarget",
    "OperationConformanceCaseResult",
    "OperationConformanceExpectation",
    "OperationConformanceObservation",
    "WmsConformanceReport",
    "build_wms_conformance_report",
    "build_wms_release_conformance_report",
    "conformance_endpoint_digest",
    "verify_wms_conformance_report",
    "verify_wms_release_conformance_report",
]
