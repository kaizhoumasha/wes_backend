"""add wms business event idempotency index

Revision ID: 7fadfb5469ee
Revises: 46f11dd0a874
Create Date: 2026-07-30 14:17:37.965921+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7fadfb5469ee"
down_revision: Union[str, Sequence[str], None] = "46f11dd0a874"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """普通 WMS event 的 source_system + source_event_id 跨类型唯一。"""

    op.create_index(
        "ux_wes_runtime_runtime_inbox_wms_business_event",
        "runtime_inbox",
        ["provider_code", "source_event_id"],
        unique=True,
        schema="wes_runtime",
        postgresql_where=sa.text(
            "provider_code = 'WMS' AND event_type IN "
            "('WMS_GRN_RECEIVED', 'WMS_PALLET_ARRIVED', "
            "'WMS_INVENTORY_UPDATED', 'WMS_PDA_OPERATION_RECORDED')"
        ),
    )


def downgrade() -> None:
    """移除普通 WMS event 的专用唯一索引。"""

    op.drop_index(
        "ux_wes_runtime_runtime_inbox_wms_business_event",
        table_name="runtime_inbox",
        schema="wes_runtime",
    )
