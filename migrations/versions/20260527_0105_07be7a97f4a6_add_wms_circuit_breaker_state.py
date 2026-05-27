"""add wms circuit breaker state

Revision ID: 07be7a97f4a6
Revises: 793f8773f841
Create Date: 2026-05-27 01:05:40.030363+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "07be7a97f4a6"
down_revision: Union[str, Sequence[str], None] = "793f8773f841"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "wes_biz"


def _data_columns() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(), nullable=False, comment="创建时间 (UTC)"),
        sa.Column("updated_at", sa.DateTime(), nullable=True, comment="更新时间 (UTC)"),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键 ID"),
    ]


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "wms_circuit_breaker_state",
        *_data_columns(),
        sa.Column("target_code", sa.String(length=240), nullable=False, comment="WMS/RCS 目标编码"),
        sa.Column("operation_name", sa.String(length=120), nullable=False, comment="WMS 操作名"),
        sa.Column(
            "state",
            sa.Enum(
                "CLOSED",
                "OPEN",
                "HALF_OPEN",
                name="wmscircuitbreakerstatus",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=False,
            server_default="CLOSED",
            comment="熔断器状态",
        ),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0", comment="连续失败次数"),
        sa.Column(
            "half_open_attempt_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="HALF_OPEN 探测尝试次数",
        ),
        sa.Column(
            "half_open_success_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="HALF_OPEN 探测成功次数",
        ),
        sa.Column(
            "half_open_probe_generation",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="HALF_OPEN 探测代次",
        ),
        sa.Column("half_open_probe_expires_at", sa.DateTime(), nullable=True, comment="HALF_OPEN 当前探针过期时间"),
        sa.Column("last_failure_at", sa.DateTime(), nullable=True, comment="最近一次失败时间"),
        sa.Column("opened_until", sa.DateTime(), nullable=True, comment="OPEN 状态持续到该时间"),
        sa.Column("last_evidence_key", sa.String(length=240), nullable=True, comment="最近关联的 evidence_key"),
        sa.Column("last_transition_at", sa.DateTime(), nullable=False, comment="最近状态转换时间"),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ux_wms_circuit_breaker_target_operation",
        "wms_circuit_breaker_state",
        ["target_code", "operation_name"],
        unique=True,
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("wms_circuit_breaker_state", schema=SCHEMA)
