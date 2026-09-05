"""添加工作线插件选择并放开同角色设备

Revision ID: b42147d0d086
Revises: ff5d0af61f91
Create Date: 2026-09-05 11:02:38.979515+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b42147d0d086"
down_revision: Union[str, Sequence[str], None] = "ff5d0af61f91"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM wes_biz.work_lines AS workline
                    LEFT JOIN wes_biz.line_run_epochs AS epoch
                      ON epoch.workline_id = workline.id
                     AND epoch.status = 'ACTIVE'
                    WHERE NOT workline.is_deleted
                    GROUP BY workline.id, workline.is_active
                    HAVING (workline.is_active AND count(epoch.id) <> 1)
                        OR (NOT workline.is_active AND count(epoch.id) <> 0)
                ) THEN
                    RAISE EXCEPTION 'WorkLine active state and ACTIVE Epoch are inconsistent';
                END IF;
            END
            $$;
            """
        )
    )
    op.add_column(
        "work_lines",
        sa.Column("plugin_key", sa.String(length=100), nullable=True),
        schema="wes_biz",
    )
    op.execute(
        sa.text(
            """
            UPDATE wes_biz.work_lines AS workline
            SET plugin_key = epoch.plugin_key
            FROM wes_biz.line_run_epochs AS epoch
            WHERE epoch.workline_id = workline.id
              AND epoch.status = 'ACTIVE'
              AND workline.is_active
              AND NOT workline.is_deleted
            """
        )
    )
    op.create_index("ix_wes_biz_work_lines_plugin_key", "work_lines", ["plugin_key"], unique=False, schema="wes_biz")
    op.drop_constraint(
        "ux_line_run_epoch_device_bindings_epoch_device_role",
        "line_run_epoch_device_bindings",
        schema="wes_biz",
        type_="unique",
    )


def downgrade() -> None:
    """Downgrade schema."""

    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM wes_biz.line_run_epoch_device_bindings
                    GROUP BY line_run_epoch_id, device_role
                    HAVING count(*) > 1
                ) THEN
                    RAISE EXCEPTION
                        'Cannot downgrade: one Epoch already contains multiple devices with the same role';
                END IF;
            END
            $$;
            """
        )
    )
    op.create_unique_constraint(
        "ux_line_run_epoch_device_bindings_epoch_device_role",
        "line_run_epoch_device_bindings",
        ["line_run_epoch_id", "device_role"],
        schema="wes_biz",
    )
    op.drop_index("ix_wes_biz_work_lines_plugin_key", table_name="work_lines", schema="wes_biz")
    op.drop_column("work_lines", "plugin_key", schema="wes_biz")
