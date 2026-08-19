"""为运行代际冻结配置快照

Revision ID: 53e560430c1a
Revises: ec18b2a79400
Create Date: 2026-08-19 08:04:05.665017+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "53e560430c1a"
down_revision: Union[str, Sequence[str], None] = "ec18b2a79400"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """冻结完整配置快照；未发布系统不承接既有 Epoch 数据。"""

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM wes_biz.line_run_epochs LIMIT 1) THEN
                RAISE EXCEPTION
                    'LineRunEpoch 存在开发期数据；配置快照 direct cutover 要求清空后重建 Epoch';
            END IF;
        END
        $$
        """
    )
    op.add_column(
        "line_run_epochs",
        sa.Column("configuration_snapshot_json", sa.JSON(), nullable=False),
        schema="wes_biz",
    )


def downgrade() -> None:
    """移除尚未发布的配置快照列。"""

    op.drop_column("line_run_epochs", "configuration_snapshot_json", schema="wes_biz")
