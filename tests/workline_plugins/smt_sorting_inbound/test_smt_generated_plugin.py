"""SMT generated Plugin 的 route 决策合同。"""

from __future__ import annotations

import pytest

from src.app.runtime.extension_identity import sha256_digest
from src.app.runtime.orchestration.runtime_intent import RuntimeIntentKind
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge import (
    GeneratedPluginAttemptRunner,
)
from src.app.runtime.system_capabilities.outcomes import BusinessReject
from src.app.runtime.workline_plugins.attempt_coordinator import AttemptSnapshot, PluginAttemptContext
from src.app.runtime.workline_plugins.contracts import CapabilityEffectResultInput, CommandResultInput
from src.app.runtime.workline_plugins.dispatcher import (
    PinnedPluginSnapshot,
    PluginAttemptFactSource,
    PluginDispatchRequest,
    WorklinePluginDispatcher,
)
from src.app.runtime.workline_plugins.generated_index import WORKLINE_PLUGIN_INDEX_DIGEST
from src.app.runtime.workline_plugins.smt_sorting_inbound.contracts import (
    SmtSortingInboundConfig,
    SmtSortingInboundFacts,
    SmtSortingInboundState,
    SourcePickRequestInput,
)
from src.app.runtime.workline_plugins.smt_sorting_inbound.definition import DEFINITION
from src.app.runtime.workline_plugins.smt_sorting_inbound.handlers import build_facts, decide


def _facts(**overrides: object) -> SmtSortingInboundFacts:
    snapshot = PinnedPluginSnapshot(
        plugin_key="smt_sorting_inbound",
        contract_version="smt_sorting_inbound.v1",
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
        SourcePickRequestInput(),
        state=SmtSortingInboundState(),
        config=SmtSortingInboundConfig(provider_profile="runtime"),
        facts=_facts(),
        gateway=object(),
    )

    assert decision.outcome_code == "SOURCE_PICK_REQUESTED"
    assert decision.next_state.current_correlation is None
    assert len(decision.intents) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state",
    (
        SmtSortingInboundState(current_correlation="SC-PENDING"),
        SmtSortingInboundState(phase="WAITING_SCAN"),
    ),
)
async def test_source_pick_request_is_idempotent_outside_initial_wait(state: SmtSortingInboundState) -> None:
    decision = await decide(
        SourcePickRequestInput(),
        state=state,
        config=SmtSortingInboundConfig(provider_profile="runtime"),
        facts=_facts(),
        gateway=object(),
    )

    assert decision.outcome_code == "SOURCE_PICK_REQUEST_IGNORED"
    assert decision.next_state == state
    assert decision.intents == ()


def test_smt_definition_uses_fixed_generated_identity() -> None:
    assert DEFINITION.contract_version == "smt_sorting_inbound.v1"


def test_smt_definition_declares_source_arm_command_and_effect_contract() -> None:
    assert DEFINITION.schema.devices[0].role == "SORTING_SOURCE_ARM"
    assert DEFINITION.schema.commands[0].command == "SORTING_SOURCE_PICK"
    assert DEFINITION.schema.commands[0].target_device_role == "SORTING_SOURCE_ARM"
    assert DEFINITION.allowed_capabilities == (
        ("device.device_command_write", "v1"),
        ("runtime.session_hold", "v1"),
    )

    with pytest.raises(ValueError):
        SmtSortingInboundConfig(provider_profile="runtime", source_arm_role="UNDECLARED_ARM")


