"""set handling full box completion policy

Revision ID: c5d469c98d89
Revises: 3cf0dc588be9
Create Date: 2026-05-26 15:44:49.447745+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c5d469c98d89"
down_revision: Union[str, Sequence[str], None] = "3cf0dc588be9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    op.execute(
        sa.text(
            """
            UPDATE wes_biz.handling_operations
            SET completion_policy = 'CALLBACK_PLUS_RECONCILIATION'
            WHERE upper(operation_type) LIKE '%FULL_BOX_EXCHANGE%'
            """
        )
    )


def downgrade() -> None:
    """Downgrade schema."""

    op.execute(
        sa.text(
            """
            UPDATE wes_biz.handling_operations
            SET completion_policy = 'CALLBACK_TRUSTED'
            WHERE upper(operation_type) LIKE '%FULL_BOX_EXCHANGE%'
            """
        )
    )
