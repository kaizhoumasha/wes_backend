"""add workline rack tasks

Revision ID: 083e85d1bf93
Revises: b4685be483de
Create Date: 2026-05-20 14:53:17.194398+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "083e85d1bf93"
down_revision: Union[str, Sequence[str], None] = "b4685be483de"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "wes_biz"


def _data_columns() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(), nullable=False, comment="创建时间 (UTC)"),
        sa.Column("updated_at", sa.DateTime(), nullable=True, comment="更新时间 (UTC)"),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键 ID"),
    ]


def _json_object_column(name: str, *, comment: str) -> sa.Column:
    return sa.Column(
        name,
        postgresql.JSON(astext_type=sa.Text()),
        server_default=sa.text("'{}'::json"),
        nullable=False,
        comment=comment,
    )


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "workline_rack_tasks",
        *_data_columns(),
        sa.Column("task_key", sa.String(length=240), nullable=False, comment="任务幂等键"),
        sa.Column(
            "task_type",
            sa.Enum(
                "RACK_SUPPLY",
                "FULL_BOX_EXCHANGE",
                "MOVE_TO_EMPTY_AREA",
                name="worklineracktasktype",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=False,
            comment="任务类型",
        ),
        sa.Column(
            "task_status",
            sa.Enum(
                "PLANNED",
                "REQUESTED",
                "IN_PROGRESS",
                "SUCCEEDED",
                "FAILED",
                "RECONCILING",
                "CANCELLED",
                name="worklineracktaskstatus",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=False,
            server_default="PLANNED",
            comment="任务状态",
        ),
        sa.Column("workline_id", sa.BigInteger(), nullable=False, comment="关联 WorkLine.id"),
        sa.Column("workline_code", sa.String(length=50), nullable=True, comment="工作线编码"),
        sa.Column("material_session_id", sa.BigInteger(), nullable=True, comment="关联的物料/料盘 Session.id"),
        sa.Column("rack_code", sa.String(length=100), nullable=True, comment="货架编码"),
        sa.Column("position_code", sa.String(length=100), nullable=True, comment="目标位置编码"),
        sa.Column("dispatch_key", sa.String(length=240), nullable=True, comment="外部派发幂等键"),
        sa.Column("outbox_id", sa.BigInteger(), nullable=True, comment="关联 WorklineOutbox.id"),
        sa.Column("target_code", sa.String(length=200), nullable=True, comment="外部目标编码或地址"),
        sa.Column("source_system", sa.String(length=100), nullable=True, comment="外部系统"),
        sa.Column("trace_id", sa.String(length=100), nullable=True, comment="Trace ID"),
        sa.Column("source_event_id", sa.String(length=200), nullable=True, comment="来源事件 ID"),
        sa.Column("source_version", sa.String(length=100), nullable=True, comment="来源版本"),
        _json_object_column("request_json", comment="请求证据"),
        _json_object_column("callback_json", comment="回调证据"),
        _json_object_column("result_json", comment="结果证据"),
        sa.Column("error_code", sa.String(length=100), nullable=True, comment="错误码"),
        sa.Column("error_message", sa.Text(), nullable=True, comment="错误消息"),
        sa.Column("requested_at", sa.DateTime(), nullable=True, comment="请求时间"),
        sa.Column("started_at", sa.DateTime(), nullable=True, comment="开始时间"),
        sa.Column("completed_at", sa.DateTime(), nullable=True, comment="完成时间"),
        sa.ForeignKeyConstraint(["workline_id"], [f"{SCHEMA}.work_lines.id"]),
        sa.ForeignKeyConstraint(["material_session_id"], [f"{SCHEMA}.workline_sessions.id"]),
        sa.ForeignKeyConstraint(["outbox_id"], [f"{SCHEMA}.workline_outbox.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index("ix_wes_biz_workline_rack_tasks_id", "workline_rack_tasks", ["id"], schema=SCHEMA)
    op.create_index("ux_workline_rack_tasks_key", "workline_rack_tasks", ["task_key"], unique=True, schema=SCHEMA)
    op.create_index(
        "ux_workline_rack_tasks_dispatch_key",
        "workline_rack_tasks",
        ["dispatch_key"],
        unique=True,
        schema=SCHEMA,
    )
    for column_name in (
        "task_key",
        "task_type",
        "task_status",
        "workline_id",
        "workline_code",
        "material_session_id",
        "rack_code",
        "position_code",
        "dispatch_key",
        "outbox_id",
        "trace_id",
        "source_event_id",
        "error_code",
        "requested_at",
        "started_at",
        "completed_at",
    ):
        op.create_index(
            f"ix_wes_biz_workline_rack_tasks_{column_name}",
            "workline_rack_tasks",
            [column_name],
            schema=SCHEMA,
        )
    op.create_index(
        "ix_workline_rack_tasks_session_status",
        "workline_rack_tasks",
        ["material_session_id", "task_status"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_workline_rack_tasks_rack_status",
        "workline_rack_tasks",
        ["rack_code", "task_status"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("workline_rack_tasks", schema=SCHEMA)
