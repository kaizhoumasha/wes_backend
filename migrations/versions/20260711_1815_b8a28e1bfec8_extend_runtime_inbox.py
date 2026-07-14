"""extend runtime inbox

Revision ID: b8a28e1bfec8
Revises: f0851c5bcfdb
Create Date: 2026-07-11 18:15:25.064764+08:00

本迁移保留 workline_runtime_status_projections 与 bin_transit_memberships
的 runtime_status 所有权，不修改两者结构。
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8a28e1bfec8"
down_revision: Union[str, Sequence[str], None] = "f0851c5bcfdb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

RUNTIME_SCHEMA = "wes_runtime"
PRE_CUTOVER_AUDIT_ONLY = "PRE_CUTOVER_AUDIT_ONLY"
PRE_CUTOVER_AUDIT_MESSAGE = "Pre-cutover RuntimeInbox row has no canonical payload; retained for audit only"

_CONSTRAINTS: tuple[tuple[str, str], ...] = (
    (
        "ck_runtime_inbox_kind_valid",
        "kind IN ('COMMAND_RESULT', 'DEVICE_EVENT', 'EXTERNAL_HTTP', "
        "'INTERNAL_EVENT', 'TIMER_TIMEOUT', 'REPLAY_REQUEST')",
    ),
    (
        "ck_runtime_inbox_status_valid",
        "status IN ('RECEIVED', 'PROCESSING', 'PROCESSED', 'FAILED', 'DEAD_LETTER')",
    ),
    ("ck_runtime_inbox_max_retries_positive", "max_retries >= 1"),
    (
        "ck_runtime_inbox_conditional_envelope",
        f"""
        (
            status = 'DEAD_LETTER'
            AND last_error_code = '{PRE_CUTOVER_AUDIT_ONLY}'
            AND last_error_message IS NOT NULL
            AND received_at IS NOT NULL
            AND failed_at IS NOT NULL
            AND kind IS NULL
            AND workline_id IS NULL
            AND device_id IS NULL
            AND command_id IS NULL
            AND trace_id IS NULL
            AND event_id IS NULL
            AND causation_id IS NULL
            AND payload_json IS NULL
            AND payload_hash IS NULL
            AND payload_schema_version IS NULL
            AND claim_bucket_key IS NULL
            AND processor_token IS NULL
            AND processed_at IS NULL
        )
        OR
        (
            last_error_code IS DISTINCT FROM '{PRE_CUTOVER_AUDIT_ONLY}'
            AND kind IS NOT NULL
            AND provider_code IS NOT NULL
            AND event_type IS NOT NULL
            AND source_event_id IS NOT NULL
            AND payload_json IS NOT NULL
            AND payload_hash IS NOT NULL
            AND payload_schema_version IS NOT NULL
            AND claim_bucket_key IS NOT NULL
            AND received_at IS NOT NULL
        )
        """,
    ),
)


_COLUMNS: tuple[sa.Column, ...] = (
    sa.Column("kind", sa.String(length=40), nullable=True),
    sa.Column("workline_id", sa.Integer(), nullable=True),
    sa.Column("device_id", sa.Integer(), nullable=True),
    sa.Column("command_id", sa.Integer(), nullable=True),
    sa.Column("trace_id", sa.String(length=120), nullable=True),
    sa.Column("event_id", sa.String(length=120), nullable=True),
    sa.Column("causation_id", sa.String(length=120), nullable=True),
    sa.Column("payload_json", sa.JSON(), nullable=True),
    sa.Column("payload_schema_version", sa.Integer(), nullable=True),
    sa.Column("claim_bucket_key", sa.String(length=120), nullable=True),
    sa.Column("processor_token", sa.String(length=80), nullable=True),
    sa.Column("received_at", sa.BigInteger(), nullable=True),
    sa.Column("processed_at", sa.BigInteger(), nullable=True),
    sa.Column("failed_at", sa.BigInteger(), nullable=True),
)

# 热表索引由 Revision C 在 autocommit block 中 CONCURRENTLY 创建。
_INDEXES: tuple[tuple[str, tuple[str, ...], str | None], ...] = ()


def upgrade() -> None:
    """扩展 canonical envelope、claim 字段与 hot-claim indexes。"""
    inspector = sa.inspect(op.get_bind())
    existing_columns = {column["name"] for column in inspector.get_columns("runtime_inbox", schema=RUNTIME_SCHEMA)}
    for column in _COLUMNS:
        if column.name not in existing_columns:
            op.add_column("runtime_inbox", column, schema=RUNTIME_SCHEMA)

    bind = op.get_bind()
    invalid_status_count = bind.execute(
        sa.text(
            """
            SELECT count(*)
            FROM wes_runtime.runtime_inbox
            WHERE status NOT IN ('RECEIVED', 'PROCESSING', 'PROCESSED', 'FAILED', 'DEAD_LETTER')
            """
        )
    ).scalar_one()
    if invalid_status_count:
        raise RuntimeError(f"Revision A cannot classify {invalid_status_count} RuntimeInbox row(s) with invalid status")

    unclassifiable_count = bind.execute(
        sa.text(
            """
            SELECT count(*)
            FROM wes_runtime.runtime_inbox
            WHERE provider_code IS NULL OR btrim(provider_code) = ''
               OR event_type IS NULL OR btrim(event_type) = ''
            """
        )
    ).scalar_one()
    if unclassifiable_count:
        raise RuntimeError(
            f"Revision A cannot safely classify {unclassifiable_count} RuntimeInbox row(s) without source identity"
        )

    # parent row (无 payload_json) ──> 明确 audit-only 终态
    #                              └─> received_at == failed_at == DB 当前 Unix ms
    op.execute(
        sa.text(
            """
            UPDATE wes_runtime.runtime_inbox
            SET status = 'DEAD_LETTER',
                last_error_code = :audit_code,
                last_error_message = :audit_message,
                received_at = floor(extract(epoch FROM statement_timestamp()) * 1000)::bigint,
                failed_at = floor(extract(epoch FROM statement_timestamp()) * 1000)::bigint,
                payload_hash = NULL,
                processor_token = NULL
            WHERE payload_json IS NULL
            """
        ).bindparams(
            audit_code=PRE_CUTOVER_AUDIT_ONLY,
            audit_message=PRE_CUTOVER_AUDIT_MESSAGE,
        )
    )

    inspector = sa.inspect(bind)
    existing_constraints = {
        constraint["name"] for constraint in inspector.get_check_constraints("runtime_inbox", schema=RUNTIME_SCHEMA)
    }
    for name, condition in _CONSTRAINTS:
        if name not in existing_constraints:
            op.create_check_constraint(op.f(name), "runtime_inbox", condition, schema=RUNTIME_SCHEMA)

    inspector = sa.inspect(bind)
    existing_indexes = {index["name"] for index in inspector.get_indexes("runtime_inbox", schema=RUNTIME_SCHEMA)}
    for name, columns, predicate in _INDEXES:
        if name not in existing_indexes:
            op.create_index(
                name,
                "runtime_inbox",
                list(columns),
                schema=RUNTIME_SCHEMA,
                postgresql_where=sa.text(predicate) if predicate is not None else None,
            )


def downgrade() -> None:
    """仅允许 audit-only 行安全回切；canonical payload 不可无损降级。"""
    bind = op.get_bind()
    canonical_row_count = bind.execute(
        sa.text(
            """
            SELECT count(*)
            FROM wes_runtime.runtime_inbox
            WHERE status IS DISTINCT FROM 'DEAD_LETTER'
               OR last_error_code IS DISTINCT FROM 'PRE_CUTOVER_AUDIT_ONLY'
               OR kind IS NOT NULL
               OR workline_id IS NOT NULL
               OR device_id IS NOT NULL
               OR command_id IS NOT NULL
               OR trace_id IS NOT NULL
               OR event_id IS NOT NULL
               OR causation_id IS NOT NULL
               OR payload_json IS NOT NULL
               OR payload_hash IS NOT NULL
               OR payload_schema_version IS NOT NULL
               OR claim_bucket_key IS NOT NULL
               OR processor_token IS NOT NULL
               OR processed_at IS NOT NULL
            """
        )
    ).scalar_one()
    if canonical_row_count:
        raise RuntimeError(
            f"Revision A downgrade refused: {canonical_row_count} canonical RuntimeInbox row(s) would lose data"
        )

    inspector = sa.inspect(bind)
    existing_indexes = {index["name"] for index in inspector.get_indexes("runtime_inbox", schema=RUNTIME_SCHEMA)}
    for name, _columns, _predicate in reversed(_INDEXES):
        if name in existing_indexes:
            op.drop_index(name, table_name="runtime_inbox", schema=RUNTIME_SCHEMA)

    existing_constraints = {
        constraint["name"] for constraint in inspector.get_check_constraints("runtime_inbox", schema=RUNTIME_SCHEMA)
    }
    for name, _condition in reversed(_CONSTRAINTS):
        if name in existing_constraints:
            op.drop_constraint(op.f(name), "runtime_inbox", schema=RUNTIME_SCHEMA, type_="check")

    existing_columns = {column["name"] for column in inspector.get_columns("runtime_inbox", schema=RUNTIME_SCHEMA)}
    for column in reversed(_COLUMNS):
        if column.name in existing_columns:
            op.drop_column("runtime_inbox", column.name, schema=RUNTIME_SCHEMA)
