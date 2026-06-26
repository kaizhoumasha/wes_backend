"""AP2 + AP4(slice) device FK 环消解迁移双向测试。

Phase 1 实施: 破坏性迁移消解 P0-004 §4.4 device session FK 环
(device_commands.session_id_int ↔ workline_sessions.awaiting_command_id
双向 use_alter=True), 改为 workline_sessions.awaiting_device_command_code
(VARCHAR, 引用 device_commands.command_code, 无强 FK)。

测试不依赖真实数据库 schema (避免 CI Alembic 启动慢); 改为检查
迁移文件的 upgrade/downgrade 必含关键 schema change (regex 文本验证),
以及模型的字段定义 (import 时检查)。
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_FILE = REPO_ROOT / "migrations" / "versions" / "20260626_1200_0e9de1e6c7e3_phase1_device_fk_ring_dissolve.py"


def test_migration_file_exists():
    """迁移文件存在。"""
    assert MIGRATION_FILE.exists(), f"迁移文件缺失: {MIGRATION_FILE}"


def test_migration_drops_old_device_fk():
    """upgrade 必删 device_commands.session_id_int 的旧 FK。"""
    content = MIGRATION_FILE.read_text()
    # drop column session_id_int
    assert re.search(r"drop_column\([^)]*session_id_int", content) or re.search(
        r"DROP\s+COLUMN[^;]*session_id_int", content, re.IGNORECASE
    ), "upgrade 必须 drop device_commands.session_id_int 字段"


def test_migration_renames_awaiting_command_id_to_code():
    """upgrade 必把 awaiting_command_id 改为 awaiting_device_command_code (VARCHAR)。"""
    content = MIGRATION_FILE.read_text()
    # RENAME TO awaiting_device_command_code (不是 awaiting_command_id_code)
    assert re.search(r"RENAME\s+TO\s+awaiting_device_command_code\b", content), (
        "迁移必须 RENAME 字段到 awaiting_device_command_code"
    )
    assert "VARCHAR(100)" in content, "awaiting_device_command_code 必须是 VARCHAR(100)"


def test_migration_drops_old_fk_constraints():
    """upgrade 必删 use_alter 双向 FK 约束。"""
    content = MIGRATION_FILE.read_text()
    assert "fk_device_commands_session_id_int_workline_sessions" in content
    assert "fk_workline_sessions_awaiting_command_id_device_commands" in content
    # drop constraint IF EXISTS
    assert "DROP CONSTRAINT IF EXISTS" in content, "upgrade 必须 drop FK 约束"
    assert re.search(
        r"DROP\s+CONSTRAINT\s+IF\s+EXISTS\s+",
        content,
        re.IGNORECASE,
    ), "upgrade 必须用 DROP CONSTRAINT IF EXISTS"


def test_migration_downgrade_restores_original_fk():
    """downgrade 必加回双向 use_alter FK 约束 (Phase 0 状态)。"""
    content = MIGRATION_FILE.read_text()
    downgrade = re.search(r"def downgrade.*?(?=\ndef |\Z)", content, re.DOTALL)
    assert downgrade, "downgrade() 函数缺失"
    body = downgrade.group(0)
    # FK 名通过常量引用 f-string, 不字面出现在源码; 验证常量 + ADD CONSTRAINT 模板
    assert "OLD_DEVICE_FK_NAME" in body and "OLD_SESSION_FK_NAME" in body, "downgrade 必须引用 FK 名常量"
    assert "ADD CONSTRAINT" in body, "downgrade 必须 ADD CONSTRAINT 加回 FK"
    # DEFERRABLE INITIALLY DEFERRED (use_alter 等价)
    assert "DEFERRABLE INITIALLY DEFERRED" in body


def test_migration_downgrade_restores_int_type():
    """downgrade 必把 awaiting_device_command_code 改回 INTEGER (VARCHAR->INT USING NULL)。"""
    content = MIGRATION_FILE.read_text()
    downgrade = re.search(r"def downgrade.*?(?=\ndef |\Z)", content, re.DOTALL).group(0)
    assert "TYPE INTEGER USING NULL" in downgrade or "TYPE INTEGER" in downgrade


def test_model_workline_session_has_awaiting_device_command_code():
    """模型 WorklineSession 含 awaiting_device_command_code 字段 (VARCHAR 100)。"""
    from src.app.workline.models.session import WorklineSession

    field = WorklineSession.model_fields.get("awaiting_device_command_code")
    assert field is not None, "WorklineSession 必须含 awaiting_device_command_code 字段"
    assert str(field.annotation).startswith("str"), f"应 str | None, 实际 {field.annotation}"


def test_model_workline_session_no_longer_has_awaiting_command_id():
    """模型 WorklineSession 不再有旧 awaiting_command_id (int FK 字段已消解)。"""
    from src.app.workline.models.session import WorklineSession

    assert "awaiting_command_id" not in WorklineSession.model_fields, (
        "WorklineSession 不应再有 awaiting_command_id int FK 字段"
    )


def test_model_device_command_no_longer_has_session_id_int():
    """模型 DeviceCommand 不再有旧 session_id_int (int FK 字段已消解)。"""
    from src.app.device.models.command import CommandBase

    assert "session_id_int" not in CommandBase.model_fields, "DeviceCommand 不应再有 session_id_int int FK 字段"


def test_migration_has_revision_chain():
    """迁移文件有正确的 revision chain (挂载到 Phase 0 最后一个迁移)。"""
    content = MIGRATION_FILE.read_text()
    assert re.search(r'^revision:\s*str\s*=\s*"0e9de1e6c7e3"', content, re.MULTILINE)
    assert re.search(r'^down_revision:\s*Union\[str,\s*Sequence\[str\],\s*None\]\s*=\s*"', content, re.MULTILINE), (
        "down_revision 必须挂载到前一个迁移"
    )
