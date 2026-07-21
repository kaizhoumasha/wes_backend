"""粗分机准入 capability 对封闭 inventory QUERY outcome 的穷尽转换。"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.app.runtime.system_capabilities import BusinessReject, ContractViolation, RetryableFailure, Success
from src.app.runtime.system_capabilities.wms.rough_sorter_inventory_admission.contracts import (
    RoughSorterBindingSnapshot,
    RoughSorterInventoryAdmissionInput,
)
from src.app.runtime.system_capabilities.wms.rough_sorter_inventory_admission.definition import DEFINITION
from src.app.runtime.system_capabilities.wms.rough_sorter_inventory_admission.handler import (
    RoughSorterInventoryAdmissionHandler,
)
from src.app.wms_integration.ports.query_inventory_operation import (
    InventoryAuthorityItem,
    InventoryQueryOperationPort,
    InventoryQueryOperationResult,
)
from src.app.wms_integration.ports.query_outcome import (
    QueryBusinessReject,
    QueryContractFailure,
    QuerySuccess,
    QueryTechnicalFailure,
)


def admission_input(**overrides: object) -> RoughSorterInventoryAdmissionInput:
    values: dict[str, object] = {
        "business_key": "scan:RS-01:evt-001",
        "hhpn": "MAT-001",
        "lot_code": "LOT-2026-07",
        "warehouse_code": "WH-A",
        "owner_code": "OWNER-A",
        "diameter_mm": Decimal("12.50"),
        "thickness_mm": Decimal("1.20"),
        "binding_snapshot": RoughSorterBindingSnapshot(
            binding_id=9,
            binding_version=2,
            profile_identity="wms.2026-07-06.material-flow.sandbox",
            plugin_config_hash="a" * 64,
            generated_index_digest="b" * 64,
        ),
    }
    values.update(overrides)
    return RoughSorterInventoryAdmissionInput(**values)


class FakeInventoryQueryOperationPort:
    def __init__(self, outcome: object) -> None:
        self.outcome = outcome
        self.requests: list[object] = []

    async def execute(self, request):
        self.requests.append(request)
        return self.outcome


def inventory_success(*, source_version: str | None = "WMS-42", items=()) -> QuerySuccess:
    return QuerySuccess(InventoryQueryOperationResult(items=items, source_version=source_version), evidence_key="ev-1")


@pytest.mark.asyncio
async def test_typed_success_preserves_decimal_and_maps_all_filters_once() -> None:
    port = FakeInventoryQueryOperationPort(
        inventory_success(
            items=(
                InventoryAuthorityItem(
                    material_code="MAT-001",
                    available_quantity=Decimal("8.5000000000000000001"),
                    warehouse_code="WH-A",
                    lot_no="LOT-2026-07",
                ),
            )
        )
    )

    outcome = await RoughSorterInventoryAdmissionHandler(port)(admission_input())

    assert isinstance(outcome, Success)
    assert outcome.payload.available_quantity == Decimal("8.5000000000000000001")
    assert outcome.payload.source_version == "WMS-42"
    assert port.requests[0].model_dump(exclude_none=True) == {
        "material_code": "MAT-001",
        "warehouse_code": "WH-A",
        "owner_code": "OWNER-A",
        "lot_no": "LOT-2026-07",
    }


@pytest.mark.asyncio
async def test_explicit_empty_success_remains_business_policy_reject() -> None:
    outcome = await RoughSorterInventoryAdmissionHandler(FakeInventoryQueryOperationPort(inventory_success()))(
        admission_input()
    )

    assert isinstance(outcome, BusinessReject)
    assert outcome.reason_code == "WMS_REJECTED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query_outcome", "runtime_type", "expected_code"),
    [
        (QueryBusinessReject("SKU_REJECTED", "rejected"), BusinessReject, "SKU_REJECTED"),
        (QueryTechnicalFailure("WMS_TIMEOUT", "timeout", True), RetryableFailure, "WMS_TIMEOUT"),
        (QueryTechnicalFailure("AUTH_INVALID", "auth", False), ContractViolation, "AUTH_INVALID"),
        (QueryContractFailure("WMS_MALFORMED_RESPONSE", "bad"), ContractViolation, "WMS_MALFORMED_RESPONSE"),
    ],
)
async def test_closed_query_outcomes_are_exhaustively_translated(query_outcome, runtime_type, expected_code) -> None:
    outcome = await RoughSorterInventoryAdmissionHandler(FakeInventoryQueryOperationPort(query_outcome))(
        admission_input()
    )

    assert isinstance(outcome, runtime_type)
    assert getattr(outcome, "reason_code", getattr(outcome, "error_code", None)) == expected_code


@pytest.mark.asyncio
async def test_missing_source_version_is_contract_violation_not_fabricated() -> None:
    outcome = await RoughSorterInventoryAdmissionHandler(
        FakeInventoryQueryOperationPort(inventory_success(source_version=None))
    )(admission_input())

    assert isinstance(outcome, ContractViolation)
    assert outcome.error_code == "WMS_SOURCE_VERSION_MISSING"


def test_definition_requires_only_operation_scoped_query_port() -> None:
    assert DEFINITION.required_ports == (InventoryQueryOperationPort,)


@pytest.mark.parametrize("field", ["diameter_mm", "thickness_mm"])
def test_measurements_must_be_positive(field: str) -> None:
    with pytest.raises(ValueError):
        admission_input(**{field: 0})
