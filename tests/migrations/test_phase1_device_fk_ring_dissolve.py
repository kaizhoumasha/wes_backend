"""AP2 + AP4(slice) device FK 环消解迁移双向测试。

Phase 1 实施: 破坏性迁移消解 P0-004 §4.4 device session FK 环
(device_commands.session_id/session_id_int ↔ workline_sessions.awaiting_command_id
双向 use_alter=True), 改为 DeviceCommand.correlation_id +
workline_sessions.awaiting_device_command_code
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
    """upgrade 必删除旧 FK 约束和 device_commands.session_id/session_id_int 列。"""
    content = MIGRATION_FILE.read_text()
    # 兼容两种模式: 显式 DROP CONSTRAINT IF EXISTS 或动态 pg_constraint 发现
    assert (
        "DROP CONSTRAINT IF EXISTS" in content
        or re.search(r"DROP\s+CONSTRAINT\s+%I", content, re.IGNORECASE) is not None
    ), "upgrade 必须 drop FK 约束 (显式或动态发现)"
    assert "DROP INDEX IF EXISTS" in content
    assert "ix_wes_biz_device_commands_session_id" in content, "drop session_id 前必须先幂等删除旧 index"
    assert re.search(
        r'op\.drop_column\(\s*["\']device_commands["\']\s*,\s*["\']session_id["\']\s*,\s*schema=SCHEMA\s*\)',
        content,
        re.DOTALL,
    ), "DeviceCommand 不应保留旧 session_id 字段"
    assert re.search(
        r'op\.drop_column\(\s*["\']device_commands["\']\s*,\s*["\']session_id_int["\']\s*,\s*schema=SCHEMA\s*\)',
        content,
        re.DOTALL,
    ), "op.drop_column 必须按 table_name, column_name 顺序调用"
    assert "session_id_int" in content


def test_migration_adds_device_command_correlation_id():
    """upgrade 必新增 DeviceCommand.correlation_id 目标态字段和索引。"""
    content = MIGRATION_FILE.read_text()
    assert "correlation_id" in content
    assert re.search(
        r'sa\.Column\(\s*["\']correlation_id["\']\s*,\s*sa\.String\(length=120\)',
        content,
        re.DOTALL,
    ), "DeviceCommand.correlation_id 必须是 VARCHAR(120)"
    assert "ix_wes_biz_device_commands_correlation_id" in content


def test_migration_backfills_awaiting_device_command_code_before_dropping_old_column():
    """upgrade 必先按 awaiting_command_id -> device_commands.command_code 回填等待锚点。"""
    content = MIGRATION_FILE.read_text()
    upgrade = re.search(r"def upgrade.*?(?=\ndef |\Z)", content, re.DOTALL).group(0)

    assert "USING NULL" not in upgrade, "upgrade 不能清空在途 session 的等待锚点"
    assert re.search(
        r'sa\.Column\(\s*["\']awaiting_device_command_code["\']\s*,\s*sa\.String\(length=100\)',
        upgrade,
        re.DOTALL,
    ), "upgrade 必须先新增 awaiting_device_command_code VARCHAR(100)"
    assert "UPDATE" in upgrade and "FROM" in upgrade, "upgrade 必须通过 UPDATE ... FROM 回填"
    assert "ws.awaiting_command_id = dc.id" in upgrade, "upgrade 必须用旧 awaiting_command_id 匹配 device_commands.id"
    assert "awaiting_device_command_code = dc.command_code" in upgrade, (
        "upgrade 必须回填 DeviceCommand.command_code, 不能直接 cast 旧 int"
    )
    assert re.search(
        r'op\.drop_column\(\s*["\']workline_sessions["\']\s*,\s*["\']awaiting_command_id["\']\s*,\s*schema=SCHEMA\s*\)',
        upgrade,
        re.DOTALL,
    ), "回填完成后才能删除旧 awaiting_command_id 列"


def test_migration_drops_old_fk_constraints():
    """upgrade 必删 use_alter 双向 FK 约束。"""
    content = MIGRATION_FILE.read_text()
    assert "fk_device_commands_session_id_int_workline_sessions" in content
    assert "fk_workline_sessions_awaiting_command_id_device_commands" in content
    # drop constraint: 兼容显式 DROP CONSTRAINT IF EXISTS 或动态 pg_constraint 发现
    has_explicit = "DROP CONSTRAINT IF EXISTS" in content
    has_dynamic = re.search(r"DROP\s+CONSTRAINT\s+%I", content, re.IGNORECASE) is not None
    assert has_explicit or has_dynamic, "upgrade 必须 drop FK 约束 (显式或动态发现)"
    assert re.search(
        r"DROP\s+CONSTRAINT\s+(IF\s+EXISTS\s+|\%I)",
        content,
        re.IGNORECASE,
    ), "upgrade 必须用 DROP CONSTRAINT (IF EXISTS 或动态 %I)"


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
    """downgrade 必恢复 awaiting_command_id 并尽量按 command_code 回填旧 int 锚点。"""
    content = MIGRATION_FILE.read_text()
    downgrade = re.search(r"def downgrade.*?(?=\ndef |\Z)", content, re.DOTALL).group(0)
    assert re.search(
        r'sa\.Column\(\s*["\']awaiting_command_id["\']\s*,\s*sa\.Integer\(\)',
        downgrade,
        re.DOTALL,
    ), "downgrade 必须加回 awaiting_command_id INTEGER"
    assert "ws.awaiting_device_command_code = dc.command_code" in downgrade
    assert "awaiting_command_id = dc.id" in downgrade
    assert re.search(
        r'op\.drop_column\(\s*["\']workline_sessions["\']\s*,\s*["\']awaiting_device_command_code["\']\s*,\s*schema=SCHEMA\s*\)',
        downgrade,
        re.DOTALL,
    ), "downgrade 回填后应删除 awaiting_device_command_code"


def test_model_workline_session_has_awaiting_device_command_code():
    """模型 WorklineSession 含 awaiting_device_command_code 字段 (VARCHAR 100)。"""
    from src.app.runtime.orchestration.models.session import WorklineSession

    field = WorklineSession.model_fields.get("awaiting_device_command_code")
    assert field is not None, "WorklineSession 必须含 awaiting_device_command_code 字段"
    assert str(field.annotation).startswith("str"), f"应 str | None, 实际 {field.annotation}"


def test_model_workline_session_no_longer_has_awaiting_command_id():
    """模型 WorklineSession 不再暴露旧 awaiting_command_id 字段。"""
    from src.app.runtime.orchestration.models.session import WorklineSession

    assert "awaiting_command_id" not in WorklineSession.model_fields


def test_model_device_command_no_longer_has_session_id_int():
    """模型 DeviceCommand 不再暴露旧 session_id_int 字段。"""
    from src.app.device.models.command import CommandBase, DeviceCommand

    assert "session_id_int" not in CommandBase.model_fields
    assert "session_id_int" not in DeviceCommand.model_fields


def test_model_device_command_no_longer_has_session_id():
    """模型 DeviceCommand 不再暴露旧 session_id 字段。"""
    from src.app.device.models.command import DeviceCommand

    assert "session_id" not in DeviceCommand.model_fields


def test_model_device_command_has_correlation_id():
    """模型 DeviceCommand 只持跨域 correlation_id, 无 session FK。"""
    from src.app.device.models.command import DeviceCommand

    field = DeviceCommand.model_fields.get("correlation_id")
    assert field is not None, "DeviceCommand 必须含 correlation_id 字段"
    assert str(field.annotation).startswith("str"), f"应 str | None, 实际 {field.annotation}"


def test_migration_has_revision_chain():
    """迁移文件有正确的 revision chain (挂载到 Phase 0 最后一个迁移)。"""
    content = MIGRATION_FILE.read_text()
    assert re.search(r'^revision:\s*str\s*=\s*"0e9de1e6c7e3"', content, re.MULTILINE)
    assert re.search(r'^down_revision:\s*Union\[str,\s*Sequence\[str\],\s*None\]\s*=\s*"', content, re.MULTILINE), (
        "down_revision 必须挂载到前一个迁移"
    )
