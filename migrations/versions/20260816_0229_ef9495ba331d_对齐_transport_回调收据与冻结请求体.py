"""对齐 Transport 回调收据与冻结请求体。

Revision ID: ef9495ba331d
Revises: fa685260524f
Create Date: 2026-08-16 02:29:30.170971+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ef9495ba331d"
down_revision: Union[str, Sequence[str], None] = "fa685260524f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """直接替换未发布的 Transport schema，并清理旧开发/测试数据。"""
    op.execute("DELETE FROM wes_runtime.transport_position_projections")
    op.execute("DELETE FROM wes_runtime.transport_evidence")
    op.execute("DELETE FROM wes_runtime.transport_resource_bindings")
    op.execute("DELETE FROM wes_runtime.transport_members")
    op.execute("DELETE FROM wes_runtime.transport_tasks")

    op.create_table(
        "transport_callback_receipts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("operation_id", sa.String(length=36), nullable=False),
        sa.Column("operation", sa.String(length=80), nullable=False),
        sa.Column("message_digest", sa.String(length=64), nullable=False),
        sa.Column("message_json", sa.JSON(), nullable=False),
        sa.Column("response_http_status", sa.Integer(), nullable=False),
        sa.Column("response_code", sa.String(length=20), nullable=False),
        sa.Column("response_timestamp_ms", sa.BigInteger(), nullable=False),
        sa.Column("response_data_json", sa.JSON(), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_transport_callback_receipts")),
        sa.UniqueConstraint("operation", "operation_id", name="ux_transport_callback_receipts_identity"),
        schema="wes_runtime",
    )
    op.alter_column(
        "transport_evidence",
        "payload_digest",
        new_column_name="message_digest",
        schema="wes_runtime",
    )
    op.alter_column(
        "transport_tasks",
        "payload_digest",
        new_column_name="request_digest",
        schema="wes_runtime",
    )
    op.drop_column("transport_tasks", "submit_payload_json", schema="wes_runtime")
    op.add_column(
        "transport_tasks",
        sa.Column("submit_request_body", sa.Text(), nullable=False),
        schema="wes_runtime",
    )
    op.alter_column(
        "transport_tasks",
        "submit_payload_digest",
        new_column_name="submit_request_body_digest",
        schema="wes_runtime",
    )


def downgrade() -> None:
    """恢复旧列结构；同样不保留未发布 Transport 数据。"""
    op.execute("DELETE FROM wes_runtime.transport_position_projections")
    op.execute("DELETE FROM wes_runtime.transport_evidence")
    op.execute("DELETE FROM wes_runtime.transport_resource_bindings")
    op.execute("DELETE FROM wes_runtime.transport_members")
    op.execute("DELETE FROM wes_runtime.transport_tasks")

    op.alter_column(
        "transport_tasks",
        "submit_request_body_digest",
        new_column_name="submit_payload_digest",
        schema="wes_runtime",
    )
    op.drop_column("transport_tasks", "submit_request_body", schema="wes_runtime")
    op.add_column(
        "transport_tasks",
        sa.Column("submit_payload_json", sa.JSON(), nullable=False),
        schema="wes_runtime",
    )
    op.alter_column(
        "transport_tasks",
        "request_digest",
        new_column_name="payload_digest",
        schema="wes_runtime",
    )
    op.alter_column(
        "transport_evidence",
        "message_digest",
        new_column_name="payload_digest",
        schema="wes_runtime",
    )
    op.drop_table("transport_callback_receipts", schema="wes_runtime")
