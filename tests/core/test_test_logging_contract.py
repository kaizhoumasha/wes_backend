"""测试日志输出与 pytest 捕获策略的合同。"""

import sys
import tomllib
from pathlib import Path
from typing import Any


def test_pytest_addopts_keeps_output_capture_enabled() -> None:
    pyproject_path = Path(__file__).parents[2] / "pyproject.toml"
    pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    addopts = pyproject["tool"]["pytest"]["ini_options"]["addopts"]

    assert "-s" not in addopts
    assert "--capture=no" not in addopts


def test_debug_console_sink_respects_configured_log_level(monkeypatch, tmp_path: Path) -> None:
    from src.core import logger as logger_module

    added_sinks: list[tuple[Any, dict[str, Any]]] = []

    def capture_sink(sink: Any, **kwargs: Any) -> int:
        added_sinks.append((sink, kwargs))
        return len(added_sinks)

    monkeypatch.setattr(logger_module, "_initialized", False)
    monkeypatch.setattr(logger_module.settings, "APP_DEBUG", True)
    monkeypatch.setattr(logger_module.settings, "LOG_LEVEL", "WARNING")
    monkeypatch.setattr(logger_module, "_resolve_log_dir", lambda: tmp_path)
    monkeypatch.setenv("LOG_DISABLE_FILE", "true")
    monkeypatch.setattr(logger_module._logger, "remove", lambda: None)
    monkeypatch.setattr(logger_module._logger, "add", capture_sink)
    monkeypatch.setattr(logger_module._logger, "info", lambda _message: None)
    monkeypatch.setattr(logger_module.logging, "basicConfig", lambda **_kwargs: None)
    monkeypatch.setattr(logger_module, "_configure_standard_loggers", lambda _is_debug: None)

    logger_module.setup_logger()

    assert added_sinks == [
        (
            sys.stderr,
            {
                "format": logger_module.DEBUG_CONSOLE_FORMAT,
                "level": "WARNING",
                "colorize": True,
                "backtrace": True,
                "diagnose": True,
                "filter": logger_module.add_request_id_filter,
            },
        )
    ]
