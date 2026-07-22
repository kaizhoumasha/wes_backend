"""QUERY shadow comparison 的纯合同、bounded evaluator 与 readiness 计算。"""

from __future__ import annotations

import math
from collections import Counter
from datetime import datetime  # noqa: TC003  # Pydantic runtime validation 需要具体类型
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from src.app.runtime.extension_identity import canonical_json, sha256_digest

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
QUERY_SHADOW_EVALUATOR_VERSION = "query-shadow-evaluator.v1"
QUERY_SHADOW_READINESS_CONTRACT_VERSION = "query-shadow-readiness.v1"
QUERY_SHADOW_READINESS_GENERATOR_VERSION = "query-shadow-readiness-generator.v1"


class ShadowDifferenceClass(StrEnum):
    """受控差异分类；禁止把业务 payload 当作自由 diff 写入。"""

    MATCH = "MATCH"
    ACTION_MISMATCH = "ACTION_MISMATCH"
    REASON_MISMATCH = "REASON_MISMATCH"
    ERROR_CLASS_MISMATCH = "ERROR_CLASS_MISMATCH"
    EVALUATOR_ERROR = "EVALUATOR_ERROR"


class ShadowComparisonStatus(StrEnum):
    """持久化 comparison 的完整性状态；冲突一旦出现不得恢复。"""

    STORED = "STORED"
    CONFLICT = "CONFLICT"


class ReadinessVerdict(StrEnum):
    READY = "READY"
    NOT_READY = "NOT_READY"
    INVALID = "INVALID"


class ReadinessApprovalDecision(StrEnum):
    GO = "GO"
    NO_GO = "NO_GO"


class ShadowVersionSet(BaseModel):
    """任何成员变化都必须重置连续观察窗口。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    legacy_policy_version: str = Field(min_length=1, max_length=100)
    candidate_policy_version: str = Field(min_length=1, max_length=100)
    legacy_contract_version: str = Field(min_length=1, max_length=100)
    candidate_contract_version: str = Field(min_length=1, max_length=100)
    normalization_version: str = Field(min_length=1, max_length=100)
    evaluator_version: str = Field(min_length=1, max_length=100)

    @property
    def digest(self) -> str:
        return sha256_digest(self.model_dump(mode="json"))


class QueryShadowExpected(BaseModel):
    """只允许作为 durable QUERY evidence 子对象持久化的 expected 权威。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    shadow_eligible: bool
    comparison_key: str = Field(pattern=_SHA256_PATTERN)
    provider_profile_identity: str = Field(min_length=1, max_length=240)
    operation_identity: str = Field(min_length=1, max_length=240)
    versions: ShadowVersionSet
    observed_at: datetime
    evidence_ref: str = Field(min_length=1, max_length=240)
    input_hash: str = Field(pattern=_SHA256_PATTERN)
    output_hash: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_aware_timestamp(self) -> QueryShadowExpected:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("shadow expected observed_at must be timezone-aware")
        return self


def build_query_shadow_expected(
    *,
    attempt_id: str,
    capability_key: str,
    provider_profile_identity: str,
    operation_identity: str,
    versions: ShadowVersionSet,
    observed_at: datetime,
    input_hash: str,
    output_hash: str,
    shadow_eligible: bool = True,
) -> QueryShadowExpected:
    """由 attempt/query/version pins 派生稳定 comparison identity。"""

    comparison_key = sha256_digest(
        {
            "attempt_id": attempt_id,
            "capability_key": capability_key,
            "provider_profile_identity": provider_profile_identity,
            "operation_identity": operation_identity,
            "versions": versions.model_dump(mode="json"),
            "input_hash": input_hash,
            "output_hash": output_hash,
        }
    )
    return QueryShadowExpected(
        shadow_eligible=shadow_eligible,
        comparison_key=comparison_key,
        provider_profile_identity=provider_profile_identity,
        operation_identity=operation_identity,
        versions=versions,
        observed_at=observed_at,
        evidence_ref=f"query-evidence:{comparison_key}",
        input_hash=input_hash,
        output_hash=output_hash,
    )


class ShadowDecision(BaseModel):
    """policy 的最小、脱敏、可比较投影。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=120)
    error_class: str = Field(min_length=1, max_length=100)


class QueryShadowEvaluationLimits(BaseModel):
    """主路径纯比较器的 canonical CPU/input 边界。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_decision_bytes: int = Field(default=2 * 1024, gt=0, le=64 * 1024)
    max_diff_entries: int = Field(default=3, gt=0, le=3)
    max_policy_duration_ns: int = Field(default=50_000_000, gt=0)


