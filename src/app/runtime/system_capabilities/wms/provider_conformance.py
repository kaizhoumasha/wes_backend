"""WMS Provider 共用的纯 conformance 题库与报告评估器。"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import datetime  # noqa: TC003 - Pydantic 运行时解析报告字段类型。
from enum import Enum
from typing import TYPE_CHECKING, Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from src.app.runtime.system_capabilities.wms.inventory.query_inventory.contract import (
    CONTRACT as QUERY_INVENTORY_CONTRACT,
)
from src.app.runtime.system_capabilities.wms.operation_index_builder import WmsOperationIndexBuilder
from src.app.runtime.system_capabilities.wms.provider_catalog import WMS_PROVIDER_PROFILES

if TYPE_CHECKING:
    from src.app.runtime.system_capabilities.wms.contracts import WmsProviderProfile

StableText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
CaseId = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[a-z][a-z0-9_]*$")]
ConformanceCode = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Z][A-Z0-9_]*$")]
EndpointRevision = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-f]{64}$"),
]
Sha256Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
WMS_PROVIDER_CONFORMANCE_SUITE_VERSION = "wms-provider-conformance.v1"


class ConformanceOutcomeKind(str, Enum):
    """与 T3 QUERY outcome 一一对应的封闭分类。"""

    SUCCESS = "SUCCESS"
    BUSINESS_REJECT = "BUSINESS_REJECT"
    TECHNICAL_FAILURE = "TECHNICAL_FAILURE"
    CONTRACT_FAILURE = "CONTRACT_FAILURE"


class ConformanceTarget(str, Enum):
    """明确区分 CI、test-only simulator、纯 replay 与 staging live。"""

    CI_ADAPTER = "CI_ADAPTER"
    SIMULATOR = "SIMULATOR"
    REPLAY = "REPLAY"
    STAGING_LIVE = "STAGING_LIVE"


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
    """可重算摘要的不可变 conformance 报告。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["wms-conformance-report.v1"] = "wms-conformance-report.v1"
    suite_version: Literal["wms-provider-conformance.v1"] = WMS_PROVIDER_CONFORMANCE_SUITE_VERSION
    suite_digest: Sha256Digest
    profile_identity: StableText = Field(max_length=300)
    profile_digest: Sha256Digest
    target: ConformanceTarget
    fixture_digest: Sha256Digest
    endpoint_revision: EndpointRevision | None = None
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


