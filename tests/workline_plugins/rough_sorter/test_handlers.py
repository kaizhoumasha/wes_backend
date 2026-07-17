"""approved 13-case fixture 到粗分机纯 handler 的参数化合同。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from src.app.runtime.system_capabilities.gateway import GatewayQueryResult
from src.app.runtime.system_capabilities.outcomes import BusinessReject, RetryableFailure, Success
from src.app.runtime.system_capabilities.wms.rough_sorter_inventory_admission.contracts import (
    RoughSorterBindingSnapshot,
    RoughSorterInventoryAdmissionOutput,
)
from src.app.runtime.workline_plugins.contracts import PluginContext, PluginDecision
from src.app.runtime.workline_plugins.dispatcher import WorklinePluginDispatcher
from src.app.runtime.workline_plugins.rough_sorter.config import RoughSorterConfig
from src.app.runtime.workline_plugins.rough_sorter.definition import DEFINITION, ROUTE_HANDLERS
from src.app.runtime.workline_plugins.rough_sorter.handlers import RoughSorterFacts, decide
from src.app.runtime.workline_plugins.rough_sorter.inputs import (
    parse_business_timeout,
    parse_pick_and_put_result,
    parse_replay_request,
    parse_scan_completed,
)
from src.app.runtime.workline_plugins.rough_sorter.state import RoughSorterState

if TYPE_CHECKING:
    from src.app.runtime.orchestration.runtime_intent import RuntimeIntent

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_PATH = REPO_ROOT / "tests/fixtures/workline_contract/rough_sorter/scan_decision_cases.json"


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
        "COMMAND_RESULT": parse_pick_and_put_result,
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
            plugin_config_hash="a" * 64,
            generated_index_digest="b" * 64,
        ),
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
        assert (capability_key, contract_version) == ("wms.rough_sorter_inventory_admission", "v1")
        assert input_data is not None
        self.calls += 1
        admission = self.discriminator.get("wms_admission")
        if admission == "ADMIT":
            outcome = Success(
                payload=RoughSorterInventoryAdmissionOutput(
                    accepted=True,
                    material_code="HH-001",
                    batch_no="LOT-01",
                    warehouse_code="WH-01",
                    matched_item_count=1,
                    available_quantity=1,
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
    state = RoughSorterState(phase="PICK_TO_PIPELINE", current_correlation="CMD-PICK-CURRENT")
    facts = _facts(case)

    decision = await decide(logical_input, state=state, config=_config(), facts=facts, gateway=gateway)

    assert isinstance(decision, PluginDecision)
    assert decision.outcome_code == case["expected_outcome"]["result"]
    assert _intent_signature(decision.intents) == _expected_signature(case)
    assert decision.capability_identities == tuple(
        dict.fromkeys(intent.source_system for intent in decision.intents if intent.source_system)
    )
    # Task 6 先保存 capability identity；Task 7 才统一转换为 SYSTEM_CAPABILITY kind。
    assert all(intent.source_system is not None for intent in decision.intents)
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
@pytest.mark.parametrize("case", _cases(), ids=lambda case: f"replay-{case['case_id']}")
async def test_replay_never_calls_provider_or_creates_new_effect(case: dict[str, Any]) -> None:
    logical_input = _logical_input(case)
    gateway = _Gateway(case["trigger"]["decision_discriminator"])

    decision = await decide(
        logical_input,
        state=RoughSorterState(phase="PICK_TO_PIPELINE"),
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
    dispatcher = WorklinePluginDispatcher(
        plugin_index={(DEFINITION.plugin_key, DEFINITION.contract_version): DEFINITION},
        route_handlers=ROUTE_HANDLERS,
    )

    result = await dispatcher.dispatch(
        plugin_key=DEFINITION.plugin_key,
        contract_version=DEFINITION.contract_version,
        logical_route="SCAN_COMPLETED",
        raw_input=case["trigger"]["payload"],
        state=RoughSorterState(),
        context=PluginContext(state=RoughSorterState()),
        handler_kwargs={"config": _config(), "facts": _facts(case)},
        gateway=_Gateway(case["trigger"]["decision_discriminator"]),
    )

    assert isinstance(result, PluginDecision)
    assert result.outcome_code == "PICK_AND_PUT_PERSISTED"


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["identity", "route", "state", "capability"])
async def test_dispatcher_fails_closed_for_invalid_contract(mutation: str) -> None:
    case = next(item for item in _cases() if item["case_id"] == "RS-SD-001")
    dispatcher = WorklinePluginDispatcher(
        plugin_index={(DEFINITION.plugin_key, DEFINITION.contract_version): DEFINITION},
        route_handlers=ROUTE_HANDLERS,
    )
    kwargs: dict[str, Any] = {
        "plugin_key": DEFINITION.plugin_key,
        "contract_version": DEFINITION.contract_version,
        "logical_route": "SCAN_COMPLETED",
        "raw_input": case["trigger"]["payload"],
        "state": RoughSorterState(),
        "context": PluginContext(state=RoughSorterState()),
        "handler_kwargs": {"config": _config(), "facts": _facts(case)},
        "gateway": _Gateway(case["trigger"]["decision_discriminator"]),
        "requested_capabilities": (),
    }
    if mutation == "identity":
        kwargs["contract_version"] = "unknown"
    elif mutation == "route":
        kwargs["logical_route"] = "UNKNOWN"
    elif mutation == "state":
        kwargs["state"] = {"phase": 123}
    else:
        kwargs["requested_capabilities"] = (("undeclared", "v1"),)

    result = await dispatcher.dispatch(**kwargs)

    assert result.kind == "contract_violation"
