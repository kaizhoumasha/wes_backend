"""粗分机现有 Q14 消费者迁入 registry InventorySnapshot 合同。"""

from decimal import Decimal

import pytest

from src.app.runtime.capabilities.material_flow.contracts.rough_sorter_inventory_admission import (
    RoughSorterBindingSnapshot,
    RoughSorterInventoryAdmissionPolicyInput,
    RoughSorterInventoryQueryOutcomeKind,
    RoughSorterInventoryQuerySnapshot,
)
from src.app.runtime.capabilities.material_flow.rough_sorter_inventory_admission_policy import (
    decide_rough_sorter_inventory_admission,
)
from src.app.wms_integration.ports.inventory_operations import (
    QUERY_INVENTORY,
    InventoryRecord,
    InventorySnapshotQueryResult,
)


def _binding() -> RoughSorterBindingSnapshot:
    return RoughSorterBindingSnapshot(
        binding_id=9,
        binding_version=2,
        profile_identity="wms.2026-07-28.full-factory.sandbox",
        plugin_config_hash="a" * 64,
        generated_index_digest="b" * 64,
    )


def _input(snapshot: RoughSorterInventoryQuerySnapshot) -> RoughSorterInventoryAdmissionPolicyInput:
    return RoughSorterInventoryAdmissionPolicyInput(
        material_code="MAT-001",
        lot_no="LOT-001",
        warehouse_code="WH-A",
        owner_code="OWNER-A",
        binding_snapshot=_binding(),
        supported_profile_identities=("wms.2026-07-28.full-factory.sandbox",),
        source_operation=QUERY_INVENTORY.identity,
        query_snapshot=snapshot,
    )


def _result(*, matched: bool = True) -> InventorySnapshotQueryResult:
    return InventorySnapshotQueryResult(
        items=(
            InventoryRecord(
                material_code="MAT-001",
                lot_no="LOT-001" if matched else "LOT-OTHER",
                available_quantity=Decimal("8.5"),
                total_quantity=Decimal("8.5"),
                reserved_quantity=Decimal("0"),
            ),
        ),
        source_version="42",
    )


@pytest.mark.parametrize(("matched", "decision"), [(True, "ADMIT"), (False, "REJECT")])
def test_inventory_snapshot_success_is_deterministic(matched: bool, decision: str) -> None:
    policy_input = _input(
        RoughSorterInventoryQuerySnapshot(
            outcome_kind=RoughSorterInventoryQueryOutcomeKind.SUCCESS,
            result=_result(matched=matched),
            evidence_key="query:q14:1",
        )
    )

    first = decide_rough_sorter_inventory_admission(policy_input)
    replay = decide_rough_sorter_inventory_admission(policy_input)

    assert first == replay
    assert first.decision == decision
    assert first.provenance.source.operation_identity == QUERY_INVENTORY.identity
    assert first.provenance.source.source_version == "42"


@pytest.mark.parametrize(
    ("kind", "reason", "decision"),
    [
        (RoughSorterInventoryQueryOutcomeKind.BUSINESS_REJECT, "FILTER_REJECTED", "REJECT"),
        (RoughSorterInventoryQueryOutcomeKind.TECHNICAL_FAILURE, "TIMEOUT", "HOLD"),
        (RoughSorterInventoryQueryOutcomeKind.CONTRACT_FAILURE, "MALFORMED", "HOLD"),
    ],
)
def test_non_success_outcomes_remain_closed(
    kind: RoughSorterInventoryQueryOutcomeKind,
    reason: str,
    decision: str,
) -> None:
    result = decide_rough_sorter_inventory_admission(
        _input(
            RoughSorterInventoryQuerySnapshot(
                outcome_kind=kind,
                reason_code=reason,
                message=reason,
                retryable=kind is RoughSorterInventoryQueryOutcomeKind.TECHNICAL_FAILURE,
            )
        )
    )

    assert result.decision == decision
