"""persist automatic source service completion handoff

Revision ID: 6680af0fe85b
Revises: de97e0860fd5
Create Date: 2026-10-19 23:56:43.859561+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6680af0fe85b"
down_revision: Union[str, Sequence[str], None] = "de97e0860fd5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 旧交接链缺少成员标记时，无法区分尚未服务和已服务后清理。
    lost = op.get_bind().scalar(
        sa.text("""
        SELECT EXISTS (
            SELECT 1 FROM wes_biz.automatic_picking_return_motions current
            WHERE current.archived_at IS NULL AND current.source_feed_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM wes_biz.automatic_picking_feeds feed
                  WHERE feed.id = current.source_feed_id AND feed.workline_id = current.workline_id
              )
        ) OR EXISTS (
            SELECT 1 FROM wes_biz.automatic_picking_feeds current
            JOIN wes_biz.picking_task_bin_source_racks owner ON owner.id = current.source_member_id
            WHERE current.archived_at IS NULL AND current.inbound_client_request_id IS NOT NULL
              AND EXISTS (
                  SELECT 1 FROM wes_biz.picking_task_bin_source_racks member
                  WHERE member.picking_task_id = current.picking_task_id AND member.rack_id = owner.rack_id
                    AND member.id != owner.id AND member.created_at <= current.created_at
                    AND member.cancelled_evidence_id IS NULL
                    AND NOT EXISTS (
                        SELECT 1 FROM wes_biz.automatic_picking_feeds marker
                        WHERE marker.source_member_id = member.id AND marker.workline_id = current.workline_id
                          AND marker.archived_at IS NULL
                    )
              )
        )
        """)
    )
    if lost:
        raise RuntimeError("cannot recover automatic source service completion from cleaned feed")
    for table in ("automatic_picking_feeds", "automatic_picking_return_motions"):
        op.add_column(
            table,
            sa.Column("completed_source_member_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
            schema="wes_biz",
        )
    # 仅承接仍存在的当前完成事实；不从缺失的历史推断完成。
    op.execute("""
        UPDATE wes_biz.automatic_picking_feeds target
        SET completed_source_member_ids = COALESCE((
            SELECT json_agg(done.source_member_id ORDER BY done.source_member_id)
            FROM wes_biz.automatic_picking_feeds done
            JOIN wes_biz.picking_task_bin_source_racks member ON member.id = done.source_member_id
            JOIN wes_biz.inbound_evidences result ON result.id = done.response_evidence_id
            JOIN wes_biz.picking_task_bin_source_racks owner ON owner.id = target.source_member_id
            WHERE done.workline_id = target.workline_id AND done.picking_task_id = target.picking_task_id
              AND member.rack_id = owner.rack_id AND done.phase = 'FEED_COMPLETE'
              AND result.apply_status = 'APPLIED' AND result.published_at IS NOT NULL
              AND done.archived_at IS NULL
        ), '[]'::json)
    """)
    op.execute("""
        UPDATE wes_biz.automatic_picking_return_motions target
        SET completed_source_member_ids = feed.completed_source_member_ids
        FROM wes_biz.automatic_picking_feeds feed
        WHERE feed.id = target.source_feed_id AND feed.workline_id = target.workline_id
    """)


def downgrade() -> None:
    lost = op.get_bind().scalar(
        sa.text("""
        WITH progress AS (
            SELECT workline_id, completed_source_member_ids, archived_at
            FROM wes_biz.automatic_picking_feeds
            UNION ALL
            SELECT workline_id, completed_source_member_ids, archived_at
            FROM wes_biz.automatic_picking_return_motions
        )
        SELECT EXISTS (
            SELECT 1 FROM progress current,
                json_array_elements_text(current.completed_source_member_ids) AS member_id(value)
            WHERE current.archived_at IS NULL AND NOT EXISTS (
                SELECT 1 FROM wes_biz.automatic_picking_feeds feed
                WHERE feed.source_member_id = member_id.value::bigint
                  AND feed.workline_id = current.workline_id
                  AND feed.phase = 'FEED_COMPLETE' AND feed.response_evidence_id IS NOT NULL
                  AND feed.archived_at IS NULL
            )
        )
    """)
    )
    if lost:
        raise RuntimeError("cannot discard automatic source service completion handoff")
    for table in ("automatic_picking_return_motions", "automatic_picking_feeds"):
        op.drop_column(table, "completed_source_member_ids", schema="wes_biz")
