import json
from typing import Annotated

import pytest
from pydantic import BaseModel, Field, TypeAdapter, ValidationError, field_validator

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


class RejectingPayload(BaseModel):
    item_id: int

    @field_validator("item_id")
    @classmethod
    def reject_item(cls, value: int) -> int:
        raise ValueError(f"item {value} is invalid")


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


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_retryable_failure_rejects_non_finite_retry_delay(value: float) -> None:
    with pytest.raises(ValidationError):
        RetryableFailure(error_code="PORT_TIMEOUT", message="端口调用超时", retry_after_seconds=value)


def test_retryable_failure_finite_retry_delay_round_trip_preserves_semantics() -> None:
    original = RetryableFailure(error_code="PORT_TIMEOUT", message="端口调用超时", retry_after_seconds=1.5)

    restored = RetryableFailure.model_validate_json(original.model_dump_json())

    assert restored == original
    assert restored.retry_after_seconds == 1.5


@pytest.mark.parametrize(
    ("model", "required"),
    [
        (BusinessReject, {"reason_code": "NOT_ALLOWED", "message": "业务拒绝"}),
        (RetryableFailure, {"error_code": "PORT_TIMEOUT", "message": "端口超时"}),
        (ContractViolation, {"error_code": "INVALID_CONTRACT", "message": "合同不合法"}),
    ],
)
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_outcome_details_recursively_reject_non_finite_numbers(
    model: type[BaseModel], required: dict[str, str], value: float
) -> None:
    with pytest.raises(ValidationError):
        model(**required, details={"nested": {"values": [value]}})


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


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_non_standard_json_numeric_constants_fail_closed(constant: str) -> None:
    raw = f'{{"kind":"business_reject","reason_code":"NOT_ALLOWED","message":"拒绝","details":{{"value":{constant}}}}}'

    outcome = parse_outcome(raw, payload_type=Payload)

    assert isinstance(outcome, ContractViolation)
    assert outcome.error_code == "INVALID_OUTCOME_JSON"
    assert json.loads(outcome.model_dump_json())["kind"] == "contract_violation"


def test_validation_error_contract_violation_remains_json_serializable() -> None:
    outcome = parse_outcome({"kind": "success", "payload": {"item_id": 7}}, payload_type=RejectingPayload)

    assert isinstance(outcome, ContractViolation)
    assert outcome.error_code == "INVALID_OUTCOME_CONTRACT"
    assert json.loads(outcome.model_dump_json())["details"]["validation_errors"]


def test_outcome_details_reject_non_json_values() -> None:
    with pytest.raises(ValidationError):
        ContractViolation(error_code="INVALID_DETAILS", message="invalid", details={"value": object()})


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
