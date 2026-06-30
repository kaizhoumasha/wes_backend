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
