"""QUERY shadow comparison 分区表与不可变 readiness/approval 表。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Any, ClassVar

from sqlalchemy import CheckConstraint, Column, DateTime, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from src.core.mixins import BaseMixin
from src.database.schema_conf import SchemaType


class QueryShadowComparison(BaseMixin, table=True):
    """按 observed_at 月分区的引用式 comparison 行。"""

    __tablename__: ClassVar[str] = "query_shadow_comparisons"  # pyright: ignore[reportIncompatibleVariableOverride]
    __table_args__ = (
        CheckConstraint(
            "comparison_status IN ('STORED', 'CONFLICT')",
            name="ck_query_shadow_comparisons_status",
        ),
        Index(
            "ix_query_shadow_comparisons_profile_observed",
            "provider_profile_identity",
            "operation_identity",
            "observed_at",
        ),
        Index(
            "ix_query_shadow_comparisons_window_difference",
            "version_set_digest",
            "difference_class",
            "observed_at",
        ),
        Index("ix_query_shadow_comparisons_trace_evidence", "trace_id", "evidence_ref"),
        {"schema": SchemaType.RUNTIME.value, "postgresql_partition_by": "RANGE (observed_at)"},
    )

    observed_at: datetime = Field(primary_key=True, sa_type=DateTime(timezone=True))
    comparison_key: str = Field(primary_key=True, min_length=64, max_length=64)
    comparison_status: str = Field(min_length=1, max_length=20)
    evidence_ref: str = Field(min_length=1, max_length=240)
    trace_id: str | None = Field(default=None, max_length=120)
    provider_profile_identity: str = Field(min_length=1, max_length=240)
    operation_identity: str = Field(min_length=1, max_length=240)
    version_set_digest: str = Field(min_length=64, max_length=64)
    legacy_policy_version: str = Field(min_length=1, max_length=100)
    candidate_policy_version: str = Field(min_length=1, max_length=100)
    legacy_contract_version: str = Field(min_length=1, max_length=100)
    candidate_contract_version: str = Field(min_length=1, max_length=100)
    normalization_version: str = Field(min_length=1, max_length=100)
    evaluator_version: str = Field(min_length=1, max_length=100)
    input_hash: str = Field(min_length=64, max_length=64)
    output_hash: str = Field(min_length=64, max_length=64)
    legacy_action: str | None = Field(default=None, max_length=100)
    legacy_reason: str | None = Field(default=None, max_length=120)
    legacy_error_class: str | None = Field(default=None, max_length=100)
    candidate_action: str | None = Field(default=None, max_length=100)
    candidate_reason: str | None = Field(default=None, max_length=120)
    candidate_error_class: str | None = Field(default=None, max_length=100)
    difference_class: str = Field(min_length=1, max_length=50)
    divergence_diff: dict[str, Any] = Field(sa_column=Column(JSONB, nullable=False))
    evaluator_error_code: str | None = Field(default=None, max_length=120)
    legacy_policy_duration_ns: int = Field(ge=0)
    candidate_policy_duration_ns: int = Field(ge=0)
    query_end_to_end_duration_ms: float = Field(ge=0)


class QueryShadowReadinessReportRecord(BaseMixin, table=True):
    """append-only content-addressed readiness 报告。"""

    __tablename__: ClassVar[str] = "query_shadow_readiness_reports"  # pyright: ignore[reportIncompatibleVariableOverride]
    __table_args__ = ({"schema": SchemaType.RUNTIME.value},)

    report_id: str = Field(primary_key=True, min_length=64, max_length=64)
    generated_at: datetime = Field(sa_type=DateTime(timezone=True))
    provider_profile_identity: str = Field(min_length=1, max_length=240)
    operation_identity: str = Field(min_length=1, max_length=240)
    verdict: str = Field(min_length=1, max_length=50)
    report_json: dict[str, Any] = Field(sa_column=Column(JSONB, nullable=False))


class QueryShadowReadinessApprovalRecord(BaseMixin, table=True):
    """append-only approval，只引用 readiness report ID。"""

    __tablename__: ClassVar[str] = "query_shadow_readiness_approvals"  # pyright: ignore[reportIncompatibleVariableOverride]
    __table_args__ = ({"schema": SchemaType.RUNTIME.value},)

    report_id: str = Field(primary_key=True, min_length=64, max_length=64)
    decision: str = Field(min_length=1, max_length=20)
    approved_by: str = Field(min_length=1, max_length=120)
    approved_at: datetime = Field(sa_type=DateTime(timezone=True))


__all__ = [
    "QueryShadowComparison",
    "QueryShadowReadinessApprovalRecord",
    "QueryShadowReadinessReportRecord",
]
