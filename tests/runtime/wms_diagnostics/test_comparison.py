"""比较只解释正式 schema 与当次错误，不重执行业务。"""

from pydantic import BaseModel, ConfigDict, Field

from src.app.wms_diagnostics.comparison import compare_fields


class RequestContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    count: int = Field(ge=1)
    note: str | None


def test_missing_and_null_have_different_presence_and_actual_values() -> None:
    fields = compare_fields(RequestContract.model_json_schema(), {"count": 2}, side="request", source="contract@build")
    note = next(item for item in fields if item.path == "$.note")
    assert note.actual_present is False
    assert "required" in note.expected_rule
    fields = compare_fields(
        RequestContract.model_json_schema(), {"count": 2, "note": None}, side="request", source="contract@build"
    )
    note = next(item for item in fields if item.path == "$.note")
    assert note.actual_present is True and note.actual_value is None


def test_actual_validation_error_supplies_failure_without_guessing_business_expectations() -> None:
    fields = compare_fields(
        RequestContract.model_json_schema(),
        {"count": "2", "note": None},
        side="request",
        source="contract@build",
        errors=({"loc": ("count",), "type": "int_type"},),
        validated=False,
    )
    count = next(item for item in fields if item.path == "$.count")
    assert count.verdict == "ERROR"
    assert count.actual_value == "2"
    assert count.expected_value is None
    assert count.source == "contract@build"


def test_no_validation_evidence_never_claims_pass() -> None:
    fields = compare_fields(
        RequestContract.model_json_schema(), {"count": 2, "note": None}, side="response", source="contract@build"
    )
    assert all(item.verdict == "NOT_VALIDATED" for item in fields)


def test_comparison_displays_frozen_identity_from_the_actual_failed_check() -> None:
    fields = compare_fields(
        {"type": "object", "properties": {"operation_id": {"type": "string"}}},
        {"operation_id": "actual-id"},
        side="response",
        source="actual-validator",
        errors=(
            {
                "loc": ("operation_id",),
                "type": "contract_error",
                "msg": "响应 identity 必须匹配请求",
                "expected_value": "frozen-id",
            },
        ),
    )
    field = next(item for item in fields if item.path == "$.operation_id")
    assert field.expected_value == "frozen-id"
    assert field.actual_value == "actual-id"
    assert "响应 identity 必须匹配请求" in field.expected_rule


def test_union_branch_validation_errors_remain_visible_without_guessing_branch() -> None:
    fields = compare_fields(
        {"anyOf": [{"type": "object"}, {"type": "null"}]},
        {"count": "bad"},
        side="response",
        source="actual-validator",
        errors=({"loc": ("AcceptedResponse", "data", "count"), "type": "int_type"},),
    )
    assert any(item.verdict == "ERROR" and "int_type" in item.expected_rule for item in fields)


def test_validated_nested_discriminator_shows_only_selected_formal_contract() -> None:
    from src.app.wms_adapter.outbound_picking.completion_confirm_wire import CompletionConfirmDecidedResponse
    from src.core.uuid7 import new_uuid7

    response = CompletionConfirmDecidedResponse.model_validate(
        {
            "operation_id": new_uuid7(),
            "code": "DECIDED",
            "timestamp": 1,
            "data": {"result": "BUSINESS_IN_PROGRESS", "retry_after_ms": 100},
        }
    )
    fields = compare_fields(
        CompletionConfirmDecidedResponse.model_json_schema(),
        response.model_dump(mode="json"),
        side="response",
        source="actual-validator",
        validated=True,
    )
    retry = next(item for item in fields if item.path == "$.data.retry_after_ms")
    assert '"minimum": 1' in retry.expected_rule
    assert '"maximum": 60000' in retry.expected_rule
    assert not any(item.path == "$.data.current_plan_revision" for item in fields)
