"""QUERY shadow comparison/readiness 的唯一数据库访问边界。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, and_, cast, not_, select, text
from sqlalchemy.dialects.postgresql import insert as postgresql_insert

from src.app.runtime.orchestration.models.timeline import TimelineActionType, WorklineTimeline
from src.app.runtime.system_capabilities.shadow_models import (
    QueryShadowComparison,
    QueryShadowReadinessApprovalRecord,
    QueryShadowReadinessReportRecord,
)
from src.app.runtime.system_capabilities.shadow_readiness import (
    QueryShadowComparisonDraft,
    QueryShadowExpected,
    QueryShadowReadinessApproval,
    QueryShadowReadinessReport,
    ShadowComparisonStatus,
    ShadowDecision,
    ShadowDifferenceClass,
    ShadowVersionSet,
    verify_query_shadow_readiness_report,
)


class QueryShadowPartitionMissing(RuntimeError):
    """目标月分区缺失；consumer 必须失败，禁止落入 default。"""


class QueryShadowComparisonRepository:
    """append-only comparison store。"""

    async def append_from_task(
        self,
        db: Any,
        *,
        payload: dict[str, Any],
        trace_id: str | None = None,
    ) -> QueryShadowComparison:
        draft = _draft_from_task(payload)
        partition_name = _partition_name(draft.expected.observed_at)
        lookup = await db.execute(
            text("SELECT to_regclass(:partition_name)"),
            {"partition_name": f"wes_runtime.{partition_name}"},
        )
        if lookup.scalar_one_or_none() is None:
            raise QueryShadowPartitionMissing(f"QUERY shadow target partition missing: {partition_name}")
        row = _comparison_row(draft, trace_id=trace_id)
        values = {column.name: getattr(row, column.name) for column in QueryShadowComparison.__table__.columns}
        insert_statement = postgresql_insert(QueryShadowComparison).values(**values)
        exact_duplicate = and_(
            *(
                QueryShadowComparison.__table__.c[column_name].is_not_distinct_from(
                    insert_statement.excluded[column_name]
                )
                for column_name in values
                if column_name != "comparison_status"
            )
        )
        statement = insert_statement.on_conflict_do_update(
            index_elements=["observed_at", "comparison_key"],
            set_={"comparison_status": ShadowComparisonStatus.CONFLICT.value},
            where=not_(exact_duplicate),
        )
        await db.execute(statement)
        return row


class QueryShadowReadinessRepository:
    """从 durable evidence 对账 expected，并 append immutable report/approval。"""

    async def list_expected(
        self,
        db: Any,
        *,
        provider_profile_identity: str,
        operation_identity: str,
        observed_from: datetime,
        observed_until: datetime,
    ) -> list[QueryShadowExpected]:
        # Timeline 行可能在 observed evidence 之后才提交，尤其会跨过月界；expected、comparison
        # 与 readiness 必须统一按 immutable shadow_expected.observed_at 查询和排序。
        _for_db(observed_from)
        _for_db(observed_until)
        observed_from_utc = observed_from.astimezone(UTC)
        observed_until_utc = observed_until.astimezone(UTC)
        expected_observed_at = cast(
            WorklineTimeline.payload_json["evidence"]["shadow_expected"]["observed_at"].as_string(),
            DateTime(timezone=True),
        )
        result = await db.execute(
            select(WorklineTimeline)
            .where(
                WorklineTimeline.action_type == TimelineActionType.DECISION_MADE,
                WorklineTimeline.payload_json["record_type"].as_string() == "SYSTEM_CAPABILITY_EVIDENCE",
                expected_observed_at >= observed_from_utc,
                expected_observed_at < observed_until_utc,
            )
            .order_by(expected_observed_at.asc(), WorklineTimeline.id.asc())
        )
        expected: list[QueryShadowExpected] = []
        for timeline in result.scalars().all():
            payload = getattr(timeline, "payload_json", None)
            if not isinstance(payload, dict) or payload.get("record_type") != "SYSTEM_CAPABILITY_EVIDENCE":
                continue
            evidence = payload.get("evidence")
            raw_expected = evidence.get("shadow_expected") if isinstance(evidence, dict) else None
            if not isinstance(raw_expected, dict):
                continue
            item = QueryShadowExpected.model_validate(raw_expected)
            if (
                observed_from_utc <= item.observed_at.astimezone(UTC) < observed_until_utc
                and item.provider_profile_identity == provider_profile_identity
                and item.operation_identity == operation_identity
            ):
                expected.append(item)
        return expected

    async def list_comparisons(
        self,
        db: Any,
        *,
        provider_profile_identity: str,
        operation_identity: str,
        observed_from: datetime,
        observed_until: datetime,
    ) -> list[QueryShadowComparisonDraft]:
        result = await db.execute(
            select(QueryShadowComparison)
            .where(
                QueryShadowComparison.provider_profile_identity == provider_profile_identity,
                QueryShadowComparison.operation_identity == operation_identity,
                QueryShadowComparison.observed_at >= _for_db(observed_from),
                QueryShadowComparison.observed_at < _for_db(observed_until),
            )
            .order_by(QueryShadowComparison.observed_at.asc(), QueryShadowComparison.comparison_key.asc())
        )
        return [_draft_from_row(row) for row in result.scalars().all()]

    def add_report(self, db: Any, report: QueryShadowReadinessReport) -> None:
        verify_query_shadow_readiness_report(report)
        db.add(
            QueryShadowReadinessReportRecord(
                report_id=report.report_id,
                generated_at=_for_db(report.generated_at),
                provider_profile_identity=report.provider_profile_identity,
                operation_identity=report.operation_identity,
                verdict=report.verdict.value,
                report_json=report.model_dump(mode="json"),
            )
        )

    def add_approval(self, db: Any, approval: QueryShadowReadinessApproval) -> None:
        db.add(
            QueryShadowReadinessApprovalRecord(
                report_id=approval.report_id,
                decision=approval.decision.value,
                approved_by=approval.approved_by,
                approved_at=_for_db(approval.approved_at),
            )
        )


def _draft_from_task(payload: dict[str, Any]) -> QueryShadowComparisonDraft:
    allowed_fields = {
        "candidate_decision",
        "candidate_policy_duration_ns",
        "comparison_key",
        "difference_class",
        "divergence_diff",
        "evaluator_error_code",
        "evidence_ref",
        "input_hash",
        "legacy_decision",
        "legacy_policy_duration_ns",
        "observed_at",
        "operation_identity",
        "output_hash",
        "provider_profile_identity",
        "query_end_to_end_duration_ms",
        "versions",
    }
    unexpected = set(payload) - allowed_fields
    if unexpected:
        raise ValueError(f"QUERY shadow task contains unexpected fields: {sorted(unexpected)}")
    versions = ShadowVersionSet.model_validate(payload.get("versions"))
    expected = QueryShadowExpected(
        shadow_eligible=True,
        comparison_key=payload.get("comparison_key"),
        provider_profile_identity=payload.get("provider_profile_identity"),
        operation_identity=payload.get("operation_identity"),
        versions=versions,
        observed_at=payload.get("observed_at"),
        evidence_ref=payload.get("evidence_ref"),
        input_hash=payload.get("input_hash"),
        output_hash=payload.get("output_hash"),
    )
    legacy = payload.get("legacy_decision")
    candidate = payload.get("candidate_decision")
    return QueryShadowComparisonDraft(
        expected=expected,
        comparison_status=ShadowComparisonStatus.STORED,
        legacy_decision=ShadowDecision.model_validate(legacy) if isinstance(legacy, dict) else None,
        candidate_decision=ShadowDecision.model_validate(candidate) if isinstance(candidate, dict) else None,
        difference_class=payload.get("difference_class"),
        divergence_diff=payload.get("divergence_diff") or {},
        legacy_policy_duration_ns=payload.get("legacy_policy_duration_ns"),
        candidate_policy_duration_ns=payload.get("candidate_policy_duration_ns"),
        query_end_to_end_duration_ms=payload.get("query_end_to_end_duration_ms"),
        evaluator_error_code=payload.get("evaluator_error_code"),
    )


def _comparison_row(draft: QueryShadowComparisonDraft, *, trace_id: str | None) -> QueryShadowComparison:
    expected = draft.expected
    versions = expected.versions
    return QueryShadowComparison(
        observed_at=_for_db(expected.observed_at),
        comparison_key=expected.comparison_key,
        comparison_status=ShadowComparisonStatus.STORED.value,
        evidence_ref=expected.evidence_ref,
        trace_id=trace_id,
        provider_profile_identity=expected.provider_profile_identity,
        operation_identity=expected.operation_identity,
        version_set_digest=versions.digest,
        legacy_policy_version=versions.legacy_policy_version,
        candidate_policy_version=versions.candidate_policy_version,
        legacy_contract_version=versions.legacy_contract_version,
        candidate_contract_version=versions.candidate_contract_version,
        normalization_version=versions.normalization_version,
        evaluator_version=versions.evaluator_version,
        input_hash=expected.input_hash,
        output_hash=expected.output_hash,
        legacy_action=draft.legacy_decision.action if draft.legacy_decision else None,
        legacy_reason=draft.legacy_decision.reason if draft.legacy_decision else None,
        legacy_error_class=draft.legacy_decision.error_class if draft.legacy_decision else None,
        candidate_action=draft.candidate_decision.action if draft.candidate_decision else None,
        candidate_reason=draft.candidate_decision.reason if draft.candidate_decision else None,
        candidate_error_class=draft.candidate_decision.error_class if draft.candidate_decision else None,
        difference_class=draft.difference_class.value,
        divergence_diff=draft.divergence_diff,
        evaluator_error_code=draft.evaluator_error_code,
        legacy_policy_duration_ns=draft.legacy_policy_duration_ns,
        candidate_policy_duration_ns=draft.candidate_policy_duration_ns,
        query_end_to_end_duration_ms=draft.query_end_to_end_duration_ms,
    )


def _draft_from_row(row: QueryShadowComparison) -> QueryShadowComparisonDraft:
    versions = ShadowVersionSet(
        legacy_policy_version=row.legacy_policy_version,
        candidate_policy_version=row.candidate_policy_version,
        legacy_contract_version=row.legacy_contract_version,
        candidate_contract_version=row.candidate_contract_version,
        normalization_version=row.normalization_version,
        evaluator_version=row.evaluator_version,
    )
    observed_at = row.observed_at.replace(tzinfo=UTC) if row.observed_at.tzinfo is None else row.observed_at
    expected = QueryShadowExpected(
        shadow_eligible=True,
        comparison_key=row.comparison_key,
        provider_profile_identity=row.provider_profile_identity,
        operation_identity=row.operation_identity,
        versions=versions,
        observed_at=observed_at,
        evidence_ref=row.evidence_ref,
        input_hash=row.input_hash,
        output_hash=row.output_hash,
    )
    legacy = (
        ShadowDecision(action=row.legacy_action, reason=row.legacy_reason, error_class=row.legacy_error_class)
        if row.legacy_action and row.legacy_reason and row.legacy_error_class
        else None
    )
    candidate = (
        ShadowDecision(action=row.candidate_action, reason=row.candidate_reason, error_class=row.candidate_error_class)
        if row.candidate_action and row.candidate_reason and row.candidate_error_class
        else None
    )
    return QueryShadowComparisonDraft(
        expected=expected,
        comparison_status=ShadowComparisonStatus(row.comparison_status),
        legacy_decision=legacy,
        candidate_decision=candidate,
        difference_class=ShadowDifferenceClass(row.difference_class),
        divergence_diff=row.divergence_diff,
        legacy_policy_duration_ns=row.legacy_policy_duration_ns,
        candidate_policy_duration_ns=row.candidate_policy_duration_ns,
        query_end_to_end_duration_ms=row.query_end_to_end_duration_ms,
        evaluator_error_code=row.evaluator_error_code,
    )


def _partition_name(observed_at: datetime) -> str:
    aware = observed_at if observed_at.tzinfo is not None else observed_at.replace(tzinfo=UTC)
    return f"query_shadow_comparisons_{aware.astimezone(UTC):%Y_%m}"


def _for_db(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("QUERY shadow timestamp must be timezone-aware")
    return value.astimezone(UTC).replace(tzinfo=None)


query_shadow_comparison_repository = QueryShadowComparisonRepository()
query_shadow_readiness_repository = QueryShadowReadinessRepository()

__all__ = [
    "QueryShadowComparisonRepository",
    "QueryShadowPartitionMissing",
    "QueryShadowReadinessRepository",
    "query_shadow_comparison_repository",
    "query_shadow_readiness_repository",
]
