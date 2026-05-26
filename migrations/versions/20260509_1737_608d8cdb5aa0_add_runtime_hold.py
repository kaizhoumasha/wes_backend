"""add runtime hold

Revision ID: 608d8cdb5aa0
Revises: 49e5ef9fa864
Create Date: 2026-05-09 17:37:17.755568+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "608d8cdb5aa0"
down_revision: Union[str, Sequence[str], None] = "49e5ef9fa864"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "runtime_holds",
        sa.Column("version", sa.Integer(), server_default="0", nullable=False, comment="版本号"),
        sa.Column("created_at", sa.DateTime(), nullable=False, comment="创建时间 (UTC)"),
        sa.Column("updated_at", sa.DateTime(), nullable=True, comment="更新时间 (UTC)"),
        sa.Column("created_by", sa.BigInteger(), nullable=True, comment="创建人ID"),
        sa.Column("updated_by", sa.BigInteger(), nullable=True, comment="更新人ID"),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键 ID"),
        sa.Column(
            "hold_type",
            sa.Enum(
                "RUNTIME_RECONCILIATION",
                "SAFETY_ESTOP",
                "MANUAL_HOLD",
                name="runtimeholdtype",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "OPEN",
                "IN_PROGRESS",
                "RESOLVED",
                "VOIDED",
                "REOPENED",
                name="runtimeholdstatus",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=False,
        ),
        sa.Column("blocking", sa.Boolean(), nullable=False),
        sa.Column("workline_id", sa.BigInteger(), nullable=False),
        sa.Column("session_id", sa.BigInteger(), nullable=True),
        sa.Column("trace_id", sa.String(length=100), nullable=True),
        sa.Column("plugin_key", sa.String(length=100), nullable=True),
        sa.Column("contract_version", sa.String(length=50), nullable=True),
        sa.Column("source_kind", sa.String(length=100), nullable=False),
        sa.Column("source_reason", sa.String(length=200), nullable=False),
        sa.Column("source_idempotency_key", sa.String(length=300), nullable=False),
        sa.Column("source_inbox_id", sa.BigInteger(), nullable=True),
        sa.Column("source_outbox_id", sa.BigInteger(), nullable=True),
        sa.Column("source_command_id", sa.BigInteger(), nullable=True),
        sa.Column("source_device_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "evidence_snapshot_json",
            postgresql.JSON(astext_type=sa.Text()),
            server_default=sa.text("'{}'::json"),
            nullable=False,
        ),
        sa.Column(
            "release_evidence_json",
            postgresql.JSON(astext_type=sa.Text()),
            server_default=sa.text("'{}'::json"),
            nullable=False,
        ),
        sa.Column(
            "material_disposition",
            sa.Enum(
                "CONTINUE",
                "RETURN_TO_NG",
                name="materialdisposition",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=True,
        ),
        sa.Column(
            "ng_reason_source",
            sa.Enum(
                "PLUGIN",
                "DEVICE_ERROR",
                "RUNTIME",
                "MANUAL",
                name="ngreasonsource",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=True,
        ),
        sa.Column("ng_reason_code", sa.String(length=100), nullable=True),
        sa.Column("ng_reason_label", sa.String(length=200), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_by", sa.BigInteger(), nullable=True),
        sa.Column("voided_at", sa.DateTime(), nullable=True),
        sa.Column("voided_by", sa.BigInteger(), nullable=True),
        sa.Column("reopened_from_hold_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["reopened_from_hold_id"], ["wes_biz.runtime_holds.id"]),
        sa.ForeignKeyConstraint(["session_id"], ["wes_biz.workline_sessions.id"]),
        sa.ForeignKeyConstraint(["source_command_id"], ["wes_biz.device_commands.id"]),
        sa.ForeignKeyConstraint(["source_device_id"], ["wes_biz.devices.id"]),
        sa.ForeignKeyConstraint(["source_inbox_id"], ["wes_biz.workline_inbox.id"]),
        sa.ForeignKeyConstraint(["workline_id"], ["wes_biz.work_lines.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_idempotency_key", name="uq_runtime_holds_source_idempotency_key"),
        schema="wes_biz",
    )
    op.create_index(
        "ix_runtime_holds_active_blocking", "runtime_holds", ["workline_id", "status", "blocking"], schema="wes_biz"
    )
    op.create_index(
        "ix_runtime_holds_source_refs",
        "runtime_holds",
        ["source_kind", "source_inbox_id", "source_outbox_id", "source_command_id", "source_device_id"],
        schema="wes_biz",
    )
    op.create_index(op.f("ix_wes_biz_runtime_holds_id"), "runtime_holds", ["id"], unique=True, schema="wes_biz")
    op.create_index(op.f("ix_wes_biz_runtime_holds_hold_type"), "runtime_holds", ["hold_type"], schema="wes_biz")
    op.create_index(op.f("ix_wes_biz_runtime_holds_status"), "runtime_holds", ["status"], schema="wes_biz")
    op.create_index(op.f("ix_wes_biz_runtime_holds_blocking"), "runtime_holds", ["blocking"], schema="wes_biz")
    op.create_index(op.f("ix_wes_biz_runtime_holds_workline_id"), "runtime_holds", ["workline_id"], schema="wes_biz")
    op.create_index(op.f("ix_wes_biz_runtime_holds_session_id"), "runtime_holds", ["session_id"], schema="wes_biz")
    op.create_index(op.f("ix_wes_biz_runtime_holds_trace_id"), "runtime_holds", ["trace_id"], schema="wes_biz")
    op.create_index(op.f("ix_wes_biz_runtime_holds_plugin_key"), "runtime_holds", ["plugin_key"], schema="wes_biz")
    op.create_index(op.f("ix_wes_biz_runtime_holds_source_kind"), "runtime_holds", ["source_kind"], schema="wes_biz")
    op.create_index(
        op.f("ix_wes_biz_runtime_holds_source_reason"), "runtime_holds", ["source_reason"], schema="wes_biz"
    )
    op.create_index(
        op.f("ix_wes_biz_runtime_holds_source_inbox_id"), "runtime_holds", ["source_inbox_id"], schema="wes_biz"
    )
    op.create_index(
        op.f("ix_wes_biz_runtime_holds_source_outbox_id"), "runtime_holds", ["source_outbox_id"], schema="wes_biz"
    )
    op.create_index(
        op.f("ix_wes_biz_runtime_holds_source_command_id"),
        "runtime_holds",
        ["source_command_id"],
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_runtime_holds_source_device_id"), "runtime_holds", ["source_device_id"], schema="wes_biz"
    )
    op.create_index(
        op.f("ix_wes_biz_runtime_holds_material_disposition"),
        "runtime_holds",
        ["material_disposition"],
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_runtime_holds_ng_reason_source"),
        "runtime_holds",
        ["ng_reason_source"],
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_runtime_holds_ng_reason_code"), "runtime_holds", ["ng_reason_code"], schema="wes_biz"
    )
    op.create_index(op.f("ix_wes_biz_runtime_holds_resolved_at"), "runtime_holds", ["resolved_at"], schema="wes_biz")
    op.create_index(op.f("ix_wes_biz_runtime_holds_voided_at"), "runtime_holds", ["voided_at"], schema="wes_biz")
    op.create_index(
        op.f("ix_wes_biz_runtime_holds_reopened_from_hold_id"),
        "runtime_holds",
        ["reopened_from_hold_id"],
        schema="wes_biz",
    )

    op.create_table(
        "ng_return_items",
        sa.Column("version", sa.Integer(), server_default="0", nullable=False, comment="版本号"),
        sa.Column("created_at", sa.DateTime(), nullable=False, comment="创建时间 (UTC)"),
        sa.Column("updated_at", sa.DateTime(), nullable=True, comment="更新时间 (UTC)"),
        sa.Column("created_by", sa.BigInteger(), nullable=True, comment="创建人ID"),
        sa.Column("updated_by", sa.BigInteger(), nullable=True, comment="更新人ID"),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键 ID"),
        sa.Column("source_workline_id", sa.BigInteger(), nullable=False),
        sa.Column("source_session_id", sa.BigInteger(), nullable=False),
        sa.Column("source_command_id", sa.BigInteger(), nullable=True),
        sa.Column("source_event_id", sa.String(length=200), nullable=True),
        sa.Column("material_identity_key", sa.String(length=300), nullable=False),
        sa.Column(
            "material_identity_json",
            postgresql.JSON(astext_type=sa.Text()),
            server_default=sa.text("'{}'::json"),
            nullable=False,
        ),
        sa.Column(
            "physical_handoff_evidence_json",
            postgresql.JSON(astext_type=sa.Text()),
            server_default=sa.text("'{}'::json"),
            nullable=False,
        ),
        sa.Column(
            "disposition",
            sa.Enum(
                "CONTINUE",
                "RETURN_TO_NG",
                name="ngreturnitemdisposition",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            server_default="RETURN_TO_NG",
            nullable=False,
        ),
        sa.Column(
            "ng_reason_source",
            sa.Enum(
                "PLUGIN",
                "DEVICE_ERROR",
                "RUNTIME",
                "MANUAL",
                name="ngreturnitemngreasonsource",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=True,
        ),
        sa.Column("ng_reason_code", sa.String(length=100), nullable=True),
        sa.Column("ng_reason_label", sa.String(length=200), nullable=True),
        sa.Column("operator_note", sa.Text(), nullable=True),
        sa.Column("created_from_runtime_hold_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "WAITING_REWORK",
                "REWORKING",
                "REWORKED",
                "CANCELLED",
                name="ngreturnitemstatus",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=False,
        ),
        sa.Column("confirmed_by", sa.BigInteger(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["created_from_runtime_hold_id"], ["wes_biz.runtime_holds.id"]),
        sa.ForeignKeyConstraint(["source_command_id"], ["wes_biz.device_commands.id"]),
        sa.ForeignKeyConstraint(["source_session_id"], ["wes_biz.workline_sessions.id"]),
        sa.ForeignKeyConstraint(["source_workline_id"], ["wes_biz.work_lines.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "created_from_runtime_hold_id",
            "material_identity_key",
            name="uq_ng_return_items_hold_material_identity",
        ),
        schema="wes_biz",
    )
    op.create_index(
        "ix_ng_return_items_source_refs",
        "ng_return_items",
        ["source_workline_id", "source_session_id", "source_command_id"],
        schema="wes_biz",
    )
    op.create_index(op.f("ix_wes_biz_ng_return_items_id"), "ng_return_items", ["id"], unique=True, schema="wes_biz")
    op.create_index(
        op.f("ix_wes_biz_ng_return_items_source_workline_id"),
        "ng_return_items",
        ["source_workline_id"],
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_ng_return_items_source_session_id"),
        "ng_return_items",
        ["source_session_id"],
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_ng_return_items_source_command_id"),
        "ng_return_items",
        ["source_command_id"],
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_ng_return_items_source_event_id"),
        "ng_return_items",
        ["source_event_id"],
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_ng_return_items_material_identity_key"),
        "ng_return_items",
        ["material_identity_key"],
        schema="wes_biz",
    )
    op.create_index(
        "uq_ng_return_items_active_material_identity",
        "ng_return_items",
        ["material_identity_key"],
        unique=True,
        schema="wes_biz",
        postgresql_where=sa.text("status IN ('WAITING_REWORK', 'REWORKING')"),
        sqlite_where=sa.text("status IN ('WAITING_REWORK', 'REWORKING')"),
    )
    op.create_index(
        op.f("ix_wes_biz_ng_return_items_disposition"),
        "ng_return_items",
        ["disposition"],
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_ng_return_items_ng_reason_source"),
        "ng_return_items",
        ["ng_reason_source"],
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_ng_return_items_ng_reason_code"),
        "ng_return_items",
        ["ng_reason_code"],
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_ng_return_items_created_from_runtime_hold_id"),
        "ng_return_items",
        ["created_from_runtime_hold_id"],
        schema="wes_biz",
    )
    op.create_index(op.f("ix_wes_biz_ng_return_items_status"), "ng_return_items", ["status"], schema="wes_biz")
    op.create_index(
        op.f("ix_wes_biz_ng_return_items_confirmed_at"), "ng_return_items", ["confirmed_at"], schema="wes_biz"
    )


def downgrade() -> None:
    """Downgrade schema."""

    op.drop_index(op.f("ix_wes_biz_ng_return_items_confirmed_at"), table_name="ng_return_items", schema="wes_biz")
    op.drop_index(op.f("ix_wes_biz_ng_return_items_status"), table_name="ng_return_items", schema="wes_biz")
    op.drop_index(
        op.f("ix_wes_biz_ng_return_items_ng_reason_code"),
        table_name="ng_return_items",
        schema="wes_biz",
    )
    op.drop_index(
        op.f("ix_wes_biz_ng_return_items_ng_reason_source"),
        table_name="ng_return_items",
        schema="wes_biz",
    )
    op.drop_index(op.f("ix_wes_biz_ng_return_items_disposition"), table_name="ng_return_items", schema="wes_biz")
    op.drop_index(
        op.f("ix_wes_biz_ng_return_items_created_from_runtime_hold_id"),
        table_name="ng_return_items",
        schema="wes_biz",
    )
    op.drop_index(
        op.f("ix_wes_biz_ng_return_items_material_identity_key"),
        table_name="ng_return_items",
        schema="wes_biz",
    )
    op.drop_index(
        "uq_ng_return_items_active_material_identity",
        table_name="ng_return_items",
        schema="wes_biz",
        postgresql_where=sa.text("status IN ('WAITING_REWORK', 'REWORKING')"),
        sqlite_where=sa.text("status IN ('WAITING_REWORK', 'REWORKING')"),
    )
    op.drop_index(
        op.f("ix_wes_biz_ng_return_items_source_event_id"),
        table_name="ng_return_items",
        schema="wes_biz",
    )
    op.drop_index(
        op.f("ix_wes_biz_ng_return_items_source_command_id"),
        table_name="ng_return_items",
        schema="wes_biz",
    )
    op.drop_index(
        op.f("ix_wes_biz_ng_return_items_source_session_id"),
        table_name="ng_return_items",
        schema="wes_biz",
    )
    op.drop_index(
        op.f("ix_wes_biz_ng_return_items_source_workline_id"),
        table_name="ng_return_items",
        schema="wes_biz",
    )
    op.drop_index(op.f("ix_wes_biz_ng_return_items_id"), table_name="ng_return_items", schema="wes_biz")
    op.drop_index("ix_ng_return_items_source_refs", table_name="ng_return_items", schema="wes_biz")
    op.drop_table("ng_return_items", schema="wes_biz")

    op.drop_index(
        op.f("ix_wes_biz_runtime_holds_reopened_from_hold_id"),
        table_name="runtime_holds",
        schema="wes_biz",
    )
    op.drop_index(op.f("ix_wes_biz_runtime_holds_voided_at"), table_name="runtime_holds", schema="wes_biz")
    op.drop_index(op.f("ix_wes_biz_runtime_holds_resolved_at"), table_name="runtime_holds", schema="wes_biz")
    op.drop_index(op.f("ix_wes_biz_runtime_holds_ng_reason_code"), table_name="runtime_holds", schema="wes_biz")
    op.drop_index(op.f("ix_wes_biz_runtime_holds_ng_reason_source"), table_name="runtime_holds", schema="wes_biz")
    op.drop_index(
        op.f("ix_wes_biz_runtime_holds_material_disposition"),
        table_name="runtime_holds",
        schema="wes_biz",
    )
    op.drop_index(op.f("ix_wes_biz_runtime_holds_source_device_id"), table_name="runtime_holds", schema="wes_biz")
    op.drop_index(op.f("ix_wes_biz_runtime_holds_source_command_id"), table_name="runtime_holds", schema="wes_biz")
    op.drop_index(op.f("ix_wes_biz_runtime_holds_source_outbox_id"), table_name="runtime_holds", schema="wes_biz")
    op.drop_index(op.f("ix_wes_biz_runtime_holds_source_inbox_id"), table_name="runtime_holds", schema="wes_biz")
    op.drop_index(op.f("ix_wes_biz_runtime_holds_source_reason"), table_name="runtime_holds", schema="wes_biz")
    op.drop_index(op.f("ix_wes_biz_runtime_holds_source_kind"), table_name="runtime_holds", schema="wes_biz")
    op.drop_index(op.f("ix_wes_biz_runtime_holds_plugin_key"), table_name="runtime_holds", schema="wes_biz")
    op.drop_index(op.f("ix_wes_biz_runtime_holds_trace_id"), table_name="runtime_holds", schema="wes_biz")
    op.drop_index(op.f("ix_wes_biz_runtime_holds_session_id"), table_name="runtime_holds", schema="wes_biz")
    op.drop_index(op.f("ix_wes_biz_runtime_holds_workline_id"), table_name="runtime_holds", schema="wes_biz")
    op.drop_index(op.f("ix_wes_biz_runtime_holds_blocking"), table_name="runtime_holds", schema="wes_biz")
    op.drop_index(op.f("ix_wes_biz_runtime_holds_status"), table_name="runtime_holds", schema="wes_biz")
    op.drop_index(op.f("ix_wes_biz_runtime_holds_hold_type"), table_name="runtime_holds", schema="wes_biz")
    op.drop_index(op.f("ix_wes_biz_runtime_holds_id"), table_name="runtime_holds", schema="wes_biz")
    op.drop_index("ix_runtime_holds_source_refs", table_name="runtime_holds", schema="wes_biz")
    op.drop_index("ix_runtime_holds_active_blocking", table_name="runtime_holds", schema="wes_biz")
    op.drop_table("runtime_holds", schema="wes_biz")
