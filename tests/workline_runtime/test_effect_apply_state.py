"""Generated attempt effect state 与事务回滚回归。"""

from __future__ import annotations

from dataclasses import fields
from types import SimpleNamespace
from typing import Any

import pytest

from src.app.runtime.orchestration.effect_result import RuntimeIntentEffectResult
from src.app.runtime.orchestration.runtime_intent import BlockScope, RuntimeIntent
from src.app.runtime.orchestration.runtime_intent_effects import RuntimeIntentEffectApplier


def _effect_state_type() -> type[Any]:
    from src.app.workline.services import write_back_service

    state_type = getattr(write_back_service, "EffectApplyState", None)
    assert state_type is not None, "generated effect 必须使用 typed EffectApplyState"
    return state_type


def _ctx() -> dict[str, Any]:
    return {
        "db": SimpleNamespace(),
        "session": SimpleNamespace(
            id=31,
            workline_id=41,
            status="RUNNING",
            trace_id=None,
            context_json={},
            current_wait_type=None,
            awaiting_device_command_code=None,
        ),
        "workline": SimpleNamespace(id=41, line_code="LINE-A"),
        "inbox": SimpleNamespace(id=501, payload_json={}),
        "devices_by_role": {},
        "source_device": None,
        "trace_id": "trace-effect-state",
        "effect_state": _effect_state_type()(),
        "current_status": "RUNNING",
        "session_ctx": {},
        "now": SimpleNamespace(),
        "awaiting_device_command_pk": None,
        "awaiting_command_code": None,
        "next_timeline_seq_no": None,
    }


def test_effect_apply_state_only_contains_effect_applier_mutable_fields() -> None:
    state_type = _effect_state_type()

    assert {field.name for field in fields(state_type)} == {
        "context_patch",
        "failure",
        "skip_next_material_unit_intent",
    }


@pytest.mark.asyncio
async def test_effect_apply_state_preserves_noop_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.app.workline.services import write_back_service as workline_effects

    monkeypatch.setattr(workline_effects, "_sync_effect_trace_fields", lambda _ctx: None)
    calls: list[str] = []
    applier = RuntimeIntentEffectApplier()

    async def apply_noop(ctx: dict[str, Any]) -> None:
        assert "orch_result" not in ctx
        assert isinstance(ctx["effect_state"], _effect_state_type())
        calls.append("noop")

    monkeypatch.setattr(applier, "_apply_noop_completion", apply_noop)

    assert (await applier.apply(_ctx(), [])).disposition.value == "PROCESSED"
    assert calls == ["noop"]


@pytest.mark.asyncio
async def test_effect_apply_state_preserves_block_and_wait_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.app.workline.services import write_back_service as workline_effects

    monkeypatch.setattr(workline_effects, "_sync_effect_trace_fields", lambda _ctx: None)
    calls: list[str] = []
    applier = RuntimeIntentEffectApplier()

    async def apply_block(ctx: dict[str, Any], _intent: RuntimeIntent) -> None:
        assert isinstance(ctx["effect_state"], _effect_state_type())
        calls.append("block")

    async def apply_wait(ctx: dict[str, Any], _intent: RuntimeIntent) -> RuntimeIntentEffectResult:
        assert isinstance(ctx["effect_state"], _effect_state_type())
        calls.append("wait")
        return RuntimeIntentEffectResult.resource_retry()

    monkeypatch.setattr(applier, "_apply_block", apply_block)
    monkeypatch.setattr(applier, "_apply_resource_wait", apply_wait)

    blocked = await applier.apply(
        _ctx(),
        [
            RuntimeIntent.block(
                scope=BlockScope.COMMAND,
                reason_code="SOURCE_PICK_FAILED",
                message="source pick failed",
            )
        ],
    )
    waiting = await applier.apply(
        _ctx(),
        [
            RuntimeIntent.resource_wait(
                subject_type="STATION",
                subject_key="STATION-A",
                projection_type="STATION_LEASE",
                reason_code="WAIT",
                message="wait",
            )
        ],
    )

    assert blocked.disposition.value == "PROCESSED"
    assert waiting.disposition.value == "RESOURCE_RETRY"
    assert calls == ["block", "wait"]


@pytest.mark.asyncio
async def test_effect_apply_state_preserves_complete_and_continue_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.app.workline.services import write_back_service as workline_effects

    monkeypatch.setattr(workline_effects, "_sync_effect_trace_fields", lambda _ctx: None)
    monkeypatch.setattr(workline_effects, "_apply_context_patch", lambda _ctx: None)
    calls: list[str] = []
    applier = RuntimeIntentEffectApplier()

    async def apply_completion(ctx: dict[str, Any]) -> bool:
        assert isinstance(ctx["effect_state"], _effect_state_type())
        calls.append("complete")
        return True

    async def cleanup(ctx: dict[str, Any]) -> None:
        assert isinstance(ctx["effect_state"], _effect_state_type())

    async def apply_continue(ctx: dict[str, Any], _intent: RuntimeIntent) -> None:
        assert isinstance(ctx["effect_state"], _effect_state_type())
        calls.append("continue")

    monkeypatch.setattr(workline_effects, "_apply_completion_transition", apply_completion)
    monkeypatch.setattr(applier, "_cleanup_completed_material_unit", cleanup)
    monkeypatch.setattr(applier, "_apply_continue_next", apply_continue)

    completed = await applier.apply(_ctx(), [RuntimeIntent.complete({"phase": "DONE"})])
    continued = await applier.apply(_ctx(), [RuntimeIntent.continue_next(action="SOURCE_PICK_COMPLETED")])

    assert completed.disposition.value == "PROCESSED"
    assert continued.disposition.value == "PROCESSED"
    assert calls == ["complete", "continue"]
