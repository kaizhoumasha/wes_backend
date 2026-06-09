import importlib
from types import SimpleNamespace

import pytest

from src.core.conf import settings
from src.workline_runtime import orchestrator
from src.workline_runtime.null_plugin import NullPlugin


def _ctx() -> SimpleNamespace:
    return SimpleNamespace(logger=SimpleNamespace(info=lambda *_: None, warning=lambda *_: None))


@pytest.mark.asyncio
async def test_null_plugin_handlers_return_empty_runtime_intents() -> None:
    plugin = NullPlugin()
    inbox = SimpleNamespace(
        id=1,
        payload_json={"event_type": "SCAN_COMPLETED", "command_type": "PICK_AND_PUT", "result": "SUCCESS"},
    )

    assert await plugin.on_device_event(_ctx(), inbox) == []
    assert await plugin.on_command_result(_ctx(), inbox) == []
    assert await plugin.on_external_http(_ctx(), inbox) == []
    assert await plugin.on_manual_operation(_ctx(), inbox) == []


def test_null_plugin_env_allow_is_limited_to_non_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKLINE_ALLOW_NULL_PLUGIN", "1")

    monkeypatch.setattr(orchestrator.settings, "APP_ENV", "dev")
    assert orchestrator._env_allows_null_plugin() is True

    monkeypatch.setattr(orchestrator.settings, "APP_ENV", "test")
    assert orchestrator._env_allows_null_plugin() is True

    monkeypatch.setattr(orchestrator.settings, "APP_ENV", "prod")
    with pytest.raises(RuntimeError, match="WORKLINE_ALLOW_NULL_PLUGIN"):
        orchestrator._env_allows_null_plugin()


def test_null_plugin_env_prod_guard_fails_at_module_import(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKLINE_ALLOW_NULL_PLUGIN", "1")
    monkeypatch.setattr(settings, "APP_ENV", "prod")

    try:
        with pytest.raises(RuntimeError, match="WORKLINE_ALLOW_NULL_PLUGIN"):
            importlib.reload(orchestrator)
    finally:
        monkeypatch.delenv("WORKLINE_ALLOW_NULL_PLUGIN", raising=False)
        monkeypatch.setattr(settings, "APP_ENV", "dev")
        importlib.reload(orchestrator)
