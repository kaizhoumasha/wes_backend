"""SMT 分类插件集成测试共享 fixture。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.workline_plugins.smt_classifier import SmtClassifierPlugin
from src.workline_runtime.services import WorklineRuntimeServices


class PluginContextMock(MagicMock):
    """测试用 PluginContext；从 session 当前字段投影业务阶段。"""

    @property
    def plugin_state(self) -> str:
        session = getattr(self, "session", None)
        session_state = getattr(session, "plugin_state", None)
        if isinstance(session_state, str) and session_state:
            return session_state
        return "IDLE"

    @plugin_state.setter
    def plugin_state(self, value: str) -> None:
        session = getattr(self, "session", None)
        if session is not None:
            session.plugin_state = value


@pytest.fixture
def plugin() -> SmtClassifierPlugin:
    """插件实例。"""
    return SmtClassifierPlugin()


@pytest.fixture
def mock_context() -> MagicMock:
    """Mock 插件上下文。"""
    ctx = PluginContextMock()
    ctx.logger = MagicMock()
    ctx.services = WorklineRuntimeServices()
    ctx.session = MagicMock()
    ctx.session.id = 42
    ctx.session.context_json = {}
    ctx.devices_by_role = {
        "INPUT_ARM": [MagicMock(id=123)],
        "CONVEYOR": [MagicMock(id=456)],
        "OUTPUT_ARM": [MagicMock(id=789)],
    }
    return ctx
