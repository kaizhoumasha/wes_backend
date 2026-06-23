"""add object transition events

Revision ID: 194dcb39daf4
Revises: 84c693e1bac9
Create Date: 2026-06-23 02:24:18.135153+08:00

"""

from collections.abc import Sequence
from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "194dcb39daf4"
down_revision: Union[str, Sequence[str], None] = "84c693e1bac9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS wes_biz.object_transition_events (
            id BIGSERIAL PRIMARY KEY,
            domain VARCHAR(50) NOT NULL,
            object_type VARCHAR(100) NOT NULL,
            object_key VARCHAR(300) NOT NULL,
            projection_type VARCHAR(100) NOT NULL,
            from_state VARCHAR(100),
            to_state VARCHAR(100) NOT NULL,
            reason_code VARCHAR(100) NOT NULL,
            source_event_id VARCHAR(200) NOT NULL,
            source_ref_json JSONB NOT NULL DEFAULT '{}'::JSONB,
            evidence_json JSONB NOT NULL DEFAULT '{}'::JSONB,
            workline_session_id BIGINT,
            trace_id VARCHAR(100),
            occurred_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
            idempotency_key TEXT,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
            updated_at TIMESTAMP WITHOUT TIME ZONE
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_object_transition_events_idempotency_key_not_null
        ON wes_biz.object_transition_events (idempotency_key)
        WHERE idempotency_key IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_object_transition_events_trace_occurred
        ON wes_biz.object_transition_events (trace_id, occurred_at)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_object_transition_events_session_occurred
        ON wes_biz.object_transition_events (workline_session_id, occurred_at)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_object_transition_events_object_occurred
        ON wes_biz.object_transition_events (domain, object_type, object_key, occurred_at)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_object_transition_events_domain_source
        ON wes_biz.object_transition_events (domain, source_event_id)
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP INDEX IF EXISTS wes_biz.ix_object_transition_events_domain_source")
    op.execute("DROP INDEX IF EXISTS wes_biz.ix_object_transition_events_object_occurred")
    op.execute("DROP INDEX IF EXISTS wes_biz.ix_object_transition_events_session_occurred")
    op.execute("DROP INDEX IF EXISTS wes_biz.ix_object_transition_events_trace_occurred")
    op.execute("DROP INDEX IF EXISTS wes_biz.uq_object_transition_events_idempotency_key_not_null")
    op.execute("DROP TABLE IF EXISTS wes_biz.object_transition_events")
