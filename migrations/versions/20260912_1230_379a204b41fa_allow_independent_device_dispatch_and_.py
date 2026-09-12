"""allow independent device dispatch and result messages

Revision ID: 379a204b41fa
Revises: b7da7ecdc74b
Create Date: 2026-09-12 12:30:26.692735+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "379a204b41fa"
down_revision: Union[str, Sequence[str], None] = "b7da7ecdc74b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """独立命令可同时领取，同命令不同结果消息各自留存。"""
    op.drop_index("ux_device_commands_dispatching_device", table_name="device_commands", schema="wes_biz")
    op.drop_index("ux_inbound_evidences_device_result", table_name="inbound_evidences", schema="wes_biz")
    op.alter_column(
        "inbound_evidences", "command_code", type_=sa.String(160), existing_type=sa.String(100), schema="wes_biz"
    )


def downgrade() -> None:
    """存量重复结果、并行领取或长身份会拒绝降级，不删除或截断事实。"""
    op.alter_column(
        "inbound_evidences", "command_code", type_=sa.String(100), existing_type=sa.String(160), schema="wes_biz"
    )
    op.create_index(
        "ux_inbound_evidences_device_result",
        "inbound_evidences",
        ["command_code"],
        unique=True,
        schema="wes_biz",
        postgresql_where=sa.text("kind = 'DEVICE_RESULT' AND command_code IS NOT NULL"),
    )
    op.create_index(
        "ux_device_commands_dispatching_device",
        "device_commands",
        ["device_code"],
        unique=True,
        schema="wes_biz",
        postgresql_where=sa.text("status = 'DISPATCHING'"),
    )
