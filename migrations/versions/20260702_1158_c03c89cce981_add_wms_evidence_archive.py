"""add wms evidence archive

Revision ID: c03c89cce981
Revises: 629d8e64eb13
Create Date: 2026-07-02 11:58:03.353776+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c03c89cce981"
down_revision: Union[str, Sequence[str], None] = "629d8e64eb13"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "wes_biz"


def _data_columns() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(), nullable=False, comment="创建时间 (UTC)"),
        sa.Column("updated_at", sa.DateTime(), nullable=True, comment="更新时间 (UTC)"),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键 ID"),
    ]


def _jsonb_object_column(name: str, *, comment: str) -> sa.Column:
    return sa.Column(
        name,
        postgresql.JSONB(astext_type=sa.Text()),
        server_default=sa.text("'{}'::jsonb"),
        nullable=False,
        comment=comment,
    )


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "wms_call_evidence_archive",
        *_data_columns(),
        sa.Column("original_evidence_id", sa.BigInteger(), nullable=False, comment="原 wms_call_evidence.id"),
        sa.Column("evidence_key", sa.String(length=240), nullable=False, comment="证据幂等键"),
        sa.Column("operation_name", sa.String(length=120), nullable=False, comment="WMS 操作名"),
        sa.Column("target_code", sa.String(length=240), nullable=True, comment="WMS/RCS 目标编码"),
        sa.Column(
            "status",
            sa.Enum(
                "STARTED",
                "SUCCEEDED",
                "FAILED",
                "ASYNC_RECORDED",
                name="wmsevidencestatus",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=False,
            comment="证据状态",
        ),
        sa.Column("request_id", sa.String(length=120), nullable=True, comment="请求 ID"),
        sa.Column("trace_id", sa.String(length=120), nullable=True, comment="Trace ID"),
        sa.Column("dispatch_key", sa.String(length=240), nullable=True, comment="Outbox 派发键"),
        sa.Column("source_ref_type", sa.String(length=50), nullable=True, comment="异步事实源类型"),
        sa.Column("source_ref_id", sa.String(length=120), nullable=True, comment="异步事实源 ID"),
        _jsonb_object_column("request_snapshot", comment="脱敏请求或异步摘要"),
        _jsonb_object_column("response_snapshot", comment="脱敏响应摘要"),
        sa.Column("request_hash", sa.String(length=64), nullable=False, comment="canonical request sha256"),
        sa.Column("response_hash", sa.String(length=64), nullable=True, comment="canonical response sha256"),
        sa.Column("http_status", sa.Integer(), nullable=True, comment="HTTP 状态码"),
        sa.Column("reason_code", sa.String(length=120), nullable=True, comment="WMS 原因码"),
        sa.Column("retryable", sa.Boolean(), nullable=True, comment="调用方是否可重试"),
        sa.Column("started_at", sa.DateTime(), nullable=False, comment="调用开始时间"),
        sa.Column("finished_at", sa.DateTime(), nullable=True, comment="调用结束时间"),
        sa.Column("archived_at", sa.DateTime(), nullable=False, comment="归档时间"),
        sa.Column("retention_cutoff_at", sa.DateTime(), nullable=False, comment="本次归档 cutoff 时间"),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ux_wms_call_evidence_archive_original_id",
        "wms_call_evidence_archive",
        ["original_evidence_id"],
        unique=True,
        schema=SCHEMA,
    )
    op.create_index(
        "ux_wms_call_evidence_archive_key",
        "wms_call_evidence_archive",
        ["evidence_key"],
        unique=True,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_wms_call_evidence_archive_operation_started",
        "wms_call_evidence_archive",
        ["operation_name", "started_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_wms_call_evidence_archive_archived",
        "wms_call_evidence_archive",
        ["archived_at"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("wms_call_evidence_archive", schema=SCHEMA)