class StagingConformanceExecutorAttestation(BaseModel):
    """由部署 composition 生成的冻结执行身份，只携带不可逆 revision/endpoint 摘要。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["wms-staging-conformance-attestation.v1"] = "wms-staging-conformance-attestation.v1"
    profile_identity: StableText = Field(max_length=300)
    profile_revision: Sha256Digest
    binding_identity: Literal["wms.inventory.query_inventory@v1"] = QUERY_INVENTORY_CONTRACT.identity
    binding_revision: Sha256Digest
    endpoint_identity_digest: Sha256Digest
    endpoint_revision: EndpointRevision
    attestation_digest: Sha256Digest

    @model_validator(mode="after")
    def verify_integrity(self) -> StagingConformanceExecutorAttestation:
        expected = _digest(self.model_dump(mode="json", exclude={"attestation_digest"}))
        if not hmac.compare_digest(self.attestation_digest, expected):
            raise ValueError("staging conformance executor attestation digest mismatch")
        return self


class StagingQueryConformanceExecutor(Protocol):
    """受控 staging composition 暴露的具名 executor 合同。"""

    @property
    def attestation(self) -> StagingConformanceExecutorAttestation: ...

    async def execute(self, case: ConformanceCaseExpectation) -> ConformanceObservation: ...


def build_wms_conformance_report(
    *,
    cases: tuple[ConformanceCaseExpectation, ...],
    observations: tuple[ConformanceObservation, ...],
    target: ConformanceTarget,
    profile: WmsProviderProfile,
    fixture_digest: str,
    endpoint_revision: str | None,
    generated_at: datetime,
) -> WmsConformanceReport:
    """纯比较固定题库与脱敏观察；自身没有 endpoint/credential/I/O 能力。"""

    if cases != QUERY_INVENTORY_CONFORMANCE_CASES:
        raise ValueError("WMS QUERY conformance core question bank cannot be overridden")
    _validate_execution_environment(target=target, profile=profile, endpoint_revision=endpoint_revision)
    expected_ids = tuple(case.case_id for case in cases)
    observed_by_id = {observation.case_id: observation for observation in observations}
    if len(observed_by_id) != len(observations) or set(observed_by_id) != set(expected_ids):
        raise ValueError("every conformance case must be observed exactly once")

    results = tuple(_evaluate_case(case, observed_by_id[case.case_id]) for case in cases)
    payload = {
        "schema_version": "wms-conformance-report.v1",
        "suite_version": WMS_PROVIDER_CONFORMANCE_SUITE_VERSION,
        "suite_digest": QUERY_INVENTORY_CONFORMANCE_SUITE_DIGEST,
        "profile_identity": profile.identity.identity,
        "profile_digest": _profile_digest(profile),
        "target": target.value,
        "fixture_digest": fixture_digest,
        "endpoint_revision": endpoint_revision,
        "generated_at": generated_at.isoformat().replace("+00:00", "Z"),
        "cases": [result.model_dump(mode="json") for result in results],
        "passed": all(result.passed for result in results),
    }
    return WmsConformanceReport.model_validate({**payload, "report_digest": _digest(payload)})


def verify_wms_conformance_report(payload: dict[str, object]) -> WmsConformanceReport:
    """从持久化 JSON 重建，并对固定题库、profile 环境和每题判定重新验算。"""

    report = WmsConformanceReport.model_validate(payload)
    if not hmac.compare_digest(report.suite_digest, QUERY_INVENTORY_CONFORMANCE_SUITE_DIGEST):
        raise ValueError("conformance report suite digest mismatch")

    expected_ids = tuple(case.case_id for case in QUERY_INVENTORY_CONFORMANCE_CASES)
    if tuple(case.case_id for case in report.cases) != expected_ids:
        raise ValueError("conformance report case identity order or count mismatch")

    profile = WMS_PROVIDER_PROFILES.get(report.profile_identity)
    if profile is None or not hmac.compare_digest(report.profile_digest, _profile_digest(profile)):
        raise ValueError("conformance report profile identity or digest mismatch")
    _validate_execution_environment(
        target=report.target,
        profile=profile,
        endpoint_revision=report.endpoint_revision,
    )

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


def build_staging_conformance_executor_attestation(
    *,
    profile: WmsProviderProfile,
    endpoint_identity_digest: str,
    endpoint_revision: str,
) -> StagingConformanceExecutorAttestation:
    """将已验证 staging profile/binding 与部署 endpoint 摘要冻结为 executor 身份。"""

    _validate_execution_environment(
        target=ConformanceTarget.STAGING_LIVE,
        profile=profile,
        endpoint_revision=endpoint_revision,
    )
    if not re.fullmatch(r"[0-9a-f]{64}", endpoint_identity_digest):
        raise ValueError("staging conformance executor requires an opaque endpoint identity digest")
    payload = {
        "schema_version": "wms-staging-conformance-attestation.v1",
        "profile_identity": profile.identity.identity,
        "profile_revision": _profile_digest(profile),
        "binding_identity": QUERY_INVENTORY_CONTRACT.identity,
        "binding_revision": _query_binding_revision(profile),
        "endpoint_identity_digest": endpoint_identity_digest,
        "endpoint_revision": endpoint_revision,
    }
    return StagingConformanceExecutorAttestation.model_validate({**payload, "attestation_digest": _digest(payload)})


async def run_query_inventory_staging_live_conformance(
    *,
    profile: WmsProviderProfile,
    executor: StagingQueryConformanceExecutor,
    fixture_digest: str,
    endpoint_identity_digest: str,
    endpoint_revision: str,
    generated_at: datetime,
) -> WmsConformanceReport:
    """显式 staging 入口；验证具名 executor attestation 后才执行固定题库。"""

    expected_attestation = build_staging_conformance_executor_attestation(
        profile=profile,
        endpoint_identity_digest=endpoint_identity_digest,
        endpoint_revision=endpoint_revision,
    )
    supplied_attestation = getattr(executor, "attestation", None)
    if not isinstance(supplied_attestation, StagingConformanceExecutorAttestation):
        raise TypeError("STAGING_LIVE target requires an attested executor")
    validated_attestation = StagingConformanceExecutorAttestation.model_validate(
        supplied_attestation.model_dump(mode="json")
    )
    if validated_attestation != expected_attestation:
        raise ValueError("staging conformance executor attestation mismatch")

    observations = tuple([await executor.execute(case) for case in QUERY_INVENTORY_CONFORMANCE_CASES])
    return build_wms_conformance_report(
        cases=QUERY_INVENTORY_CONFORMANCE_CASES,
        observations=observations,
        target=ConformanceTarget.STAGING_LIVE,
        profile=profile,
        fixture_digest=fixture_digest,
        endpoint_revision=endpoint_revision,
        generated_at=generated_at,
    )


def _validate_execution_environment(
    *,
    target: ConformanceTarget,
    profile: WmsProviderProfile,
    endpoint_revision: str | None,
) -> None:
    query_bindings = tuple(
        binding for binding in profile.bindings if binding.operation.identity == QUERY_INVENTORY_CONTRACT.identity
    )
    if len(query_bindings) != 1 or query_bindings[0].operation != QUERY_INVENTORY_CONTRACT:
        raise ValueError("conformance profile requires exactly one canonical query_inventory binding")
    environment = profile.identity.environment
    if target is ConformanceTarget.STAGING_LIVE:
        if environment != "staging":
            raise ValueError("STAGING_LIVE target requires an explicit staging environment")
        if not isinstance(endpoint_revision, str) or not re.fullmatch(r"[0-9a-f]{64}", endpoint_revision):
            raise ValueError("STAGING_LIVE target requires an explicit endpoint revision")
        return
    if environment != "sandbox" or endpoint_revision is not None:
        raise ValueError("non-staging conformance requires a sandbox environment without endpoint revision")


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


def _profile_digest(profile: WmsProviderProfile) -> str:
    operation_index = WmsOperationIndexBuilder.build(profile)
    payload = {
        "callbacks": tuple((callback.operation.identity, callback.callback_type) for callback in profile.callbacks),
        "identity": profile.identity.model_dump(mode="json"),
        "operation_index_digest": operation_index.digest,
        "outbound_auth": tuple(
            {
                "credential_reference": binding.outbound_auth.credential_reference,
                "operation_identity": binding.operation.identity,
                "scheme": binding.outbound_auth.scheme.value,
            }
            for binding in profile.bindings
        ),
    }
    return _digest(payload)


def _query_binding_revision(profile: WmsProviderProfile) -> str:
    binding = next(
        binding for binding in profile.bindings if binding.operation.identity == QUERY_INVENTORY_CONTRACT.identity
    )
    credential_reference = binding.outbound_auth.credential_reference
    return _digest(
        {
            "auth_scheme": binding.outbound_auth.scheme.value,
            "credential_reference_digest": _digest(credential_reference) if credential_reference else None,
            "operation_identity": binding.operation.identity,
            "profile_revision": _profile_digest(profile),
        }
    )


def _digest(payload: object) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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
    "StagingConformanceExecutorAttestation",
    "StagingQueryConformanceExecutor",
    "WmsConformanceReport",
    "build_staging_conformance_executor_attestation",
    "build_wms_conformance_report",
    "run_query_inventory_staging_live_conformance",
    "verify_wms_conformance_report",
]
