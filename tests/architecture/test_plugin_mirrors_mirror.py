"""Phase 2 burn-down 阶段 2 C5b — plugin + orchestrator 镜像与重导出。

C5b 镜像 19 个文件:
  - src/app/workline/plugins/{plugin_base, plugin_context, session_resolver,
                                 null_plugin, plugin_next}.py (5)
  - src/app/workline/plugins/plugin_sdk/ 包 (11)
  - src/app/workline/domain/plugin_manifest.py (1)
  - src/app/runtime/orchestration/{orchestrator_bridge, timeline_generator}.py (2)

不验证运行时行为, 只验证 mirror 文件存在 + 关键公开类/函数已导出 + 无 wlr 自引用。
"""

from __future__ import annotations

import importlib
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path("/Users/kaizhou/codeDev/wes_backend-worktrees/phase2-stage2")


def test_plugin_base_mirror_exposes_public_api() -> None:
    """src/app/workline/plugins/plugin_base.py 导出 WorklinePlugin + on_command + on_event。"""
    from src.app.workline.plugins import plugin_base

    assert hasattr(plugin_base, "WorklinePlugin")
    assert hasattr(plugin_base, "on_command")
    assert hasattr(plugin_base, "on_event")


def test_plugin_context_mirror_exposes_public_api() -> None:
    """plugin_context 导出 PluginContext + PluginContextBuilder。"""
    from src.app.workline.plugins import plugin_context

    assert hasattr(plugin_context, "PluginContext")
    assert hasattr(plugin_context, "PluginContextBuilder")


def test_session_resolver_mirror_exposes_public_api() -> None:
    """session_resolver 公开符号导出验证。"""
    from src.app.workline.plugins import session_resolver

    assert hasattr(session_resolver, "SessionResolver")
    assert hasattr(session_resolver, "session_resolver")
    assert hasattr(session_resolver, "SessionResolveError")
    assert hasattr(session_resolver, "reapply_pending_session_ingress_metadata")


def test_plugin_manifest_mirror_exposes_public_api() -> None:
    """domain/plugin_manifest 导出 WorklinePluginManifest + EventCategory + ResourceBoundary。"""
    from src.app.workline.domain import plugin_manifest

    assert hasattr(plugin_manifest, "WorklinePluginManifest")
    assert hasattr(plugin_manifest, "EventCategory")
    assert hasattr(plugin_manifest, "ResourceBoundary")


def test_null_plugin_mirror_exposes_null_plugin_singleton() -> None:
    """plugins/null_plugin 导出 null_plugin 单例。"""
    from src.app.workline.plugins import null_plugin

    assert hasattr(null_plugin, "null_plugin")
    assert null_plugin.null_plugin is not None


def test_plugin_next_mirror_exposes_plugin_next() -> None:
    """plugins/plugin_next 导出 PluginNext。"""
    from src.app.workline.plugins import plugin_next

    assert hasattr(plugin_next, "PluginNext")


def test_orchestrator_bridge_exposes_public_api() -> None:
    """orchestrator_bridge 导出 OrchestratorResult + OrchestratorService。"""
    from src.app.runtime.orchestration import orchestrator_bridge

    assert hasattr(orchestrator_bridge, "OrchestratorResult")
    assert hasattr(orchestrator_bridge, "OrchestratorService")


def test_timeline_generator_mirror_exposes_timeline_generator() -> None:
    """timeline_generator 导出 timeline_generator。"""
    from src.app.runtime.orchestration import timeline_generator

    assert hasattr(timeline_generator, "timeline_generator")


def test_plugin_sdk_package_imports_cleanly() -> None:
    """plugin_sdk 包及所有子模块可独立 import。"""
    assert importlib.import_module("src.app.workline.plugins.plugin_sdk") is not None
    assert importlib.import_module("src.app.workline.plugins.plugin_sdk.classifiers") is not None
    assert importlib.import_module("src.app.workline.plugins.plugin_sdk.contracts") is not None
    assert importlib.import_module("src.app.workline.plugins.plugin_sdk.normalizers") is not None
    assert importlib.import_module("src.app.workline.plugins.plugin_sdk.classifiers.result_classifier") is not None
    assert importlib.import_module("src.app.workline.plugins.plugin_sdk.contracts.normalized_event") is not None
    assert importlib.import_module("src.app.workline.plugins.plugin_sdk.contracts.normalized_external") is not None
    assert importlib.import_module("src.app.workline.plugins.plugin_sdk.contracts.normalized_result") is not None
    assert importlib.import_module("src.app.workline.plugins.plugin_sdk.contracts.runtime_config") is not None
    assert importlib.import_module("src.app.workline.plugins.plugin_sdk.normalizers.event_mapper") is not None
    assert importlib.import_module("src.app.workline.plugins.plugin_sdk.normalizers.input_normalizer") is not None


def test_plugin_sdk_exposes_public_symbols() -> None:
    """plugin_sdk 关键符号导出验证。"""
    from src.app.workline.plugins.plugin_sdk import normalize_inbox_input, resolve_execution_context
    from src.app.workline.plugins.plugin_sdk.contracts import ResolvedExecutionContext

    assert callable(normalize_inbox_input)
    assert callable(resolve_execution_context)
    assert ResolvedExecutionContext is not None


def test_no_wlr_self_imports_in_new_mirrors() -> None:
    """所有新 mirror 文件 + plugin_sdk 包不能含 wlr 自引用(R-WLR guardrail clean)。"""
    mirror_paths = [
        "src/app/workline/plugins/plugin_base.py",
        "src/app/workline/plugins/plugin_context.py",
        "src/app/workline/plugins/session_resolver.py",
        "src/app/workline/plugins/null_plugin.py",
        "src/app/workline/plugins/plugin_next.py",
        "src/app/workline/domain/plugin_manifest.py",
        "src/app/runtime/orchestration/orchestrator_bridge.py",
        "src/app/runtime/orchestration/timeline_generator.py",
    ]
    result = subprocess.run(
        [  # noqa: S607
            "grep",
            "-rnE",
            "from src\\.workline_runtime|import src\\.workline_runtime",
            *mirror_paths,
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(PROJECT_ROOT),
    )
    # 仅 plugin_sdk 是目录,需要单独处理
    sdk_result = subprocess.run(
        [  # noqa: S607
            "grep",
            "-rnE",
            "from src\\.workline_runtime|import src\\.workline_runtime",
            "src/app/workline/plugins/plugin_sdk/",
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(PROJECT_ROOT),
    )
    bad_lines = []
    if result.stdout.strip():
        bad_lines.extend(result.stdout.strip().split("\n"))
    if sdk_result.stdout.strip():
        bad_lines.extend(sdk_result.stdout.strip().split("\n"))
    # 允许 plugin_sdk 目录内出现相对 import (e.g. `from .contracts import (...)`)
    # 但不允许 src.workline_runtime 自引用
    real_bad = [line for line in bad_lines if "src.workline_runtime" in line]
    assert real_bad == [], f"以下 mirror 文件仍含 wlr 自引用: {real_bad}"
