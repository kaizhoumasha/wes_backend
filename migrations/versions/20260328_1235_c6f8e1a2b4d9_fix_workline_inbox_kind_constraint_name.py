"""fix workline inbox kind constraint name

Revision ID: c6f8e1a2b4d9
Revises: 9b1a6a4f2d3e
Create Date: 2026-03-28 12:35:00+08:00

"""

from collections.abc import Sequence
from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c6f8e1a2b4d9"
down_revision: Union[str, Sequence[str], None] = "9b1a6a4f2d3e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _recreate_inbox_kind_constraint(*, include_command_result: bool) -> None:
    allowed_kinds = [
        "DEVICE_EVENT",
        "EXTERNAL_HTTP",
        "TIMER_TIMEOUT",
        "MANUAL_HOLD",
        "MANUAL_RESUME",
        "MANUAL_CANCEL",
        "REPLAY_REQUEST",
    ]
    if include_command_result:
        allowed_kinds.insert(1, "COMMAND_RESULT")

    allowed_kinds_sql = ",\n                ".join(f"'{kind}'" for kind in allowed_kinds)

    op.execute(
        """
        ALTER TABLE wes_biz.workline_inbox
        DROP CONSTRAINT IF EXISTS inboxkind
        """
    )
    op.execute(
        """
        ALTER TABLE wes_biz.workline_inbox
        DROP CONSTRAINT IF EXISTS ck_workline_inbox_inboxkind
        """
    )
    op.execute(
        f"""
        ALTER TABLE wes_biz.workline_inbox
        ADD CONSTRAINT ck_workline_inbox_inboxkind
        CHECK (
            kind IN (
                {allowed_kinds_sql}
            )
        )
        """
    )


def upgrade() -> None:
    """Upgrade schema."""
    _recreate_inbox_kind_constraint(include_command_result=True)


def downgrade() -> None:
    """Downgrade schema."""
    _recreate_inbox_kind_constraint(include_command_result=False)
