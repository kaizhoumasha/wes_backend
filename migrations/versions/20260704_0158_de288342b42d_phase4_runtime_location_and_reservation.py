"""phase4 runtime location and reservation

Revision ID: de288342b42d
Revises: f88092809f4b
Create Date: 2026-07-04 01:58:09.792158+08:00

"""

# ruff: noqa: S608

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "de288342b42d"
down_revision: Union[str, Sequence[str], None] = "f88092809f4b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "wes_biz"
RESERVATION_TABLE = "workline_bin_cell_reservations"
LOCATION_TABLE = "runtime_location_events"
RESERVATION_STATUS_CONSTRAINT = "bincellreservationstatus"
RESERVATION_STATUS_NAMING_CONVENTION_CONSTRAINT = f"ck_{RESERVATION_TABLE}_{RESERVATION_STATUS_CONSTRAINT}"


def _data_columns() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(), nullable=False, comment="创建时间 (UTC)"),
        sa.Column("updated_at", sa.DateTime(), nullable=True, comment="更新时间 (UTC)"),
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


def _drop_reservation_status_constraint_if_exists() -> None:
    for constraint_name in (
        RESERVATION_STATUS_CONSTRAINT,
        RESERVATION_STATUS_NAMING_CONVENTION_CONSTRAINT,
    ):
        op.execute(
            sa.text(
                f'ALTER TABLE "{SCHEMA}"."{RESERVATION_TABLE}" '
                f'DROP CONSTRAINT IF EXISTS "{constraint_name}"'
            )
        )


def _block_reconciling_reservations_before_downgrade() -> None:
    op.execute(
        sa.text(
            f"""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM "{SCHEMA}"."{RESERVATION_TABLE}"
                    WHERE reservation_status = 'RECONCILING'
                ) THEN
                    RAISE EXCEPTION
                        'Cannot downgrade workline_bin_cell_reservations with RECONCILING reservations';
                END IF;
            END $$;
            """
        )
    )


