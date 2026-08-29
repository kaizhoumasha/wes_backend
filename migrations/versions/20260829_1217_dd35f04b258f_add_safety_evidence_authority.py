"""add safety evidence authority

Revision ID: dd35f04b258f
Revises: 273898a3f09b
Create Date: 2026-08-29 12:17:41.906601+08:00

"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "dd35f04b258f"
down_revision: Union[str, Sequence[str], None] = "273898a3f09b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "workline_safety_incidents",
        sa.Column("source_evidence_id", sa.BigInteger(), nullable=True),
        schema="wes_biz",
    )
    op.create_foreign_key(
        op.f("fk_workline_safety_incidents_source_evidence_id_inbound_evidences"),
        "workline_safety_incidents",
        "inbound_evidences",
        ["source_evidence_id"],
        ["id"],
        source_schema="wes_biz",
        referent_schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_workline_safety_incidents_source_evidence_id"),
        "workline_safety_incidents",
        ["source_evidence_id"],
        unique=False,
        schema="wes_biz",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f("ix_wes_biz_workline_safety_incidents_source_evidence_id"),
        table_name="workline_safety_incidents",
        schema="wes_biz",
    )
    op.drop_constraint(
        op.f("fk_workline_safety_incidents_source_evidence_id_inbound_evidences"),
        "workline_safety_incidents",
        schema="wes_biz",
        type_="foreignkey",
    )
    op.drop_column("workline_safety_incidents", "source_evidence_id", schema="wes_biz")
