"""E08/E09 履约域投影的静态 operation 合同。"""

from __future__ import annotations

from pydantic import ValidationError

from src.app.wms_integration.operation_registry import EFFECT_OPERATIONS

E08 = "wms.fulfillment.request_rack_supply@v1"
E09 = "wms.fulfillment.request_rack_transport@v1"
E11 = "wms.fulfillment.full_box_exchange@v1"


def _operation(identity: str):
    return next(operation for operation in EFFECT_OPERATIONS if operation.identity == identity)


def test_only_e08_e09_and_e11_declare_a_domain_projection_kind() -> None:
    projected = {
        operation.identity: operation.domain_projection_kind
        for operation in EFFECT_OPERATIONS
        if operation.domain_projection_kind is not None
    }

    assert set(projected) == {E08, E09, E11}
    assert len(set(projected.values())) == 3


def test_domain_projection_kind_is_part_of_the_frozen_definition_schema() -> None:
    field = type(_operation(E08)).model_fields.get("domain_projection_kind")

    assert field is not None
    assert field.default is None


def test_unrelated_effect_cannot_claim_an_e08_or_e09_domain_projection_kind() -> None:
    e08 = _operation(E08)
    unrelated = next(
        operation
        for operation in EFFECT_OPERATIONS
        if operation.identity not in {E08, E09}
        and operation.completion_mode is not None
        and operation.completion_mode.value == "ASYNC_TASK"
    )
    payload = unrelated.model_dump(mode="python", exclude_computed_fields=True)
    payload["domain_projection_kind"] = e08.domain_projection_kind

    try:
        type(unrelated).model_validate(payload)
    except ValidationError as exc:
        assert "domain_projection_kind" in str(exc)
    else:
        raise AssertionError("non-E08/E09 operation accepted a fulfillment domain projection kind")
