"""add smt inbound handoff

Revision ID: fb02178f9772
Revises: e563116f56f1
Create Date: 2026-06-11 07:31:11.032714+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "fb02178f9772"
down_revision: Union[str, Sequence[str], None] = "e563116f56f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "wes_biz"


def _data_columns() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(), nullable=False, comment="创建时间 (UTC)"),
        sa.Column("updated_at", sa.DateTime(), nullable=True, comment="更新时间 (UTC)"),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键 ID"),
    ]


def _recreate_inbox_kind_constraint(*, include_internal_event: bool) -> None:
    allowed_kinds = [
        "DEVICE_EVENT",
        "COMMAND_RESULT",
        "EXTERNAL_HTTP",
        "TIMER_TIMEOUT",
        "MANUAL_HOLD",
        "MANUAL_RESUME",
        "MANUAL_CANCEL",
        "REPLAY_REQUEST",
    ]
    if include_internal_event:
        allowed_kinds.insert(3, "INTERNAL_EVENT")

    allowed_kinds_sql = ",\n                ".join(f"'{kind}'" for kind in allowed_kinds)

    op.execute(
        """
        ALTER TABLE wes_biz.workline_inbox
        DROP CONSTRAINT IF EXISTS inboxkind
        """
    )
    op.execute(
        """
        ALTER TABLE wes_biz.workline_inbox
        DROP CONSTRAINT IF EXISTS ck_workline_inbox_inboxkind
        """
    )
    op.execute(
        f"""
        ALTER TABLE wes_biz.workline_inbox
        ADD CONSTRAINT ck_workline_inbox_inboxkind
        CHECK (
            kind IN (
                {allowed_kinds_sql}
            )
        )
        """
    )


def _guard_no_internal_event_rows_for_downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM wes_biz.workline_inbox
                WHERE kind = 'INTERNAL_EVENT'
            ) THEN
                RAISE EXCEPTION 'Cannot downgrade while workline_inbox contains INTERNAL_EVENT rows; archive or delete those rows first';
            END IF;
        END $$
        """
    )


