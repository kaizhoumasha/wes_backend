"""add epoch owner to wms confirmation

Revision ID: 5098dc1b2b63
Revises: 5d3e6e4df5be
Create Date: 2026-09-07 04:27:18.238339+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5098dc1b2b63"
down_revision: Union[str, Sequence[str], None] = "5d3e6e4df5be"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("wms_confirmations", sa.Column("line_run_epoch_id", sa.BigInteger(), nullable=True), schema="wes_biz")
    op.create_foreign_key(
        op.f("fk_wms_confirmations_line_run_epoch_id_line_run_epochs"),
        "wms_confirmations",
        "line_run_epochs",
        ["line_run_epoch_id"],
        ["id"],
        source_schema="wes_biz",
        referent_schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_wms_confirmations_line_run_epoch_id"),
        "wms_confirmations",
        ["line_run_epoch_id"],
        schema="wes_biz",
    )
    op.drop_constraint(
        op.f("ck_wms_confirmations_wms_confirmation_exactly_one_owner"), "wms_confirmations", schema="wes_biz"
    )
    op.create_check_constraint(
        op.f("ck_wms_confirmations_wms_confirmation_exactly_one_owner"),
        "wms_confirmations",
        "(CASE WHEN material_execution_id IS NOT NULL THEN 1 ELSE 0 END + "
        "CASE WHEN bin_execution_id IS NOT NULL THEN 1 ELSE 0 END + "
        "CASE WHEN picking_task_id IS NOT NULL THEN 1 ELSE 0 END + "
        "CASE WHEN line_run_epoch_id IS NOT NULL THEN 1 ELSE 0 END) = 1",
        schema="wes_biz",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_wms_confirmations_wms_confirmation_exactly_one_owner"), "wms_confirmations", schema="wes_biz"
    )
    op.drop_index(
        op.f("ix_wes_biz_wms_confirmations_line_run_epoch_id"), table_name="wms_confirmations", schema="wes_biz"
    )
    op.drop_constraint(
        op.f("fk_wms_confirmations_line_run_epoch_id_line_run_epochs"), "wms_confirmations", schema="wes_biz"
    )
    op.drop_column("wms_confirmations", "line_run_epoch_id", schema="wes_biz")
    op.create_check_constraint(
        op.f("ck_wms_confirmations_wms_confirmation_exactly_one_owner"),
        "wms_confirmations",
        "(CASE WHEN material_execution_id IS NOT NULL THEN 1 ELSE 0 END + "
        "CASE WHEN bin_execution_id IS NOT NULL THEN 1 ELSE 0 END + "
        "CASE WHEN picking_task_id IS NOT NULL THEN 1 ELSE 0 END) = 1",
        schema="wes_biz",
    )