def upgrade() -> None:
    """Upgrade schema."""

    op.add_column(
        RESERVATION_TABLE,
        sa.Column("correlation_id", sa.String(length=120), nullable=True, comment="跨域 correlation ID"),
        schema=SCHEMA,
    )
    op.add_column(
        RESERVATION_TABLE,
        _json_object_column("evidence_json", comment="预占证据"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_wes_biz_workline_bin_cell_reservations_correlation_id",
        RESERVATION_TABLE,
        ["correlation_id"],
        schema=SCHEMA,
    )

    op.drop_index(
        "ux_workline_bin_cell_reservations_active_cell",
        table_name=RESERVATION_TABLE,
        schema=SCHEMA,
    )
    _drop_reservation_status_constraint_if_exists()
    op.create_check_constraint(
        RESERVATION_STATUS_CONSTRAINT,
        RESERVATION_TABLE,
        "reservation_status IN ('PLANNED', 'CONSUMED', 'RELEASED', 'CANCELLED', 'RECONCILING')",
        schema=SCHEMA,
    )
    op.create_index(
        "ux_workline_bin_cell_reservations_active_cell",
        RESERVATION_TABLE,
        ["bin_code", "bin_cell_index"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("reservation_status IN ('PLANNED', 'RECONCILING')"),
        sqlite_where=sa.text("reservation_status IN ('PLANNED', 'RECONCILING')"),
    )

    op.create_table(
        LOCATION_TABLE,
        *_data_columns(),
        sa.Column("object_type", sa.String(length=80), nullable=False, comment="对象类型"),
        sa.Column("object_key", sa.String(length=300), nullable=False, comment="对象业务键"),
        sa.Column("location_scope", sa.String(length=80), nullable=False, comment="位置作用域"),
        sa.Column("location_code", sa.String(length=300), nullable=False, comment="位置编码"),
        sa.Column("business_step", sa.String(length=120), nullable=False, comment="业务步骤"),
        sa.Column("source", sa.String(length=80), nullable=False, comment="位置事实来源"),
        _json_object_column("evidence_json", comment="位置事实证据"),
        sa.Column("correlation_id", sa.String(length=120), nullable=True, comment="ExecutionCorrelation"),
        sa.Column("source_event_id", sa.String(length=200), nullable=True, comment="来源事件 ID"),
        sa.Column("source_version", sa.String(length=80), nullable=True, comment="来源版本"),
        sa.Column("idempotency_key", sa.Text(), nullable=True, comment="位置事实幂等键"),
        sa.Column("external_reference_type", sa.String(length=100), nullable=True, comment="外部引用类型"),
        sa.Column("external_reference_value", sa.String(length=300), nullable=True, comment="外部引用值"),
        sa.Column("provider_code", sa.String(length=80), nullable=True, comment="provider code"),
        sa.Column("occurred_at", sa.DateTime(), nullable=False, comment="事实发生时间 UTC"),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    for column_name in (
        "object_type",
        "object_key",
        "location_scope",
        "location_code",
        "business_step",
        "source",
        "correlation_id",
        "source_event_id",
        "source_version",
        "external_reference_type",
        "external_reference_value",
        "provider_code",
        "occurred_at",
    ):
        op.create_index(
            f"ix_wes_biz_runtime_location_events_{column_name}",
            LOCATION_TABLE,
            [column_name],
            schema=SCHEMA,
        )
    op.create_index(
        "uq_runtime_location_events_idempotency_key_not_null",
        LOCATION_TABLE,
        ["idempotency_key"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
        sqlite_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_index(
        "ix_runtime_location_events_object_occurred",
        LOCATION_TABLE,
        ["object_type", "object_key", "occurred_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_runtime_location_events_correlation_occurred",
        LOCATION_TABLE,
        ["correlation_id", "occurred_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_runtime_location_events_external_ref",
        LOCATION_TABLE,
        ["provider_code", "external_reference_type", "external_reference_value", "occurred_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_runtime_location_events_source_event",
        LOCATION_TABLE,
        ["source", "source_event_id"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Downgrade schema."""

    _block_reconciling_reservations_before_downgrade()

    op.drop_index("ix_runtime_location_events_source_event", table_name=LOCATION_TABLE, schema=SCHEMA)
    op.drop_index("ix_runtime_location_events_external_ref", table_name=LOCATION_TABLE, schema=SCHEMA)
    op.drop_index("ix_runtime_location_events_correlation_occurred", table_name=LOCATION_TABLE, schema=SCHEMA)
    op.drop_index("ix_runtime_location_events_object_occurred", table_name=LOCATION_TABLE, schema=SCHEMA)
    op.drop_index("uq_runtime_location_events_idempotency_key_not_null", table_name=LOCATION_TABLE, schema=SCHEMA)
    for column_name in (
        "occurred_at",
        "provider_code",
        "external_reference_value",
        "external_reference_type",
        "source_version",
        "source_event_id",
        "correlation_id",
        "source",
        "business_step",
        "location_code",
        "location_scope",
        "object_key",
        "object_type",
    ):
        op.drop_index(f"ix_wes_biz_runtime_location_events_{column_name}", table_name=LOCATION_TABLE, schema=SCHEMA)
    op.drop_table(LOCATION_TABLE, schema=SCHEMA)

    op.drop_index(
        "ux_workline_bin_cell_reservations_active_cell",
        table_name=RESERVATION_TABLE,
        schema=SCHEMA,
    )
    _drop_reservation_status_constraint_if_exists()
    op.create_check_constraint(
        RESERVATION_STATUS_CONSTRAINT,
        RESERVATION_TABLE,
        "reservation_status IN ('PLANNED', 'CONSUMED', 'RELEASED', 'CANCELLED')",
        schema=SCHEMA,
    )
    op.create_index(
        "ux_workline_bin_cell_reservations_active_cell",
        RESERVATION_TABLE,
        ["bin_code", "bin_cell_index"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("reservation_status = 'PLANNED'"),
    )
    op.drop_index(
        "ix_wes_biz_workline_bin_cell_reservations_correlation_id",
        table_name=RESERVATION_TABLE,
        schema=SCHEMA,
    )
    op.drop_column(RESERVATION_TABLE, "evidence_json", schema=SCHEMA)
    op.drop_column(RESERVATION_TABLE, "correlation_id", schema=SCHEMA)