@pytest.mark.asyncio
async def test_smt_source_pick_binds_generated_command_code_for_command_result() -> None:
    config = SmtSortingInboundConfig(provider_profile="runtime")
    snapshot = PinnedPluginSnapshot(
        plugin_key=DEFINITION.plugin_key,
        contract_version=DEFINITION.contract_version,
        binding_identity="binding:1:1",
        binding_id=1,
        binding_version=1,
        config_hash=sha256_digest(config.model_dump(mode="json")),
        index_digest=WORKLINE_PLUGIN_INDEX_DIGEST,
        profile_identity="runtime",
    )
    request = PluginDispatchRequest(
        plugin_key=DEFINITION.plugin_key,
        contract_version=DEFINITION.contract_version,
        logical_route="SOURCE_PICK_REQUESTED",
        raw_config=config.model_dump(mode="json"),
        raw_state={},
        context_state={},
        raw_input={},
        fact_source=PluginAttemptFactSource(snapshot=snapshot),
        snapshot=snapshot,
    )
    write_set = await GeneratedPluginAttemptRunner().run(
        PluginAttemptContext(
            attempt_id="smt-source-pick",
            inbox_id=11,
            session_id=2,
            workline_id=3,
            event_type="SOURCE_PICK_REQUESTED",
            payload={},
            plugin_state={},
            snapshot=AttemptSnapshot(
                processor_token="smt-source-pick",
                session_version=1,
                plugin_state_version=0,
                binding_id=1,
                binding_version=1,
                device_fact_versions=(("SORTING_SOURCE_ARM", 31, 0),),
            ),
            runtime=type("Runtime", (), {"gateway": object()})(),
            dispatch_request=request,
        )
    )

    assert write_set.outcome_code == "SOURCE_PICK_REQUESTED", write_set.hold_reason
    [command] = write_set.intents
    assert command.kind is RuntimeIntentKind.SYSTEM_CAPABILITY
    assert command.payload_json["command_code"].startswith("SC-")
    assert write_set.next_state.current_correlation == command.payload_json["command_code"]

    callback_request = request.model_copy(
        update={
            "logical_route": "COMMAND_RESULT",
            "raw_state": write_set.next_state.model_dump(mode="json"),
            "context_state": write_set.next_state.model_dump(mode="json"),
            "raw_input": {
                "route": "COMMAND_RESULT",
                "command_code": command.payload_json["command_code"],
                "command_type": "SORTING_SOURCE_PICK",
                "result": "SUCCESS",
            },
            "fact_source": PluginAttemptFactSource(snapshot=snapshot),
        }
    )
    result = await WorklinePluginDispatcher().dispatch(request=callback_request, gateway=object())

    assert result.outcome_code == "SOURCE_PICK_COMPLETED"


@pytest.mark.asyncio
async def test_smt_failed_command_result_converts_to_declared_session_hold() -> None:
    config = SmtSortingInboundConfig(provider_profile="runtime")
    snapshot = PinnedPluginSnapshot(
        plugin_key=DEFINITION.plugin_key,
        contract_version=DEFINITION.contract_version,
        binding_identity="binding:1:1",
        binding_id=1,
        binding_version=1,
        config_hash=sha256_digest(config.model_dump(mode="json")),
        index_digest=WORKLINE_PLUGIN_INDEX_DIGEST,
        profile_identity="runtime",
    )
    request = PluginDispatchRequest(
        plugin_key=DEFINITION.plugin_key,
        contract_version=DEFINITION.contract_version,
        logical_route="COMMAND_RESULT",
        raw_config=config.model_dump(mode="json"),
        raw_state={"current_correlation": "SC-FAILED"},
        context_state={"current_correlation": "SC-FAILED"},
        raw_input={
            "route": "COMMAND_RESULT",
            "command_code": "SC-FAILED",
            "command_type": "SORTING_SOURCE_PICK",
            "result": "FAILED",
        },
        fact_source=PluginAttemptFactSource(snapshot=snapshot),
        snapshot=snapshot,
    )
    write_set = await GeneratedPluginAttemptRunner().run(
        PluginAttemptContext(
            attempt_id="smt-source-pick-failed",
            inbox_id=12,
            session_id=2,
            workline_id=3,
            event_type="COMMAND_RESULT",
            payload={},
            plugin_state={"current_correlation": "SC-FAILED"},
            snapshot=AttemptSnapshot(
                processor_token="smt-source-pick-failed",
                session_version=2,
                plugin_state_version=1,
                session_status="WAITING_DEVICE_RESULT",
                binding_id=1,
                binding_version=1,
            ),
            runtime=type("Runtime", (), {"gateway": object()})(),
            dispatch_request=request,
        )
    )

    assert write_set.outcome_code == "SOURCE_PICK_FAILED"
    [hold] = write_set.intents
    assert hold.kind is RuntimeIntentKind.SYSTEM_CAPABILITY
    assert hold.capability_key == "runtime.session_hold"
    assert hold.contract_version == "v1"
    assert hold.payload_json["reason_code"] == "SOURCE_PICK_FAILED"


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
