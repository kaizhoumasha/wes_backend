"""add workline plugin runtime binding

Revision ID: fa15ba0aef65
Revises: e0d58415afc9
Create Date: 2026-07-17 07:39:35.100100+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "fa15ba0aef65"
down_revision: Union[str, Sequence[str], None] = "e0d58415afc9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

BIZ_SCHEMA = "wes_biz"
RUNTIME_SCHEMA = "wes_runtime"

_PIN_COLUMNS: tuple[tuple[str, str], ...] = (
    (BIZ_SCHEMA, "workline_sessions"),
    (RUNTIME_SCHEMA, "execution_sessions"),
    (RUNTIME_SCHEMA, "execution_work_items"),
)


def upgrade() -> None:
    """创建 immutable binding，并给配置与运行聚合增加同一版本 pin。"""

    op.create_table(
        "workline_plugin_bindings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workline_id", sa.Integer(), nullable=False),
        sa.Column("plugin_key", sa.String(length=100), nullable=False),
        sa.Column("contract_version", sa.String(length=60), nullable=False),
        sa.Column("binding_version", sa.Integer(), nullable=False),
        sa.Column("typed_config_json", sa.JSON(), nullable=False),
        sa.Column("typed_config_hash", sa.String(length=64), nullable=False),
        sa.Column("provider_profile_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("port_requirements_json", sa.JSON(), nullable=False),
        sa.Column("device_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("generated_index_digest", sa.String(length=64), nullable=False),
        sa.Column("environment", sa.String(length=30), nullable=False),
        sa.Column("activated_at", sa.DateTime(), nullable=False),
        sa.Column("activated_by", sa.String(length=100), nullable=False),
        sa.Column("activated_reason", sa.String(length=500), nullable=False),
        sa.Column("valid_from", sa.DateTime(), nullable=True),
        sa.Column("valid_until", sa.DateTime(), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("disabled_at", sa.DateTime(), nullable=True),
        sa.Column("disabled_by", sa.String(length=100), nullable=True),
        sa.Column("disabled_reason", sa.String(length=500), nullable=True),
        sa.CheckConstraint("binding_version >= 1", name="ck_workline_plugin_binding_version_positive"),
        sa.ForeignKeyConstraint(["workline_id"], [f"{BIZ_SCHEMA}.work_lines.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workline_id",
            "plugin_key",
            "contract_version",
            "binding_version",
            name="uq_workline_plugin_binding_identity",
        ),
        schema=BIZ_SCHEMA,
    )
    op.create_index(
        "ix_wes_biz_workline_plugin_bindings_workline_id",
        "workline_plugin_bindings",
        ["workline_id"],
        schema=BIZ_SCHEMA,
    )
    op.create_index(
        "ix_wes_biz_workline_plugin_bindings_plugin_key",
        "workline_plugin_bindings",
        ["plugin_key"],
        schema=BIZ_SCHEMA,
    )
    op.create_index(
        "ix_wes_biz_workline_plugin_bindings_typed_config_hash",
        "workline_plugin_bindings",
        ["typed_config_hash"],
        schema=BIZ_SCHEMA,
    )

    for name, column in (
        ("active_plugin_binding_id", sa.Integer()),
        ("active_plugin_binding_version", sa.Integer()),
        ("active_plugin_config_hash", sa.String(length=64)),
        ("active_plugin_index_digest", sa.String(length=64)),
        ("active_plugin_provider_requirements_json", sa.JSON()),
        ("active_plugin_port_requirements_json", sa.JSON()),
    ):
        default = sa.text("'[]'::json") if name.endswith("requirements_json") else None
        op.add_column(
            "work_lines",
            sa.Column(name, column, nullable=not name.endswith("requirements_json"), server_default=default),
            schema=BIZ_SCHEMA,
        )
    op.create_foreign_key(
        "fk_work_lines_active_plugin_binding",
        "work_lines",
        "workline_plugin_bindings",
        ["active_plugin_binding_id"],
        ["id"],
        source_schema=BIZ_SCHEMA,
        referent_schema=BIZ_SCHEMA,
    )
    op.create_index(
        "ix_wes_biz_work_lines_active_plugin_binding_id",
        "work_lines",
        ["active_plugin_binding_id"],
        schema=BIZ_SCHEMA,
    )

    for schema, table in _PIN_COLUMNS:
        op.add_column(table, sa.Column("plugin_binding_id", sa.Integer(), nullable=True), schema=schema)
        op.add_column(table, sa.Column("plugin_binding_version", sa.Integer(), nullable=True), schema=schema)
        op.add_column(table, sa.Column("plugin_config_hash", sa.String(length=64), nullable=True), schema=schema)
        op.add_column(table, sa.Column("plugin_index_digest", sa.String(length=64), nullable=True), schema=schema)
        op.add_column(
            table,
            sa.Column("plugin_state_json", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False),
            schema=schema,
        )
        op.add_column(
            table,
            sa.Column("plugin_state_version", sa.Integer(), server_default=sa.text("0"), nullable=False),
            schema=schema,
        )
        op.create_foreign_key(
            f"fk_{table}_plugin_binding",
            table,
            "workline_plugin_bindings",
            ["plugin_binding_id"],
            ["id"],
            source_schema=schema,
            referent_schema=BIZ_SCHEMA,
        )
        op.create_index(
            f"ix_{schema}_{table}_plugin_binding_id",
            table,
            ["plugin_binding_id"],
            schema=schema,
        )

    op.add_column(
        "execution_work_items",
        sa.Column("plugin_key", sa.String(length=100), nullable=True),
        schema=RUNTIME_SCHEMA,
    )
    op.create_index(
        "ix_wes_runtime_execution_work_items_plugin_key",
        "execution_work_items",
        ["plugin_key"],
        schema=RUNTIME_SCHEMA,
    )


def downgrade() -> None:
    """只删除本 revision 新增的 binding、pin 和 JSON state。"""

    op.drop_index(
        "ix_wes_runtime_execution_work_items_plugin_key",
        table_name="execution_work_items",
        schema=RUNTIME_SCHEMA,
    )
    op.drop_column("execution_work_items", "plugin_key", schema=RUNTIME_SCHEMA)

    for schema, table in reversed(_PIN_COLUMNS):
        op.drop_index(f"ix_{schema}_{table}_plugin_binding_id", table_name=table, schema=schema)
        op.drop_constraint(f"fk_{table}_plugin_binding", table, schema=schema, type_="foreignkey")
        for column in (
            "plugin_state_version",
            "plugin_state_json",
            "plugin_index_digest",
            "plugin_config_hash",
            "plugin_binding_version",
            "plugin_binding_id",
        ):
            op.drop_column(table, column, schema=schema)

    op.drop_index(
        "ix_wes_biz_work_lines_active_plugin_binding_id",
        table_name="work_lines",
        schema=BIZ_SCHEMA,
    )
    op.drop_constraint("fk_work_lines_active_plugin_binding", "work_lines", schema=BIZ_SCHEMA, type_="foreignkey")
    for column in (
        "active_plugin_port_requirements_json",
        "active_plugin_provider_requirements_json",
        "active_plugin_index_digest",
        "active_plugin_config_hash",
        "active_plugin_binding_version",
        "active_plugin_binding_id",
    ):
        op.drop_column("work_lines", column, schema=BIZ_SCHEMA)

    for index_name in (
        "ix_wes_biz_workline_plugin_bindings_typed_config_hash",
        "ix_wes_biz_workline_plugin_bindings_plugin_key",
        "ix_wes_biz_workline_plugin_bindings_workline_id",
    ):
        op.drop_index(index_name, table_name="workline_plugin_bindings", schema=BIZ_SCHEMA)
    op.drop_table("workline_plugin_bindings", schema=BIZ_SCHEMA)