class QueryShadowComparisonDraft(BaseModel):
    """可进入 task queue 的引用式 comparison；不含原请求/authority payload。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    expected: QueryShadowExpected
    comparison_status: ShadowComparisonStatus
    legacy_decision: ShadowDecision | None
    candidate_decision: ShadowDecision | None
    difference_class: ShadowDifferenceClass
    divergence_diff: dict[str, list[str]] = Field(default_factory=dict)
    legacy_policy_duration_ns: int = Field(ge=0)
    candidate_policy_duration_ns: int = Field(ge=0)
    query_end_to_end_duration_ms: float = Field(ge=0)
    evaluator_error_code: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def validate_controlled_diff(self) -> QueryShadowComparisonDraft:
        allowed = {"action", "reason", "error_class"}
        if not set(self.divergence_diff) <= allowed:
            raise ValueError("divergence diff contains unsupported fields")
        if any(
            len(values) != 2 or any(len(value) > 120 for value in values) for values in self.divergence_diff.values()
        ):
            raise ValueError("divergence diff must contain bounded before/after values")
        if self.difference_class is ShadowDifferenceClass.EVALUATOR_ERROR:
            if self.evaluator_error_code is None or self.candidate_decision is not None or self.divergence_diff:
                raise ValueError("evaluator error comparison must carry only a stable error code")
            return self
        if self.legacy_decision is None or self.candidate_decision is None or self.evaluator_error_code is not None:
            raise ValueError("non-error comparison requires both decisions and no evaluator error")
        expected_diff = _decision_diff(self.legacy_decision, self.candidate_decision)
        expected_class = _difference_class(expected_diff)
        if self.divergence_diff != expected_diff or self.difference_class is not expected_class:
            raise ValueError("comparison classification does not match bounded decisions")
        return self

    def task_payload(self) -> dict[str, JsonValue]:
        """展平为 Celery JSON，仍只携带引用、hash、版本和受控差异。"""

        expected = self.expected
        return {
            "comparison_key": expected.comparison_key,
            "evidence_ref": expected.evidence_ref,
            "provider_profile_identity": expected.provider_profile_identity,
            "operation_identity": expected.operation_identity,
            "observed_at": expected.observed_at.isoformat(),
            "input_hash": expected.input_hash,
            "output_hash": expected.output_hash,
            "versions": expected.versions.model_dump(mode="json"),
            "legacy_decision": (
                self.legacy_decision.model_dump(mode="json") if self.legacy_decision is not None else None
            ),
            "candidate_decision": (
                self.candidate_decision.model_dump(mode="json") if self.candidate_decision is not None else None
            ),
            "difference_class": self.difference_class.value,
            "divergence_diff": self.divergence_diff,
            "legacy_policy_duration_ns": self.legacy_policy_duration_ns,
            "candidate_policy_duration_ns": self.candidate_policy_duration_ns,
            "query_end_to_end_duration_ms": self.query_end_to_end_duration_ms,
            "evaluator_error_code": self.evaluator_error_code,
        }


class BoundedQueryShadowEvaluator:
    """仅比较已归一化决策；无 I/O、无业务副作用、无原 payload 保留。"""

    def __init__(self, limits: QueryShadowEvaluationLimits | None = None) -> None:
        self._limits = limits or QueryShadowEvaluationLimits()

    def compare(
        self,
        *,
        expected: QueryShadowExpected,
        legacy_decision: ShadowDecision,
        candidate_decision: ShadowDecision,
        legacy_policy_duration_ns: int,
        candidate_policy_duration_ns: int,
        query_end_to_end_duration_ms: float,
    ) -> QueryShadowComparisonDraft:
        """执行 bounded 结构比较；失败显式写 EVALUATOR_ERROR，不影响 legacy 决策。"""

        error_code = self._budget_error(
            legacy_decision,
            candidate_decision,
            legacy_policy_duration_ns=legacy_policy_duration_ns,
            candidate_policy_duration_ns=candidate_policy_duration_ns,
        )
        if error_code is not None:
            return QueryShadowComparisonDraft(
                expected=expected,
                comparison_status=ShadowComparisonStatus.STORED,
                legacy_decision=legacy_decision,
                candidate_decision=None,
                difference_class=ShadowDifferenceClass.EVALUATOR_ERROR,
                divergence_diff={},
                legacy_policy_duration_ns=max(legacy_policy_duration_ns, 0),
                candidate_policy_duration_ns=max(candidate_policy_duration_ns, 0),
                query_end_to_end_duration_ms=max(query_end_to_end_duration_ms, 0),
                evaluator_error_code=error_code,
            )
        diff = _decision_diff(legacy_decision, candidate_decision)
        if len(diff) > self._limits.max_diff_entries:
            return QueryShadowComparisonDraft(
                expected=expected,
                comparison_status=ShadowComparisonStatus.STORED,
                legacy_decision=legacy_decision,
                candidate_decision=None,
                difference_class=ShadowDifferenceClass.EVALUATOR_ERROR,
                divergence_diff={},
                legacy_policy_duration_ns=legacy_policy_duration_ns,
                candidate_policy_duration_ns=candidate_policy_duration_ns,
                query_end_to_end_duration_ms=query_end_to_end_duration_ms,
                evaluator_error_code="SHADOW_DIFF_BUDGET_EXCEEDED",
            )
        difference_class = _difference_class(diff)
        return QueryShadowComparisonDraft(
            expected=expected,
            comparison_status=ShadowComparisonStatus.STORED,
            legacy_decision=legacy_decision,
            candidate_decision=candidate_decision,
            difference_class=difference_class,
            divergence_diff=diff,
            legacy_policy_duration_ns=legacy_policy_duration_ns,
            candidate_policy_duration_ns=candidate_policy_duration_ns,
            query_end_to_end_duration_ms=query_end_to_end_duration_ms,
        )

    def _budget_error(
        self,
        legacy_decision: ShadowDecision,
        candidate_decision: ShadowDecision,
        *,
        legacy_policy_duration_ns: int,
        candidate_policy_duration_ns: int,
    ) -> str | None:
        try:
            legacy_size = len(canonical_json(legacy_decision.model_dump(mode="json")).encode("utf-8"))
            candidate_size = len(canonical_json(candidate_decision.model_dump(mode="json")).encode("utf-8"))
        except (RecursionError, TypeError, ValueError):
            return "SHADOW_DECISION_INVALID"
        if max(legacy_size, candidate_size) > self._limits.max_decision_bytes:
            return "SHADOW_DECISION_BUDGET_EXCEEDED"
        if (
            legacy_policy_duration_ns < 0
            or candidate_policy_duration_ns < 0
            or max(legacy_policy_duration_ns, candidate_policy_duration_ns) > self._limits.max_policy_duration_ns
        ):
            return "SHADOW_POLICY_DEADLINE_EXCEEDED"
        return None


class QueryShadowReadinessPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    min_window_days: int = Field(default=7, ge=0)
    min_eligible_samples: int = Field(default=1_000, gt=0)
    max_candidate_policy_p99_increase_ratio: float = Field(default=0.10, ge=0)
    max_query_end_to_end_p99_ms: float = Field(default=1_000.0, gt=0)


class QueryShadowReadinessReport(BaseModel):
    """content-addressed 不可变报告；唯一审批依据是 report_id。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    report_id: str = Field(pattern=_SHA256_PATTERN)
    contract_version: str
    generator_version: str
    generated_at: datetime
    provider_profile_identity: str
    operation_identity: str
    version_set: ShadowVersionSet | None
    window_started_at: datetime | None
    window_ended_at: datetime | None
    eligible_samples: int = Field(ge=0)
    excluded_samples: int = Field(ge=0)
    stored_comparisons: int = Field(ge=0)
    expected_stored_gap: int = Field(ge=0)
    difference_counts: dict[str, int]
    reset_reasons: tuple[str, ...]
    legacy_policy_p99_ns: int | None
    candidate_policy_p99_ns: int | None
    query_end_to_end_p99_ms: float | None
    policy_slo_passed: bool
    query_slo_passed: bool
    evidence_refs: tuple[str, ...]
    verdict: ReadinessVerdict


