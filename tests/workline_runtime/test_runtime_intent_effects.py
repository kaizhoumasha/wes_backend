from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.workline_runtime.orchestrator import OrchestratorResult
from src.workline_runtime.runtime_intent import RuntimeIntent
from tests.workline_runtime.support.runtime_intent_effects import (
    _session,
)


@pytest.mark.asyncio
async def test_apply_orchestrator_effects_dispatches_runtime_intents(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, Any] = {}

    class CapturingApplier:
        async def apply(self, ctx: dict[str, Any], intents: list[RuntimeIntent]) -> None:
            called["session"] = ctx["session"]
            called["intents"] = intents

    monkeypatch.setattr("src.workline_runtime.runtime_intent_effects.RuntimeIntentEffectApplier", CapturingApplier)

    session = _session(status="RUNNING", current_wait_type=None, awaiting_device_command_code=None)
    intents = [RuntimeIntent.update_context({"pkg_id": "PKG-001"})]
    from src.app.workline.services.write_back_service import orchestrator_write_back_service

    await orchestrator_write_back_service.write_back(
        SimpleNamespace(add=MagicMock()),
        session=session,
        workline=SimpleNamespace(id=1, plugin_key="demo_plugin"),
        inbox=SimpleNamespace(id=10, trace_id="trace-runtime"),
        devices_by_role={},
        source_device=None,
        orch_result=OrchestratorResult(success=True, intents=intents),
    )

    assert called == {"session": session, "intents": intents}
