"""add query shadow readiness

Revision ID: 8db8cbba582c
Revises: a92c1f8ee28b
Create Date: 2026-07-22 01:25:57.955403+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "8db8cbba582c"
down_revision: Union[str, Sequence[str], None] = "a92c1f8ee28b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        sa.text(
            """
            CREATE TABLE wes_runtime.query_shadow_comparisons (
                observed_at TIMESTAMP WITH TIME ZONE NOT NULL,
                comparison_key VARCHAR(64) NOT NULL,
                comparison_status VARCHAR(20) NOT NULL,
                evidence_ref VARCHAR(240) NOT NULL,
                trace_id VARCHAR(120),
                provider_profile_identity VARCHAR(240) NOT NULL,
                operation_identity VARCHAR(240) NOT NULL,
                version_set_digest VARCHAR(64) NOT NULL,
                legacy_policy_version VARCHAR(100) NOT NULL,
                candidate_policy_version VARCHAR(100) NOT NULL,
                legacy_contract_version VARCHAR(100) NOT NULL,
                candidate_contract_version VARCHAR(100) NOT NULL,
                normalization_version VARCHAR(100) NOT NULL,
                evaluator_version VARCHAR(100) NOT NULL,
                input_hash VARCHAR(64) NOT NULL,
                output_hash VARCHAR(64) NOT NULL,
                legacy_action VARCHAR(100),
                legacy_reason VARCHAR(120),
                legacy_error_class VARCHAR(100),
                candidate_action VARCHAR(100),
                candidate_reason VARCHAR(120),
                candidate_error_class VARCHAR(100),
                difference_class VARCHAR(50) NOT NULL,
                divergence_diff JSONB NOT NULL,
                evaluator_error_code VARCHAR(120),
                legacy_policy_duration_ns BIGINT NOT NULL,
                candidate_policy_duration_ns BIGINT NOT NULL,
                query_end_to_end_duration_ms DOUBLE PRECISION NOT NULL,
                PRIMARY KEY (observed_at, comparison_key),
                CHECK (comparison_status IN ('STORED', 'CONFLICT')),
                CHECK (legacy_policy_duration_ns >= 0),
                CHECK (candidate_policy_duration_ns >= 0),
                CHECK (query_end_to_end_duration_ms >= 0)
            ) PARTITION BY RANGE (observed_at)
            """
        )
    )
    op.execute(
        sa.text(
            """
            DO $$
            DECLARE
                month_offset INTEGER;
                partition_start DATE;
                partition_end DATE;
                partition_name TEXT;
            BEGIN
                FOR month_offset IN 0..3 LOOP
                    partition_start := (
                        date_trunc('month', CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
                        + make_interval(months => month_offset)
                    )::date;
                    partition_end := (partition_start + INTERVAL '1 month')::date;
                    partition_name := 'query_shadow_comparisons_' || to_char(partition_start, 'YYYY_MM');
                    EXECUTE format(
                        'CREATE TABLE wes_runtime.%I '
                        'PARTITION OF wes_runtime.query_shadow_comparisons '
                        'FOR VALUES FROM (%L) TO (%L)',
                        partition_name,
                        partition_start::text || ' 00:00:00+00',
                        partition_end::text || ' 00:00:00+00'
                    );
                END LOOP;
            END
            $$
            """
        )
    )
    op.create_index(
        "ix_query_shadow_comparisons_profile_observed",
        "query_shadow_comparisons",
        ["provider_profile_identity", "operation_identity", "observed_at"],
        schema="wes_runtime",
    )
    op.create_index(
        "ix_query_shadow_comparisons_window_difference",
        "query_shadow_comparisons",
        ["version_set_digest", "difference_class", "observed_at"],
        schema="wes_runtime",
    )
    op.create_index(
        "ix_query_shadow_comparisons_trace_evidence",
        "query_shadow_comparisons",
        ["trace_id", "evidence_ref"],
        schema="wes_runtime",
    )
    op.create_table(
        "query_shadow_readiness_reports",
        sa.Column("report_id", sa.String(length=64), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider_profile_identity", sa.String(length=240), nullable=False),
        sa.Column("operation_identity", sa.String(length=240), nullable=False),
        sa.Column("verdict", sa.String(length=50), nullable=False),
        sa.Column("report_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("report_id"),
        schema="wes_runtime",
    )
    op.create_table(
        "query_shadow_readiness_approvals",
        sa.Column("report_id", sa.String(length=64), nullable=False),
        sa.Column("decision", sa.String(length=20), nullable=False),
        sa.Column("approved_by", sa.String(length=120), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["wes_runtime.query_shadow_readiness_reports.report_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("report_id"),
        schema="wes_runtime",
    )
    op.execute(
        sa.text(
            """
            CREATE FUNCTION wes_runtime.query_shadow_raise_exception()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'QUERY shadow readiness records are immutable';
            END;
            $$ LANGUAGE plpgsql
            """
        )
    )
    for table_name in ("query_shadow_readiness_reports", "query_shadow_readiness_approvals"):
        op.execute(
            sa.text(
                f"CREATE TRIGGER trg_{table_name}_immutable "
                f"BEFORE UPDATE OR DELETE ON wes_runtime.{table_name} "
                "FOR EACH ROW EXECUTE FUNCTION wes_runtime.query_shadow_raise_exception()"
            )
        )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("query_shadow_readiness_approvals", schema="wes_runtime")
    op.drop_table("query_shadow_readiness_reports", schema="wes_runtime")
    op.execute(sa.text("DROP FUNCTION wes_runtime.query_shadow_raise_exception()"))
    op.execute(sa.text("DROP TABLE wes_runtime.query_shadow_comparisons CASCADE"))
