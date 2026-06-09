"""add workline inbox claim bucket key

Revision ID: 2937b05e1b1c
Revises: fa9a235a48fd
Create Date: 2026-06-09 22:08:21.081415+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2937b05e1b1c"
down_revision: Union[str, Sequence[str], None] = "fa9a235a48fd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "workline_inbox",
        sa.Column("claim_bucket_key", sa.String(length=200), nullable=True),
        schema="wes_biz",
    )
    op.execute(
        sa.text(
            """
            WITH normalized AS (
                SELECT
                    id,
                    CASE
                        WHEN session_id IS NOT NULL THEN 'session:' || session_id::text
                        WHEN device_id IS NOT NULL THEN 'device:' || device_id::text
                        WHEN NULLIF(btrim(payload_json ->> 'device_code'), '') IS NOT NULL
                            THEN 'device_code:' || NULLIF(btrim(payload_json ->> 'device_code'), '')
                        WHEN NULLIF(btrim(payload_json ->> 'location'), '') IS NOT NULL
                            THEN 'device_code:' || NULLIF(btrim(payload_json ->> 'location'), '')
                        WHEN workline_id IS NOT NULL THEN 'workline:' || workline_id::text
                        ELSE 'serial:unknown'
                    END AS raw_claim_bucket_key
                FROM wes_biz.workline_inbox
                WHERE claim_bucket_key IS NULL
            )
            UPDATE wes_biz.workline_inbox AS inbox
            SET claim_bucket_key = CASE
                WHEN length(normalized.raw_claim_bucket_key) <= 200 THEN normalized.raw_claim_bucket_key
                ELSE substring(normalized.raw_claim_bucket_key from 1 for 183)
                    || ':' || substring(md5(normalized.raw_claim_bucket_key) from 1 for 16)
            END
            FROM normalized
            WHERE inbox.id = normalized.id
            """
        )
    )
    null_count = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM wes_biz.workline_inbox WHERE claim_bucket_key IS NULL"))
        .scalar_one()
    )
    if null_count:
        raise RuntimeError(f"workline_inbox.claim_bucket_key backfill left NULL rows: {null_count}")
    op.alter_column(
        "workline_inbox",
        "claim_bucket_key",
        nullable=False,
        schema="wes_biz",
    )
    op.create_index(
        "ix_wes_biz_workline_inbox_hot_claim_bucket_fifo",
        "workline_inbox",
        ["claim_bucket_key", "received_at", "id"],
        unique=False,
        schema="wes_biz",
        postgresql_where=sa.text("status IN ('NEW', 'RETRY', 'PROCESSING')"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_wes_biz_workline_inbox_hot_claim_bucket_fifo",
        table_name="workline_inbox",
        schema="wes_biz",
    )
    op.drop_column("workline_inbox", "claim_bucket_key", schema="wes_biz")
