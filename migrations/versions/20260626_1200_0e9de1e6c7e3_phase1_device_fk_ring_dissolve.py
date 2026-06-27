"""phase1 device fk ring dissolve

Revision ID: 0e9de1e6c7e3
Revises: 8a1b17cba3db
Create Date: 2026-06-26 12:00:00.000000+08:00

Phase 1 AP2 + AP4(slice) 消解 P0-004 §4.4 device session FK 环:

- Phase 0 现状:
- device_commands.session_id (VARCHAR, legacy session trace)
- device_commands.session_id_int (INT, FK -> workline_sessions.id, use_alter=True)
- workline_sessions.awaiting_command_id (INT, FK -> device_commands.id, use_alter=True)
- 双向 use_alter=True 形成外键环, 测试库 drop_all 清理顺序 warning

Phase 1 消解 (主计划 §10.2 CEO-010 验证栏, 外部 review 时已落地):
1. device_commands.session_id/session_id_int 删除, 新增 correlation_id
   (DeviceCommand 只持 ExecutionCorrelation.correlation_id, 无 session FK)
2. workline_sessions.awaiting_command_id -> awaiting_device_command_code
   (VARCHAR 100, 通过旧 device_commands.id 回填 device_commands.command_code,
   去掉 use_alter FK, 仅保留普通 index; command_code UniqueConstraint 提供幂等性)

data migration: 升级时先按 awaiting_command_id -> device_commands.command_code
回填 awaiting_device_command_code, 再删除旧整型 FK 列, 避免在途会话丢失等待锚点。

注意: device_commands.correlation_id 是 Phase 1 全新列, Phase 0 不存在
execution_correlations 表, 因此升级后所有历史 DeviceCommand 行的
correlation_id = NULL。Application 层必须把 NULL 视为 "uninitialized",
不要隐式 join `WHERE dc.correlation_id = ec.correlation_id` 期待 0 行结果。
Phase 1 之后新建的 DeviceCommand 由 caller 通过 device_command_gateway 写入
正确 correlation_id。
"""

# ruff: noqa: S608

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0e9de1e6c7e3"
down_revision: Union[str, Sequence[str], None] = "8a1b17cba3db"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "wes_biz"
OLD_DEVICE_FK_NAME = "fk_device_commands_session_id_int_workline_sessions"
OLD_SESSION_FK_NAME = "fk_workline_sessions_awaiting_command_id_device_commands"


