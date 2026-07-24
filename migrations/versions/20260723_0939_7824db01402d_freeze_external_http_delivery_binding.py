"""freeze external http delivery binding

Revision ID: 7824db01402d
Revises: 2c1407a3606e
Create Date: 2026-07-23 09:39:28.221216+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7824db01402d"
down_revision: Union[str, Sequence[str], None] = "2c1407a3606e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """新增 EXTERNAL_HTTP 非秘密 target、binding 与 credential reference 快照。"""

    op.add_column(
        "system_outbox",
        sa.Column(
            "provider_profile_hash",
            sa.String(length=64),
            nullable=True,
            comment="EXTERNAL_HTTP author-time Provider profile SHA-256",
        ),
        schema="wes_biz",
    )
    op.add_column(
        "system_outbox",
        sa.Column(
            "binding_revision",
            sa.String(length=64),
            nullable=True,
            comment="EXTERNAL_HTTP operation binding revision SHA-256",
        ),
        schema="wes_biz",
    )
    op.add_column(
        "system_outbox",
        sa.Column(
            "target_snapshot_json",
            sa.JSON(),
            nullable=True,
            comment="EXTERNAL_HTTP 完整非秘密 endpoint/target 快照",
        ),
        schema="wes_biz",
    )
    op.add_column(
        "system_outbox",
        sa.Column(
            "target_snapshot_hash",
            sa.String(length=64),
            nullable=True,
            comment="target_snapshot_json 的 SHA-256",
        ),
        schema="wes_biz",
    )
    op.add_column(
        "system_outbox",
        sa.Column(
            "auth_scheme",
            sa.String(length=50),
            nullable=True,
            comment="冻结的封闭出站认证 scheme",
        ),
        schema="wes_biz",
    )
    op.add_column(
        "system_outbox",
        sa.Column(
            "credential_reference",
            sa.String(length=240),
            nullable=True,
            comment="冻结的版本化 credential reference；不包含 secret material",
        ),
        schema="wes_biz",
    )
    op.create_check_constraint(
        "ck_system_outbox_external_http_frozen_binding",
        "system_outbox",
        "dispatch_type != 'EXTERNAL_HTTP' OR "
        "(provider_profile_hash IS NOT NULL AND length(provider_profile_hash) = 64 "
        "AND binding_revision IS NOT NULL AND length(binding_revision) = 64 "
        "AND target_snapshot_json IS NOT NULL "
        "AND target_snapshot_hash IS NOT NULL AND length(target_snapshot_hash) = 64 "
        "AND auth_scheme = 'HMAC_SHA256' "
        "AND credential_reference IS NOT NULL)",
        schema="wes_biz",
    )


def downgrade() -> None:
    """移除 EXTERNAL_HTTP 冻结 target、binding 与 credential reference 快照。"""

    op.drop_constraint(
        "ck_system_outbox_external_http_frozen_binding",
        "system_outbox",
        schema="wes_biz",
        type_="check",
    )
    op.drop_column("system_outbox", "credential_reference", schema="wes_biz")
    op.drop_column("system_outbox", "auth_scheme", schema="wes_biz")
    op.drop_column("system_outbox", "target_snapshot_hash", schema="wes_biz")
    op.drop_column("system_outbox", "target_snapshot_json", schema="wes_biz")
    op.drop_column("system_outbox", "binding_revision", schema="wes_biz")
    op.drop_column("system_outbox", "provider_profile_hash", schema="wes_biz")
