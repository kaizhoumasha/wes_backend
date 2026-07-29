"""WMS Provider 合同测试使用的纯 conformance 题库与确定性报告评估器。"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime  # noqa: TC003 - Pydantic 运行时解析报告字段类型。
from enum import Enum
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

if TYPE_CHECKING:
    from src.app.wms_integration.endpoint_compiler import CompiledWmsProviderProfile

StableText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
CaseId = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[a-z][a-z0-9_]*$")]
ConformanceCode = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Z][A-Z0-9_]*$")]
Sha256Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
WMS_PROVIDER_CONFORMANCE_SUITE_VERSION = "wms-provider-q14-query-inventory.v1"


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
        reason_code="INSUFFICIENT_STOCK",
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


class ConformanceCaseResult(BaseModel):
    """报告中的单题判定，只保存固定分类与不可逆失败摘要。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: CaseId = Field(max_length=80)
    passed: bool
    outcome_kind: ConformanceOutcomeKind
    reason_code: ConformanceCode | None = Field(default=None, max_length=120)
    retryable: bool | None = None
    retry_after_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    evidence_recorded: bool
    semantic_marker: ConformanceCode = Field(max_length=120)
    failure_evidence_digest: Sha256Digest | None = None


class WmsConformanceReport(BaseModel):
    """不依赖签名 trust root、可由固定题库确定性重算的不可变报告。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["wms-conformance-report.v1"] = "wms-conformance-report.v1"
    suite_version: Literal["wms-provider-q14-query-inventory.v1"] = WMS_PROVIDER_CONFORMANCE_SUITE_VERSION
    suite_digest: Sha256Digest
    profile_identity: StableText = Field(max_length=300)
    profile_digest: Sha256Digest
    target: ConformanceTarget
    fixture_digest: Sha256Digest
    generated_at: datetime
    cases: tuple[ConformanceCaseResult, ...]
    passed: bool
    report_digest: Sha256Digest

    @field_validator("generated_at")
    @classmethod
    def require_aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("generated_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def verify_integrity(self) -> WmsConformanceReport:
        expected = _digest(self.model_dump(mode="json", exclude={"report_digest"}))
        if not hmac.compare_digest(self.report_digest, expected):
            raise ValueError("conformance report digest mismatch")
        if self.passed is not all(case.passed for case in self.cases):
            raise ValueError("conformance report passed flag mismatch")
        return self


def build_wms_conformance_report(
    *,
    compiled_profile: CompiledWmsProviderProfile,
    cases: tuple[ConformanceCaseExpectation, ...],
    observations: tuple[ConformanceObservation, ...],
    target: ConformanceTarget,
    fixture_digest: str,
    generated_at: datetime,
) -> WmsConformanceReport:
    """比较固定题库与脱敏观察，并绑定当前部署唯一 active profile。"""

    if cases != QUERY_INVENTORY_CONFORMANCE_CASES:
        raise ValueError("WMS QUERY conformance core question bank cannot be overridden")
    expected_ids = tuple(case.case_id for case in cases)
    observed_by_id = {observation.case_id: observation for observation in observations}
    if len(observed_by_id) != len(observations) or set(observed_by_id) != set(expected_ids):
        raise ValueError("every conformance case must be observed exactly once")

    results = tuple(_evaluate_case(case, observed_by_id[case.case_id]) for case in cases)
    payload = {
        "schema_version": "wms-conformance-report.v1",
        "suite_version": WMS_PROVIDER_CONFORMANCE_SUITE_VERSION,
        "suite_digest": QUERY_INVENTORY_CONFORMANCE_SUITE_DIGEST,
        "profile_identity": compiled_profile.profile.profile.identity,
        "profile_digest": compiled_profile.profile_digest,
        "target": target.value,
        "fixture_digest": fixture_digest,
        "generated_at": generated_at.isoformat().replace("+00:00", "Z"),
        "cases": [result.model_dump(mode="json") for result in results],
        "passed": all(result.passed for result in results),
    }
    return WmsConformanceReport.model_validate({**payload, "report_digest": _digest(payload)})


def verify_wms_conformance_report(
    payload: dict[str, object],
    *,
    compiled_profile: CompiledWmsProviderProfile,
) -> WmsConformanceReport:
    """从持久化 JSON 重建，并按当前 active profile 与固定题库重新验算。"""

    report = WmsConformanceReport.model_validate(payload)
    if not hmac.compare_digest(report.suite_digest, QUERY_INVENTORY_CONFORMANCE_SUITE_DIGEST):
        raise ValueError("conformance report suite digest mismatch")

    expected_ids = tuple(case.case_id for case in QUERY_INVENTORY_CONFORMANCE_CASES)
    if tuple(case.case_id for case in report.cases) != expected_ids:
        raise ValueError("conformance report case identity order or count mismatch")
    if report.profile_identity != compiled_profile.profile.profile.identity or not hmac.compare_digest(
        report.profile_digest,
        compiled_profile.profile_digest,
    ):
        raise ValueError("conformance report profile identity or digest mismatch")

    for expected, result in zip(QUERY_INVENTORY_CONFORMANCE_CASES, report.cases, strict=True):
        observed = ConformanceObservation(
            case_id=result.case_id,
            outcome_kind=result.outcome_kind,
            reason_code=result.reason_code,
            retryable=result.retryable,
            retry_after_seconds=result.retry_after_seconds,
            evidence_recorded=result.evidence_recorded,
            semantic_marker=result.semantic_marker,
        )
        if result != _evaluate_case(expected, observed):
            raise ValueError(f"conformance report case result mismatch: {result.case_id}")
    return report


def _evaluate_case(
    expected: ConformanceCaseExpectation,
    observed: ConformanceObservation,
) -> ConformanceCaseResult:
    expected_payload = expected.model_dump(mode="json", exclude={"case_id"})
    observed_payload = observed.model_dump(mode="json", exclude={"case_id"})
    passed = expected_payload == observed_payload
    failure_digest = None if passed else _digest({"expected": expected_payload, "observed": observed_payload})
    return ConformanceCaseResult(
        case_id=expected.case_id,
        passed=passed,
        outcome_kind=observed.outcome_kind,
        reason_code=observed.reason_code,
        retryable=observed.retryable,
        retry_after_seconds=observed.retry_after_seconds,
        evidence_recorded=observed.evidence_recorded,
        semantic_marker=observed.semantic_marker,
        failure_evidence_digest=failure_digest,
    )


def _digest(payload: object) -> str:
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def _canonical_json_bytes(payload: object) -> bytes:
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return canonical.encode("utf-8")


QUERY_INVENTORY_CONFORMANCE_SUITE_DIGEST = _digest(
    [case.model_dump(mode="json") for case in QUERY_INVENTORY_CONFORMANCE_CASES]
)


__all__ = [
    "QUERY_INVENTORY_CONFORMANCE_CASES",
    "QUERY_INVENTORY_CONFORMANCE_SUITE_DIGEST",
    "WMS_PROVIDER_CONFORMANCE_SUITE_VERSION",
    "ConformanceCaseExpectation",
    "ConformanceCaseResult",
    "ConformanceObservation",
    "ConformanceOutcomeKind",
    "ConformanceTarget",
    "WmsConformanceReport",
    "build_wms_conformance_report",
    "verify_wms_conformance_report",
]
