"""add_log_center_fields

Revision ID: a1b2c3d4e5f6
Revises: 3b7c9d2e4f11
Create Date: 2026-04-13 21:30:00.000000+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "3b7c9d2e4f11"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """为日志中心补齐审计稳定维度与索引。"""
    op.execute(
        """
        ALTER TABLE wes_sys.audit_logs
        ALTER COLUMN args TYPE jsonb
        USING CASE
            WHEN args IS NULL THEN NULL
            ELSE args::jsonb
        END
        """
    )

    op.add_column("audit_logs", sa.Column("object_type", sa.String(length=100), nullable=True), schema="wes_sys")
    op.add_column("audit_logs", sa.Column("action", sa.String(length=50), nullable=True), schema="wes_sys")
    op.add_column("audit_logs", sa.Column("object_id", sa.String(length=64), nullable=True), schema="wes_sys")
    op.add_column("audit_logs", sa.Column("change_summary", sa.String(length=255), nullable=True), schema="wes_sys")

    op.execute(
        """
        UPDATE wes_sys.audit_logs
        SET
            object_type = NULLIF(BTRIM(args ->> 'model'), ''),
            action = NULLIF(BTRIM(args ->> 'operation'), ''),
            object_id = NULLIF(BTRIM(args ->> 'record_id'), ''),
            change_summary = CASE
                WHEN NULLIF(BTRIM(args ->> 'operation'), '') = 'update'
                     AND jsonb_typeof(args -> 'changes') = 'object'
                     AND EXISTS (
                         SELECT 1
                         FROM jsonb_object_keys(args -> 'changes') AS key
                     )
                THEN CONCAT(
                    '更新字段：',
                    (
                        SELECT string_agg(key, '、' ORDER BY key)
                        FROM (
                            SELECT key
                            FROM jsonb_object_keys(args -> 'changes') AS key
                            ORDER BY key
                            LIMIT 3
                        ) AS top_keys
                    ),
                    CASE
                        WHEN (
                            SELECT COUNT(*)
                            FROM jsonb_object_keys(args -> 'changes') AS key
                        ) > 3
                        THEN CONCAT(
                            ' 等 ',
                            (
                                SELECT COUNT(*)
                                FROM jsonb_object_keys(args -> 'changes') AS key
                            ),
                            ' 个字段'
                        )
                        ELSE ''
                    END
                )
                WHEN NULLIF(BTRIM(args ->> 'operation'), '') = 'create'
                     AND jsonb_typeof(args -> 'changes') = 'object'
                     AND EXISTS (
                         SELECT 1
                         FROM jsonb_object_keys(args -> 'changes') AS key
                     )
                THEN CONCAT(
                    '创建记录，写入字段：',
                    (
                        SELECT string_agg(key, '、' ORDER BY key)
                        FROM (
                            SELECT key
                            FROM jsonb_object_keys(args -> 'changes') AS key
                            ORDER BY key
                            LIMIT 3
                        ) AS top_keys
                    ),
                    CASE
                        WHEN (
                            SELECT COUNT(*)
                            FROM jsonb_object_keys(args -> 'changes') AS key
                        ) > 3
                        THEN CONCAT(
                            ' 等 ',
                            (
                                SELECT COUNT(*)
                                FROM jsonb_object_keys(args -> 'changes') AS key
                            ),
                            ' 个字段'
                        )
                        ELSE ''
                    END
                )
                WHEN NULLIF(BTRIM(args ->> 'operation'), '') = 'delete'
                     AND jsonb_typeof(args -> 'changes') = 'object'
                     AND EXISTS (
                         SELECT 1
                         FROM jsonb_object_keys(args -> 'changes') AS key
                     )
                THEN CONCAT(
                    '删除记录，保留快照字段：',
                    (
                        SELECT string_agg(key, '、' ORDER BY key)
                        FROM (
                            SELECT key
                            FROM jsonb_object_keys(args -> 'changes') AS key
                            ORDER BY key
                            LIMIT 3
                        ) AS top_keys
                    ),
                    CASE
                        WHEN (
                            SELECT COUNT(*)
                            FROM jsonb_object_keys(args -> 'changes') AS key
                        ) > 3
                        THEN CONCAT(
                            ' 等 ',
                            (
                                SELECT COUNT(*)
                                FROM jsonb_object_keys(args -> 'changes') AS key
                            ),
                            ' 个字段'
                        )
                        ELSE ''
                    END
                )
                WHEN NULLIF(BTRIM(args ->> 'operation'), '') = 'create' THEN '创建记录'
                WHEN NULLIF(BTRIM(args ->> 'operation'), '') = 'update' THEN '更新记录'
                WHEN NULLIF(BTRIM(args ->> 'operation'), '') = 'delete' THEN '删除记录'
                WHEN NULLIF(BTRIM(args ->> 'operation'), '') = 'read' THEN '读取记录'
                ELSE NULL
            END
        WHERE args IS NOT NULL
        """
    )

    op.create_index(
        "ix_audit_log_object_type_opera_time",
        "audit_logs",
        ["object_type", "opera_time"],
        unique=False,
        schema="wes_sys",
    )
    op.create_index(
        "ix_audit_log_action_opera_time",
        "audit_logs",
        ["action", "opera_time"],
        unique=False,
        schema="wes_sys",
    )
    op.create_index(
        "ix_audit_log_status_opera_time",
        "audit_logs",
        ["status", "opera_time"],
        unique=False,
        schema="wes_sys",
    )


def downgrade() -> None:
    """回滚日志中心审计字段增强。"""
    op.drop_index("ix_audit_log_status_opera_time", table_name="audit_logs", schema="wes_sys")
    op.drop_index("ix_audit_log_action_opera_time", table_name="audit_logs", schema="wes_sys")
    op.drop_index("ix_audit_log_object_type_opera_time", table_name="audit_logs", schema="wes_sys")

    op.drop_column("audit_logs", "change_summary", schema="wes_sys")
    op.drop_column("audit_logs", "object_id", schema="wes_sys")
    op.drop_column("audit_logs", "action", schema="wes_sys")
    op.drop_column("audit_logs", "object_type", schema="wes_sys")

    op.execute(
        """
        ALTER TABLE wes_sys.audit_logs
        ALTER COLUMN args TYPE json
        USING CASE
            WHEN args IS NULL THEN NULL
            ELSE args::json
        END
        """
    )
