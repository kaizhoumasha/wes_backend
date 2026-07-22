"""通用 inventory QUERY System Capability 合同。"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.app.runtime.system_capabilities.outcomes import BusinessReject, ContractViolation, RetryableFailure, Success
from src.app.runtime.system_capabilities.wms.inventory.query_inventory.definition import DEFINITION
from src.app.runtime.system_capabilities.wms.inventory.query_inventory.handler import InventoryQueryCapabilityHandler
from src.app.wms_integration.ports.query_inventory_operation import (
    InventoryAuthorityItem,
    InventoryQueryOperationRequest,
    InventoryQueryOperationResult,
)
from src.app.wms_integration.ports.query_outcome import (
    QueryBusinessReject,
    QueryContractFailure,
    QuerySuccess,
    QueryTechnicalFailure,
)


def _result() -> InventoryQueryOperationResult:
    return InventoryQueryOperationResult(
        items=(
            InventoryAuthorityItem(
                material_code="MAT-1",
                lot_no="LOT-1",
                warehouse_code="WH-1",
                available_quantity=Decimal("1.25"),
            ),
        ),
        source_version="provider-v1",
    )


class _Port:
    def __init__(self, outcome: object) -> None:
        self.outcome = outcome
        self.requests: list[InventoryQueryOperationRequest] = []

    async def execute(self, request: InventoryQueryOperationRequest) -> object:
        self.requests.append(request)
        return self.outcome


def test_definition_uses_generic_operation_identity_and_typed_contract() -> None:
    assert (DEFINITION.capability_key, DEFINITION.contract_version) == ("wms.inventory.query_inventory", "v1")
    assert DEFINITION.input_model is InventoryQueryOperationRequest
    assert DEFINITION.output_model is InventoryQueryOperationResult


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("port_outcome", "outcome_type", "stable_code", "query_kind"),
    [
        (QuerySuccess(_result(), evidence_key="ev-success"), Success, None, None),
        (
            QueryBusinessReject("QUERY_FORBIDDEN", "forbidden", evidence_key="ev-reject"),
            BusinessReject,
            "QUERY_FORBIDDEN",
            "BUSINESS_REJECT",
        ),
        (
            QueryTechnicalFailure("TIMEOUT", "timeout", True, 2, "ev-timeout"),
            RetryableFailure,
            "TIMEOUT",
            "TECHNICAL_FAILURE",
        ),
        (
            QueryTechnicalFailure("UPSTREAM_UNAVAILABLE", "offline", False, evidence_key="ev-offline"),
            ContractViolation,
            "UPSTREAM_UNAVAILABLE",
            "TECHNICAL_FAILURE",
        ),
        (
            QueryContractFailure("INVALID_PROVIDER_PAYLOAD", "invalid", evidence_key="ev-contract"),
            ContractViolation,
            "INVALID_PROVIDER_PAYLOAD",
            "CONTRACT_FAILURE",
        ),
    ],
)
async def test_handler_preserves_closed_query_outcome(
    port_outcome: object,
    outcome_type: type[object],
    stable_code: str | None,
    query_kind: str | None,
) -> None:
    port = _Port(port_outcome)
    request = InventoryQueryOperationRequest(material_code="MAT-1", warehouse_code="WH-1", lot_no="LOT-1")

    outcome = await InventoryQueryCapabilityHandler(port)(request)

    assert isinstance(outcome, outcome_type)
    assert port.requests == [request]
    if isinstance(outcome, Success):
        assert outcome.payload == _result()
    else:
        code = getattr(outcome, "reason_code", None) or getattr(outcome, "error_code", None)
        assert code == stable_code
        assert outcome.details["query_outcome_kind"] == query_kind
