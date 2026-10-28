"""人工投料请求创建时冻结本地归属，确定响应随后补记。"""

import sqlalchemy as sa
from alembic import op

revision = "c4b1d8e29f63"
down_revision = "0f1be9a476d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("manual_picking_inbound_batches", "response_evidence_id", nullable=True, schema="wes_biz")


def downgrade() -> None:
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS(SELECT 1 FROM wes_biz.manual_picking_inbound_batches WHERE response_evidence_id IS NULL)"
            )
        )
        .scalar()
    ):
        raise RuntimeError("pending inbound requests exist; downgrade would discard frozen ownership")
    op.alter_column("manual_picking_inbound_batches", "response_evidence_id", nullable=False, schema="wes_biz")
