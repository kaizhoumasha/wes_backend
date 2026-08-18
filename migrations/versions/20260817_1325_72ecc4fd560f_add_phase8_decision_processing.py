"""add phase8 decision processing

Revision ID: 72ecc4fd560f
Revises: 48c71f31cafb
Create Date: 2026-08-17 13:25:01.278007+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "72ecc4fd560f"
down_revision: Union[str, Sequence[str], None] = "48c71f31cafb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """直接切换到 Phase 8 Decision processing schema，不承接开发期 execution 数据。"""

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM wes_biz.line_run_epoch_device_bindings LIMIT 1)
               OR EXISTS (SELECT 1 FROM wes_biz.inbound_evidences LIMIT 1) THEN
                RAISE EXCEPTION
                    'Phase 8 decision processing direct cutover 要求清空 Epoch binding 与 inbound evidence 开发数据';
            END IF;
        END
        $$
        """
    )

    op.add_column(
        "line_run_epoch_device_bindings",
        sa.Column("device_role", sa.String(length=50), nullable=False),
        schema="wes_biz",
    )
    op.create_unique_constraint(
        "ux_line_run_epoch_device_bindings_epoch_device_role",
        "line_run_epoch_device_bindings",
        ["line_run_epoch_id", "device_role"],
        schema="wes_biz",
    )

    op.add_column(
        "device_commands",
        sa.Column("material_execution_id", sa.Integer(), nullable=True),
        schema="wes_biz",
    )
    op.create_foreign_key(
        "fk_device_commands_material_execution_id_material_executions",
        "device_commands",
        "material_executions",
        ["material_execution_id"],
        ["id"],
        source_schema="wes_biz",
        referent_schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_device_commands_material_execution_id"),
        "device_commands",
        ["material_execution_id"],
        schema="wes_biz",
    )

    for column in (
        sa.Column("decision_digest", sa.String(length=64), nullable=True),
        sa.Column("decision_attempt_count", sa.Integer(), nullable=False),
        sa.Column("decision_next_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("decision_claim_token", sa.String(length=80), nullable=True),
        sa.Column("decision_claim_expires_at", sa.DateTime(), nullable=True),
    ):
        op.add_column("inbound_evidences", column, schema="wes_biz")
    op.create_check_constraint(
        "inbound_evidence_decision_attempt_count_nonnegative",
        "inbound_evidences",
        "decision_attempt_count >= 0",
        schema="wes_biz",
    )
    op.create_check_constraint(
        "inbound_evidence_decision_claim_complete",
        "inbound_evidences",
        "(decision_claim_token IS NULL) = (decision_claim_expires_at IS NULL)",
        schema="wes_biz",
    )
    op.create_check_constraint(
        "inbound_evidence_published_decision_complete",
        "inbound_evidences",
        "published_at IS NULL OR (decision_digest IS NOT NULL AND decision_claim_token IS NULL)",
        schema="wes_biz",
    )
    op.create_index(
        "ix_inbound_evidences_decision_eligible",
        "inbound_evidences",
        ["decision_next_attempt_at", "decision_claim_expires_at", "received_at", "id"],
        schema="wes_biz",
        postgresql_where=sa.text(
            "apply_status = 'APPLIED' AND published_at IS NULL "
            "AND NOT (kind = 'DEVICE_RESULT' AND material_execution_id IS NULL)"
        ),
    )

    op.create_table(
        "inbound_evidence_execution_bindings",
        *_enterprise_columns(),
        sa.Column("inbound_evidence_id", sa.Integer(), nullable=False),
        sa.Column("material_execution_id", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "ordinal >= 0",
            name="inbound_evidence_execution_binding_ordinal_nonnegative",
        ),
        sa.ForeignKeyConstraint(["inbound_evidence_id"], ["wes_biz.inbound_evidences.id"]),
        sa.ForeignKeyConstraint(["material_execution_id"], ["wes_biz.material_executions.id"]),
        sa.UniqueConstraint(
            "inbound_evidence_id",
            "material_execution_id",
            name="ux_inbound_evidence_execution_bindings_evidence_execution",
        ),
        sa.UniqueConstraint(
            "inbound_evidence_id",
            "ordinal",
            name="ux_inbound_evidence_execution_bindings_evidence_ordinal",
        ),
        schema="wes_biz",
    )
    for column in ("inbound_evidence_id", "material_execution_id"):
        op.create_index(
            op.f(f"ix_wes_biz_inbound_evidence_execution_bindings_{column}"),
            "inbound_evidence_execution_bindings",
            [column],
            schema="wes_biz",
        )
    op.create_index(
        op.f("ix_wes_biz_inbound_evidence_execution_bindings_id"),
        "inbound_evidence_execution_bindings",
        ["id"],
        unique=True,
        schema="wes_biz",
    )

    op.create_table(
        "rack_replacement_transport_bindings",
        *_enterprise_columns(),
        sa.Column("rack_replacement_id", sa.String(length=160), nullable=False),
        sa.Column("leg", sa.String(length=20), nullable=False),
        sa.Column("client_request_id", sa.String(length=120), nullable=False),
        sa.Column("source_evidence_id", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "leg IN ('OLD_OUT', 'NEW_IN')",
            name="rack_replacement_transport_binding_leg_valid",
        ),
        sa.ForeignKeyConstraint(["source_evidence_id"], ["wes_biz.inbound_evidences.id"]),
        sa.UniqueConstraint(
            "rack_replacement_id",
            "leg",
            name="ux_rack_replacement_transport_bindings_business_identity",
        ),
        sa.UniqueConstraint(
            "client_request_id",
            name="ux_rack_replacement_transport_bindings_client_request_id",
        ),
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_rack_replacement_transport_bindings_source_evidence_id"),
        "rack_replacement_transport_bindings",
        ["source_evidence_id"],
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_rack_replacement_transport_bindings_id"),
        "rack_replacement_transport_bindings",
        ["id"],
        unique=True,
        schema="wes_biz",
    )


def downgrade() -> None:
    raise NotImplementedError("Phase 8 direct cutover 不支持恢复已替换的 decision processing schema")


def _enterprise_columns() -> tuple[sa.Column[object], ...]:
    return (
        sa.Column("version", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
