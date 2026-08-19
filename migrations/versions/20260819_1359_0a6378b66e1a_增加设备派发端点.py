"""增加设备派发端点

Revision ID: 0a6378b66e1a
Revises: a05b2676f681
Create Date: 2026-08-19 13:59:14.908181+08:00

"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0a6378b66e1a"
down_revision: Union[str, Sequence[str], None] = "a05b2676f681"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add nullable Device Endpoint and required frozen binding Endpoint."""

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM wes_biz.line_run_epochs LIMIT 1) THEN
                RAISE EXCEPTION
                    'Device Endpoint direct cutover requires an empty line_run_epochs table';
            END IF;
        END
        $$
        """
    )
    op.add_column(
        "devices",
        sa.Column("endpoint_base_url", sa.String(length=255), nullable=True),
        schema="wes_biz",
    )
    op.add_column(
        "line_run_epoch_device_bindings",
        sa.Column("endpoint_base_url", sa.String(length=255), nullable=False),
        schema="wes_biz",
    )
    op.create_check_constraint(
        "line_run_epoch_binding_endpoint_nonempty",
        "line_run_epoch_device_bindings",
        "length(endpoint_base_url) > 0",
        schema="wes_biz",
    )


def downgrade() -> None:
    """Remove Device and frozen binding Endpoints."""

    op.drop_constraint(
        "line_run_epoch_binding_endpoint_nonempty",
        "line_run_epoch_device_bindings",
        schema="wes_biz",
        type_="check",
    )
    op.drop_column("line_run_epoch_device_bindings", "endpoint_base_url", schema="wes_biz")
    op.drop_column("devices", "endpoint_base_url", schema="wes_biz")
