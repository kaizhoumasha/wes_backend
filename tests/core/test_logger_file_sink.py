"""项目文件日志 sink 的环境隔离合同。"""

from pathlib import Path


def test_logger_resolves_absolute_configured_log_directory(monkeypatch, tmp_path: Path) -> None:
    from src.core import logger as logger_module

    monkeypatch.setattr(logger_module.settings, "LOG_DIR", str(tmp_path))

    assert logger_module._resolve_log_dir() == tmp_path
    assert tmp_path.is_dir()