def upgrade() -> None:
    """Upgrade schema: 消解 device ↔ session FK 环。"""
    # 1. 清理 workline_sessions.awaiting_command_id 旧 FK (避免 drop column 冲突)
    #    Phase 0 用 use_alter=True 可能产生不同命名 (workline_sessions_awaiting_command_id_fkey
    #    / fk_workline_sessions_awaiting_command_id_device_commands 等), 因此用 pg_constraint
    #    动态发现所有指向 device_commands 的 FK 并一次性 DROP, 避免硬编码名 silent no-op。
    op.execute(
        f"""
        DO $$
        DECLARE
            c record;
        BEGIN
            FOR c IN
                SELECT conname
                FROM pg_constraint
                WHERE conrelid = '{SCHEMA}.workline_sessions'::regclass
                  AND contype = 'f'
                  AND pg_get_constraintdef(oid) LIKE '%device_commands%'
            LOOP
                EXECUTE format('ALTER TABLE {SCHEMA}.workline_sessions DROP CONSTRAINT %I', c.conname);
            END LOOP;
        END$$;
        """
    )

    # 2. 清理 device_commands.session_id_int 旧 FK (同样用 pg_constraint 动态发现)
    op.execute(
        f"""
        DO $$
        DECLARE
            c record;
        BEGIN
            FOR c IN
                SELECT conname
                FROM pg_constraint
                WHERE conrelid = '{SCHEMA}.device_commands'::regclass
                  AND contype = 'f'
                  AND pg_get_constraintdef(oid) LIKE '%workline_sessions%'
            LOOP
                EXECUTE format('ALTER TABLE {SCHEMA}.device_commands DROP CONSTRAINT %I', c.conname);
            END LOOP;
        END$$;
        """
    )

    # 3. 先把旧 awaiting_command_id 指向的 DeviceCommand.command_code 回填到目标列。
    #    之后才能删除 device_commands.session_id/session_id_int,
    #    但 device_commands.id/command_code 需保留。
    op.add_column(
        "workline_sessions",
        sa.Column("awaiting_device_command_code", sa.String(length=100), nullable=True),
        schema=SCHEMA,
    )
    op.execute(
        f"""
        UPDATE {SCHEMA}.workline_sessions AS ws
        SET awaiting_device_command_code = dc.command_code
        FROM {SCHEMA}.device_commands AS dc
        WHERE ws.awaiting_command_id = dc.id
        """
    )

    # 4. 删除 device_commands 旧 session FK 字段，改用 correlation_id。
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.ix_wes_biz_device_commands_session_id")
    op.drop_column("device_commands", "session_id", schema=SCHEMA)
    op.drop_column("device_commands", "session_id_int", schema=SCHEMA)
    op.add_column(
        "device_commands",
        sa.Column("correlation_id", sa.String(length=120), nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_wes_biz_device_commands_correlation_id",
        "device_commands",
        ["correlation_id"],
        schema=SCHEMA,
    )

    # 5. 删除旧 awaiting_command_id 整型 FK 列。
    op.drop_column("workline_sessions", "awaiting_command_id", schema=SCHEMA)

    # 6. 新增 awaiting_device_command_code 普通 index (无 FK, command_code 已有 UniqueConstraint)
    op.create_index(
        "ix_workline_sessions_awaiting_device_command_code",
        "workline_sessions",
        ["awaiting_device_command_code"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Downgrade schema: 反向消解 (回滚到 Phase 0 状态, 尽量按 command_code 回填旧锚点)。"""
    # 1. 删 index
    op.drop_index(
        "ix_wes_biz_device_commands_correlation_id",
        table_name="device_commands",
        schema=SCHEMA,
    )
    op.drop_column("device_commands", "correlation_id", schema=SCHEMA)
    op.drop_index(
        "ix_workline_sessions_awaiting_device_command_code",
        table_name="workline_sessions",
        schema=SCHEMA,
    )

    # 2. 恢复 awaiting_command_id, 按 awaiting_device_command_code -> device_commands.id 尽量回填。
    op.add_column(
        "workline_sessions",
        sa.Column("awaiting_command_id", sa.Integer(), nullable=True),
        schema=SCHEMA,
    )
    op.execute(
        f"""
        UPDATE {SCHEMA}.workline_sessions AS ws
        SET awaiting_command_id = dc.id
        FROM {SCHEMA}.device_commands AS dc
        WHERE ws.awaiting_device_command_code = dc.command_code
        """
    )
    op.drop_column("workline_sessions", "awaiting_device_command_code", schema=SCHEMA)

    # 3. 加回 device_commands.session_id/session_id_int (legacy, NULL)
    op.add_column(
        "device_commands",
        sa.Column("session_id", sa.String(length=100), nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_wes_biz_device_commands_session_id",
        "device_commands",
        ["session_id"],
        schema=SCHEMA,
    )
    op.add_column(
        "device_commands",
        sa.Column("session_id_int", sa.Integer(), nullable=True),
        schema=SCHEMA,
    )

    # 4. 加回 use_alter 双向 FK
    op.execute(
        f"ALTER TABLE {SCHEMA}.device_commands ADD CONSTRAINT {OLD_DEVICE_FK_NAME} "
        f"FOREIGN KEY (session_id_int) REFERENCES {SCHEMA}.workline_sessions(id) "
        f"DEFERRABLE INITIALLY DEFERRED"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.workline_sessions ADD CONSTRAINT {OLD_SESSION_FK_NAME} "
        f"FOREIGN KEY (awaiting_command_id) REFERENCES {SCHEMA}.device_commands(id) "
        f"DEFERRABLE INITIALLY DEFERRED"
    )
