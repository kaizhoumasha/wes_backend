"""SMT generated Plugin 的 route 决策合同。"""

from __future__ import annotations

import pytest

from src.app.runtime.system_capabilities.outcomes import BusinessReject
from src.app.runtime.workline_plugins.contracts import CapabilityEffectResultInput, CommandResultInput
from src.app.runtime.workline_plugins.dispatcher import PinnedPluginSnapshot, PluginAttemptFactSource
from src.app.runtime.workline_plugins.smt_sorting_inbound.contracts import (
    SmtSortingInboundConfig,
    SmtSortingInboundFacts,
    SmtSortingInboundState,
    SourcePickRequestInput,
)
from src.app.runtime.workline_plugins.smt_sorting_inbound.handlers import build_facts, decide


def _facts(**overrides: object) -> SmtSortingInboundFacts:
    snapshot = PinnedPluginSnapshot(
        plugin_key="smt_sorting_inbound",
        contract_version="v1",
        binding_identity="binding:1:1",
        binding_id=1,
        binding_version=1,
        config_hash="a" * 64,
        index_digest="b" * 64,
        profile_identity="runtime",
    )
    source = PluginAttemptFactSource(snapshot=snapshot, **overrides)
    return build_facts(source)


@pytest.mark.asyncio
async def test_source_pick_request_waits_for_its_command_result() -> None:
    decision = await decide(
        SourcePickRequestInput(command_code="CMD-1"),
        state=SmtSortingInboundState(),
        config=SmtSortingInboundConfig(provider_profile="runtime"),
        facts=_facts(),
        gateway=object(),
    )

    assert decision.outcome_code == "SOURCE_PICK_REQUESTED"
    assert decision.next_state.current_correlation == "CMD-1"
    assert len(decision.intents) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "expected"),
    [("SUCCESS", "SOURCE_PICK_COMPLETED"), ("FAILED", "SOURCE_PICK_FAILED")],
)
async def test_source_pick_result_has_stable_terminal_decision(result: str, expected: str) -> None:
    decision = await decide(
        CommandResultInput(command_code="CMD-1", command_type="SORTING_SOURCE_PICK", result=result),
        state=SmtSortingInboundState(current_correlation="CMD-1"),
        config=SmtSortingInboundConfig(provider_profile="runtime"),
        facts=_facts(),
        gateway=object(),
    )

    assert decision.outcome_code == expected


@pytest.mark.asyncio
async def test_source_pick_correlation_mismatch_and_capability_reject_have_zero_effect() -> None:
    config = SmtSortingInboundConfig(provider_profile="runtime")
    state = SmtSortingInboundState(current_correlation="CMD-1")
    mismatch = await decide(
        CommandResultInput(command_code="CMD-OTHER", command_type="SORTING_SOURCE_PICK", result="SUCCESS"),
        state=state,
        config=config,
        facts=_facts(correlation_matches=False),
        gateway=object(),
    )
    rejected = await decide(
        CapabilityEffectResultInput(
            data={
                "session_id": 1,
                "effect_evidence": {
                    "capability_key": "inventory.write",
                    "contract_version": "v1",
                    "operation_key": "op-1",
                    "idempotency_key": "key-1",
                    "payload_hash": "a" * 64,
                    "outcome_kind": "business_reject",
                    "outcome_code": "REJECTED",
                    "outcome": BusinessReject(reason_code="REJECTED", message="拒绝"),
                    "occurred_at_ms": 1,
                },
            }
        ),
        state=state,
        config=config,
        facts=_facts(),
        gateway=object(),
    )

    assert mismatch.outcome_code == "COMMAND_RESULT_CORRELATION_MISMATCH"
    assert mismatch.intents == ()
    assert rejected.outcome_code == "CAPABILITY_REJECTED"
    assert rejected.intents == ()
