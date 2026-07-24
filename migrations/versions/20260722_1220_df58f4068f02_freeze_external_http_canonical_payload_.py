"""freeze external http canonical payload bytes

Revision ID: df58f4068f02
Revises: 8fb4b595a85c
Create Date: 2026-07-22 12:20:04.975664+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "df58f4068f02"
down_revision: Union[str, Sequence[str], None] = "8fb4b595a85c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """为 EXTERNAL_HTTP 增加唯一权威的冻结请求体。"""

    op.add_column(
        "system_outbox",
        sa.Column(
            "canonical_payload_bytes",
            sa.LargeBinary(),
            nullable=True,
            comment="EXTERNAL_HTTP 唯一权威的冻结 canonical 请求体",
        ),
        schema="wes_biz",
    )
    op.add_column(
        "system_outbox",
        sa.Column(
            "payload_hash",
            sa.String(length=64),
            nullable=True,
            comment="canonical_payload_bytes 的 SHA-256",
        ),
        schema="wes_biz",
    )
    op.create_check_constraint(
        "ck_system_outbox_external_http_canonical_payload",
        "system_outbox",
        "dispatch_type != 'EXTERNAL_HTTP' OR "
        "(canonical_payload_bytes IS NOT NULL AND length(canonical_payload_bytes) > 0 "
        "AND payload_hash IS NOT NULL AND length(payload_hash) = 64)",
        schema="wes_biz",
    )


def downgrade() -> None:
    """移除 EXTERNAL_HTTP canonical 请求体字段。"""

    op.drop_constraint(
        "ck_system_outbox_external_http_canonical_payload",
        "system_outbox",
        schema="wes_biz",
        type_="check",
    )
    op.drop_column("system_outbox", "payload_hash", schema="wes_biz")
    op.drop_column("system_outbox", "canonical_payload_bytes", schema="wes_biz")
