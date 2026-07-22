"""add effect reconciliation cases

Revision ID: c325aab03400
Revises: 8de7cb4de434
Create Date: 2026-07-23 00:27:52.523878+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c325aab03400"
down_revision: Union[str, Sequence[str], None] = "8de7cb4de434"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """新增 EFFECT 独立 ReconciliationCase。"""

    op.create_table(
        "reconciliation_cases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("runtime_intent_log_id", sa.Integer(), nullable=False),
        sa.Column("dispatch_key", sa.String(length=240), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("reason_code", sa.String(length=120), nullable=False),
        sa.Column("evidence_history_json", sa.JSON(), nullable=False),
        sa.Column("decision_json", sa.JSON(), nullable=False),
        sa.Column("opened_at_ms", sa.BigInteger(), nullable=False),
        sa.Column("resolved_at_ms", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "status IN ('OPEN', 'RESOLVED')",
            name=op.f("ck_reconciliation_cases_reconciliation_case_status"),
        ),
        sa.CheckConstraint(
            "(status = 'OPEN' AND resolved_at_ms IS NULL) OR (status = 'RESOLVED' AND resolved_at_ms IS NOT NULL)",
            name=op.f("ck_reconciliation_cases_resolution_state"),
        ),
        sa.ForeignKeyConstraint(
            ["runtime_intent_log_id"],
            ["wes_runtime.runtime_intent_logs.id"],
            name="fk_reconciliation_case_intent",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_reconciliation_cases"),
        schema="wes_runtime",
    )
    op.create_index(
        "ix_wes_runtime_reconciliation_cases_runtime_intent_log_id",
        "reconciliation_cases",
        ["runtime_intent_log_id"],
        schema="wes_runtime",
    )
    op.create_index(
        "ix_wes_runtime_reconciliation_cases_dispatch_key",
        "reconciliation_cases",
        ["dispatch_key"],
        schema="wes_runtime",
    )
    op.create_index(
        "ix_wes_runtime_reconciliation_cases_status",
        "reconciliation_cases",
        ["status"],
        schema="wes_runtime",
    )
    op.create_index(
        "ux_reconciliation_cases_open_dispatch_key",
        "reconciliation_cases",
        ["dispatch_key"],
        unique=True,
        schema="wes_runtime",
        postgresql_where=sa.text("status = 'OPEN'"),
    )


def downgrade() -> None:
    """移除 EFFECT ReconciliationCase。"""

    op.drop_table("reconciliation_cases", schema="wes_runtime")
