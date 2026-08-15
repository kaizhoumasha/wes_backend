"""删除退役插件活动残留

Revision ID: fa685260524f
Revises: ce53af214081
Create Date: 2026-08-15 04:27:16.388269+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "fa685260524f"
down_revision: Union[str, Sequence[str], None] = "ce53af214081"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """删除诊断、RuntimeHold 与 RuntimeIntentLog 的退役插件身份，并收紧 NG reason 来源。"""

    op.drop_column("workline_diagnostics", "plugin_key", schema="wes_biz")
    op.drop_index(
        "ix_wes_biz_runtime_holds_plugin_key",
        table_name="runtime_holds",
        schema="wes_biz",
    )
    op.drop_column("runtime_holds", "plugin_key", schema="wes_biz")
    op.drop_column("runtime_holds", "contract_version", schema="wes_biz")

    op.drop_index(
        "ix_wes_runtime_runtime_intent_logs_plugin_key",
        table_name="runtime_intent_logs",
        schema="wes_runtime",
    )
    op.drop_column("runtime_intent_logs", "plugin_key", schema="wes_runtime")
    op.drop_column("runtime_intent_logs", "plugin_contract_version", schema="wes_runtime")
    op.alter_column(
        "runtime_intent_logs",
        "operation_kind",
        schema="wes_runtime",
        existing_type=sa.String(length=80),
        existing_nullable=False,
        server_default=None,
    )

    for table_name, constraint_name in (
        ("runtime_holds", "ck_runtime_holds_ngreasonsource"),
        ("ng_return_items", "ck_ng_return_items_ngreturnitemngreasonsource"),
    ):
        op.drop_constraint(
            op.f(constraint_name),
            table_name,
            schema="wes_biz",
            type_="check",
        )
        op.create_check_constraint(
            op.f(constraint_name),
            table_name,
            "ng_reason_source IN ('DEVICE_ERROR', 'RUNTIME', 'MANUAL')",
            schema="wes_biz",
        )


def downgrade() -> None:
    """未发布系统不恢复退役插件身份。"""

    raise NotImplementedError("不支持恢复退役插件诊断身份")