def upgrade() -> None:
    """Upgrade schema."""
    _recreate_inbox_kind_constraint(include_internal_event=True)

    op.create_table(
        "smt_inbound_handoff_demands",
        *_data_columns(),
        sa.Column("demand_key", sa.String(length=200), nullable=False, comment="handoff demand 幂等键"),
        sa.Column("rack_release_id", sa.String(length=200), nullable=False, comment="粗分机释放货架的稳定事实 ID"),
        sa.Column("source_workline_id", sa.BigInteger(), nullable=True, comment="粗分机工作线 ID"),
        sa.Column("source_workline_code", sa.String(length=100), nullable=True, comment="粗分机工作线编码"),
        sa.Column("target_workline_id", sa.BigInteger(), nullable=True, comment="目标分拣工作线 ID"),
        sa.Column("target_workline_code", sa.String(length=100), nullable=True, comment="目标分拣工作线编码"),
        sa.Column("single_layer_rack_code", sa.String(length=100), nullable=False, comment="被释放的单层货架编码"),
        sa.Column("release_reason_code", sa.String(length=120), nullable=True, comment="释放原因码"),
        sa.Column(
            "bin_snapshots_json",
            postgresql.JSON(astext_type=sa.Text()),
            server_default=sa.text("'{}'::json"),
            nullable=False,
            comment="释放时料箱和料格快照，仅作为 release evidence",
        ),
        sa.Column("decision_status", sa.String(length=50), nullable=True, comment="满箱交换决策状态"),
        sa.Column("handling_operation_key", sa.String(length=200), nullable=True, comment="满箱交换 handling 操作键"),
        sa.Column(
            "sorting_source_demand_key", sa.String(length=200), nullable=True, comment="分拣 source demand 幂等键"
        ),
        sa.Column(
            "status",
            sa.Enum(
                "CREATED",
                "EVALUATING",
                "WAITING_FULL_BOX_EXCHANGE",
                "RECONCILING",
                "FULL_BOX_EXCHANGED",
                "READY_FOR_SORTING",
                "CLAIMED_BY_SORTING",
                "SORTING_IN_PROGRESS",
                "COMPLETED",
                "MANUAL_HOLD",
                "CANCELLED",
                name="smtinboundhandoffdemandstatus",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=False,
            comment="handoff demand 主状态",
        ),
        sa.Column("failure_code", sa.String(length=120), nullable=True, comment="受控失败原因码"),
        sa.Column("failure_message", sa.Text(), nullable=True, comment="失败说明"),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True, comment="下一次兜底扫描时间"),
        sa.Column("trace_id", sa.String(length=100), nullable=True, comment="跨链路追踪 ID"),
        sa.ForeignKeyConstraint(["source_workline_id"], [f"{SCHEMA}.work_lines.id"]),
        sa.ForeignKeyConstraint(["target_workline_id"], [f"{SCHEMA}.work_lines.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("demand_key", name="uq_smt_inbound_handoff_demands_demand_key"),
        sa.UniqueConstraint("rack_release_id", name="uq_smt_inbound_handoff_demands_rack_release_id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_smt_inbound_handoff_demands_due_scan",
        "smt_inbound_handoff_demands",
        ["next_attempt_at", "updated_at", "id"],
        unique=False,
        schema=SCHEMA,
        postgresql_where=sa.text("status IN ('CREATED', 'EVALUATING', 'FULL_BOX_EXCHANGED', 'READY_FOR_SORTING')"),
    )
    op.create_index(
        "ix_smt_inbound_handoff_demands_status_target_updated",
        "smt_inbound_handoff_demands",
        ["status", "target_workline_id", "updated_at"],
        unique=False,
        schema=SCHEMA,
    )
    for column_name in ("failure_code", "id", "next_attempt_at", "status", "trace_id"):
        op.create_index(
            f"ix_wes_biz_smt_inbound_handoff_demands_{column_name}",
            "smt_inbound_handoff_demands",
            [column_name],
            unique=column_name == "id",
            schema=SCHEMA,
        )

    op.create_table(
        "smt_inbound_handoff_source_items",
        *_data_columns(),
        sa.Column("handoff_demand_id", sa.BigInteger(), nullable=False, comment="所属 handoff demand ID"),
        sa.Column("item_key", sa.String(length=200), nullable=False, comment="demand 内 source item 幂等键"),
        sa.Column("bin_code", sa.String(length=100), nullable=True, comment="source 料箱编码"),
        sa.Column("bin_cell_index", sa.Integer(), nullable=True, comment="source 料格序号"),
        sa.Column("bin_cell_code", sa.String(length=100), nullable=True, comment="source 料格编码"),
        sa.Column("material_identity_key", sa.String(length=200), nullable=True, comment="source 物料身份键"),
        sa.Column("pkg_code", sa.String(length=200), nullable=True, comment="source 流水号"),
        sa.Column("reel_thickness_mm", sa.Numeric(10, 3), nullable=True, comment="盘厚 evidence，单位 mm"),
        sa.Column(
            "status",
            sa.Enum(
                "READY",
                "PICK_REQUESTED",
                "CLAIMED_BY_SORTING",
                "PICKED",
                "SORTING",
                "SORTED",
                "EXCHANGED",
                "SKIPPED",
                "MANUAL_HOLD",
                name="smtinboundhandoffsourceitemstatus",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=False,
            comment="source item 主状态",
        ),
        sa.Column("target_workline_id", sa.BigInteger(), nullable=True, comment="实际认领的分拣工作线 ID"),
        sa.Column("target_workline_code", sa.String(length=100), nullable=True, comment="实际认领的分拣工作线编码"),
        sa.Column(
            "sorting_session_id", sa.BigInteger(), nullable=True, comment="认领后的 SMT_SORTING_INBOUND session ID"
        ),
        sa.Column("claim_attempt_no", sa.Integer(), nullable=False, comment="source pick request 代次"),
        sa.Column(
            "source_pick_inbox_id",
            sa.BigInteger(),
            nullable=True,
            comment="SORTING_SOURCE_PICK_REQUESTED 内部 Inbox ID",
        ),
        sa.Column(
            "source_pick_command_id", sa.BigInteger(), nullable=True, comment="首盘 SORTING_SOURCE_PICK command ID"
        ),
        sa.Column("source_pick_command_code", sa.String(length=200), nullable=True, comment="首盘 command code"),
        sa.Column("source_pick_dispatch_key", sa.String(length=200), nullable=True, comment="首盘 dispatch evidence"),
        sa.Column("failure_code", sa.String(length=120), nullable=True, comment="item 级受控失败原因码"),
        sa.Column("failure_message", sa.Text(), nullable=True, comment="item 级失败说明"),
        sa.Column("claimed_at", sa.DateTime(), nullable=True, comment="认领时间"),
        sa.Column("completed_at", sa.DateTime(), nullable=True, comment="完成时间"),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True, comment="下一次可重试时间"),
        sa.ForeignKeyConstraint(["handoff_demand_id"], [f"{SCHEMA}.smt_inbound_handoff_demands.id"]),
        sa.ForeignKeyConstraint(["sorting_session_id"], [f"{SCHEMA}.workline_sessions.id"]),
        sa.ForeignKeyConstraint(["source_pick_command_id"], [f"{SCHEMA}.device_commands.id"]),
        sa.ForeignKeyConstraint(["source_pick_inbox_id"], [f"{SCHEMA}.workline_inbox.id"]),
        sa.ForeignKeyConstraint(["target_workline_id"], [f"{SCHEMA}.work_lines.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "handoff_demand_id",
            "item_key",
            name="uq_smt_inbound_handoff_source_items_demand_item_key",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_smt_inbound_handoff_source_items_ready_claim",
        "smt_inbound_handoff_source_items",
        ["next_attempt_at", "handoff_demand_id", "id"],
        unique=False,
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'READY'"),
    )
    op.create_index(
        "ix_smt_inbound_handoff_source_items_post_claim_recovery",
        "smt_inbound_handoff_source_items",
        ["source_pick_inbox_id", "updated_at", "id"],
        unique=False,
        schema=SCHEMA,
        postgresql_where=sa.text(
            "status IN ('PICK_REQUESTED', 'CLAIMED_BY_SORTING') AND source_pick_inbox_id IS NOT NULL"
        ),
    )
    op.create_index(
        "ix_smt_inbound_handoff_source_items_demand_status_id",
        "smt_inbound_handoff_source_items",
        ["handoff_demand_id", "status", "id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_wes_biz_smt_inbound_handoff_source_items_id",
        "smt_inbound_handoff_source_items",
        ["id"],
        unique=True,
        schema=SCHEMA,
    )
    for index_name, column_name in (
        ("ix_smt_in_handoff_items_demand_id", "handoff_demand_id"),
        ("ix_smt_in_handoff_items_failure_code", "failure_code"),
        ("ix_smt_in_handoff_items_material_key", "material_identity_key"),
        ("ix_smt_in_handoff_items_next_attempt", "next_attempt_at"),
        ("ix_smt_in_handoff_items_pick_command", "source_pick_command_id"),
        ("ix_smt_in_handoff_items_pick_inbox", "source_pick_inbox_id"),
        ("ix_smt_in_handoff_items_sorting_session", "sorting_session_id"),
        ("ix_smt_in_handoff_items_status", "status"),
    ):
        op.create_index(index_name, "smt_inbound_handoff_source_items", [column_name], unique=False, schema=SCHEMA)


def downgrade() -> None:
    """Downgrade schema."""
    _guard_no_internal_event_rows_for_downgrade()

    for index_name in (
        "ix_smt_in_handoff_items_status",
        "ix_smt_in_handoff_items_sorting_session",
        "ix_smt_in_handoff_items_pick_inbox",
        "ix_smt_in_handoff_items_pick_command",
        "ix_smt_in_handoff_items_next_attempt",
        "ix_smt_in_handoff_items_material_key",
        "ix_smt_in_handoff_items_failure_code",
        "ix_smt_in_handoff_items_demand_id",
        "ix_wes_biz_smt_inbound_handoff_source_items_id",
        "ix_smt_inbound_handoff_source_items_demand_status_id",
        "ix_smt_inbound_handoff_source_items_post_claim_recovery",
        "ix_smt_inbound_handoff_source_items_ready_claim",
    ):
        op.drop_index(index_name, table_name="smt_inbound_handoff_source_items", schema=SCHEMA)
    op.drop_table("smt_inbound_handoff_source_items", schema=SCHEMA)

    for index_name in (
        "ix_wes_biz_smt_inbound_handoff_demands_trace_id",
        "ix_wes_biz_smt_inbound_handoff_demands_status",
        "ix_wes_biz_smt_inbound_handoff_demands_next_attempt_at",
        "ix_wes_biz_smt_inbound_handoff_demands_id",
        "ix_wes_biz_smt_inbound_handoff_demands_failure_code",
        "ix_smt_inbound_handoff_demands_status_target_updated",
        "ix_smt_inbound_handoff_demands_due_scan",
    ):
        op.drop_index(index_name, table_name="smt_inbound_handoff_demands", schema=SCHEMA)
    op.drop_table("smt_inbound_handoff_demands", schema=SCHEMA)

    _recreate_inbox_kind_constraint(include_internal_event=False)
