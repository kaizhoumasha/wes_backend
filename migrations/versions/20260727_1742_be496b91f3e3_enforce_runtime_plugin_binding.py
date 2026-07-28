"""enforce runtime plugin binding

Revision ID: be496b91f3e3
Revises: deb3e0c39e98
Create Date: 2026-07-27 17:42:07.205461+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "be496b91f3e3"
down_revision: Union[str, Sequence[str], None] = "deb3e0c39e98"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

BIZ_SCHEMA = "wes_biz"
RUNTIME_SCHEMA = "wes_runtime"

_PIN_COLUMNS: tuple[tuple[str, str, str, bool, bool], ...] = (
    (BIZ_SCHEMA, "workline_sessions", "contract_version", False, True),
    (RUNTIME_SCHEMA, "execution_sessions", "manifest_version", True, False),
    (RUNTIME_SCHEMA, "execution_work_items", "manifest_version", True, False),
)


def upgrade() -> None:
    """直接建立运行态 binding 必填目标 schema，不处理旧数据。"""

    op.add_column(
        "execution_work_items",
        sa.Column("manifest_version", sa.String(length=60), nullable=False),
        schema=RUNTIME_SCHEMA,
    )
    for schema, table, version_column, _plugin_key_was_nullable, _version_was_nullable in _PIN_COLUMNS:
        op.alter_column(
            table,
            "plugin_key",
            existing_type=sa.String(length=100),
            nullable=False,
            schema=schema,
        )
        op.alter_column(
            table,
            version_column,
            existing_type=sa.String(length=50 if schema == BIZ_SCHEMA else 60),
            nullable=False,
            schema=schema,
        )
        op.alter_column(
            table,
            "plugin_binding_id",
            existing_type=sa.Integer(),
            nullable=False,
            schema=schema,
        )
        op.alter_column(
            table,
            "plugin_binding_version",
            existing_type=sa.Integer(),
            nullable=False,
            schema=schema,
        )
        for column in ("plugin_config_hash", "plugin_index_digest"):
            op.alter_column(
                table,
                column,
                existing_type=sa.String(length=64),
                nullable=False,
                schema=schema,
            )


def downgrade() -> None:
    """撤销 mandatory binding 约束。"""

    for schema, table, version_column, plugin_key_was_nullable, version_was_nullable in reversed(_PIN_COLUMNS):
        for column in ("plugin_index_digest", "plugin_config_hash"):
            op.alter_column(
                table,
                column,
                existing_type=sa.String(length=64),
                nullable=True,
                schema=schema,
            )
        op.alter_column(
            table,
            "plugin_binding_version",
            existing_type=sa.Integer(),
            nullable=True,
            schema=schema,
        )
        op.alter_column(
            table,
            "plugin_binding_id",
            existing_type=sa.Integer(),
            nullable=True,
            schema=schema,
        )
        if table != "execution_work_items" and version_was_nullable:
            op.alter_column(
                table,
                version_column,
                existing_type=sa.String(length=50 if schema == BIZ_SCHEMA else 60),
                nullable=True,
                schema=schema,
            )
        if plugin_key_was_nullable:
            op.alter_column(
                table,
                "plugin_key",
                existing_type=sa.String(length=100),
                nullable=True,
                schema=schema,
            )
    op.drop_column("execution_work_items", "manifest_version", schema=RUNTIME_SCHEMA)
