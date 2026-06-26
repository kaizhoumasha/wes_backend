"""phase1 device fk ring dissolve

Revision ID: 0e9de1e6c7e3
Revises: 8a1b17cba3db
Create Date: 2026-06-26 12:00:00.000000+08:00

Phase 1 AP2 + AP4(slice) 消解 P0-004 §4.4 device session FK 环:

Phase 0 现状:
- device_commands.session_id_int (INT, FK -> workline_sessions.id, use_alter=True)
- workline_sessions.awaiting_command_id (INT, FK -> device_commands.id, use_alter=True)
- 双向 use_alter=True 形成外键环, 测试库 drop_all 清理顺序 warning

Phase 1 消解 (主计划 §10.2 CEO-010 验证栏, 外部 review 时已落地):
1. device_commands.session_id_int 删除 (command_code 已有幂等键)
2. workline_sessions.awaiting_command_id -> awaiting_device_command_code
   (VARCHAR 100, 引用 device_commands.command_code, 去掉 use_alter FK
   仅保留普通 index; command_code UniqueConstraint 提供幂等性)

data migration: session_id_int (int) 不可直接转 awaiting_device_command_code
(VARCHAR); 迁移填充 NULL + 警告, 需 WES owner 确认完整 data migration 策略。
"""

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
    op.execute(f"ALTER TABLE {SCHEMA}.workline_sessions DROP CONSTRAINT IF EXISTS {OLD_SESSION_FK_NAME}")

    # 2. 清理 device_commands.session_id_int 旧 FK
    op.execute(f"ALTER TABLE {SCHEMA}.device_commands DROP CONSTRAINT IF EXISTS {OLD_DEVICE_FK_NAME}")

    # 3. device_commands.session_id_int 字段删除 (model 同步删除)
    op.drop_column("session_id_int", table_name="device_commands", schema=SCHEMA)

    # 4. workline_sessions.awaiting_command_id -> awaiting_device_command_code
    #    类型 INTEGER -> VARCHAR(100), data migration 全部填 NULL (int -> str 不可转)
    op.execute(
        f"""
        ALTER TABLE {SCHEMA}.workline_sessions
        ALTER COLUMN awaiting_command_id DROP DEFAULT,
        ALTER COLUMN awaiting_command_id TYPE VARCHAR(100) USING NULL,
        ALTER COLUMN awaiting_command_id RENAME TO awaiting_device_command_code
        """
    )

    # 5. 新增 awaiting_device_command_code 普通 index (无 FK, command_code 已有 UniqueConstraint)
    op.create_index(
        "ix_workline_sessions_awaiting_device_command_code",
        "workline_sessions",
        ["awaiting_device_command_code"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Downgrade schema: 反向消解 (回滚到 Phase 0 状态, data loss 接受)。"""
    # 1. 删 index
    op.drop_index(
        "ix_workline_sessions_awaiting_device_command_code",
        table_name="workline_sessions",
        schema=SCHEMA,
    )

    # 2. awaiting_device_command_code -> awaiting_command_id (VARCHAR -> INTEGER, 全 NULL)
    op.execute(
        f"""
        ALTER TABLE {SCHEMA}.workline_sessions
        ALTER COLUMN awaiting_device_command_code TYPE INTEGER USING NULL,
        ALTER COLUMN awaiting_device_command_code RENAME TO awaiting_command_id
        """
    )

    # 3. 加回 device_commands.session_id_int (INT, NULL)
    op.add_column(
        "session_id_int",
        sa.Integer(),
        nullable=True,
        schema=SCHEMA,
        table_name="device_commands",
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
