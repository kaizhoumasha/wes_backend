"""add wms evidence gin indexes

Revision ID: 629d8e64eb13
Revises: f04718a3f04f
Create Date: 2026-07-02 09:53:21.286030+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "629d8e64eb13"
down_revision: Union[str, Sequence[str], None] = "f04718a3f04f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "wes_biz"
TABLE = "wms_call_evidence"


def upgrade() -> None:
    """Upgrade schema."""
    for column_name in ("request_snapshot", "response_snapshot"):
        op.alter_column(
            TABLE,
            column_name,
            existing_type=postgresql.JSON(astext_type=sa.Text()),
            existing_nullable=False,
            server_default=None,
            schema=SCHEMA,
        )
    op.alter_column(
        TABLE,
        "request_snapshot",
        existing_type=postgresql.JSON(astext_type=sa.Text()),
        type_=postgresql.JSONB(astext_type=sa.Text()),
        existing_nullable=False,
        postgresql_using="request_snapshot::jsonb",
        schema=SCHEMA,
    )
    op.alter_column(
        TABLE,
        "response_snapshot",
        existing_type=postgresql.JSON(astext_type=sa.Text()),
        type_=postgresql.JSONB(astext_type=sa.Text()),
        existing_nullable=False,
        postgresql_using="response_snapshot::jsonb",
        schema=SCHEMA,
    )
    for column_name in ("request_snapshot", "response_snapshot"):
        op.alter_column(
            TABLE,
            column_name,
            existing_type=postgresql.JSONB(astext_type=sa.Text()),
            existing_nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            schema=SCHEMA,
        )
    op.create_index(
        "ix_wms_call_evidence_request_snapshot_gin",
        TABLE,
        ["request_snapshot"],
        schema=SCHEMA,
        postgresql_using="gin",
    )
    op.create_index(
        "ix_wms_call_evidence_response_snapshot_gin",
        TABLE,
        ["response_snapshot"],
        schema=SCHEMA,
        postgresql_using="gin",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_wms_call_evidence_response_snapshot_gin", table_name=TABLE, schema=SCHEMA)
    op.drop_index("ix_wms_call_evidence_request_snapshot_gin", table_name=TABLE, schema=SCHEMA)
    for column_name in ("request_snapshot", "response_snapshot"):
        op.alter_column(
            TABLE,
            column_name,
            existing_type=postgresql.JSONB(astext_type=sa.Text()),
            existing_nullable=False,
            server_default=None,
            schema=SCHEMA,
        )
    op.alter_column(
        TABLE,
        "response_snapshot",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        type_=postgresql.JSON(astext_type=sa.Text()),
        existing_nullable=False,
        postgresql_using="response_snapshot::json",
        schema=SCHEMA,
    )
    op.alter_column(
        TABLE,
        "request_snapshot",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        type_=postgresql.JSON(astext_type=sa.Text()),
        existing_nullable=False,
        postgresql_using="request_snapshot::json",
        schema=SCHEMA,
    )
    for column_name in ("request_snapshot", "response_snapshot"):
        op.alter_column(
            TABLE,
            column_name,
            existing_type=postgresql.JSON(astext_type=sa.Text()),
            existing_nullable=False,
            server_default=sa.text("'{}'::json"),
            schema=SCHEMA,
        )
