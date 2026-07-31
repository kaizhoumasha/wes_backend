"""allow external http none auth

Revision ID: 36aa187238cc
Revises: be496b91f3e3
Create Date: 2026-07-29 18:26:09.771798+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "36aa187238cc"
down_revision: Union[str, Sequence[str], None] = "be496b91f3e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """冻结网络信任事实并开放 NONE/HMAC 封闭组合。"""

    op.add_column(
        "system_outbox",
        sa.Column(
            "network_trust_mode",
            sa.String(length=50),
            nullable=True,
            comment="冻结的网络信任事实",
        ),
        schema="wes_biz",
    )
    op.drop_constraint(
        "ck_system_outbox_external_http_frozen_binding",
        "system_outbox",
        schema="wes_biz",
        type_="check",
    )
    op.create_check_constraint(
        "ck_system_outbox_external_http_frozen_binding",
        "system_outbox",
        "dispatch_type != 'EXTERNAL_HTTP' OR "
        "(provider_profile_hash IS NOT NULL AND length(provider_profile_hash) = 64 "
        "AND binding_revision IS NOT NULL AND length(binding_revision) = 64 "
        "AND target_snapshot_json IS NOT NULL "
        "AND target_snapshot_hash IS NOT NULL AND length(target_snapshot_hash) = 64 "
        "AND ((auth_scheme = 'NONE' AND network_trust_mode = 'isolated_lan' "
        "AND credential_reference IS NULL) "
        "OR (auth_scheme = 'HMAC_SHA256' "
        "AND network_trust_mode IN ('isolated_lan', 'authenticated_network') "
        "AND credential_reference IS NOT NULL "
        "AND credential_reference ~ '^[a-z][a-z0-9+.-]*://[^@[:space:]]+@v[1-9][0-9]*$')))",
        schema="wes_biz",
    )


def downgrade() -> None:
    """恢复 HMAC-only 冻结绑定约束。"""

    op.drop_constraint(
        "ck_system_outbox_external_http_frozen_binding",
        "system_outbox",
        schema="wes_biz",
        type_="check",
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
    op.drop_column("system_outbox", "network_trust_mode", schema="wes_biz")
