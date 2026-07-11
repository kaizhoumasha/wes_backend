"""RuntimeInbox processor 生产所有权边界。"""

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
_LEGACY_PROCESSOR = _SRC / "app/runtime/orchestration/services/inbox/inbox_batch_processor.py"


def test_legacy_inbox_batch_processor_surface_is_physically_removed() -> None:
    """未发布项目不保留旧 processor 文件、shim 或空壳入口。"""
    assert not _LEGACY_PROCESSOR.exists()


def test_active_source_does_not_reference_legacy_inbox_batch_processor() -> None:
    """生产代码只能依赖 RuntimeInbox-owned processor 表面。"""
    violations: list[str] = []
    for path in _SRC.rglob("*.py"):
        content = path.read_text(encoding="utf-8")
        if "InboxBatchProcessor" in content or "inbox_batch_processor" in content:
            violations.append(str(path.relative_to(_ROOT)))

    assert violations == []
