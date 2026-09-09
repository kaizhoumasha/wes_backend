"""rename workline positions and support generic slots

Revision ID: d11f8c6fdb0d
Revises: a7e8ad4339e5
Create Date: 2026-09-09 09:15:36.259995+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d11f8c6fdb0d"
down_revision: Union[str, Sequence[str], None] = "a7e8ad4339e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _rename_storage(old: str, new: str) -> None:
    """固定迁移名称；PostgreSQL 原地改名保留数据和依赖关系。"""
    op.rename_table(old, new, schema="wes_biz")
    constraints = (
        f"pk_{old}",
        f"fk_{old}_workline_id_work_lines",
        f"fk_{old}_device_id_devices",
        f"ck_{old}_worklinerackpositionrole",
        f"ck_{old}_rackkind",
        f"ck_{old}_ck_{old}_capacity_positive",
    )
    preparer = op.get_bind().dialect.identifier_preparer
    for name in constraints:
        replacement = name.replace(old, new)
        old_name = preparer.truncate_and_render_constraint_name(sa.sql.elements.conv(name))
        new_name = preparer.truncate_and_render_constraint_name(sa.sql.elements.conv(replacement))
        op.execute(sa.text(f"ALTER TABLE wes_biz.{new} RENAME CONSTRAINT {old_name} TO {new_name}"))
    indexes = (
        f"ux_{old}_line_position",
        *(
            f"ix_wes_biz_{old}_{column}"
            for column in (
                "workline_id",
                "workline_code",
                "position_code",
                "logic_location_code",
                "external_location_code",
                "device_role",
                "enabled",
                "device_id",
            )
        ),
    )
    for name in indexes:
        replacement = name.replace(old, new)
        op.execute(sa.text(f'ALTER INDEX wes_biz."{name}" RENAME TO "{replacement}"'))
    op.execute(sa.text(f"ALTER SEQUENCE wes_biz.{old}_id_seq RENAME TO {new}_id_seq"))


def upgrade() -> None:
    """保留已有货架位身份；普通工作位不伪造货架属性。"""
    _rename_storage("workline_rack_positions", "workline_positions")
    op.add_column(
        "workline_positions",
        sa.Column("position_type", sa.String(30), nullable=False, server_default="RACK_POSITION"),
        schema="wes_biz",
    )
    for column in ("position_role", "allowed_rack_kind"):
        op.alter_column("workline_positions", column, nullable=True, schema="wes_biz")
    op.create_check_constraint(
        "ck_workline_positions_resource_type",
        "workline_positions",
        "(position_type = 'RACK_POSITION' AND position_role IS NOT NULL AND allowed_rack_kind IS NOT NULL) OR "
        "(position_type = 'STATION' AND position_role IS NULL AND allowed_rack_kind IS NULL)",
        schema="wes_biz",
    )


def downgrade() -> None:
    """普通工作位无法无损回退成货架位。"""
    if (
        op.get_bind()
        .execute(
            sa.text("SELECT EXISTS (SELECT 1 FROM wes_biz.workline_positions WHERE position_type <> 'RACK_POSITION')")
        )
        .scalar_one()
    ):
        raise RuntimeError("普通工作位仍存在，不能回退货架专属结构")
    op.drop_constraint("ck_workline_positions_resource_type", "workline_positions", schema="wes_biz", type_="check")
    for column in ("position_role", "allowed_rack_kind"):
        op.alter_column("workline_positions", column, nullable=False, schema="wes_biz")
    op.drop_column("workline_positions", "position_type", schema="wes_biz")

    _rename_storage("workline_positions", "workline_rack_positions")
