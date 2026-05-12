"""Orchestrator RuntimeIntent 返回路径测试。"""

from types import SimpleNamespace

import pytest

from src.workline_runtime.diagnostics import ErrorCode
from src.workline_runtime.orchestrator import OrchestratorService
from src.workline_runtime.runtime_intent import RuntimeIntent
from src.workline_runtime.services import WorklineRuntimeServices


def test_process_intents_returns_success_result_and_preserves_order() -> None:
    orchestrator = OrchestratorService()
    intents = [
        RuntimeIntent.update_context({"pkg_id": "L0001-1"}),
        RuntimeIntent.continue_next(action="MOVE_FORWARD", payload={"pkg_id": "L0001-1"}),
        RuntimeIntent.complete({"bin_code": "BIN_463"}),
    ]

    result = orchestrator._process_intents(intents, session=SimpleNamespace())

    assert result.success is True
    assert result.intents == intents


def test_process_intents_rejects_context_patch_with_reserved_runtime_key() -> None:
    orchestrator = OrchestratorService()
    intents = [
        RuntimeIntent.update_context({"pkg_id": "L0001-1"}),
        RuntimeIntent.update_context({"awaiting_command_id": 42}),
    ]

    result = orchestrator._process_intents(intents, session=SimpleNamespace())

    assert result.success is False
    assert result.error_code == ErrorCode.PLUGIN_TRANSITION_INVALID.value
    assert result.intents is None


@pytest.mark.asyncio
async def test_process_read_phase_routes_runtime_intent_list() -> None:
    returned_intents = [RuntimeIntent.update_context({"pkg_id": "L0001-1"})]

    class RuntimeIntentPlugin:
        contract_version = "1.0"

        async def on_device_event(self, ctx, inbox):
            return returned_intents

    session = SimpleNamespace(
        id=1,
        status="RUNNING",
        context_json={},
        contract_version="1.0",
    )
    workline = SimpleNamespace(
        id=1,
        plugin_class=RuntimeIntentPlugin,
    )
    inbox = SimpleNamespace(
        id=100,
        kind="DEVICE_EVENT",
        payload_json={"message_type": "DEVICE_EVENT"},
    )
    orchestrator = OrchestratorService()

    result = await orchestrator._process_read_phase(
        session=session,
        workline=workline,
        inbox=inbox,
        devices_by_role={},
        services=WorklineRuntimeServices(),
        trace_id="trace-runtime-intents",
    )

    assert result.success is True
    assert result.intents == returned_intents
