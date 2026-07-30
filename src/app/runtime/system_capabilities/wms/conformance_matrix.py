"""T8 全工厂 WMS Provider conformance 矩阵与确定性报告。"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime  # noqa: TC003 - Pydantic 运行时字段。
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator, model_validator

from src.app.wms_integration.operation_registry import WMS_OPERATIONS
from src.app.wms_integration.provider_manifest import WMS_CONFORMANCE_REQUIREMENTS

if TYPE_CHECKING:
    from src.app.wms_integration.endpoint_compiler import CompiledWmsProviderProfile

StableText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
OutcomeKind = Literal[
    "SUCCESS",
    "IN_PROGRESS",
    "PARTIAL_FAILURE",
    "BUSINESS_REJECT",
    "TECHNICAL_FAILURE",
    "CONTRACT_FAILURE",
]
TargetKind = Literal["CI_ADAPTER", "SIMULATOR", "REPLAY", "REAL_TCP"]
WMS_PROVIDER_CONFORMANCE_SUITE_VERSION = "wms-provider-full-factory.v2"

_SUCCESS_CASES = frozenset(
    {
        "success",
        "empty",
        "pagination",
        "precision",
        "accepted",
        "idempotent_replay",
        "in_progress",
        "status_query",
    }
)
_TECHNICAL_CASE_CODES = {
    "timeout": "WMS_PROVIDER_TIMEOUT",
    "rate_limit": "WMS_RATE_LIMITED",
    "unavailable": "WMS_UNAVAILABLE",
}
_CONTRACT_CASE_CODES = {
    "missing_field": "WMS_MALFORMED_RESPONSE",
    "invalid_decimal": "WMS_MALFORMED_RESPONSE",
    "malformed": "WMS_MALFORMED_RESPONSE",
    "budget": "WMS_WIRE_BUDGET_EXCEEDED",
    "evidence_failure": "WMS_EVIDENCE_WRITE_FAILED",
}


class OperationConformanceExpectation(BaseModel):
    """单 operation 的 mode-family 题库期望。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_identity: StableText
    case_id: StableText
    outcome_kind: OutcomeKind
    reason_code: StableText | None = None
    retryable: bool | None = None
    evidence_recorded: bool
    semantic_marker: StableText


class OperationConformanceObservation(OperationConformanceExpectation):
    """runner 返回的脱敏观察。"""


class OperationConformanceCaseResult(OperationConformanceExpectation):
    """报告中的单题判定。"""

    passed: bool
    failure_evidence_digest: Sha256Digest | None = None


def _case_expectation(
    operation_identity: str,
    reject_codes: tuple[str, ...],
    case_id: str,
) -> OperationConformanceExpectation:
    retryable = None
    if case_id == "in_progress":
        outcome_kind = "IN_PROGRESS"
        reason_code = "IDEMPOTENCY_REQUEST_IN_PROGRESS"
        retryable = True
    elif case_id == "idempotency_conflict":
        outcome_kind = "CONTRACT_FAILURE"
        reason_code = "IDEMPOTENCY_CONFLICT"
        retryable = False
    elif case_id == "partial_failure":
        outcome_kind = "PARTIAL_FAILURE"
        reason_code = None
        retryable = False
    elif case_id in _SUCCESS_CASES:
        outcome_kind: OutcomeKind = "SUCCESS"
        reason_code = None
    elif case_id in _TECHNICAL_CASE_CODES:
        outcome_kind = "TECHNICAL_FAILURE"
        reason_code = _TECHNICAL_CASE_CODES[case_id]
        retryable = True
    elif case_id in _CONTRACT_CASE_CODES:
        outcome_kind = "CONTRACT_FAILURE"
        reason_code = _CONTRACT_CASE_CODES[case_id]
    else:
        outcome_kind = "BUSINESS_REJECT"
        reason_code = reject_codes[0]
    return OperationConformanceExpectation(
        operation_identity=operation_identity,
        case_id=case_id,
        outcome_kind=outcome_kind,
        reason_code=reason_code,
        retryable=retryable,
        evidence_recorded=case_id != "evidence_failure",
        semantic_marker=case_id.upper(),
    )


