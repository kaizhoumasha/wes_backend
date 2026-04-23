"""SMT 分类插件集成测试共享 fixture。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.workline_plugins.smt_classifier import SmtClassifierPlugin


@pytest.fixture
def plugin() -> SmtClassifierPlugin:
    """插件实例。"""
    return SmtClassifierPlugin()


@pytest.fixture
def mock_context() -> MagicMock:
    """Mock 插件上下文。"""
    ctx = MagicMock()
    ctx.logger = MagicMock()
    ctx.session = MagicMock()
    ctx.session.id = 42
    ctx.session.context_json = {}
    ctx.devices_by_role = {
        "INPUT_ARM": [MagicMock(id=123)],
        "CONVEYOR": [MagicMock(id=456)],
        "OUTPUT_ARM": [MagicMock(id=789)],
    }
    return ctx
