"""add wms writeback evidence

Revision ID: 1fdeed75fd3a
Revises: 7541d77ecf3b
Create Date: 2026-05-16 18:58:51.746966+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "1fdeed75fd3a"
down_revision: Union[str, Sequence[str], None] = "7541d77ecf3b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _enterprise_columns() -> list[sa.Column]:
    return [
        sa.Column("version", sa.Integer(), server_default="0", nullable=False, comment="版本号"),
        sa.Column("created_at", sa.DateTime(), nullable=False, comment="创建时间 (UTC)"),
        sa.Column("updated_at", sa.DateTime(), nullable=True, comment="更新时间 (UTC)"),
        sa.Column("created_by", sa.BigInteger(), nullable=True, comment="创建人ID"),
        sa.Column("updated_by", sa.BigInteger(), nullable=True, comment="更新人ID"),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键 ID"),
    ]


def _json_object_column(name: str, *, comment: str) -> sa.Column:
    return sa.Column(
        name,
        postgresql.JSON(astext_type=sa.Text()),
        server_default=sa.text("'{}'::json"),
        nullable=False,
        comment=comment,
    )


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "resource_wms_writeback_evidence",
        *_enterprise_columns(),
        sa.Column("evidence_code", sa.String(length=160), nullable=False, comment="WMS 回写证据编码"),
        sa.Column("request_id", sa.String(length=120), nullable=False, comment="WES 请求 ID"),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False, comment="WMS 回写幂等键"),
        sa.Column("dispatch_key", sa.String(length=200), nullable=True, comment="Outbox 派发键"),
        sa.Column("endpoint", sa.String(length=300), nullable=False, comment="WMS 接口或回调类型"),
        sa.Column(
            "source_system",
            sa.Enum(
                "WMS",
                "RCS",
                "ECS",
                "WES_RUNTIME",
                "MANUAL_IMPORT",
                "MANUAL",
                name="resourcesourcesystem",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=False,
            comment="WMS/RCS 来源系统",
        ),
        sa.Column("request_hash", sa.String(length=128), nullable=False, comment="脱敏请求摘要 hash"),
        sa.Column("response_hash", sa.String(length=128), nullable=True, comment="脱敏响应摘要 hash"),
        _json_object_column("request_summary_json", comment="脱敏请求摘要"),
        _json_object_column("response_summary_json", comment="脱敏响应摘要"),
        sa.Column("http_status", sa.Integer(), nullable=True, comment="HTTP 状态"),
        sa.Column("wms_document_id", sa.String(length=160), nullable=True, comment="WMS 单据或任务引用"),
        sa.Column("inventory_version", sa.String(length=160), nullable=True, comment="WMS 库存或业务版本"),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True, comment="WMS 确认时间"),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False, comment="重试次数"),
        sa.Column("failure_code", sa.String(length=120), nullable=True, comment="失败原因"),
        sa.Column("trace_id", sa.String(length=100), nullable=True, comment="WorkLine trace"),
        sa.Column("session_id", sa.String(length=100), nullable=True, comment="WorkLine Session"),
        sa.PrimaryKeyConstraint("id"),
        schema="wes_biz",
    )
    op.create_index(
        "ux_resource_wms_writeback_evidence_code",
        "resource_wms_writeback_evidence",
        ["evidence_code"],
        unique=True,
        schema="wes_biz",
    )
    op.create_index(
        "ux_resource_wms_writeback_evidence_idempotency",
        "resource_wms_writeback_evidence",
        ["idempotency_key"],
        unique=True,
        schema="wes_biz",
    )
    op.create_index(
        "ix_resource_wms_writeback_evidence_dispatch",
        "resource_wms_writeback_evidence",
        ["dispatch_key"],
        schema="wes_biz",
    )
    op.create_index(
        "ix_resource_wms_writeback_evidence_confirmed",
        "resource_wms_writeback_evidence",
        ["wms_document_id", "confirmed_at"],
        schema="wes_biz",
    )
    op.create_index(
        "ix_resource_wms_writeback_evidence_request_id",
        "resource_wms_writeback_evidence",
        ["request_id"],
        schema="wes_biz",
    )
    op.create_index(
        "ix_resource_wms_writeback_evidence_failure_code",
        "resource_wms_writeback_evidence",
        ["failure_code"],
        schema="wes_biz",
    )
    op.create_index(
        "ix_resource_wms_writeback_evidence_trace_id",
        "resource_wms_writeback_evidence",
        ["trace_id"],
        schema="wes_biz",
    )
    op.create_index(
        "ix_resource_wms_writeback_evidence_session_id",
        "resource_wms_writeback_evidence",
        ["session_id"],
        schema="wes_biz",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("resource_wms_writeback_evidence", schema="wes_biz")
