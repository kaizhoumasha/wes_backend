"""冻结 Transport Binding causal token

Revision ID: 106825879f00
Revises: 1d3045ea8e62
Create Date: 2026-09-21 07:24:09.337787+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "106825879f00"
down_revision: Union[str, Sequence[str], None] = "1d3045ea8e62"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """为每个 immutable decision 分配可比较的因果 token。"""
    op.execute(
        "CREATE SEQUENCE wes_biz.transport_decision_binding_causal_token_seq AS BIGINT START WITH 1 INCREMENT BY 1"
    )
    op.add_column(
        "transport_decision_bindings",
        sa.Column(
            "causal_token",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("nextval('wes_biz.transport_decision_binding_causal_token_seq'::regclass)"),
        ),
        schema="wes_biz",
    )


def downgrade() -> None:
    """移除 decision 因果 token。"""
    op.drop_column("transport_decision_bindings", "causal_token", schema="wes_biz")
    op.execute("DROP SEQUENCE wes_biz.transport_decision_binding_causal_token_seq")