class QueryShadowReadinessApproval(BaseModel):
    """审批记录仅引用 immutable report ID，不复制报告内容。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    report_id: str = Field(pattern=_SHA256_PATTERN)
    decision: ReadinessApprovalDecision
    approved_by: str = Field(min_length=1, max_length=120)
    approved_at: datetime


class ReadinessGateError(RuntimeError):
    """删除旧 capability 前 readiness/approval 未满足。"""


def build_query_shadow_readiness_report(
    *,
    provider_profile_identity: str,
    operation_identity: str,
    expected_samples: list[QueryShadowExpected],
    comparisons: list[QueryShadowComparisonDraft],
    generated_at: datetime,
    policy: QueryShadowReadinessPolicy | None = None,
) -> QueryShadowReadinessReport:
    """expected/stored 对账并只计算最后一个无失败的连续版本窗口。"""

    active_policy = policy or QueryShadowReadinessPolicy()
    filtered = sorted(
        (
            item
            for item in expected_samples
            if item.provider_profile_identity == provider_profile_identity
            and item.operation_identity == operation_identity
        ),
        key=lambda item: (item.observed_at, item.comparison_key),
    )
    comparison_counts = Counter(item.expected.comparison_key for item in comparisons)
    stored = {item.expected.comparison_key: item for item in comparisons}
    segment: list[tuple[QueryShadowExpected, QueryShadowComparisonDraft]] = []
    active_versions: ShadowVersionSet | None = None
    excluded = 0
    current_gap = 0
    current_evaluator_failure = False
    current_duplicate = False
    current_conflict = False
    reset_reasons: list[str] = []
    for expected in filtered:
        if active_versions is not None and expected.versions != active_versions:
            _append_once(reset_reasons, "VERSION_CHANGED")
            segment.clear()
            current_gap = 0
            current_evaluator_failure = False
            current_duplicate = False
            current_conflict = False
        active_versions = expected.versions
        if not expected.shadow_eligible:
            excluded += 1
            continue
        if comparison_counts[expected.comparison_key] > 1:
            _append_once(reset_reasons, "DUPLICATE_COMPARISON")
            segment.clear()
            current_gap = 0
            current_evaluator_failure = False
            current_duplicate = True
            current_conflict = False
            continue
        comparison = stored.get(expected.comparison_key)
        if comparison is None:
            _append_once(reset_reasons, "EXPECTED_STORED_GAP")
            segment.clear()
            current_gap = 1
            current_evaluator_failure = False
            current_duplicate = False
            current_conflict = False
            continue
        if comparison.expected != expected:
            _append_once(reset_reasons, "EXPECTED_STORED_MISMATCH")
            segment.clear()
            current_gap = 1
            current_evaluator_failure = False
            current_duplicate = False
            current_conflict = False
            continue
        if comparison.comparison_status is ShadowComparisonStatus.CONFLICT:
            _append_once(reset_reasons, "COMPARISON_CONFLICT")
            segment.clear()
            current_gap = 0
            current_evaluator_failure = False
            current_duplicate = False
            current_conflict = True
            continue
        if comparison.difference_class is ShadowDifferenceClass.EVALUATOR_ERROR:
            _append_once(reset_reasons, "EVALUATOR_ERROR")
            segment.clear()
            current_gap = 0
            current_evaluator_failure = True
            current_duplicate = False
            current_conflict = False
            continue
        if not segment:
            current_gap = 0
            current_evaluator_failure = False
            current_duplicate = False
            current_conflict = False
        segment.append((expected, comparison))

    legacy_p99 = _p99([item.legacy_policy_duration_ns for _, item in segment])
    candidate_p99 = _p99([item.candidate_policy_duration_ns for _, item in segment])
    query_p99 = _p99_float([item.query_end_to_end_duration_ms for _, item in segment])
    policy_slo_passed = _policy_slo_passed(
        legacy_p99,
        candidate_p99,
        max_increase_ratio=active_policy.max_candidate_policy_p99_increase_ratio,
    )
    query_slo_passed = query_p99 is not None and query_p99 <= active_policy.max_query_end_to_end_p99_ms
    window_started_at = segment[0][0].observed_at if segment else None
    window_ended_at = segment[-1][0].observed_at if segment else None
    window_days = (
        (window_ended_at - window_started_at).total_seconds() / 86_400
        if window_started_at is not None and window_ended_at is not None
        else 0.0
    )
    differences = {difference.value: 0 for difference in ShadowDifferenceClass}
    for _, item in segment:
        differences[item.difference_class.value] += 1
    unexplained = sum(count for name, count in differences.items() if name != ShadowDifferenceClass.MATCH.value)
    if current_gap or current_evaluator_failure or current_duplicate or current_conflict:
        verdict = ReadinessVerdict.INVALID
    elif (
        len(segment) >= active_policy.min_eligible_samples
        and window_days >= active_policy.min_window_days
        and unexplained == 0
        and policy_slo_passed
        and query_slo_passed
    ):
        verdict = ReadinessVerdict.READY
    else:
        verdict = ReadinessVerdict.NOT_READY

    content: dict[str, Any] = {
        "contract_version": QUERY_SHADOW_READINESS_CONTRACT_VERSION,
        "generator_version": QUERY_SHADOW_READINESS_GENERATOR_VERSION,
        "generated_at": generated_at,
        "provider_profile_identity": provider_profile_identity,
        "operation_identity": operation_identity,
        "version_set": active_versions,
        "window_started_at": window_started_at,
        "window_ended_at": window_ended_at,
        "eligible_samples": len(segment),
        "excluded_samples": excluded,
        "stored_comparisons": len(segment),
        "expected_stored_gap": current_gap,
        "difference_counts": differences,
        "reset_reasons": tuple(reset_reasons),
        "legacy_policy_p99_ns": legacy_p99,
        "candidate_policy_p99_ns": candidate_p99,
        "query_end_to_end_p99_ms": query_p99,
        "policy_slo_passed": policy_slo_passed,
        "query_slo_passed": query_slo_passed,
        "evidence_refs": tuple(item.evidence_ref for item, _ in segment),
        "verdict": verdict,
    }
    provisional = QueryShadowReadinessReport(report_id="0" * 64, **content)
    report_id = _report_digest(provisional)
    return provisional.model_copy(update={"report_id": report_id})


def verify_query_shadow_readiness_report(report: QueryShadowReadinessReport) -> None:
    """重算 content digest，拒绝自洽 schema 下被替换的报告字段。"""

    if report.report_id != _report_digest(report):
        raise ReadinessGateError("readiness report content digest does not match report ID")


def require_approved_readiness_report(
    *,
    report: QueryShadowReadinessReport,
    approval: QueryShadowReadinessApproval,
) -> None:
    """T7 删除门禁：只接受对同一 READY report ID 的显式 GO。"""

    verify_query_shadow_readiness_report(report)
    if approval.report_id != report.report_id:
        raise ReadinessGateError("readiness approval report ID does not match immutable report")
    if report.verdict is not ReadinessVerdict.READY:
        raise ReadinessGateError("readiness report is not READY")
    if approval.decision is not ReadinessApprovalDecision.GO:
        raise ReadinessGateError("readiness report was not approved for GO")


def _append_once(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _decision_diff(legacy: ShadowDecision, candidate: ShadowDecision) -> dict[str, list[str]]:
    return {
        field: [getattr(legacy, field), getattr(candidate, field)]
        for field in ("action", "error_class", "reason")
        if getattr(legacy, field) != getattr(candidate, field)
    }


def _difference_class(diff: dict[str, list[str]]) -> ShadowDifferenceClass:
    if "action" in diff:
        return ShadowDifferenceClass.ACTION_MISMATCH
    if "error_class" in diff:
        return ShadowDifferenceClass.ERROR_CLASS_MISMATCH
    if "reason" in diff:
        return ShadowDifferenceClass.REASON_MISMATCH
    return ShadowDifferenceClass.MATCH


def _report_digest(report: QueryShadowReadinessReport) -> str:
    return sha256_digest(report.model_dump(mode="json", exclude={"report_id"}))


def _p99(values: list[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(math.ceil(len(ordered) * 0.99) - 1, 0)]


def _p99_float(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(math.ceil(len(ordered) * 0.99) - 1, 0)]


def _policy_slo_passed(legacy_p99: int | None, candidate_p99: int | None, *, max_increase_ratio: float) -> bool:
    if legacy_p99 is None or candidate_p99 is None:
        return False
    if legacy_p99 == 0:
        return candidate_p99 == 0
    return candidate_p99 <= legacy_p99 * (1 + max_increase_ratio)


__all__ = [
    "QUERY_SHADOW_EVALUATOR_VERSION",
    "QUERY_SHADOW_READINESS_CONTRACT_VERSION",
    "QUERY_SHADOW_READINESS_GENERATOR_VERSION",
    "BoundedQueryShadowEvaluator",
    "QueryShadowComparisonDraft",
    "QueryShadowEvaluationLimits",
    "QueryShadowExpected",
    "QueryShadowReadinessApproval",
    "QueryShadowReadinessPolicy",
    "QueryShadowReadinessReport",
    "ReadinessApprovalDecision",
    "ReadinessGateError",
    "ReadinessVerdict",
    "ShadowComparisonStatus",
    "ShadowDecision",
    "ShadowDifferenceClass",
    "ShadowVersionSet",
    "build_query_shadow_expected",
    "build_query_shadow_readiness_report",
    "require_approved_readiness_report",
    "verify_query_shadow_readiness_report",
]
