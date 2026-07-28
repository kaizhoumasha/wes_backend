"""approved 13-case fixture 到粗分机纯 handler 的参数化合同。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from src.app.runtime.capabilities.material_flow.contracts.rough_sorter_inventory_admission import (
    RoughSorterBindingSnapshot,
)
from src.app.runtime.extension_identity import sha256_digest
from src.app.runtime.orchestration.runtime_intent import BlockScope
from src.app.runtime.system_capabilities.gateway import GatewayQueryResult
from src.app.runtime.system_capabilities.outcomes import BusinessReject, ContractViolation, RetryableFailure, Success
from src.app.runtime.workline_plugins.contracts import PluginDecision
from src.app.runtime.workline_plugins.dispatcher import (
    HandlerRegistration,
    PinnedPluginSnapshot,
    PluginAttemptFactSource,
    PluginDispatchRequest,
    WorklinePluginDispatcher,
)
from src.app.runtime.workline_plugins.generated_index import WORKLINE_PLUGIN_INDEX_DIGEST
from src.app.runtime.workline_plugins.rough_sorter.config import RoughSorterConfig
from src.app.runtime.workline_plugins.rough_sorter.definition import DEFINITION
from src.app.runtime.workline_plugins.rough_sorter.handlers import RoughSorterFacts, build_facts, decide
from src.app.runtime.workline_plugins.rough_sorter.inputs import (
    parse_business_timeout,
    parse_capability_effect_result,
    parse_command_result,
    parse_replay_request,
    parse_scan_completed,
)
from src.app.runtime.workline_plugins.rough_sorter.state import RoughSorterState
from src.app.wms_integration.ports.query_inventory_operation import (
    InventoryAuthorityItem,
    InventoryQueryOperationRequest,
    InventoryQueryOperationResult,
)

if TYPE_CHECKING:
    from src.app.runtime.orchestration.runtime_intent import RuntimeIntent

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_PATH = REPO_ROOT / "tests/fixtures/workline_contract/rough_sorter/scan_decision_cases.json"
MATERIAL_EFFECT = "material_flow.material_unit_write@v1"
DEVICE_EFFECT = "device.device_command_write@v1"
HOLD_EFFECT = "runtime.session_hold@v1"
EXPECTED_PHASE = {
    "RS-SD-001": "PICK_TO_PIPELINE",
    "RS-SD-002": "NG_MOVING",
    "RS-SD-003": "READY",
    "RS-SD-004": "MOVING_FORWARD",
    "RS-SD-005": "NG_MOVING",
    "RS-SD-006": "NG_MOVING",
    "RS-SD-007": "PICK_TO_PIPELINE",
    "RS-SD-008": "PICK_TO_PIPELINE",
    "RS-SD-009": "PICK_TO_PIPELINE",
    "RS-SD-010": "PICK_TO_PIPELINE",
    "RS-SD-011": "PICK_TO_PIPELINE",
    "RS-SD-012": "PICK_TO_PIPELINE",
    "RS-SD-013": "PICK_TO_PIPELINE",
}
EXPECTED_CAPABILITY_IDENTITIES = {
    "RS-SD-001": (MATERIAL_EFFECT, DEVICE_EFFECT),
    "RS-SD-002": (MATERIAL_EFFECT, DEVICE_EFFECT),
    "RS-SD-003": (HOLD_EFFECT,),
    "RS-SD-004": (MATERIAL_EFFECT, DEVICE_EFFECT),
    "RS-SD-005": (MATERIAL_EFFECT, DEVICE_EFFECT),
    "RS-SD-006": (MATERIAL_EFFECT, DEVICE_EFFECT),
    "RS-SD-007": (HOLD_EFFECT,),
    "RS-SD-008": (HOLD_EFFECT,),
    "RS-SD-009": (),
    "RS-SD-010": (HOLD_EFFECT,),
    "RS-SD-011": (),
    "RS-SD-012": (HOLD_EFFECT,),
    "RS-SD-013": (),
}
UNCHANGED_STATE_CASES = {
    "RS-SD-003",
    "RS-SD-007",
    "RS-SD-008",
    "RS-SD-009",
    "RS-SD-010",
    "RS-SD-011",
    "RS-SD-012",
    "RS-SD-013",
}


def _cases() -> list[dict[str, Any]]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["cases"]


def _config() -> RoughSorterConfig:
    return RoughSorterConfig.model_validate(
        {
            "device_roles": {
                "input_arm": "ROUGH_SORTER_INPUT_ARM",
                "conveyor": "ROUGH_SORTER_CONVEYOR",
                "output_arm": "ROUGH_SORTER_OUTPUT_ARM",
            },
            "pipeline_input_location": "PIPELINE-IN-01",
            "pipeline_output_location": "PIPELINE-OUT-01",
            "ng_location": "NG-01",
            "warehouse_code": "WH-01",
            "owner_code": "OWNER-01",
            "provider_profile": "wms.2026-07-06.material-flow.sandbox",
        }
    )


def _logical_input(case: dict[str, Any]) -> object:
    event_type = case["trigger"]["event_type"]
    parser = {
        "SCAN_COMPLETED": parse_scan_completed,
        "COMMAND_RESULT": parse_command_result,
        "TIMER_TIMEOUT": parse_business_timeout,
        "REPLAY_REQUEST": parse_replay_request,
    }[event_type]
    return parser(case["trigger"]["payload"])


def _facts(case: dict[str, Any]) -> RoughSorterFacts:
    data = case["trigger"]["payload"].get("data", {})
    discriminator = case["trigger"]["decision_discriminator"]
    return RoughSorterFacts(
        business_key=data.get("PkgID", "PKG-AUTHORITATIVE"),
        hhpn=data.get("HHPN", "HH-AUTHORITATIVE"),
        lot_code=data.get("LotCode", "LOT-AUTHORITATIVE"),
        correlation_matches=discriminator.get("correlation") != "LATE_OR_UNKNOWN_MISMATCH",
        replay_digest_matches=discriminator.get("duplicate_digest") != "DIFFERENT",
        binding_snapshot=RoughSorterBindingSnapshot(
            binding_id=1,
            binding_version=1,
            profile_identity=_config().provider_profile,
            plugin_config_hash=sha256_digest(_config().model_dump(mode="json")),
            generated_index_digest=WORKLINE_PLUGIN_INDEX_DIGEST,
        ),
    )


def _source_state(case: dict[str, Any]) -> RoughSorterState:
    event_type = case["trigger"]["event_type"]
    command_code = case["trigger"]["payload"].get("command_code")
    if event_type == "SCAN_COMPLETED":
        return RoughSorterState(phase="READY")
    if event_type in {"COMMAND_RESULT", "TIMER_TIMEOUT"}:
        correlation = "CMD-PICK-CURRENT" if case["case_id"] == "RS-SD-013" else command_code
        return RoughSorterState(phase="PICK_TO_PIPELINE", current_correlation=correlation)
    return RoughSorterState(phase="PICK_TO_PIPELINE", current_correlation="CMD-PICK-RECORDED")


def _snapshot(**overrides: object) -> PinnedPluginSnapshot:
    values: dict[str, object] = {
        "plugin_key": DEFINITION.plugin_key,
        "contract_version": DEFINITION.contract_version,
        "binding_identity": "binding:1:1",
        "binding_id": 1,
        "binding_version": 1,
        "config_hash": sha256_digest(_config().model_dump(mode="json")),
        "index_digest": WORKLINE_PLUGIN_INDEX_DIGEST,
        "profile_identity": _config().provider_profile,
    }
    values.update(overrides)
    return PinnedPluginSnapshot.model_validate(values)


def _dispatch_request(case: dict[str, Any], **overrides: object) -> PluginDispatchRequest:
    state = RoughSorterState()
    values: dict[str, object] = {
        "plugin_key": DEFINITION.plugin_key,
        "contract_version": DEFINITION.contract_version,
        "logical_route": "SCAN_COMPLETED",
        "raw_config": _config().model_dump(mode="json"),
        "raw_state": state.model_dump(mode="json"),
        "context_state": state.model_dump(mode="json"),
        "raw_input": case["trigger"]["payload"],
        "fact_source": _fact_source(case),
        "snapshot": _snapshot(),
    }
    values.update(overrides)
    return PluginDispatchRequest.model_validate(values)


def _fact_source(case: dict[str, Any]) -> PluginAttemptFactSource:
    facts = _facts(case)
    return PluginAttemptFactSource(
        snapshot=_snapshot(),
        raw_input=case["trigger"]["payload"],
        material_fact={
            "material_identity_key": facts.business_key,
            "six_in_one": {"HHPN": facts.hhpn, "LotCode": facts.lot_code},
        },
        correlation_matches=facts.correlation_matches,
        replay_digest_matches=facts.replay_digest_matches,
    )


def _intent_signature(intents: tuple[RuntimeIntent, ...]) -> tuple[tuple[str, str | None, str | None], ...]:
    return tuple(
        (
            intent.kind.value,
            intent.action,
            intent.block_scope.value if intent.block_scope is not None else None,
        )
        for intent in intents
    )


def _expected_signature(case: dict[str, Any]) -> tuple[tuple[str, str | None, str | None], ...]:
    return tuple((item["kind"], item.get("action"), item.get("scope")) for item in case["expected_intents"])


class _Gateway:
    def __init__(self, discriminator: dict[str, str]) -> None:
        self.discriminator = discriminator
        self.calls = 0

    async def execute(self, capability_key: str, contract_version: str, input_data: object) -> GatewayQueryResult:
        assert (capability_key, contract_version) == ("wms.inventory.query_inventory", "v1")
        assert isinstance(input_data, InventoryQueryOperationRequest)
        self.calls += 1
        admission = self.discriminator.get("wms_admission")
        if admission == "ADMIT":
            outcome = Success(
                payload=InventoryQueryOperationResult(
                    items=(
                        InventoryAuthorityItem(
                            material_code=input_data.material_code,
                            lot_no=input_data.lot_no,
                            warehouse_code=input_data.warehouse_code,
                            owner_code=input_data.owner_code,
                            available_quantity="1",
                        ),
                    ),
                    source_version="fixture-v1",
                )
            )
        elif admission == "REJECT":
            outcome = BusinessReject(reason_code="WMS_REJECTED", message="WMS rejected")
        else:
            outcome = RetryableFailure(error_code="TIMEOUT", message="WMS timeout")
        return GatewayQueryResult(outcome=outcome, evidence=SimpleNamespace(reference="timeline:wms"))


@pytest.mark.asyncio
@pytest.mark.parametrize("case", _cases(), ids=lambda case: case["case_id"])
async def test_approved_case_maps_to_typed_plugin_decision(case: dict[str, Any]) -> None:
    logical_input = _logical_input(case)
    discriminator = case["trigger"]["decision_discriminator"]
    gateway = _Gateway(discriminator)
    state = _source_state(case)
    facts = _facts(case)

    decision = await decide(logical_input, state=state, config=_config(), facts=facts, gateway=gateway)

    assert isinstance(decision, PluginDecision)
    assert decision.outcome_code == case["expected_outcome"]["result"]
    assert _intent_signature(decision.intents) == _expected_signature(case)
    assert decision.capability_identities == EXPECTED_CAPABILITY_IDENTITIES[case["case_id"]]
    # Task 6 先保存 capability identity；Task 7 才统一转换为 SYSTEM_CAPABILITY kind。
    assert (
        tuple(dict.fromkeys(intent.source_system for intent in decision.intents if intent.source_system))
        == (EXPECTED_CAPABILITY_IDENTITIES[case["case_id"]])
    )
    assert decision.next_state.phase == EXPECTED_PHASE[case["case_id"]]
    if case["case_id"] in {"RS-SD-004", "RS-SD-005", "RS-SD-006"}:
        assert decision.next_state.current_correlation is None
    else:
        assert decision.next_state.current_correlation == state.current_correlation
    fixture_phase = case["expected_state"]["context_phase"]
    if fixture_phase == "UNCHANGED":
        assert case["case_id"] in UNCHANGED_STATE_CASES
    else:
        assert EXPECTED_PHASE[case["case_id"]] == fixture_phase
    if case["case_id"] in UNCHANGED_STATE_CASES:
        assert decision.next_state == state
    elif case["case_id"] == "RS-SD-004":
        assert decision.next_state.measurement_evidence_ref == "measurement:CMD-PICK-004"
        assert decision.next_state.wms_evidence_ref == "timeline:wms"
    elif case["case_id"] == "RS-SD-006":
        assert decision.next_state.wms_evidence_ref == "timeline:wms"
    else:
        assert decision.next_state.measurement_evidence_ref == state.measurement_evidence_ref
        assert decision.next_state.wms_evidence_ref == state.wms_evidence_ref
    assert gateway.calls == (1 if case["case_id"] in {"RS-SD-004", "RS-SD-006", "RS-SD-010"} else 0)
    if case["expected_outcome"]["reason_code"]:
        assert decision.reason_code == case["expected_outcome"]["reason_code"]
    if case["case_id"] == "RS-SD-003":
        assert decision.outcome_code == "HOLD"
        assert all(intent.action != "MOVE_TO_NG" for intent in decision.intents)
    if case["case_id"] == "RS-SD-009":
        assert decision.reconciliation_request is not None
        assert decision.intents == ()
    if case["case_id"] == "RS-SD-013":
        assert decision.evidence_only is True
        assert decision.intents == ()


@pytest.mark.asyncio
async def test_capability_business_reject_enters_recoverable_material_hold() -> None:
    evidence = {
        "capability_key": "material_flow.material_unit_write",
        "contract_version": "v1",
        "operation_key": "mark-ng-1",
        "idempotency_key": "effect-1",
        "payload_hash": "a" * 64,
        "outcome_kind": "business_reject",
        "outcome_code": "STALE_PRECONDITION",
        "outcome": {
            "kind": "business_reject",
            "reason_code": "STALE_PRECONDITION",
            "message": "material fact changed",
            "details": {},
        },
        "occurred_at_ms": 1,
    }
    logical_input = parse_capability_effect_result(
        {
            "logical_route": "CAPABILITY_EFFECT_RESULT",
            "data": {"session_id": 41, "effect_evidence": evidence},
        }
    )
    state = RoughSorterState(phase="NG_MOVING")

    decision = await decide(
        logical_input,
        state=state,
        config=_config(),
        facts=_facts(_cases()[0]),
        gateway=_Gateway({}),
    )

    assert logical_input.effect_evidence.outcome.reason_code == "STALE_PRECONDITION"
    assert decision.next_state == state
    assert decision.reason_code == "STALE_PRECONDITION"
    assert decision.outcome_code == "HOLD"
    assert len(decision.intents) == 1
    assert decision.intents[0].kind.value == "BLOCK"
    assert decision.intents[0].block_scope == BlockScope.MATERIAL
    assert decision.intents[0].reason_code == "STALE_PRECONDITION"
    assert decision.evidence_only is False
    assert decision.zero_new_effect is False


@pytest.mark.asyncio
@pytest.mark.parametrize("case", _cases(), ids=lambda case: f"replay-{case['case_id']}")
async def test_replay_never_calls_provider_or_creates_new_effect(case: dict[str, Any]) -> None:
    logical_input = _logical_input(case)
    gateway = _Gateway(case["trigger"]["decision_discriminator"])

    decision = await decide(
        logical_input,
        state=_source_state(case),
        config=_config(),
        facts=_facts(case),
        gateway=gateway,
        replay=True,
    )

    assert gateway.calls == 0
    assert decision.intents == ()
    assert decision.zero_new_effect is True


@pytest.mark.asyncio
async def test_dispatcher_uses_exact_generated_identity_and_route_without_database() -> None:
    case = next(item for item in _cases() if item["case_id"] == "RS-SD-001")
    dispatcher = WorklinePluginDispatcher()

    result = await dispatcher.dispatch(
        request=_dispatch_request(case),
        gateway=_Gateway(case["trigger"]["decision_discriminator"]),
    )

    assert isinstance(result, PluginDecision)
    assert result.outcome_code == "PICK_AND_PUT_PERSISTED"


@pytest.mark.asyncio
async def test_dispatcher_revalidates_handler_state_model_copy_result() -> None:
    case = next(item for item in _cases() if item["case_id"] == "RS-SD-001")

    async def invalid_handler(*_args: object, **_kwargs: object) -> PluginDecision[RoughSorterState]:
        bypassed = RoughSorterState().model_copy(update={"phase": "NOT_A_REAL_PHASE"})
        return PluginDecision(intents=(), next_state=bypassed, outcome_code="INVALID_STATE")

    key = (DEFINITION.plugin_key, DEFINITION.contract_version, "SCAN_COMPLETED")
    dispatcher = WorklinePluginDispatcher(
        handler_registry={key: (HandlerRegistration(invalid_handler, RoughSorterFacts, build_facts),)}
    )

    result = await dispatcher.dispatch(
        request=_dispatch_request(case),
        gateway=_Gateway(case["trigger"]["decision_discriminator"]),
    )

    assert isinstance(result, ContractViolation)
    assert result.error_code == "PLUGIN_CONTRACT_INVALID"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("identity", "PLUGIN_IDENTITY_UNKNOWN"),
        ("route", "PLUGIN_ROUTE_UNKNOWN"),
        ("state", "PLUGIN_CONTRACT_INVALID"),
        ("context_state", "STATE_CONTEXT_MISMATCH"),
        ("config", "PLUGIN_CONTRACT_INVALID"),
        ("input", "PLUGIN_CONTRACT_INVALID"),
        ("result", "PLUGIN_CONTRACT_INVALID"),
        ("config_hash", "PLUGIN_CONFIG_HASH_MISMATCH"),
        ("index", "PLUGIN_INDEX_DIGEST_MISMATCH"),
        ("profile", "PLUGIN_PROFILE_MISMATCH"),
        ("binding", "PLUGIN_BINDING_IDENTITY_MISMATCH"),
    ],
)
async def test_dispatcher_fails_closed_for_invalid_contract(mutation: str, expected_code: str) -> None:
    case = next(item for item in _cases() if item["case_id"] == "RS-SD-001")
    dispatcher = WorklinePluginDispatcher()
    request_overrides: dict[str, object] = {}
    if mutation == "identity":
        request_overrides["contract_version"] = "unknown"
    elif mutation == "route":
        request_overrides["logical_route"] = "UNKNOWN"
    elif mutation == "state":
        request_overrides["raw_state"] = {"phase": 123}
    elif mutation == "context_state":
        request_overrides["context_state"] = {"phase": "COMPLETED"}
    elif mutation == "config":
        request_overrides["raw_config"] = {"provider_profile": _config().provider_profile}
    elif mutation == "input":
        request_overrides.update({"logical_route": "COMMAND_RESULT", "raw_input": {}})
    elif mutation == "result":
        request_overrides.update(
            {
                "logical_route": "COMMAND_RESULT",
                "raw_input": {
                    "command_code": "CMD-001",
                    "command_type": "PICK_AND_PUT",
                    "result": "PENDING",
                },
            }
        )
    elif mutation == "config_hash":
        request_overrides["snapshot"] = _snapshot(config_hash="c" * 64)
    elif mutation == "index":
        request_overrides["snapshot"] = _snapshot(index_digest="d" * 64)
    elif mutation == "profile":
        request_overrides["snapshot"] = _snapshot(profile_identity="different-profile")
    else:
        request_overrides["snapshot"] = _snapshot(binding_identity="binding:1:2", binding_version=2)
    gateway = _Gateway(case["trigger"]["decision_discriminator"])

    result = await dispatcher.dispatch(request=_dispatch_request(case, **request_overrides), gateway=gateway)

    assert result.kind == "contract_violation"
    assert result.error_code == expected_code
    assert gateway.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case_id", "phase"),
    [
        *(("RS-SD-001", phase) for phase in ("PICK_TO_PIPELINE", "MOVING_FORWARD", "NG_MOVING", "COMPLETED")),
        *(("RS-SD-004", phase) for phase in ("READY", "MOVING_FORWARD", "NG_MOVING", "COMPLETED")),
        *(("RS-SD-009", phase) for phase in ("READY", "COMPLETED")),
    ],
)
async def test_route_phase_mismatch_is_evidence_only_without_query_or_reconciliation(case_id: str, phase: str) -> None:
    case = next(item for item in _cases() if item["case_id"] == case_id)
    command_code = case["trigger"]["payload"].get("command_code")
    state = RoughSorterState(phase=phase, current_correlation=command_code)
    gateway = _Gateway(case["trigger"]["decision_discriminator"])

    decision = await decide(_logical_input(case), state=state, config=_config(), facts=_facts(case), gateway=gateway)

    assert decision.reason_code == "ROUGH_SORTER_PHASE_MISMATCH"
    assert decision.evidence_only is decision.zero_new_effect is True
    assert decision.intents == ()
    assert decision.reconciliation_request is None
    assert decision.next_state == state
    assert gateway.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case_id", "facts_match"), [("RS-SD-004", True), ("RS-SD-004", False), ("RS-SD-009", True), ("RS-SD-009", False)]
)
async def test_result_and_timeout_correlation_mismatch_is_evidence_only(case_id: str, facts_match: bool) -> None:
    case = next(item for item in _cases() if item["case_id"] == case_id)
    command_code = case["trigger"]["payload"]["command_code"]
    state = RoughSorterState(
        phase="PICK_TO_PIPELINE",
        current_correlation=command_code if not facts_match else "DIFFERENT-COMMAND",
    )
    facts = _facts(case).model_copy(update={"correlation_matches": facts_match})
    gateway = _Gateway(case["trigger"]["decision_discriminator"])

    decision = await decide(_logical_input(case), state=state, config=_config(), facts=facts, gateway=gateway)

    assert decision.reason_code == "COMMAND_RESULT_CORRELATION_MISMATCH"
    assert decision.evidence_only is decision.zero_new_effect is True
    assert decision.intents == ()
    assert decision.reconciliation_request is None
    assert decision.next_state == state
    assert gateway.calls == 0


@pytest.mark.asyncio
async def test_handler_normalizes_defensive_query_validation_failure_to_stable_hold() -> None:
    case = next(item for item in _cases() if item["case_id"] == "RS-SD-004")
    state = _source_state(case)
    # 模拟上游绕过 facts schema 的历史快照；正常 dispatcher 会更早拒绝。
    invalid_facts = _facts(case).model_copy(update={"hhpn": "H" * 121})
    gateway = _Gateway(case["trigger"]["decision_discriminator"])

    decision = await decide(_logical_input(case), state=state, config=_config(), facts=invalid_facts, gateway=gateway)

    assert decision.outcome_code == "HOLD"
    assert decision.reason_code == "ROUGH_SORTER_QUERY_CONTRACT_INVALID"
    assert gateway.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command_type", "phase", "result", "expected_outcome", "expected_reason"),
    [
        ("MOVE_FORWARD", "MOVING_FORWARD", "SUCCESS", "MOVE_FORWARD_COMPLETED", None),
        ("MOVE_TO_NG", "NG_MOVING", "SUCCESS", "MOVE_TO_NG_COMPLETED", None),
        ("MOVE_FORWARD", "MOVING_FORWARD", "FAILED", "HOLD", "DEVICE_BUSY"),
        ("MOVE_TO_NG", "NG_MOVING", "FAILED", "HOLD", "DEVICE_BUSY"),
    ],
)
async def test_followup_device_result_is_consumed_without_overlapping_wait(
    command_type: str,
    phase: str,
    result: str,
    expected_outcome: str,
    expected_reason: str | None,
) -> None:
    command_code = f"CMD-{command_type}"
    logical_input = parse_command_result(
        {
            "command_code": command_code,
            "command_type": command_type,
            "result": result,
            "error_detail": {"error_code": "DEVICE_BUSY"} if result == "FAILED" else {},
        }
    )
    state = RoughSorterState(phase=phase, current_correlation=command_code)

    decision = await decide(
        logical_input,
        state=state,
        config=_config(),
        facts=_facts(_cases()[0]),
        gateway=_Gateway({}),
    )

    assert decision.outcome_code == expected_outcome
    assert decision.reason_code == expected_reason
    if result == "SUCCESS":
        assert decision.next_state.current_correlation is None
        assert _intent_signature(decision.intents) == (("CONTINUE_NEXT", f"{command_type}_COMPLETED", None),)
    else:
        assert _intent_signature(decision.intents) == (("BLOCK", None, "COMMAND"),)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command_type", "phase"),
    [("MOVE_FORWARD", "MOVING_FORWARD"), ("MOVE_TO_NG", "NG_MOVING")],
)
async def test_followup_device_timeout_enters_reconciliation_hold(command_type: str, phase: str) -> None:
    command_code = f"CMD-{command_type}"
    decision = await decide(
        parse_business_timeout(
            {
                "command_code": command_code,
                "wait_type": "COMMAND_RESULT",
            }
        ),
        state=RoughSorterState(phase=phase, current_correlation=command_code),
        config=_config(),
        facts=_facts(_cases()[0]),
        gateway=_Gateway({}),
    )

    assert decision.outcome_code == "HOLD"
    assert decision.reason_code == "ROUGH_SORTER_COMMAND_RESULT_TIMEOUT"
    assert decision.reconciliation_request is not None
    assert decision.reconciliation_request.command_code == command_code
    assert decision.reconciliation_request.reason_code == "ROUGH_SORTER_COMMAND_RESULT_TIMEOUT"


@pytest.mark.asyncio
async def test_dispatcher_blocks_handler_undeclared_capability_without_calling_gateway() -> None:
    case = next(item for item in _cases() if item["case_id"] == "RS-SD-001")

    async def hidden_capability_handler(logical_input, *, state, config, facts, context, gateway):
        _ = (logical_input, config, facts, context)
        await gateway.execute("undeclared.capability", "v1", {})
        return PluginDecision(intents=(), next_state=state, outcome_code="MUST_NOT_ESCAPE")

    key = (DEFINITION.plugin_key, DEFINITION.contract_version, "SCAN_COMPLETED")
    dispatcher = WorklinePluginDispatcher(
        handler_registry={key: (HandlerRegistration(hidden_capability_handler, RoughSorterFacts, build_facts),)}
    )
    gateway = _Gateway(case["trigger"]["decision_discriminator"])

    result = await dispatcher.dispatch(request=_dispatch_request(case), gateway=gateway)

    assert isinstance(result, ContractViolation)
    assert result.error_code == "CAPABILITY_NOT_DECLARED"
    assert gateway.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("candidates", "expected_code"),
    [
        ((), "PLUGIN_HANDLER_MISSING"),
        (
            (
                HandlerRegistration(decide, RoughSorterFacts, build_facts),
                HandlerRegistration(decide, RoughSorterFacts, build_facts),
            ),
            "PLUGIN_HANDLER_AMBIGUOUS",
        ),
    ],
)
async def test_dispatcher_distinguishes_missing_and_ambiguous_handler_candidates(
    candidates: tuple[HandlerRegistration, ...], expected_code: str
) -> None:
    case = next(item for item in _cases() if item["case_id"] == "RS-SD-001")
    key = (DEFINITION.plugin_key, DEFINITION.contract_version, "SCAN_COMPLETED")
    dispatcher = WorklinePluginDispatcher(handler_registry={key: candidates})

    result = await dispatcher.dispatch(
        request=_dispatch_request(case), gateway=_Gateway(case["trigger"]["decision_discriminator"])
    )

    assert isinstance(result, ContractViolation)
    assert result.error_code == expected_code


@pytest.mark.asyncio
async def test_dispatcher_propagates_cancellation_without_mapping_business_outcome() -> None:
    case = next(item for item in _cases() if item["case_id"] == "RS-SD-001")

    async def cancelled_handler(logical_input, *, state, config, facts, context, gateway):
        _ = (logical_input, state, config, facts, context, gateway)
        raise asyncio.CancelledError

    key = (DEFINITION.plugin_key, DEFINITION.contract_version, "SCAN_COMPLETED")
    dispatcher = WorklinePluginDispatcher(
        handler_registry={key: (HandlerRegistration(cancelled_handler, RoughSorterFacts, build_facts),)}
    )

    with pytest.raises(asyncio.CancelledError):
        await dispatcher.dispatch(
            request=_dispatch_request(case), gateway=_Gateway(case["trigger"]["decision_discriminator"])
        )


@pytest.mark.asyncio
async def test_dispatcher_does_not_swallow_handler_programming_error() -> None:
    case = next(item for item in _cases() if item["case_id"] == "RS-SD-001")

    async def broken_handler(logical_input, *, state, config, facts, context, gateway):
        _ = (logical_input, state, config, facts, context, gateway)
        raise RuntimeError("programming defect")

    key = (DEFINITION.plugin_key, DEFINITION.contract_version, "SCAN_COMPLETED")
    dispatcher = WorklinePluginDispatcher(
        handler_registry={key: (HandlerRegistration(broken_handler, RoughSorterFacts, build_facts),)}
    )

    with pytest.raises(RuntimeError, match="programming defect"):
        await dispatcher.dispatch(
            request=_dispatch_request(case), gateway=_Gateway(case["trigger"]["decision_discriminator"])
        )
