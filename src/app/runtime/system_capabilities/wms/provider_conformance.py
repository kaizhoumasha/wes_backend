"""WMS Provider 共用的纯 conformance 题库与报告评估器。"""

from __future__ import annotations

import hashlib
import hmac
import json
from base64 import urlsafe_b64decode, urlsafe_b64encode
from datetime import datetime  # noqa: TC003 - Pydantic 运行时解析报告字段类型。
from enum import Enum
from typing import Annotated, Literal, Protocol
from weakref import WeakKeyDictionary

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from src.app.runtime.system_capabilities.wms.conformance_trust_root import (
    WMS_STAGING_CONFORMANCE_TRUST_ROOTS,
    StagingConformanceTrustRootRegistry,
)
from src.app.runtime.system_capabilities.wms.contracts import WmsProviderProfile
from src.app.runtime.system_capabilities.wms.inventory.query_inventory.contract import (
    CONTRACT as QUERY_INVENTORY_CONTRACT,
)
from src.app.runtime.system_capabilities.wms.operation_index_builder import WmsOperationIndexBuilder
from src.app.runtime.system_capabilities.wms.provider_catalog import WMS_PROVIDER_PROFILES

StableText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
CaseId = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[a-z][a-z0-9_]*$")]
ConformanceCode = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Z][A-Z0-9_]*$")]
Sha256Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
SigningKeyId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9._-]{0,79}$")]
Ed25519Signature = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{86}$")]
WMS_PROVIDER_CONFORMANCE_SUITE_VERSION = "wms-provider-conformance.v1"
_DEPLOYMENT_TRUST_ROOT_REGISTRY = WMS_STAGING_CONFORMANCE_TRUST_ROOTS


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


