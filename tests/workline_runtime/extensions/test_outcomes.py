import json
from typing import Annotated

import pytest
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from src.app.runtime.orchestration.runtime_intent import RuntimeIntent
from src.app.runtime.system_capabilities import (
    BusinessReject,
    ContractViolation,
    RetryableFailure,
    Success,
    parse_outcome,
)
from src.app.runtime.workline_plugins import MAX_PLUGIN_DECISION_INTENTS, PluginContext, PluginDecision


class Payload(BaseModel):
    item_id: int


class PluginState(BaseModel):
    scans: int


Outcome = Annotated[
    Success[Payload] | BusinessReject | RetryableFailure | ContractViolation,
    Field(discriminator="kind"),
]


def test_outcome_discriminant_and_typed_payload_round_trip() -> None:
    adapter = TypeAdapter(Outcome)
    original = Success[Payload](payload=Payload(item_id=7))

    restored = adapter.validate_json(original.model_dump_json())

    assert restored == original
    assert restored.kind == "success"
    assert isinstance(restored.payload, Payload)


def test_business_reject_is_data_with_stable_reason_code() -> None:
    reject = BusinessReject(reason_code="MATERIAL_NOT_ALLOWED", message="物料不可进入当前工位")

    restored = BusinessReject.model_validate_json(reject.model_dump_json())

    assert restored == reject
    assert restored.kind == "business_reject"
    assert restored.reason_code == "MATERIAL_NOT_ALLOWED"
    assert restored.retryable is False


def test_retryable_failure_has_stable_error_code_and_retry_semantics() -> None:
    failure = RetryableFailure(error_code="PORT_TIMEOUT", message="端口调用超时", retry_after_seconds=1.5)

    restored = RetryableFailure.model_validate_json(failure.model_dump_json())

    assert restored == failure
    assert restored.kind == "retryable_failure"
    assert restored.error_code == "PORT_TIMEOUT"
    assert restored.retryable is True


@pytest.mark.parametrize(
    ("model", "field"),
    [
        (BusinessReject, "reason_code"),
        (RetryableFailure, "error_code"),
        (ContractViolation, "error_code"),
    ],
)
def test_failure_codes_must_be_non_empty_and_stable(model: type[BaseModel], field: str) -> None:
    values = {field: " ", "message": "invalid"}
    with pytest.raises(ValidationError):
        model(**values)


def test_unknown_fifth_outcome_maps_to_contract_violation() -> None:
    unknown = json.dumps({"kind": "deferred", "ticket": "T-1"})

    outcome = parse_outcome(unknown, payload_type=Payload)

    assert isinstance(outcome, ContractViolation)
    assert outcome.kind == "contract_violation"
    assert outcome.error_code == "UNKNOWN_OUTCOME_KIND"
    assert outcome.retryable is False


def test_unhashable_outcome_kind_maps_to_contract_violation_without_exception() -> None:
    outcome = parse_outcome({"kind": []}, payload_type=Payload)

    assert isinstance(outcome, ContractViolation)
    assert outcome.error_code == "INVALID_OUTCOME_CONTRACT"
    assert outcome.retryable is False


def test_invalid_utf8_outcome_maps_to_contract_violation_without_exception() -> None:
    outcome = parse_outcome(b"\x80", payload_type=Payload)

    assert isinstance(outcome, ContractViolation)
    assert outcome.error_code == "INVALID_OUTCOME_JSON"
    assert outcome.retryable is False


def test_plugin_context_and_decision_validate_state_without_executing_intents() -> None:
    context = PluginContext[PluginState](state=PluginState(scans=1))
    intent = RuntimeIntent.complete()
    decision = PluginDecision[PluginState](
        intents=(intent,),
        next_state=PluginState(scans=2),
        outcome_code="SCAN_ACCEPTED",
    )

    assert context.state.scans == 1
    assert decision.intents == (intent,)
    assert decision.next_state.scans == 2
    assert decision.outcome_code == "SCAN_ACCEPTED"


def test_plugin_decision_rejects_too_many_intents_and_wrong_state_type() -> None:
    intent = RuntimeIntent.complete()
    with pytest.raises(ValidationError):
        PluginDecision[PluginState](
            intents=(intent,) * (MAX_PLUGIN_DECISION_INTENTS + 1),
            next_state=PluginState(scans=2),
            outcome_code="TOO_MANY",
        )
    with pytest.raises(ValidationError):
        PluginDecision[PluginState](
            intents=(),
            next_state={"unexpected": True},
            outcome_code="INVALID_STATE",
        )
