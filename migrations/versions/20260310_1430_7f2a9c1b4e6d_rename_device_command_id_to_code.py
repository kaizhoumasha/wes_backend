"""rename device command_id to command_code

Revision ID: 7f2a9c1b4e6d
Revises: e78d4187d34d
Create Date: 2026-03-10 14:30:00.000000+08:00

"""

from collections.abc import Sequence
from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7f2a9c1b4e6d"
down_revision: Union[str, Sequence[str], None] = "e78d4187d34d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        "device_commands",
        "command_id",
        new_column_name="command_code",
        schema="wes_biz",
    )
    op.drop_index(
        "ix_wes_biz_device_commands_command_id",
        table_name="device_commands",
        schema="wes_biz",
    )
    op.create_index(
        "ix_wes_biz_device_commands_command_code",
        "device_commands",
        ["command_code"],
        unique=True,
        schema="wes_biz",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_wes_biz_device_commands_command_code",
        table_name="device_commands",
        schema="wes_biz",
    )
    op.alter_column(
        "device_commands",
        "command_code",
        new_column_name="command_id",
        schema="wes_biz",
    )
    op.create_index(
        "ix_wes_biz_device_commands_command_id",
        "device_commands",
        ["command_id"],
        unique=True,
        schema="wes_biz",
    )
