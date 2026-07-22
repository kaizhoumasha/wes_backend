"""record typed external http transport results

Revision ID: 8de7cb4de434
Revises: df58f4068f02
Create Date: 2026-07-22 13:12:02.646999+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8de7cb4de434"
down_revision: Union[str, Sequence[str], None] = "df58f4068f02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """为 dispatch attempt 增加 EXTERNAL_HTTP typed transport evidence。"""

    op.add_column(
        "workline_dispatch_attempts",
        sa.Column("transport_outcome", sa.String(length=50), nullable=True),
        schema="wes_biz",
    )
    op.add_column(
        "workline_dispatch_attempts",
        sa.Column("transport_phase", sa.String(length=50), nullable=True),
        schema="wes_biz",
    )
    op.add_column(
        "workline_dispatch_attempts",
        sa.Column("protocol_result", sa.String(length=50), nullable=True),
        schema="wes_biz",
    )
    op.add_column(
        "workline_dispatch_attempts",
        sa.Column("safe_to_retry", sa.Boolean(), nullable=True),
        schema="wes_biz",
    )
    op.add_column(
        "workline_dispatch_attempts",
        sa.Column("http_status_code", sa.Integer(), nullable=True),
        schema="wes_biz",
    )
    op.create_check_constraint(
        "ck_workline_dispatch_attempts_workline_dispatch_attempt_transport_outcome",
        "workline_dispatch_attempts",
        "transport_outcome IN ('NOT_SENT', 'ACCEPTED', 'AMBIGUOUS')",
        schema="wes_biz",
    )
    op.create_check_constraint(
        "ck_workline_dispatch_attempts_workline_dispatch_attempt_transport_phase",
        "workline_dispatch_attempts",
        "transport_phase IN ('PREPARING', 'CONNECTING', 'SENDING', 'AWAITING_RESPONSE', 'RESPONSE_RECEIVED', 'SANDBOX')",
        schema="wes_biz",
    )
    op.create_check_constraint(
        "ck_workline_dispatch_attempts_workline_dispatch_attempt_protocol_result",
        "workline_dispatch_attempts",
        "protocol_result IN ('NOT_AVAILABLE', 'ACCEPTED', 'REJECTED', 'UNKNOWN')",
        schema="wes_biz",
    )
    op.create_index(
        "ix_wes_biz_workline_dispatch_attempts_transport_outcome",
        "workline_dispatch_attempts",
        ["transport_outcome"],
        schema="wes_biz",
    )
    op.create_index(
        "ix_wes_biz_workline_dispatch_attempts_protocol_result",
        "workline_dispatch_attempts",
        ["protocol_result"],
        schema="wes_biz",
    )


def downgrade() -> None:
    """移除 EXTERNAL_HTTP typed transport evidence。"""

    op.drop_index(
        "ix_wes_biz_workline_dispatch_attempts_protocol_result",
        table_name="workline_dispatch_attempts",
        schema="wes_biz",
    )
    op.drop_index(
        "ix_wes_biz_workline_dispatch_attempts_transport_outcome",
        table_name="workline_dispatch_attempts",
        schema="wes_biz",
    )
    op.drop_constraint(
        "ck_workline_dispatch_attempts_workline_dispatch_attempt_protocol_result",
        "workline_dispatch_attempts",
        schema="wes_biz",
        type_="check",
    )
    op.drop_constraint(
        "ck_workline_dispatch_attempts_workline_dispatch_attempt_transport_phase",
        "workline_dispatch_attempts",
        schema="wes_biz",
        type_="check",
    )
    op.drop_constraint(
        "ck_workline_dispatch_attempts_workline_dispatch_attempt_transport_outcome",
        "workline_dispatch_attempts",
        schema="wes_biz",
        type_="check",
    )
    op.drop_column("workline_dispatch_attempts", "http_status_code", schema="wes_biz")
    op.drop_column("workline_dispatch_attempts", "safe_to_retry", schema="wes_biz")
    op.drop_column("workline_dispatch_attempts", "protocol_result", schema="wes_biz")
    op.drop_column("workline_dispatch_attempts", "transport_phase", schema="wes_biz")
    op.drop_column("workline_dispatch_attempts", "transport_outcome", schema="wes_biz")
