"""remove workline plugin execution schema

Revision ID: de392f5ff5d0
Revises: a8d9b9eba49b
Create Date: 2026-08-10 22:14:25.824979+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "de392f5ff5d0"
down_revision: Union[str, Sequence[str], None] = "a8d9b9eba49b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """删除已退役的内嵌工作线插件执行 schema，不迁移未发布数据。"""

    def drop_column(schema: str, table: str, column: str) -> None:
        op.execute(f'ALTER TABLE "{schema}"."{table}" DROP COLUMN "{column}" CASCADE')

    for schema, table, version_column in (
        ("wes_biz", "workline_sessions", "contract_version"),
        ("wes_runtime", "execution_sessions", "manifest_version"),
        ("wes_runtime", "execution_work_items", "manifest_version"),
    ):
        for column in (
            "plugin_state_version",
            "plugin_state_json",
            "plugin_index_digest",
            "plugin_config_hash",
            "plugin_binding_version",
            "plugin_binding_id",
            version_column,
            "plugin_key",
        ):
            drop_column(schema, table, column)

    for column in (
        "active_plugin_provider_requirements_json",
        "active_plugin_index_digest",
        "active_plugin_config_hash",
        "active_plugin_binding_version",
        "active_plugin_binding_id",
        "contract_version",
        "plugin_key",
    ):
        drop_column("wes_biz", "work_lines", column)
    op.execute('DROP TABLE "wes_biz"."workline_plugin_bindings"')


def downgrade() -> None:
    raise NotImplementedError("Phase 5 不提供已退役插件执行 schema 的 downgrade")
