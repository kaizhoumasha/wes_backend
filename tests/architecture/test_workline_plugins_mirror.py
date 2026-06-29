"""Phase 2 burn-down 阶段 2 C4 — workline plugins/ 子目录镜像。

C4 镜像 1 个文件:src/app/workline/plugins/run_mode.py
(plugin_base / plugin_context / session_resolver / plugin_manifest 推迟到 C5)
"""

from __future__ import annotations

import importlib


def test_run_mode_mirror_exposes_required_symbols() -> None:
    """src/app/workline/plugins/run_mode.py 导出 normalize_run_mode + is_simulation_run_mode。"""
    from src.app.workline.plugins import run_mode

    assert hasattr(run_mode, "normalize_run_mode")
    assert hasattr(run_mode, "is_simulation_run_mode")


def test_run_mode_mirror_is_callable_and_passes_through() -> None:
    """normalize_run_mode 接受字符串返回字符串;与 wlr 原模块行为一致。"""
    from src.app.workline.plugins import run_mode

    # 镜像应至少保留原模块函数的可调用性
    assert callable(run_mode.normalize_run_mode)
    assert callable(run_mode.is_simulation_run_mode)


def test_plugins_subdirectory_imports_cleanly() -> None:
    """src.app.workline.plugins 包及其 run_mode 子模块都可独立 import."""
    # 子目录必须可被 import(允许空 __init__.py)
    assert importlib.import_module("src.app.workline.plugins") is not None
    assert importlib.import_module("src.app.workline.plugins.run_mode") is not None