class StagingConformanceExecutorAttestation(BaseModel):
    """由部署受控 signer 签发的冻结执行声明；只携带不可逆 identity/revision 摘要。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["wms-staging-conformance-attestation.v2"] = "wms-staging-conformance-attestation.v2"
    signing_key_id: SigningKeyId
    profile_identity: StableText = Field(max_length=300)
    profile_revision: Sha256Digest
    binding_identity: Literal["wms.inventory.query_inventory@v1"] = QUERY_INVENTORY_CONTRACT.identity
    binding_revision: Sha256Digest
    endpoint_identity_digest: Sha256Digest
    internal_revision_digest: Sha256Digest
    composition_identity_digest: Sha256Digest
    signature: Ed25519Signature


class StagingConformanceAttestationSigner(Protocol):
    """部署受控 signer 的最小端口；runner 不持有该能力。"""

    @property
    def key_id(self) -> str: ...

    def sign(self, payload: bytes) -> bytes: ...


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
    endpoint_revision: Sha256Digest | None = None
    staging_attestation: StagingConformanceExecutorAttestation | None = None
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


class StagingQueryConformanceExecutor(Protocol):
    """受控 staging composition factory 返回的 sealed executor 合同。"""

    async def execute(self, case: ConformanceCaseExpectation) -> ConformanceObservation: ...


def _build_controlled_executor_boundary():
    """用闭包 seal 绑定 executor、delegate 与签名声明，禁止调用方挂字段仿造。"""

    factory_seal = object()
    executor_capabilities = WeakKeyDictionary()

    class _CompositionCapability:
        __slots__ = ("attestation", "execution_delegate")

        def __init__(self, *, attestation, execution_delegate) -> None:
            self.attestation = attestation
            self.execution_delegate = execution_delegate

    class _ControlledStagingQueryConformanceExecutor:
        __slots__ = ("__weakref__",)

        def __init__(self, *, seal) -> None:
            if seal is not factory_seal:
                raise TypeError("staging conformance executor must be created by the controlled composition factory")

        async def execute(self, case: ConformanceCaseExpectation) -> ConformanceObservation:
            capability = resolve(self)
            return await capability.execution_delegate.execute(case)

    def create(*, attestation, execution_delegate):
        capability = _CompositionCapability(attestation=attestation, execution_delegate=execution_delegate)
        executor = _ControlledStagingQueryConformanceExecutor(seal=factory_seal)
        executor_capabilities[executor] = capability
        return executor

    def resolve(executor):
        if type(executor) is not _ControlledStagingQueryConformanceExecutor or executor not in executor_capabilities:
            raise TypeError("STAGING_LIVE executor must come from the controlled composition factory")
        return executor_capabilities[executor]

    return create, resolve


_create_controlled_staging_executor, _resolve_controlled_staging_executor = _build_controlled_executor_boundary()


def build_wms_conformance_report(
    *,
    cases: tuple[ConformanceCaseExpectation, ...],
    observations: tuple[ConformanceObservation, ...],
    target: ConformanceTarget,
    profile: WmsProviderProfile,
    fixture_digest: str,
    generated_at: datetime,
) -> WmsConformanceReport:
    """构建 sandbox/CI/replay 报告；staging 报告只能由验签后的 live runner 生成。"""

    if target is ConformanceTarget.STAGING_LIVE:
        raise ValueError("STAGING_LIVE report must be built by the attested live runner")
    return _build_wms_conformance_report(
        cases=cases,
        observations=observations,
        target=target,
        profile=profile,
        fixture_digest=fixture_digest,
        endpoint_revision=None,
        staging_attestation=None,
        generated_at=generated_at,
    )


def _build_wms_conformance_report(
    *,
    cases: tuple[ConformanceCaseExpectation, ...],
    observations: tuple[ConformanceObservation, ...],
    target: ConformanceTarget,
    profile: WmsProviderProfile,
    fixture_digest: str,
    endpoint_revision: str | None,
    staging_attestation: StagingConformanceExecutorAttestation | None,
    generated_at: datetime,
) -> WmsConformanceReport:
    """纯比较固定题库与脱敏观察；调用方不能自行提供 live revision。"""

    if cases != QUERY_INVENTORY_CONFORMANCE_CASES:
        raise ValueError("WMS QUERY conformance core question bank cannot be overridden")
    canonical_profile = _validate_execution_environment(target=target, profile=profile)
    if target is ConformanceTarget.STAGING_LIVE:
        if staging_attestation is None or endpoint_revision != derive_staging_endpoint_revision(staging_attestation):
            raise ValueError("STAGING_LIVE report requires a verified attestation-derived endpoint revision")
    elif endpoint_revision is not None or staging_attestation is not None:
        raise ValueError("non-staging conformance cannot carry staging attestation or endpoint revision")
    expected_ids = tuple(case.case_id for case in cases)
    observed_by_id = {observation.case_id: observation for observation in observations}
    if len(observed_by_id) != len(observations) or set(observed_by_id) != set(expected_ids):
        raise ValueError("every conformance case must be observed exactly once")

    results = tuple(_evaluate_case(case, observed_by_id[case.case_id]) for case in cases)
    payload = {
        "schema_version": "wms-conformance-report.v1",
        "suite_version": WMS_PROVIDER_CONFORMANCE_SUITE_VERSION,
        "suite_digest": QUERY_INVENTORY_CONFORMANCE_SUITE_DIGEST,
        "profile_identity": canonical_profile.identity.identity,
        "profile_digest": _profile_digest(canonical_profile),
        "target": target.value,
        "fixture_digest": fixture_digest,
        "endpoint_revision": endpoint_revision,
        "staging_attestation": staging_attestation.model_dump(mode="json") if staging_attestation else None,
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
    canonical_profile = _validate_execution_environment(target=report.target, profile=profile)
    if report.target is ConformanceTarget.STAGING_LIVE:
        if report.staging_attestation is None:
            raise ValueError("STAGING_LIVE report verification requires a signed attestation")
        verified_attestation = _verify_deployment_attestation(report.staging_attestation)
        _validate_attestation_claims(profile=canonical_profile, attestation=verified_attestation)
        if report.endpoint_revision != derive_staging_endpoint_revision(verified_attestation):
            raise ValueError("conformance report endpoint revision is not derived from its trusted attestation")
    elif report.endpoint_revision is not None or report.staging_attestation is not None:
        raise ValueError("non-staging conformance report cannot carry staging identity")

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


def compose_query_inventory_staging_conformance_executor(
    *,
    profile: WmsProviderProfile,
    endpoint_identity: str,
    internal_revision: str,
    composition_identity: str,
    signer: StagingConformanceAttestationSigner,
    execution_delegate: StagingQueryConformanceExecutor,
) -> StagingQueryConformanceExecutor:
    """由部署 signer、固定 trust root 与执行 delegate 组合 sealed staging executor。"""

    execute = getattr(execution_delegate, "execute", None)
    if not callable(execute):
        raise TypeError("staging conformance composition requires an execution delegate")
    canonical_profile = _validate_execution_environment(target=ConformanceTarget.STAGING_LIVE, profile=profile)
    for name, value in (
        ("endpoint identity", endpoint_identity),
        ("internal revision", internal_revision),
        ("composition identity", composition_identity),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"staging conformance attestation requires a non-empty {name}")
    key_id = getattr(signer, "key_id", None)
    sign = getattr(signer, "sign", None)
    if not isinstance(key_id, str) or not callable(sign):
        raise TypeError("staging conformance attestation requires a deployment-controlled signer")
    payload = {
        "schema_version": "wms-staging-conformance-attestation.v2",
        "signing_key_id": key_id,
        "profile_identity": canonical_profile.identity.identity,
        "profile_revision": _profile_digest(canonical_profile),
        "binding_identity": QUERY_INVENTORY_CONTRACT.identity,
        "binding_revision": _query_binding_revision(canonical_profile),
        "endpoint_identity_digest": _digest(endpoint_identity.strip()),
        "internal_revision_digest": _digest(internal_revision.strip()),
        "composition_identity_digest": _digest(composition_identity.strip()),
    }
    signature = sign(_canonical_json_bytes(payload))
    if not isinstance(signature, bytes) or len(signature) != 64:
        raise ValueError("staging conformance signer must return an Ed25519 signature")
    attestation = StagingConformanceExecutorAttestation.model_validate(
        {**payload, "signature": urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")}
    )
    verified_attestation = _verify_deployment_attestation(attestation)
    return _create_controlled_staging_executor(
        attestation=verified_attestation,
        execution_delegate=execution_delegate,
    )


def derive_staging_endpoint_revision(attestation: StagingConformanceExecutorAttestation) -> str:
    """从签名声明中的 endpoint identity 与内部 revision 派生报告 revision。"""

    validated = StagingConformanceExecutorAttestation.model_validate(attestation.model_dump(mode="json"))
    return _digest(
        {
            "binding_revision": validated.binding_revision,
            "endpoint_identity_digest": validated.endpoint_identity_digest,
            "internal_revision_digest": validated.internal_revision_digest,
            "profile_revision": validated.profile_revision,
        }
    )


async def run_query_inventory_staging_live_conformance(
    *,
    profile: WmsProviderProfile,
    executor: StagingQueryConformanceExecutor,
    fixture_digest: str,
    generated_at: datetime,
) -> WmsConformanceReport:
    """显式 staging 入口；完整复验 canonical profile 与签名 composition 后才执行。"""

    canonical_profile = _validate_execution_environment(target=ConformanceTarget.STAGING_LIVE, profile=profile)
    capability = _resolve_controlled_staging_executor(executor)
    validated_attestation = _verify_deployment_attestation(capability.attestation)
    _validate_attestation_claims(profile=canonical_profile, attestation=validated_attestation)

    observations = tuple([await executor.execute(case) for case in QUERY_INVENTORY_CONFORMANCE_CASES])
    return _build_wms_conformance_report(
        cases=QUERY_INVENTORY_CONFORMANCE_CASES,
        observations=observations,
        target=ConformanceTarget.STAGING_LIVE,
        profile=canonical_profile,
        fixture_digest=fixture_digest,
        endpoint_revision=derive_staging_endpoint_revision(validated_attestation),
        staging_attestation=validated_attestation,
        generated_at=generated_at,
    )


def _verify_deployment_attestation(
    attestation: StagingConformanceExecutorAttestation,
) -> StagingConformanceExecutorAttestation:
    if not isinstance(attestation, StagingConformanceExecutorAttestation):
        raise TypeError("STAGING_LIVE target requires a signed executor attestation")
    validated = StagingConformanceExecutorAttestation.model_validate(attestation.model_dump(mode="json"))
    _DEPLOYMENT_TRUST_ROOT_REGISTRY.verify_signature(
        signing_key_id=validated.signing_key_id,
        payload=_attestation_signing_payload(validated),
        signature=_decode_signature(validated.signature),
    )
    return validated


def _validate_execution_environment(
    *,
    target: ConformanceTarget,
    profile: WmsProviderProfile,
) -> WmsProviderProfile:
    expected_environment = "staging" if target is ConformanceTarget.STAGING_LIVE else "sandbox"
    return _require_canonical_author_time_profile(profile=profile, expected_environment=expected_environment)


def _require_canonical_author_time_profile(
    *,
    profile: WmsProviderProfile,
    expected_environment: Literal["sandbox", "staging"],
) -> WmsProviderProfile:
    try:
        validated = WmsProviderProfile.model_validate(profile.model_dump(mode="python"))
    except (AttributeError, ValueError) as exc:
        raise ValueError(f"conformance requires the canonical author-time {expected_environment} profile") from exc
    canonical = WMS_PROVIDER_PROFILES.get(validated.identity.identity)
    if canonical is None or canonical.identity.environment != expected_environment or validated != canonical:
        raise ValueError(f"conformance requires the canonical author-time {expected_environment} profile")
    query_bindings = tuple(
        binding for binding in canonical.bindings if binding.operation.identity == QUERY_INVENTORY_CONTRACT.identity
    )
    if len(query_bindings) != 1 or query_bindings[0].operation != QUERY_INVENTORY_CONTRACT:
        raise ValueError("canonical author-time profile requires exactly one canonical query_inventory binding")
    return canonical


def _validate_attestation_claims(
    *,
    profile: WmsProviderProfile,
    attestation: StagingConformanceExecutorAttestation,
) -> None:
    expected = {
        "profile_identity": profile.identity.identity,
        "profile_revision": _profile_digest(profile),
        "binding_identity": QUERY_INVENTORY_CONTRACT.identity,
        "binding_revision": _query_binding_revision(profile),
    }
    actual = {name: getattr(attestation, name) for name in expected}
    if actual != expected:
        raise ValueError("staging conformance attestation does not match the canonical profile or binding")


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
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def _canonical_json_bytes(payload: object) -> bytes:
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return canonical.encode("utf-8")


def _attestation_signing_payload(attestation: StagingConformanceExecutorAttestation) -> bytes:
    return _canonical_json_bytes(attestation.model_dump(mode="json", exclude={"signature"}))


def _decode_signature(signature: str) -> bytes:
    return urlsafe_b64decode(signature + "==")


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
    "StagingConformanceAttestationSigner",
    "StagingConformanceExecutorAttestation",
    "StagingConformanceTrustRootRegistry",
    "StagingQueryConformanceExecutor",
    "WmsConformanceReport",
    "build_wms_conformance_report",
    "compose_query_inventory_staging_conformance_executor",
    "derive_staging_endpoint_revision",
    "run_query_inventory_staging_live_conformance",
    "verify_wms_conformance_report",
]
