"""增加 projection causal provenance

Revision ID: 1d298eb00cc4
Revises: 106825879f00
Create Date: 2026-09-21 07:42:20.423675+08:00

"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1d298eb00cc4"
down_revision: Union[str, Sequence[str], None] = "106825879f00"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """保存 Transport projection 的最小 causal provenance。"""
    op.add_column(
        "position_projections",
        sa.Column("source_causal_token", sa.BigInteger(), nullable=True),
        schema="wes_biz",
    )
    op.add_column(
        "position_projections",
        sa.Column("source_effect_phase", sa.String(length=32), nullable=True),
        schema="wes_biz",
    )
    # Recover provenance for existing normal Transport projections when the
    # immutable task identity still resolves to a Binding.  Unmatched legacy
    # or diagnostic rows stay NULL and are intentionally fail-closed by the
    # mutation authority.
    op.execute(
        """
        UPDATE wes_biz.position_projections AS projection
        SET source_causal_token = binding.causal_token,
            source_effect_phase = 'FINAL_RESULT'
        FROM wes_runtime.transport_tasks AS task
        JOIN wes_biz.transport_decision_bindings AS binding
          ON binding.client_request_id = task.client_request_id
        WHERE projection.source_transport_task_id = task.transport_task_id
          AND projection.source_causal_token IS NULL
        """
    )


def downgrade() -> None:
    """移除 Transport projection 的 causal provenance。"""
    op.drop_column("position_projections", "source_effect_phase", schema="wes_biz")
    op.drop_column("position_projections", "source_causal_token", schema="wes_biz")
