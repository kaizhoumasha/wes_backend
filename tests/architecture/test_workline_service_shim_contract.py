"""WorkLine service shim 契约测试。"""

from __future__ import annotations

import importlib
from unittest.mock import patch


def test_runtime_reconciliation_shim_aliases_impl_module():
    """旧 import 路径必须与 runtime/orchestration 实现共享同一 module object。"""
    old_module = importlib.import_module("src.app.workline.services.runtime_reconciliation_service")
    impl_module = importlib.import_module(
        "src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl"
    )
    marker = object()

    assert old_module is impl_module

    with patch(
        "src.app.workline.services.runtime_reconciliation_service.add_timeline_with_sequence",
        new=marker,
    ):
        assert impl_module.add_timeline_with_sequence is marker

    with patch(
        "src.app.workline.services.runtime_reconciliation_service.workline_diagnostic_service",
        new=marker,
    ):
        assert impl_module.workline_diagnostic_service is marker


def test_runtime_reconciliation_facade_removed_after_stage5():
    """阶段 5:`RuntimeReconciliationFacade` 必须物理删除。"""
    # 原 facade 模块在 runtime/orchestration/services/ 下,模块移除后
    # importlib.import_module 必须抛 ModuleNotFoundError。
    with __import__("pytest").raises(ModuleNotFoundError):
        importlib.import_module("src.app.runtime.orchestration.services.runtime_reconciliation_service")

    # Facade 类与单例符号必须不再暴露在 runtime/orchestration.services 顶层。
    services_module = importlib.import_module("src.app.runtime.orchestration.services")
    assert not hasattr(services_module, "RuntimeReconciliationFacade")
    assert not hasattr(services_module, "runtime_reconciliation_facade")
    assert "RuntimeReconciliationFacade" not in services_module.__all__
    assert "runtime_reconciliation_facade" not in services_module.__all__
