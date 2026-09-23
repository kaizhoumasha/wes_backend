"""合并计划版本与运行时清理迁移

Revision ID: aa4d58c0be72
Revises: dfd0c2e671d1, 398b9ace6c7b
"""

from collections.abc import Sequence
from typing import Union

revision: str = "aa4d58c0be72"
down_revision: Union[str, Sequence[str], None] = ("dfd0c2e671d1", "398b9ace6c7b")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
