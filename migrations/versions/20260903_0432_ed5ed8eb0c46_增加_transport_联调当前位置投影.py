"""增加 Transport 联调当前位置投影

Revision ID: ed5ed8eb0c46
Revises: e0da335c057d
Create Date: 2026-09-03 04:32:39.314826+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ed5ed8eb0c46"
down_revision: Union[str, Sequence[str], None] = "e0da335c057d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "transport_debug_position_projections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("object_type", sa.String(length=10), nullable=False),
        sa.Column("object_id", sa.String(length=100), nullable=False),
        sa.Column("position_json", sa.JSON(), nullable=True),
        sa.Column("position_unknown", sa.Boolean(), nullable=False),
        sa.Column("arrival_face", sa.Text(), nullable=True),
        sa.Column("source_operation_id", sa.String(length=36), nullable=False),
        sa.Column("source_transport_task_id", sa.String(length=80), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "object_type IN ('RACK', 'BIN')",
            name=op.f("ck_transport_debug_position_projections_transport_debug_position_projection_object_type_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["source_transport_task_id"],
            ["wes_runtime.transport_tasks.transport_task_id"],
            name=op.f("fk_transport_debug_position_projections_source_transport_task_id_transport_tasks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_transport_debug_position_projections")),
        sa.UniqueConstraint(
            "object_type",
            "object_id",
            name="ux_transport_debug_position_projection_object",
        ),
        schema="wes_runtime",
    )
    op.create_index(
        "ix_transport_debug_position_projection_source_task",
        "transport_debug_position_projections",
        ["source_transport_task_id"],
        unique=False,
        schema="wes_runtime",
    )
    op.execute(
        """
        INSERT INTO wes_runtime.transport_debug_position_projections (
            object_type,
            object_id,
            position_json,
            position_unknown,
            arrival_face,
            source_operation_id,
            source_transport_task_id,
            updated_at
        )
        SELECT DISTINCT ON (member.object_type, member.object_id)
            member.object_type,
            member.object_id,
            member.final_position_json,
            member.position_unknown,
            member.arrival_face,
            member.last_operation_id,
            member.transport_task_id,
            member.updated_at
        FROM wes_runtime.transport_members AS member
        JOIN wes_runtime.transport_tasks AS task
          ON task.transport_task_id = member.transport_task_id
        JOIN wes_runtime.transport_evidence AS evidence
          ON evidence.transport_task_id = member.transport_task_id
         AND evidence.operation_id = member.last_operation_id
        WHERE task.caller_json ->> 'workline_id' = 'TRANSPORT_DEBUG'
          AND task.status IN ('SUCCEEDED', 'FAILED')
          AND member.final_position_json IS NOT NULL
          AND member.position_unknown IS FALSE
          AND member.last_operation_id IS NOT NULL
          AND evidence.operation = 'transport.task.resulted@v1'
          AND evidence.status = 'APPLIED'
        ORDER BY member.object_type, member.object_id, member.updated_at DESC, member.id DESC
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_transport_debug_position_projection_source_task",
        table_name="transport_debug_position_projections",
        schema="wes_runtime",
    )
    op.drop_table("transport_debug_position_projections", schema="wes_runtime")
