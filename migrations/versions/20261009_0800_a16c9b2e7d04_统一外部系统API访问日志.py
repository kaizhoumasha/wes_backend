"""统一外部系统 API 访问日志。

Revision ID: a16c9b2e7d04
Revises: 7a0d19c4e632
"""

import sqlalchemy as sa
from alembic import op

revision = "a16c9b2e7d04"
down_revision = "7a0d19c4e632"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 两张旧表仅承接可清理诊断；可靠消息收据与 Evidence 保持原表和事务。
    op.drop_table("callback_logs", schema="wes_biz")
    op.drop_table("api_access_logs", schema="wes_sys")
    op.create_table(
        "api_access_logs",
        sa.Column("id", sa.BigInteger(), nullable=False, comment="主键 ID"),
        sa.Column("created_at", sa.DateTime(), nullable=False, comment="创建时间 (UTC)"),
        sa.Column("updated_at", sa.DateTime(), nullable=True, comment="更新时间 (UTC)"),
        sa.Column("system_id", sa.String(64), nullable=False),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("method", sa.String(10), nullable=False),
        sa.Column("path", sa.String(500), nullable=False),
        sa.Column("peer_address", sa.String(500), nullable=True),
        sa.Column("request_id", sa.String(100), nullable=True),
        sa.Column("trace_id", sa.String(100), nullable=True),
        sa.Column("event_id", sa.String(200), nullable=True),
        sa.Column("causation_id", sa.String(200), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("response_time_ms", sa.Integer(), nullable=True),
        sa.Column("delivery_state", sa.String(32), nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_access_logs")),
        sa.CheckConstraint("direction IN ('INBOUND', 'OUTBOUND')", name=op.f("ck_api_access_logs_direction")),
        sa.CheckConstraint(
            "response_time_ms IS NULL OR response_time_ms >= 0",
            name=op.f("ck_api_access_logs_response_time_ms_nonnegative"),
        ),
        schema="wes_sys",
    )
    for name, columns in (
        ("ix_api_access_logs_created_at_id", ["created_at", "id"]),
        ("ix_api_access_logs_system_direction_created_at", ["system_id", "direction", "created_at"]),
        ("ix_wes_sys_api_access_logs_request_id", ["request_id"]),
        ("ix_wes_sys_api_access_logs_trace_id", ["trace_id"]),
    ):
        op.create_index(name, "api_access_logs", columns, schema="wes_sys")


def downgrade() -> None:
    raise RuntimeError("旧诊断日志表已退役，不转换历史；请重建开发库")
