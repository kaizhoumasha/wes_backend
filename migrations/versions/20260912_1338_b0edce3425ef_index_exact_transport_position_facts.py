"""index exact transport position facts

Revision ID: b0edce3425ef
Revises: fa4f7c135230
Create Date: 2026-09-12 13:38:23.755818+08:00

"""

from collections.abc import Sequence
from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b0edce3425ef"
down_revision: Union[str, Sequence[str], None] = "fa4f7c135230"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(
        "ix_transport_members_object_fact",
        "transport_members",
        ["object_type", "object_id", "transport_task_id"],
        schema="wes_runtime",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_transport_members_object_fact", table_name="transport_members", schema="wes_runtime")
