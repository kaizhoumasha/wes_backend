"""handling bin transit membership

Revision ID: 8a1b17cba3db
Revises: 9b660037b4bb
Create Date: 2026-06-23 04:06:29.772320+08:00

"""

from collections.abc import Sequence
from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8a1b17cba3db"
down_revision: Union[str, Sequence[str], None] = "9b660037b4bb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS wes_biz.bin_transit_memberships (
            id BIGSERIAL PRIMARY KEY,
            bin_code VARCHAR(100),
            placeholder_key VARCHAR(240),
            workline_id BIGINT,
            workline_code VARCHAR(50),
            current_queue VARCHAR(80) NOT NULL,
            membership_status VARCHAR(50) NOT NULL DEFAULT 'ACTIVE',
            handling_operation_id BIGINT,
            handling_move_id BIGINT,
            trace_id VARCHAR(100),
            workline_session_id BIGINT,
            entered_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
            left_at TIMESTAMP WITHOUT TIME ZONE,
            evidence_json JSONB NOT NULL DEFAULT '{}'::JSONB,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
            updated_at TIMESTAMP WITHOUT TIME ZONE,
            CONSTRAINT bintransitqueue CHECK (
                current_queue IN (
                    'INFEED_BUFFER_QUEUE',
                    'ENTRY_SCAN_QUEUE',
                    'WORKSTATION_WAIT_QUEUE',
                    'WORKSTATION_ACTIVE',
                    'EXIT_ROUTING_SCAN_QUEUE',
                    'RETURN_SCAN_QUEUE',
                    'RETURN_WAIT_QUEUE',
                    'NG_REJECT_QUEUE'
                )
            ),
            CONSTRAINT bintransitmembershipstatus CHECK (
                membership_status IN ('ACTIVE', 'LEFT', 'RECONCILING')
            ),
            CONSTRAINT fk_bin_transit_memberships_workline_id_work_lines
                FOREIGN KEY (workline_id) REFERENCES wes_biz.work_lines(id),
            CONSTRAINT fk_btm_workline_session
                FOREIGN KEY (workline_session_id) REFERENCES wes_biz.workline_sessions(id),
            CONSTRAINT fk_btm_handling_operation
                FOREIGN KEY (handling_operation_id) REFERENCES wes_biz.handling_operations(id),
            CONSTRAINT fk_btm_handling_move
                FOREIGN KEY (handling_move_id) REFERENCES wes_biz.handling_operation_moves(id)
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_bin_transit_memberships_active_bin
        ON wes_biz.bin_transit_memberships (bin_code)
        WHERE bin_code IS NOT NULL AND left_at IS NULL
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_bin_transit_memberships_active_placeholder
        ON wes_biz.bin_transit_memberships (placeholder_key)
        WHERE placeholder_key IS NOT NULL AND left_at IS NULL
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_bin_transit_memberships_workline_queue
        ON wes_biz.bin_transit_memberships (workline_id, current_queue)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_bin_transit_memberships_session_entered
        ON wes_biz.bin_transit_memberships (workline_session_id, entered_at)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_bin_transit_memberships_trace_entered
        ON wes_biz.bin_transit_memberships (trace_id, entered_at)
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP INDEX IF EXISTS wes_biz.ix_bin_transit_memberships_trace_entered")
    op.execute("DROP INDEX IF EXISTS wes_biz.ix_bin_transit_memberships_session_entered")
    op.execute("DROP INDEX IF EXISTS wes_biz.ix_bin_transit_memberships_workline_queue")
    op.execute("DROP INDEX IF EXISTS wes_biz.ux_bin_transit_memberships_active_placeholder")
    op.execute("DROP INDEX IF EXISTS wes_biz.ux_bin_transit_memberships_active_bin")
    op.execute("DROP TABLE IF EXISTS wes_biz.bin_transit_memberships")
