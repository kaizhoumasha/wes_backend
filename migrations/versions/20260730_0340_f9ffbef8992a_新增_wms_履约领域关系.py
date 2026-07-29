"""新增 WMS 履约领域关系

Revision ID: f9ffbef8992a
Revises: 36aa187238cc
Create Date: 2026-07-30 03:40:05.264359+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f9ffbef8992a"
down_revision: Union[str, Sequence[str], None] = "36aa187238cc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "wes_runtime"


def upgrade() -> None:
    """新增 WMS 履约领域关系，并扩展输送线队列 membership。"""
    op.create_table(
        "wms_rack_demands",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workline_id", sa.Integer(), nullable=False),
        sa.Column("station_code", sa.String(length=100), nullable=False),
        sa.Column("rack_type", sa.String(length=80), nullable=False),
        sa.Column("demand_generation", sa.Integer(), nullable=False),
        sa.Column("required_rack_code", sa.String(length=100), nullable=True),
        sa.Column("root_operation_identity", sa.String(length=160), nullable=False),
        sa.Column("root_intent_id", sa.Integer(), nullable=False),
        sa.Column("lifecycle_state", sa.String(length=20), nullable=False),
        sa.Column("opened_at_ms", sa.BigInteger(), nullable=False),
        sa.Column("closed_at_ms", sa.BigInteger(), nullable=True),
        sa.Column("reconciliation_case_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "("
            "root_operation_identity = 'wms.fulfillment.request_rack_supply@v1' "
            "AND required_rack_code IS NULL"
            ") OR ("
            "root_operation_identity = 'wms.fulfillment.request_rack_transport@v1' "
            "AND required_rack_code IS NOT NULL"
            ")",
            name=op.f("ck_wms_rack_demands_root_shape"),
        ),
        sa.CheckConstraint(
            "demand_generation > 0",
            name=op.f("ck_wms_rack_demands_generation"),
        ),
        sa.CheckConstraint(
            "("
            "lifecycle_state = 'ACTIVE' AND closed_at_ms IS NULL "
            "AND reconciliation_case_id IS NULL"
            ") OR ("
            "lifecycle_state = 'CLOSED' AND closed_at_ms IS NOT NULL "
            "AND reconciliation_case_id IS NULL"
            ") OR ("
            "lifecycle_state = 'RECONCILING' AND closed_at_ms IS NULL "
            "AND reconciliation_case_id IS NOT NULL"
            ")",
            name=op.f("ck_wms_rack_demands_lifecycle"),
        ),
        sa.ForeignKeyConstraint(
            ["reconciliation_case_id"],
            [f"{SCHEMA}.reconciliation_cases.id"],
            name="fk_wms_rack_demands_reconciliation_case",
        ),
        sa.ForeignKeyConstraint(
            ["root_intent_id"],
            [f"{SCHEMA}.runtime_intent_logs.id"],
            name="fk_wms_rack_demands_root_intent",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_wms_rack_demands"),
        sa.UniqueConstraint(
            "workline_id",
            "station_code",
            "rack_type",
            "demand_generation",
            name="uq_wms_rack_demands_generation",
        ),
        sa.UniqueConstraint(
            "root_intent_id",
            name="uq_wms_rack_demands_root_intent",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_wes_runtime_wms_rack_demands_reconciliation_case_id",
        "wms_rack_demands",
        ["reconciliation_case_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ux_wms_rack_demands_active_station_rack_type",
        "wms_rack_demands",
        ["workline_id", "station_code", "rack_type"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("lifecycle_state IN ('ACTIVE', 'RECONCILING')"),
    )

    op.create_table(
        "material_flow_owners",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workline_id", sa.Integer(), nullable=False),
        sa.Column("object_type", sa.String(length=40), nullable=False),
        sa.Column("object_key", sa.String(length=300), nullable=False),
        sa.Column("owner_type", sa.String(length=40), nullable=False),
        sa.Column("owner_key", sa.String(length=300), nullable=False),
        sa.Column("owner_intent_id", sa.Integer(), nullable=True),
        sa.Column("lifecycle_state", sa.String(length=20), nullable=False),
        sa.Column("source_event_id", sa.String(length=240), nullable=False),
        sa.Column("acquired_at_ms", sa.BigInteger(), nullable=False),
        sa.Column("released_at_ms", sa.BigInteger(), nullable=True),
        sa.Column("reconciliation_case_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "object_type IN ('RACK', 'RACK_FACE', 'BIN', 'OCCUPANCY')",
            name=op.f("ck_material_flow_owners_object_type"),
        ),
        sa.CheckConstraint(
            "owner_type IN ('FULL_BOX_EXCHANGE', 'STATION_TRANSPORT', 'PIECE_SORTING')",
            name=op.f("ck_material_flow_owners_owner_type"),
        ),
        sa.CheckConstraint(
            "("
            "lifecycle_state = 'ACTIVE' AND released_at_ms IS NULL "
            "AND reconciliation_case_id IS NULL"
            ") OR ("
            "lifecycle_state = 'RELEASED' AND released_at_ms IS NOT NULL "
            "AND reconciliation_case_id IS NULL"
            ") OR ("
            "lifecycle_state = 'RECONCILING' AND released_at_ms IS NULL "
            "AND reconciliation_case_id IS NOT NULL"
            ")",
            name=op.f("ck_material_flow_owners_lifecycle"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_intent_id"],
            [f"{SCHEMA}.runtime_intent_logs.id"],
            name="fk_material_flow_owners_intent",
        ),
        sa.ForeignKeyConstraint(
            ["reconciliation_case_id"],
            [f"{SCHEMA}.reconciliation_cases.id"],
            name="fk_material_flow_owners_reconciliation_case",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_material_flow_owners"),
        schema=SCHEMA,
    )
    for column_name in ("owner_intent_id", "reconciliation_case_id"):
        op.create_index(
            f"ix_wes_runtime_material_flow_owners_{column_name}",
            "material_flow_owners",
            [column_name],
            schema=SCHEMA,
        )
    op.create_index(
        "ux_material_flow_owners_active_object",
        "material_flow_owners",
        ["object_type", "object_key"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("lifecycle_state IN ('ACTIVE', 'RECONCILING')"),
    )

    op.create_table(
        "bin_route_instances",
        sa.Column("route_instance_id", sa.String(length=160), nullable=False),
        sa.Column("bin_code", sa.String(length=100), nullable=False),
        sa.Column("workline_id", sa.Integer(), nullable=False),
        sa.Column("created_by_e12_intent_id", sa.Integer(), nullable=False),
        sa.Column("current_node", sa.String(length=40), nullable=False),
        sa.Column("route_version", sa.Integer(), nullable=False),
        sa.Column("lifecycle_state", sa.String(length=20), nullable=False),
        sa.Column("current_rack_code", sa.String(length=100), nullable=True),
        sa.Column("current_slot_code", sa.String(length=100), nullable=True),
        sa.Column("last_transition_source", sa.String(length=80), nullable=False),
        sa.Column("last_transition_source_event_id", sa.String(length=240), nullable=False),
        sa.Column("closed_at_ms", sa.BigInteger(), nullable=True),
        sa.Column("reconciliation_case_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "route_version > 0",
            name=op.f("ck_bin_route_instances_version"),
        ),
        sa.CheckConstraint(
            "current_node IN ("
            "'FIVE_RACK', "
            "'CTU_INBOUND_IN_FLIGHT', "
            "'CONVEYOR_ENTRY', "
            "'SCAN1', "
            "'SCAN2_WORK', "
            "'SCAN3', "
            "'NG_LINE', "
            "'RETURN_QUEUE', "
            "'CTU_RETURN_IN_FLIGHT'"
            ")",
            name=op.f("ck_bin_route_instances_node"),
        ),
        sa.CheckConstraint(
            "("
            "lifecycle_state = 'ACTIVE' AND closed_at_ms IS NULL "
            "AND reconciliation_case_id IS NULL"
            ") OR ("
            "lifecycle_state = 'CLOSED' AND closed_at_ms IS NOT NULL "
            "AND reconciliation_case_id IS NULL AND current_node IN ('NG_LINE', 'FIVE_RACK')"
            ") OR ("
            "lifecycle_state = 'RECONCILING' AND closed_at_ms IS NULL "
            "AND reconciliation_case_id IS NOT NULL"
            ")",
            name=op.f("ck_bin_route_instances_lifecycle"),
        ),
        sa.CheckConstraint(
            "(current_rack_code IS NULL AND current_slot_code IS NULL) "
            "OR (current_rack_code IS NOT NULL AND current_slot_code IS NOT NULL)",
            name=op.f("ck_bin_route_instances_location_shape"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_e12_intent_id"],
            [f"{SCHEMA}.runtime_intent_logs.id"],
            name="fk_bin_route_instances_e12_intent",
        ),
        sa.ForeignKeyConstraint(
            ["reconciliation_case_id"],
            [f"{SCHEMA}.reconciliation_cases.id"],
            name="fk_bin_route_instances_reconciliation_case",
        ),
        sa.PrimaryKeyConstraint(
            "route_instance_id",
            name="pk_bin_route_instances",
        ),
        schema=SCHEMA,
    )
    for column_name in ("created_by_e12_intent_id", "reconciliation_case_id"):
        op.create_index(
            f"ix_wes_runtime_bin_route_instances_{column_name}",
            "bin_route_instances",
            [column_name],
            schema=SCHEMA,
        )
    op.create_index(
        "ux_bin_route_instances_active_bin",
        "bin_route_instances",
        ["bin_code"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("lifecycle_state IN ('ACTIVE', 'RECONCILING')"),
    )

    # 旧数据无需回填；这些字段只由 G4.1 之后新建的 route/return membership 使用。
    op.add_column(
        "conveyor_queue_memberships",
        sa.Column("route_instance_id", sa.String(length=160), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "conveyor_queue_memberships",
        sa.Column("scan3_enqueued_at", sa.DateTime(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "conveyor_queue_memberships",
        sa.Column("queue_position", sa.Integer(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "conveyor_queue_memberships",
        sa.Column("e13_claim_intent_id", sa.Integer(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "conveyor_queue_memberships",
        sa.Column("e13_claim_token", sa.String(length=64), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "conveyor_queue_memberships",
        sa.Column("e13_claim_until", sa.DateTime(), nullable=True),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_conveyor_queue_memberships_route_instance",
        "conveyor_queue_memberships",
        "bin_route_instances",
        ["route_instance_id"],
        ["route_instance_id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_conveyor_queue_memberships_e13_claim_intent",
        "conveyor_queue_memberships",
        "runtime_intent_logs",
        ["e13_claim_intent_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
    )
    op.create_check_constraint(
        op.f("ck_conveyor_queue_memberships_return_shape"),
        "conveyor_queue_memberships",
        "NOT (membership_status IN ('ACTIVE', 'RECONCILING') "
        "AND queue_role = 'RETURN_QUEUE') OR ("
        "route_instance_id IS NOT NULL "
        "AND scan3_enqueued_at IS NOT NULL "
        "AND queue_position IS NOT NULL "
        "AND queue_position > 0 "
        "AND bin_code IS NOT NULL"
        ")",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        op.f("ck_conveyor_queue_memberships_claim_shape"),
        "conveyor_queue_memberships",
        "("
        "e13_claim_intent_id IS NULL "
        "AND e13_claim_token IS NULL "
        "AND e13_claim_until IS NULL"
        ") OR ("
        "e13_claim_intent_id IS NOT NULL "
        "AND e13_claim_token IS NOT NULL "
        "AND e13_claim_until IS NOT NULL "
        "AND membership_status IN ('ACTIVE', 'RECONCILING') "
        "AND queue_role = 'RETURN_QUEUE'"
        ")",
        schema=SCHEMA,
    )
    for column_name in (
        "route_instance_id",
        "e13_claim_intent_id",
        "e13_claim_until",
    ):
        op.create_index(
            f"ix_wes_runtime_conveyor_queue_memberships_{column_name}",
            "conveyor_queue_memberships",
            [column_name],
            schema=SCHEMA,
        )
    op.create_index(
        "ux_wes_runtime_conveyor_queue_memberships_active_route",
        "conveyor_queue_memberships",
        ["route_instance_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("route_instance_id IS NOT NULL AND membership_status IN ('ACTIVE', 'RECONCILING')"),
    )
    op.create_index(
        "ix_wes_runtime_conveyor_queue_memberships_return_fifo_unclaimed",
        "conveyor_queue_memberships",
        ["workline_id", "queue_code", "scan3_enqueued_at", "queue_position", "bin_code"],
        schema=SCHEMA,
        postgresql_where=sa.text(
            "membership_status = 'ACTIVE' AND queue_role = 'RETURN_QUEUE' AND e13_claim_intent_id IS NULL"
        ),
    )

    op.create_table(
        "wms_conveyor_batch_members",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("runtime_intent_log_id", sa.Integer(), nullable=False),
        sa.Column("route_instance_id", sa.String(length=160), nullable=False),
        sa.Column("source_queue_membership_id", sa.Integer(), nullable=True),
        sa.Column("workline_id", sa.Integer(), nullable=False),
        sa.Column("queue_code", sa.String(length=80), nullable=False),
        sa.Column("direction", sa.String(length=20), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("bin_code", sa.String(length=100), nullable=False),
        sa.Column("reserved_queue_position", sa.Integer(), nullable=True),
        sa.Column("member_state", sa.String(length=20), nullable=False),
        sa.Column("staged_at_ms", sa.BigInteger(), nullable=False),
        sa.Column("accepted_at_ms", sa.BigInteger(), nullable=True),
        sa.Column("released_at_ms", sa.BigInteger(), nullable=True),
        sa.Column("terminal_at_ms", sa.BigInteger(), nullable=True),
        sa.Column("terminal_outcome", sa.String(length=80), nullable=True),
        sa.CheckConstraint(
            "sequence_no > 0 AND (reserved_queue_position IS NULL OR reserved_queue_position > 0)",
            name=op.f("ck_wms_conveyor_batch_members_sequence"),
        ),
        sa.CheckConstraint(
            "("
            "direction = 'INBOUND' AND source_queue_membership_id IS NULL "
            "AND reserved_queue_position IS NOT NULL"
            ") OR ("
            "direction = 'RETURN' AND source_queue_membership_id IS NOT NULL "
            "AND reserved_queue_position IS NULL"
            ")",
            name=op.f("ck_wms_conveyor_batch_members_direction_shape"),
        ),
        sa.CheckConstraint(
            "("
            "member_state = 'CANDIDATE' AND accepted_at_ms IS NULL "
            "AND released_at_ms IS NULL AND terminal_at_ms IS NULL "
            "AND terminal_outcome IS NULL"
            ") OR ("
            "member_state = 'ACCEPTED' AND accepted_at_ms IS NOT NULL "
            "AND released_at_ms IS NULL AND terminal_at_ms IS NULL "
            "AND terminal_outcome IS NULL"
            ") OR ("
            "member_state = 'RELEASED' AND accepted_at_ms IS NULL "
            "AND released_at_ms IS NOT NULL AND terminal_at_ms IS NULL "
            "AND terminal_outcome IS NULL"
            ") OR ("
            "member_state = 'TERMINAL' AND accepted_at_ms IS NOT NULL "
            "AND released_at_ms IS NULL AND terminal_at_ms IS NOT NULL "
            "AND terminal_outcome IS NOT NULL"
            ")",
            name=op.f("ck_wms_conveyor_batch_members_lifecycle"),
        ),
        sa.ForeignKeyConstraint(
            ["runtime_intent_log_id"],
            [f"{SCHEMA}.runtime_intent_logs.id"],
            name="fk_wms_conveyor_batch_members_intent",
        ),
        sa.ForeignKeyConstraint(
            ["route_instance_id"],
            [f"{SCHEMA}.bin_route_instances.route_instance_id"],
            name="fk_wms_conveyor_batch_members_route",
        ),
        sa.ForeignKeyConstraint(
            ["source_queue_membership_id"],
            [f"{SCHEMA}.conveyor_queue_memberships.id"],
            name="fk_wms_conveyor_batch_members_source_membership",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name="pk_wms_conveyor_batch_members",
        ),
        sa.UniqueConstraint(
            "runtime_intent_log_id",
            "route_instance_id",
            name="uq_wms_conveyor_batch_members_intent_route",
        ),
        sa.UniqueConstraint(
            "runtime_intent_log_id",
            "sequence_no",
            name="uq_wms_conveyor_batch_members_intent_sequence",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_wes_runtime_wms_conveyor_batch_members_route_instance_id",
        "wms_conveyor_batch_members",
        ["route_instance_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ux_wms_conveyor_batch_members_active_inbound_position",
        "wms_conveyor_batch_members",
        ["workline_id", "queue_code", "reserved_queue_position"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("direction = 'INBOUND' AND member_state IN ('CANDIDATE', 'ACCEPTED')"),
    )
    op.create_index(
        "ux_wms_conveyor_batch_members_active_source_membership",
        "wms_conveyor_batch_members",
        ["source_queue_membership_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text(
            "source_queue_membership_id IS NOT NULL AND member_state IN ('CANDIDATE', 'ACCEPTED')"
        ),
    )


def downgrade() -> None:
    """移除 WMS 履约领域关系及 membership 扩展。"""
    op.drop_table("wms_conveyor_batch_members", schema=SCHEMA)

    op.drop_index(
        "ix_wes_runtime_conveyor_queue_memberships_return_fifo_unclaimed",
        table_name="conveyor_queue_memberships",
        schema=SCHEMA,
    )
    op.drop_index(
        "ux_wes_runtime_conveyor_queue_memberships_active_route",
        table_name="conveyor_queue_memberships",
        schema=SCHEMA,
    )
    for column_name in (
        "e13_claim_until",
        "e13_claim_intent_id",
        "route_instance_id",
    ):
        op.drop_index(
            f"ix_wes_runtime_conveyor_queue_memberships_{column_name}",
            table_name="conveyor_queue_memberships",
            schema=SCHEMA,
        )
    op.drop_constraint(
        op.f("ck_conveyor_queue_memberships_claim_shape"),
        "conveyor_queue_memberships",
        type_="check",
        schema=SCHEMA,
    )
    op.drop_constraint(
        op.f("ck_conveyor_queue_memberships_return_shape"),
        "conveyor_queue_memberships",
        type_="check",
        schema=SCHEMA,
    )
    op.drop_constraint(
        "fk_conveyor_queue_memberships_e13_claim_intent",
        "conveyor_queue_memberships",
        type_="foreignkey",
        schema=SCHEMA,
    )
    op.drop_constraint(
        "fk_conveyor_queue_memberships_route_instance",
        "conveyor_queue_memberships",
        type_="foreignkey",
        schema=SCHEMA,
    )
    for column_name in (
        "e13_claim_until",
        "e13_claim_token",
        "e13_claim_intent_id",
        "queue_position",
        "scan3_enqueued_at",
        "route_instance_id",
    ):
        op.drop_column("conveyor_queue_memberships", column_name, schema=SCHEMA)

    op.drop_table("bin_route_instances", schema=SCHEMA)
    op.drop_table("material_flow_owners", schema=SCHEMA)
    op.drop_table("wms_rack_demands", schema=SCHEMA)