WMS_PROVIDER_CONFORMANCE_CASES = tuple(
    _case_expectation(requirement.operation.identity, requirement.operation.reject_codes, case_id)
    for requirement in WMS_CONFORMANCE_REQUIREMENTS
    for case_id in requirement.required_cases
)
WMS_PROVIDER_CONFORMANCE_SUITE_DIGEST = hashlib.sha256(
    json.dumps(
        [case.model_dump(mode="json") for case in WMS_PROVIDER_CONFORMANCE_CASES],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
).hexdigest()


def _digest(payload: object) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def conformance_endpoint_digest(compiled_profile: CompiledWmsProviderProfile) -> str:
    """绑定 35 项编译 endpoint，而不是只绑定 server origin。"""

    return _digest(
        tuple((identity, endpoint.endpoint_digest) for identity, endpoint in compiled_profile.operations.items())
    )


class WmsConformanceReport(BaseModel):
    """绑定完整 operation/endpoint/profile 的本地或真实 TCP 报告。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["wms-conformance-report.v2"] = "wms-conformance-report.v2"
    suite_version: Literal["wms-provider-full-factory.v2"] = WMS_PROVIDER_CONFORMANCE_SUITE_VERSION
    suite_digest: Sha256Digest
    profile_identity: StableText
    profile_digest: Sha256Digest
    endpoint_digest: Sha256Digest
    contract_version: StableText
    operation_identities: tuple[StableText, ...]
    target: TargetKind
    provenance: TargetKind
    fixture_digest: Sha256Digest
    wms_build_version: StableText | None = None
    responsible_person: StableText | None = None
    execution_safety_confirmed: bool = False
    generated_at: datetime
    cases: tuple[OperationConformanceCaseResult, ...]
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
        expected_digest = _digest(self.model_dump(mode="json", exclude={"report_digest"}))
        if not hmac.compare_digest(self.report_digest, expected_digest):
            raise ValueError("conformance report digest mismatch")
        if self.target != self.provenance:
            raise ValueError("conformance report target/provenance mismatch")
        if self.target == "REAL_TCP" and (
            self.wms_build_version is None or self.responsible_person is None or not self.execution_safety_confirmed
        ):
            raise ValueError("REAL_TCP conformance report requires complete release metadata")
        if self.passed is not all(case.passed for case in self.cases):
            raise ValueError("conformance report passed flag mismatch")
        return self


def build_wms_conformance_report(
    *,
    compiled_profile: CompiledWmsProviderProfile,
    cases: tuple[OperationConformanceExpectation, ...],
    observations: tuple[OperationConformanceObservation, ...],
    target: object,
    fixture_digest: str,
    generated_at: datetime,
    wms_build_version: str | None = None,
    responsible_person: str | None = None,
    execution_safety_confirmed: bool = False,
) -> WmsConformanceReport:
    """构建本地或 REAL_TCP 证据；发布资格由独立 release verifier 判定。"""

    if cases != WMS_PROVIDER_CONFORMANCE_CASES:
        raise ValueError("WMS conformance mode-family question bank cannot be overridden")
    expected_keys = tuple((case.operation_identity, case.case_id) for case in cases)
    observed_by_key = {
        (observation.operation_identity, observation.case_id): observation for observation in observations
    }
    if len(observed_by_key) != len(observations) or tuple(observed_by_key) != expected_keys:
        raise ValueError("every operation conformance case must be observed exactly once")

    target_value = getattr(target, "value", target)
    results = tuple(_evaluate_case(case, observed_by_key[key]) for case, key in zip(cases, expected_keys, strict=True))
    payload = {
        "schema_version": "wms-conformance-report.v2",
        "suite_version": WMS_PROVIDER_CONFORMANCE_SUITE_VERSION,
        "suite_digest": WMS_PROVIDER_CONFORMANCE_SUITE_DIGEST,
        "profile_identity": compiled_profile.profile.profile.identity,
        "profile_digest": compiled_profile.profile_digest,
        "endpoint_digest": conformance_endpoint_digest(compiled_profile),
        "contract_version": compiled_profile.profile.profile.contract_version,
        "operation_identities": [operation.identity for operation in WMS_OPERATIONS],
        "target": target_value,
        "provenance": target_value,
        "fixture_digest": fixture_digest,
        "wms_build_version": wms_build_version,
        "responsible_person": responsible_person,
        "execution_safety_confirmed": execution_safety_confirmed,
        "generated_at": generated_at.isoformat().replace("+00:00", "Z"),
        "cases": [result.model_dump(mode="json") for result in results],
        "passed": all(result.passed for result in results),
    }
    return WmsConformanceReport.model_validate({**payload, "report_digest": _digest(payload)})


def build_wms_release_conformance_report(**kwargs: object) -> WmsConformanceReport:
    """发布报告必须来自真实 TCP，并带齐 WMS 方责任元数据。"""

    target_value = getattr(kwargs.get("target"), "value", kwargs.get("target"))
    if target_value != "REAL_TCP":
        raise ValueError("release conformance report requires REAL_TCP provenance")
    if not kwargs.get("execution_safety_confirmed"):
        raise ValueError("WMS execution safety confirmation is required")
    report = build_wms_conformance_report(**kwargs)
    if not report.passed:
        raise ValueError("WMS release conformance requires all cases to pass")
    return report


def verify_wms_conformance_report(
    payload: dict[str, object],
    *,
    compiled_profile: CompiledWmsProviderProfile,
) -> WmsConformanceReport:
    """校验确定性摘要、完整 operation 顺序与部署 endpoint/profile。"""

    report = WmsConformanceReport.model_validate(payload)
    if not hmac.compare_digest(report.suite_digest, WMS_PROVIDER_CONFORMANCE_SUITE_DIGEST):
        raise ValueError("conformance report suite digest mismatch")
    expected_identities = tuple(operation.identity for operation in WMS_OPERATIONS)
    if report.operation_identities != expected_identities:
        raise ValueError("conformance report operation identity coverage mismatch")
    if report.profile_identity != compiled_profile.profile.profile.identity or not hmac.compare_digest(
        report.profile_digest,
        compiled_profile.profile_digest,
    ):
        raise ValueError("conformance report profile identity or digest mismatch")
    if not hmac.compare_digest(report.endpoint_digest, conformance_endpoint_digest(compiled_profile)):
        raise ValueError("conformance report endpoint digest mismatch")
    if report.contract_version != compiled_profile.profile.profile.contract_version:
        raise ValueError("conformance report contract version mismatch")
    expected_keys = tuple((case.operation_identity, case.case_id) for case in WMS_PROVIDER_CONFORMANCE_CASES)
    if tuple((case.operation_identity, case.case_id) for case in report.cases) != expected_keys:
        raise ValueError("conformance report case identity order or count mismatch")
    for expected, result in zip(WMS_PROVIDER_CONFORMANCE_CASES, report.cases, strict=True):
        observation = OperationConformanceObservation.model_validate(
            result.model_dump(mode="json", exclude={"passed", "failure_evidence_digest"})
        )
        if result != _evaluate_case(expected, observation):
            raise ValueError(f"conformance report case result mismatch: {result.operation_identity}/{result.case_id}")
    return report


def verify_wms_release_conformance_report(
    payload: dict[str, object],
    *,
    compiled_profile: CompiledWmsProviderProfile,
) -> WmsConformanceReport:
    """发布门禁只接受完整 REAL_TCP 报告；本地报告仍可独立保存和重放。"""

    report = verify_wms_conformance_report(payload, compiled_profile=compiled_profile)
    if report.target != "REAL_TCP":
        raise ValueError("release conformance report requires REAL_TCP provenance")
    if not report.passed:
        raise ValueError("WMS release conformance requires all cases to pass")
    return report


def _evaluate_case(
    expected: OperationConformanceExpectation,
    observed: OperationConformanceObservation,
) -> OperationConformanceCaseResult:
    expected_payload = expected.model_dump(mode="json")
    observed_payload = observed.model_dump(mode="json")
    passed = expected_payload == observed_payload
    return OperationConformanceCaseResult(
        **observed_payload,
        passed=passed,
        failure_evidence_digest=None
        if passed
        else _digest({"expected": expected_payload, "observed": observed_payload}),
    )


__all__ = [
    "WMS_PROVIDER_CONFORMANCE_CASES",
    "WMS_PROVIDER_CONFORMANCE_SUITE_DIGEST",
    "WMS_PROVIDER_CONFORMANCE_SUITE_VERSION",
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
