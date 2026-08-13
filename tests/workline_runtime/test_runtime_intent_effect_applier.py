"""material-flow RuntimeIntent effect applier 可执行合约。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.app.runtime.orchestration.effect_result import RuntimeIntentEffectResult
from src.app.runtime.orchestration.runtime_intent import RuntimeIntent
from src.app.runtime.orchestration.runtime_intent_effects import RuntimeIntentEffectApplier
from src.app.runtime.system_capabilities.definition import EffectCompletionMode
from src.app.runtime.system_capabilities.outcomes import BusinessReject, Success
from src.app.workline.services.write_back_service import EffectApplyState


class _RecordingReservationService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def apply_runtime_reservation(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(status="RESERVED")


def _effect_ctx() -> dict[str, Any]:
    return {
        "db": SimpleNamespace(),
        "session": SimpleNamespace(id=31, trace_id=None, context_json={}),
        "workline": SimpleNamespace(id=41, line_code="LINE-A"),
        "inbox": SimpleNamespace(id=501),
        "trace_id": "trace-runtime-effect-applier",
        "effect_state": EffectApplyState(),
    }


class _RecordingSystemCapabilityEffectService:
    def __init__(self) -> None:
        self.calls: list[tuple[object, RuntimeIntent]] = []

    async def apply(self, ctx: object, intent: RuntimeIntent) -> SimpleNamespace:
        self.calls.append((ctx, intent))
        return SimpleNamespace(
            outcome=Success(payload={"accepted": True}),
            completion_mode=EffectCompletionMode.LOCAL_TRANSACTIONAL,
            durably_accepted=False,
            remote_completed=True,
            idempotent_replay=False,
            retryable=False,
            outbox_dispatch_targets=frozenset(),
        )


class _StaleMaterialEffectService(_RecordingSystemCapabilityEffectService):
    async def apply(self, ctx: object, intent: RuntimeIntent) -> SimpleNamespace:
        self.calls.append((ctx, intent))
        if intent.capability_key != "material_flow.material_unit_write":
            raise AssertionError("BusinessReject 后不得执行后续 device effect")
        return SimpleNamespace(
            outcome=BusinessReject(reason_code="STALE_PRECONDITION", message="material fact changed"),
            completion_mode=EffectCompletionMode.LOCAL_TRANSACTIONAL,
            durably_accepted=False,
            remote_completed=False,
            idempotent_replay=False,
            retryable=False,
            evidence=SimpleNamespace(outcome_kind="business_reject"),
            outbox_dispatch_targets=frozenset(),
        )


@pytest.mark.asyncio
async def test_system_capability_intent_uses_one_generic_effect_service_branch() -> None:
    service = _RecordingSystemCapabilityEffectService()
    intent = RuntimeIntent.system_capability(
        capability_key="material_flow.material_unit_write",
        contract_version="v1",
        operation_key="scan:PKG-001:create",
        dispatch_key="system-capability:material:create:PKG-001",
        payload={"operation": "CREATE", "pkg_code": "PKG-001"},
        precondition={"expected_absent": True},
        fact_version="material-unit:v0",
        timeout_seconds=5,
        creator_authority="RUNTIME_DOMAIN_SERVICE",
        authorization_policy="DOMAIN_CAPABILITY_ALLOWLIST",
        binding_snapshot={},
        provider_snapshot={"provider_code": "RUNTIME", "profile": "runtime"},
    )
    ctx = _effect_ctx()

    await RuntimeIntentEffectApplier(system_capability_effect_service=service).apply(ctx, [intent])

    assert service.calls == [(ctx, intent)]
    assert ctx["system_capability_outcomes"][0].outcome.kind == "success"


@pytest.mark.asyncio
async def test_stale_material_effect_short_circuits_following_system_capability_effects() -> None:
    service = _StaleMaterialEffectService()
    common = {
        "timeout_seconds": 5,
        "creator_authority": "RUNTIME_DOMAIN_SERVICE",
        "authorization_policy": "DOMAIN_CAPABILITY_ALLOWLIST",
        "binding_snapshot": {},
        "provider_snapshot": {"provider_code": "RUNTIME", "profile": "runtime"},
    }
    material = RuntimeIntent.system_capability(
        capability_key="material_flow.material_unit_write",
        contract_version="v1",
        operation_key="scan:PKG-001:create",
        dispatch_key="system-capability:material:create:PKG-001",
        payload={"operation": "CREATE", "pkg_code": "PKG-001"},
        precondition={"expected_absent": True},
        fact_version="material-unit:v0",
        **common,
    )
    following_effect = RuntimeIntent.system_capability(
        capability_key="material_flow.bin_reservation_write",
        contract_version="v1",
        operation_key="scan:PKG-001:reserve",
        dispatch_key="bin-reservation:PKG-001",
        payload={"bin_code": "BIN-001"},
        precondition={"expected_available": True},
        fact_version="bin:v1",
        **common,
    )
    ctx = _effect_ctx()
    ctx["db"] = SimpleNamespace(added=[])

    await RuntimeIntentEffectApplier(system_capability_effect_service=service).apply(
        ctx,
        [material, following_effect],
    )

    assert [intent.capability_key for _, intent in service.calls] == ["material_flow.material_unit_write"]
    assert [result.outcome.kind for result in ctx["system_capability_outcomes"]] == ["business_reject"]
    assert ctx["system_capability_outcomes"][0].evidence.outcome_kind == "business_reject"
    assert ctx["db"].added == []


@pytest.mark.asyncio
async def test_resource_reservation_uses_runtime_material_flow_default_singleton(monkeypatch) -> None:
    """未注入 reservation service 时必须使用 runtime material-flow 的真实默认单例。"""
    import importlib

    from src.app.runtime.capabilities.material_flow.bin_cell_reservation_service import (
        WorklineBinCellReservationService,
        bin_cell_reservation_service,
    )

    reservation_module = importlib.import_module(
        "src.app.runtime.capabilities.material_flow.bin_cell_reservation_service"
    )
    assert isinstance(bin_cell_reservation_service, WorklineBinCellReservationService)
    assert "bin_cell_reservation_service" in reservation_module.__all__

    recording_service = _RecordingReservationService()
    monkeypatch.setattr(reservation_module, "bin_cell_reservation_service", recording_service)
    intent = RuntimeIntent.resource_reservation(
        operation="CLAIM_BIN_CELL",
        payload={
            "pkg_code": "PKG-DEFAULT-SINGLETON",
            "bin_code": "BIN-DEFAULT-01",
            "bin_cell_index": "1",
        },
        idempotency_key="reservation-default-singleton-001",
    )

    await RuntimeIntentEffectApplier().apply(_effect_ctx(), [intent])

    assert len(recording_service.calls) == 1
    assert recording_service.calls[0]["operation"] == "CLAIM_BIN_CELL"
    assert recording_service.calls[0]["payload_json"]["pkg_code"] == "PKG-DEFAULT-SINGLETON"
