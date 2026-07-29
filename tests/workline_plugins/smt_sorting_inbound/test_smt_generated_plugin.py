"""SMT generated Plugin 的 route 决策合同。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.app.runtime.extension_identity import sha256_digest
from src.app.runtime.orchestration.runtime_intent import RuntimeIntentKind
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge import (
    GeneratedPluginAttemptRunner,
    _canonical_plugin_input,
)
from src.app.runtime.system_capabilities.outcomes import BusinessReject, ContractViolation
from src.app.runtime.workline_plugins.attempt_coordinator import AttemptSnapshot, PluginAttemptContext
from src.app.runtime.workline_plugins.contracts import CapabilityEffectResultInput, CommandResultInput
from src.app.runtime.workline_plugins.dispatcher import (
    DeclaredCapabilityGateway,
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
from src.app.wms_integration.ports.fulfillment_operations import MOVE_BINS_FROM_CONVEYOR_EXIT


def _smt_config_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "provider_profile": "runtime",
        "ctu_basket_capacity": 6,
        "conveyor_entry_queue": {
            "code": "SMT-CONVEYOR-ENTRY",
            "role": "ENTRY",
            "capacity": 8,
            "order_policy": "FIFO",
        },
        "return_queue": {
            "code": "SMT-RETURN",
            "role": "RETURN_QUEUE",
            "order_policy": "FIFO",
        },
    }
    payload.update(overrides)
    return payload


def _smt_config(**overrides: object) -> SmtSortingInboundConfig:
    return SmtSortingInboundConfig.model_validate(_smt_config_payload(**overrides))


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
    source = PluginAttemptFactSource(
        snapshot=snapshot,
        device_fact_versions=(("SORTING_SOURCE_ARM", 31, 0),),
        **overrides,
    )
    return build_facts(source)


def _source_pick_input() -> SourcePickRequestInput:
    return SourcePickRequestInput(
        handoff_demand_id=11,
        handoff_source_item_id=12,
        claim_attempt_no=3,
        source_pick_request_event_id="source-pick-event-31",
    )


@pytest.mark.asyncio
async def test_source_pick_request_waits_for_its_command_result() -> None:
    decision = await decide(
        _source_pick_input(),
        state=SmtSortingInboundState(),
        config=_smt_config(),
        facts=_facts(),
        gateway=object(),
    )

    assert decision.outcome_code == "SOURCE_PICK_REQUESTED"
    assert decision.next_state.current_correlation is None
    assert len(decision.intents) == 1


@pytest.mark.asyncio
async def test_source_pick_request_carries_strict_command_correlation_evidence() -> None:
    source_pick = SourcePickRequestInput(
        handoff_demand_id=11,
        handoff_source_item_id=12,
        claim_attempt_no=3,
        source_pick_request_event_id="source-pick-event-31",
    )

    decision = await decide(
        source_pick,
        state=SmtSortingInboundState(),
        config=_smt_config(),
        facts=_facts(),
        gateway=object(),
    )

    [command] = decision.intents
    assert command.capability_key == "material_flow.smt_source_pick_command"
    assert command.payload_json["target_device_id"] == 31
    assert command.payload_json["command_code"].startswith("SC-")
    assert command.payload_json["payload"] == {
        "handoff_demand_id": 11,
        "handoff_source_item_id": 12,
        "claim_attempt_no": 3,
        "source_pick_request_event_id": "source-pick-event-31",
    }


def test_internal_source_pick_event_normalizes_to_generated_route_with_inbox_evidence() -> None:
    inbox = SimpleNamespace(
        id=31,
        kind="INTERNAL_EVENT",
        event_type="SORTING_SOURCE_PICK_REQUESTED",
        event_id="source-pick-event-31",
        payload_json={
            "logical_route": "SOURCE_PICK_REQUESTED",
            "input": {
                "route": "SOURCE_PICK_REQUESTED",
                "handoff_demand_id": 11,
                "handoff_source_item_id": 12,
                "claim_attempt_no": 3,
                "source_pick_request_event_id": "source-pick-event-31",
            },
        },
    )

    logical_route, raw_input = _canonical_plugin_input(inbox)

    assert logical_route == "SOURCE_PICK_REQUESTED"
    assert raw_input == {
        "route": "SOURCE_PICK_REQUESTED",
        "handoff_demand_id": 11,
        "handoff_source_item_id": 12,
        "claim_attempt_no": 3,
        "source_pick_request_event_id": "source-pick-event-31",
    }


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
        _source_pick_input(),
        state=state,
        config=_smt_config(),
        facts=_facts(),
        gateway=object(),
    )

    assert decision.outcome_code == "SOURCE_PICK_REQUEST_IGNORED"
    assert decision.next_state == state
    assert decision.intents == ()


def test_smt_definition_uses_fixed_generated_identity() -> None:
    assert DEFINITION.contract_version == "smt_sorting_inbound.v1"


def test_smt_factory_config_declares_frozen_typed_queues() -> None:
    config = _smt_config()

    assert config.ctu_basket_capacity == 6
    assert config.conveyor_entry_queue.model_dump(mode="json") == {
        "code": "SMT-CONVEYOR-ENTRY",
        "role": "ENTRY",
        "capacity": 8,
        "order_policy": "FIFO",
    }
    assert config.return_queue.model_dump(mode="json") == {
        "code": "SMT-RETURN",
        "role": "RETURN_QUEUE",
        "order_policy": "FIFO",
    }
    with pytest.raises(ValidationError):
        config.conveyor_entry_queue.capacity = 9
    with pytest.raises(ValidationError):
        config.return_queue.code = "CHANGED"


@pytest.mark.parametrize(
    ("field_path", "invalid_capacity"),
    [
        (("ctu_basket_capacity",), True),
        (("ctu_basket_capacity",), 0),
        (("ctu_basket_capacity",), -1),
        (("conveyor_entry_queue", "capacity"), True),
        (("conveyor_entry_queue", "capacity"), 0),
        (("conveyor_entry_queue", "capacity"), -1),
    ],
)
def test_smt_factory_config_rejects_non_positive_and_boolean_capacities(
    field_path: tuple[str, ...],
    invalid_capacity: object,
) -> None:
    payload = _smt_config_payload()
    target = payload
    for segment in field_path[:-1]:
        target = target[segment]  # type: ignore[assignment]
    target[field_path[-1]] = invalid_capacity

    with pytest.raises(ValidationError):
        SmtSortingInboundConfig.model_validate(payload)


def test_smt_factory_config_rejects_duplicate_queue_codes() -> None:
    payload = _smt_config_payload(
        return_queue={
            "code": "SMT-CONVEYOR-ENTRY",
            "role": "RETURN_QUEUE",
            "order_policy": "FIFO",
        }
    )

    with pytest.raises(ValidationError, match="code"):
        SmtSortingInboundConfig.model_validate(payload)


@pytest.mark.parametrize(
    ("queue_name", "field_name", "invalid_value"),
    [
        ("conveyor_entry_queue", "role", "RETURN_QUEUE"),
        ("return_queue", "role", "ENTRY"),
        ("conveyor_entry_queue", "order_policy", "LIFO"),
        ("return_queue", "order_policy", "LIFO"),
    ],
)
def test_smt_factory_config_rejects_invalid_queue_role_or_order_policy(
    queue_name: str,
    field_name: str,
    invalid_value: str,
) -> None:
    payload = _smt_config_payload()
    queue = payload[queue_name]
    assert isinstance(queue, dict)
    queue[field_name] = invalid_value

    with pytest.raises(ValidationError):
        SmtSortingInboundConfig.model_validate(payload)


def test_smt_factory_config_reads_ctu_limit_from_typed_wms_operation_definition() -> None:
    max_candidate_count = MOVE_BINS_FROM_CONVEYOR_EXIT.max_candidate_count
    assert isinstance(max_candidate_count, int)
    assert _smt_config(ctu_basket_capacity=max_candidate_count).ctu_basket_capacity == max_candidate_count

    with pytest.raises(ValidationError, match="max_candidate_count"):
        _smt_config(ctu_basket_capacity=max_candidate_count + 1)


def test_smt_factory_config_forbids_pipeline_queue_dsl() -> None:
    with pytest.raises(ValidationError, match="pipeline_queues"):
        _smt_config(pipeline_queues=[])


def test_smt_definition_declares_source_arm_command_and_effect_contract() -> None:
    assert DEFINITION.schema.devices[0].role == "SORTING_SOURCE_ARM"
    assert DEFINITION.schema.commands[0].command == "SORTING_SOURCE_PICK"
    assert DEFINITION.schema.commands[0].target_device_role == "SORTING_SOURCE_ARM"
    assert DEFINITION.allowed_capabilities == (
        ("material_flow.smt_source_pick_command", "v1"),
        ("material_flow.smt_source_pick_ledger", "v1"),
        ("runtime.session_hold", "v1"),
        ("wms.fulfillment.move_bins_from_conveyor_exit", "v1"),
        ("wms.fulfillment.move_bins_to_conveyor_entry", "v1"),
    )

    with pytest.raises(ValueError):
        _smt_config(source_arm_role="UNDECLARED_ARM")


@pytest.mark.asyncio
async def test_other_plugin_cannot_invoke_smt_source_pick_command_capability() -> None:
    class _ForbiddenUnderlyingGateway:
        async def execute(self, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("undeclared cross-plugin capability must not reach the runtime gateway")

    gateway = DeclaredCapabilityGateway(
        _ForbiddenUnderlyingGateway(),
        allowed_capabilities=frozenset({("material_flow.material_unit_write", "v1")}),
    )

    result = await gateway.execute(
        "material_flow.smt_source_pick_command",
        "v1",
        {"action": "SORTING_SOURCE_PICK"},
    )

    assert isinstance(result.outcome, ContractViolation)
    assert result.outcome.error_code == "CAPABILITY_NOT_DECLARED"


@pytest.mark.asyncio
async def test_smt_source_pick_binds_generated_command_code_for_command_result() -> None:
    config = _smt_config()
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
        raw_input=_source_pick_input().model_dump(mode="json"),
        fact_source=PluginAttemptFactSource(
            snapshot=snapshot,
            device_fact_versions=(("SORTING_SOURCE_ARM", 31, 0),),
        ),
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
    assert command.capability_key == "material_flow.smt_source_pick_command"
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
    ledger_effect, continue_next = result.intents
    assert ledger_effect.kind is RuntimeIntentKind.SYSTEM_CAPABILITY
    assert ledger_effect.capability_key == "material_flow.smt_source_pick_ledger"
    assert ledger_effect.payload_json == {
        "operation": "RECORD_PICKED",
        "command_code": command.payload_json["command_code"],
    }
    assert continue_next.kind is RuntimeIntentKind.CONTINUE_NEXT


@pytest.mark.asyncio
async def test_smt_failed_command_result_converts_to_declared_session_hold() -> None:
    config = _smt_config()
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
        config=_smt_config(),
        facts=_facts(),
        gateway=object(),
    )

    assert decision.outcome_code == expected
    if result == "SUCCESS":
        ledger_effect, continue_next = decision.intents
        assert ledger_effect.capability_key == "material_flow.smt_source_pick_ledger"
        assert continue_next.kind is RuntimeIntentKind.CONTINUE_NEXT


@pytest.mark.asyncio
async def test_source_pick_correlation_mismatch_and_capability_reject_have_zero_effect() -> None:
    config = _smt_config()
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
