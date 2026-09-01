"""Transport 面向扩为字符串 token

Revision ID: e0da335c057d
Revises: f9c7c2e5f501
Create Date: 2026-09-01 06:42:40.679470+08:00

"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e0da335c057d"
down_revision: Union[str, Sequence[str], None] = "f9c7c2e5f501"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """把既有单字符列原样扩宽为 TEXT。"""
    op.execute("LOCK TABLE wes_runtime.transport_members, wes_biz.position_projections IN ACCESS EXCLUSIVE MODE")
    op.execute(
        """
        DO $$
        BEGIN
            IF (
                SELECT count(*)
                FROM information_schema.columns
                WHERE column_name = 'arrival_face'
                  AND data_type = 'character varying'
                  AND character_maximum_length = 1
                  AND (
                      (table_schema = 'wes_runtime' AND table_name = 'transport_members')
                      OR (table_schema = 'wes_biz' AND table_name = 'position_projections')
                  )
            ) <> 2 THEN
                RAISE EXCEPTION 'arrival_face columns are not the expected VARCHAR(1) baseline';
            END IF;
        END
        $$
        """
    )
    op.alter_column(
        "transport_members",
        "arrival_face",
        schema="wes_runtime",
        existing_type=sa.String(length=1),
        type_=sa.Text(),
        existing_nullable=True,
    )
    op.alter_column(
        "position_projections",
        "arrival_face",
        schema="wes_biz",
        existing_type=sa.String(length=1),
        type_=sa.Text(),
        existing_nullable=True,
    )


def downgrade() -> None:
    """仅在所有既有值可无损恢复时缩回真实基线类型。"""
    op.execute("LOCK TABLE wes_runtime.transport_members, wes_biz.position_projections IN ACCESS EXCLUSIVE MODE")
    op.execute(
        """
        DO $$
        BEGIN
            IF (
                SELECT count(*)
                FROM information_schema.columns
                WHERE column_name = 'arrival_face'
                  AND data_type = 'text'
                  AND (
                      (table_schema = 'wes_runtime' AND table_name = 'transport_members')
                      OR (table_schema = 'wes_biz' AND table_name = 'position_projections')
                  )
            ) <> 2 THEN
                RAISE EXCEPTION 'arrival_face columns are not the expected TEXT head';
            END IF;
            IF EXISTS (
                SELECT 1 FROM wes_runtime.transport_members
                WHERE arrival_face IS NOT NULL AND char_length(arrival_face) > 1
                UNION ALL
                SELECT 1 FROM wes_biz.position_projections
                WHERE arrival_face IS NOT NULL AND char_length(arrival_face) > 1
            ) THEN
                RAISE EXCEPTION 'arrival_face contains values that cannot fit VARCHAR(1)';
            END IF;
        END
        $$
        """
    )
    op.alter_column(
        "position_projections",
        "arrival_face",
        schema="wes_biz",
        existing_type=sa.Text(),
        type_=sa.String(length=1),
        existing_nullable=True,
    )
    op.alter_column(
        "transport_members",
        "arrival_face",
        schema="wes_runtime",
        existing_type=sa.Text(),
        type_=sa.String(length=1),
        existing_nullable=True,
    )
