"""add return and transfer workline rack position roles

Revision ID: a7e8ad4339e5
Revises: bebf575cca2b
Create Date: 2026-09-09 05:17:52.811744+08:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7e8ad4339e5"
down_revision: str | Sequence[str] | None = "bebf575cca2b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CONSTRAINT = "ck_workline_rack_positions_worklinerackpositionrole"
_OLD_ROLES = (
    "SMT_CLASSIFIER_SINGLE_RACK_WORK",
    "SMT_RACK_EXCHANGE_AREA",
    "SMT_SORTER_QUEUE",
    "SMT_SORTER_STATION",
    "SMT_EMPTY_RACK_AREA",
)


def _replace_constraint(roles: tuple[str, ...]) -> None:
    op.drop_constraint(op.f(_CONSTRAINT), "workline_rack_positions", schema="wes_biz", type_="check")
    values = ", ".join(f"'{role}'" for role in roles)
    op.create_check_constraint(
        op.f(_CONSTRAINT), "workline_rack_positions", f"position_role IN ({values})", schema="wes_biz"
    )


def upgrade() -> None:
    """Allow return and transfer rack positions without rewriting existing rows."""
    _replace_constraint((*_OLD_ROLES, "SMT_RETURN_RACK_POSITION", "SMT_TRANSFER_RACK_POSITION"))
    op.add_column("workline_rack_positions", sa.Column("device_id", sa.BigInteger(), nullable=True), schema="wes_biz")
    op.create_foreign_key(
        op.f("fk_workline_rack_positions_device_id_devices"),
        "workline_rack_positions",
        "devices",
        ["device_id"],
        ["id"],
        source_schema="wes_biz",
        referent_schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_workline_rack_positions_device_id"), "workline_rack_positions", ["device_id"], schema="wes_biz"
    )


def downgrade() -> None:
    """Restore original roles; existing new-role rows deliberately prevent downgrade."""
    # Guard all new data before reverting either constraint or physical-device links.
    connection = op.get_bind()
    if connection.execute(
        sa.text("SELECT 1 FROM wes_biz.workline_rack_positions WHERE device_id IS NOT NULL LIMIT 1")
    ).first():
        raise RuntimeError("Cannot downgrade while rack positions reference physical devices")
    _replace_constraint(_OLD_ROLES)
    op.drop_index(
        op.f("ix_wes_biz_workline_rack_positions_device_id"), table_name="workline_rack_positions", schema="wes_biz"
    )
    op.drop_constraint(
        op.f("fk_workline_rack_positions_device_id_devices"),
        "workline_rack_positions",
        schema="wes_biz",
        type_="foreignkey",
    )
    op.drop_column("workline_rack_positions", "device_id", schema="wes_biz")
