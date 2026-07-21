"""粗分机旧 QUERY capability 到纯 policy 的临时边界合同。"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.app.runtime.capabilities.material_flow.contracts.rough_sorter_inventory_admission import (
    RoughSorterBindingSnapshot,
    RoughSorterInventoryQueryOutcomeKind,
)
from src.app.runtime.capabilities.material_flow.rough_sorter_inventory_admission_policy import (
    decide_rough_sorter_inventory_admission as real_policy,
)
from src.app.runtime.system_capabilities import BusinessReject, ContractViolation, RetryableFailure, Success
from src.app.runtime.system_capabilities.wms.rough_sorter_inventory_admission import handler as handler_module
from src.app.runtime.system_capabilities.wms.rough_sorter_inventory_admission.contracts import (
    RoughSorterInventoryAdmissionInput,
)
from src.app.runtime.system_capabilities.wms.rough_sorter_inventory_admission.handler import (
    RoughSorterInventoryAdmissionHandler,
)
from src.app.wms_integration.ports.query_inventory_operation import (
    OPERATION_IDENTITY,
    InventoryAuthorityItem,
    InventoryQueryOperationResult,
)
from src.app.wms_integration.ports.query_outcome import (
    QueryBusinessReject,
    QueryContractFailure,
    QuerySuccess,
    QueryTechnicalFailure,
)


def _request(*, profile_identity: str = "wms.2026-07-06.material-flow.sandbox") -> RoughSorterInventoryAdmissionInput:
    return RoughSorterInventoryAdmissionInput(
        business_key="scan:RS-01:evt-001",
        hhpn="MAT-001",
        lot_code="LOT-2026-07",
        warehouse_code="WH-A",
        owner_code="OWNER-A",
        diameter_mm=Decimal("12.50"),
        thickness_mm=Decimal("1.20"),
        binding_snapshot=RoughSorterBindingSnapshot(
            binding_id=9,
            binding_version=2,
            profile_identity=profile_identity,
            plugin_config_hash="a" * 64,
            generated_index_digest="b" * 64,
        ),
    )


class _Port:
    def __init__(self, outcome: object) -> None:
        self.outcome = outcome
        self.requests: list[object] = []

    async def execute(self, request: object) -> object:
        self.requests.append(request)
        return self.outcome


SUCCESS_RESULT = InventoryQueryOperationResult(
    items=(
        InventoryAuthorityItem(
            material_code="MAT-001",
            available_quantity=Decimal("2.25"),
            warehouse_code="WH-A",
            lot_no="LOT-2026-07",
        ),
    ),
    source_version="WMS-42",
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query_outcome", "expected_kind", "expected_runtime_type", "expected_code"),
    [
        (QuerySuccess(SUCCESS_RESULT, evidence_key="ev-success"), "SUCCESS", Success, None),
        (
            QueryBusinessReject("SKU_REJECTED", "rejected", "ev-reject"),
            "BUSINESS_REJECT",
            BusinessReject,
            "SKU_REJECTED",
        ),
        (
            QueryTechnicalFailure("PROVIDER_TIMEOUT", "timeout", True, evidence_key="ev-timeout"),
            "TECHNICAL_FAILURE",
            RetryableFailure,
            "WMS_TIMEOUT",
        ),
        (
            QueryTechnicalFailure("AUTH_INVALID", "auth", False, evidence_key="ev-auth"),
            "TECHNICAL_FAILURE",
            ContractViolation,
            "AUTH_INVALID",
        ),
        (
            QueryContractFailure("WMS_MALFORMED_RESPONSE", "bad", "ev-contract"),
            "CONTRACT_FAILURE",
            ContractViolation,
            "WMS_MALFORMED_RESPONSE",
        ),
        (object(), "INVALID", ContractViolation, "WMS_OUTCOME_INVALID"),
    ],
)
async def test_handler_only_converts_typed_query_outcome_into_policy_input(
    monkeypatch: pytest.MonkeyPatch,
    query_outcome: object,
    expected_kind: str,
    expected_runtime_type: type[object],
    expected_code: str | None,
) -> None:
    captured = []

    def policy_spy(policy_input):
        captured.append(policy_input)
        return real_policy(policy_input)

    monkeypatch.setattr(handler_module, "decide_rough_sorter_inventory_admission", policy_spy)
    port = _Port(query_outcome)

    outcome = await RoughSorterInventoryAdmissionHandler(port)(_request())

    assert len(captured) == 1
    assert captured[0].query_snapshot.outcome_kind == RoughSorterInventoryQueryOutcomeKind(expected_kind)
    assert captured[0].source_operation == OPERATION_IDENTITY
    assert isinstance(outcome, expected_runtime_type)
    if expected_code is not None:
        assert getattr(outcome, "reason_code", getattr(outcome, "error_code", None)) == expected_code


@pytest.mark.asyncio
async def test_profile_mismatch_is_policy_hold_without_query_io(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = []

    def policy_spy(policy_input):
        captured.append(policy_input)
        return real_policy(policy_input)

    monkeypatch.setattr(handler_module, "decide_rough_sorter_inventory_admission", policy_spy)
    port = _Port(QuerySuccess(SUCCESS_RESULT, evidence_key="must-not-query"))

    outcome = await RoughSorterInventoryAdmissionHandler(port)(
        _request(profile_identity="wms.2026-07-06.material-flow.future")
    )

    assert port.requests == []
    assert len(captured) == 1
    assert captured[0].query_snapshot is None
    assert isinstance(outcome, ContractViolation)
    assert outcome.error_code == "WMS_PROFILE_MISMATCH"
