"""粗分机库存准入纯 policy 的决策与 provenance 合同。"""

from __future__ import annotations

import ast
import inspect
from decimal import Decimal

import pytest

from src.app.runtime.capabilities.material_flow import rough_sorter_inventory_admission_policy as policy_module
from src.app.runtime.capabilities.material_flow.contracts.rough_sorter_inventory_admission import (
    POLICY_VERSION,
    RoughSorterBindingSnapshot,
    RoughSorterInventoryAdmissionPolicyInput,
    RoughSorterInventoryQueryOutcomeKind,
    RoughSorterInventoryQuerySnapshot,
)
from src.app.runtime.capabilities.material_flow.rough_sorter_inventory_admission_policy import (
    decide_rough_sorter_inventory_admission,
)
from src.app.wms_integration.ports.query_inventory_operation import (
    OPERATION_IDENTITY,
    InventoryAuthorityItem,
    InventoryQueryOperationResult,
)


def _binding(*, profile_identity: str = "wms.2026-07-28.full-factory.sandbox") -> RoughSorterBindingSnapshot:
    return RoughSorterBindingSnapshot(
        binding_id=9,
        binding_version=2,
        profile_identity=profile_identity,
        plugin_config_hash="a" * 64,
        generated_index_digest="b" * 64,
    )


def _query_snapshot(
    *,
    outcome_kind: RoughSorterInventoryQueryOutcomeKind = RoughSorterInventoryQueryOutcomeKind.SUCCESS,
    items: tuple[InventoryAuthorityItem, ...] = (),
    source_version: str | None = "WMS-42",
    evidence_key: str | None = "evidence:wms:42",
    reason_code: str | None = None,
    message: str | None = None,
    retryable: bool | None = None,
) -> RoughSorterInventoryQuerySnapshot:
    result = (
        InventoryQueryOperationResult(items=items, source_version=source_version)
        if outcome_kind is RoughSorterInventoryQueryOutcomeKind.SUCCESS
        else None
    )
    return RoughSorterInventoryQuerySnapshot(
        outcome_kind=outcome_kind,
        result=result,
        evidence_key=evidence_key,
        reason_code=reason_code,
        message=message,
        retryable=retryable,
    )


def _policy_input(
    *,
    binding: RoughSorterBindingSnapshot | None = None,
    query_snapshot: RoughSorterInventoryQuerySnapshot | None = None,
) -> RoughSorterInventoryAdmissionPolicyInput:
    return RoughSorterInventoryAdmissionPolicyInput(
        material_code="MAT-001",
        lot_no="LOT-2026-07",
        warehouse_code="WH-A",
        owner_code="OWNER-A",
        binding_snapshot=binding or _binding(),
        supported_profile_identities=frozenset(
            {
                "wms.2026-07-28.full-factory.sandbox",
                "wms.2026-07-28.full-factory.staging",
                "wms.2026-07-28.full-factory.production",
            }
        ),
        source_operation=OPERATION_IDENTITY,
        query_snapshot=query_snapshot,
    )


MATCH = InventoryAuthorityItem(
    material_code="MAT-001",
    available_quantity=Decimal("8.5000000000000000001"),
    warehouse_code="WH-A",
    owner_code="OWNER-A",
    lot_no="LOT-2026-07",
)
OTHER_LOT = InventoryAuthorityItem(
    material_code="MAT-001",
    available_quantity=Decimal("3"),
    warehouse_code="WH-A",
    owner_code="OWNER-A",
    lot_no="LOT-OTHER",
)
OTHER_WAREHOUSE = InventoryAuthorityItem(
    material_code="MAT-001",
    available_quantity=Decimal("5"),
    warehouse_code="WH-B",
    owner_code="OWNER-A",
    lot_no="LOT-2026-07",
)
OTHER_OWNER = InventoryAuthorityItem(
    material_code="MAT-001",
    available_quantity=Decimal("7"),
    warehouse_code="WH-A",
    owner_code="OWNER-B",
    lot_no="LOT-2026-07",
)
MISSING_OWNER = InventoryAuthorityItem(
    material_code="MAT-001",
    available_quantity=Decimal("6"),
    warehouse_code="WH-A",
    lot_no="LOT-2026-07",
)


@pytest.mark.parametrize(
    ("case_id", "policy_input", "expected_decision", "expected_reason", "expected_source_version"),
    [
        (
            "matching-authority-snapshot",
            _policy_input(query_snapshot=_query_snapshot(items=(MATCH,))),
            "ADMIT",
            "WMS_ADMITTED",
            "WMS-42",
        ),
        (
            "explicit-empty-authority-snapshot",
            _policy_input(query_snapshot=_query_snapshot()),
            "REJECT",
            "WMS_REJECTED",
            "WMS-42",
        ),
        (
            "non-matching-authority-snapshot",
            _policy_input(query_snapshot=_query_snapshot(items=(OTHER_LOT,))),
            "REJECT",
            "WMS_REJECTED",
            "WMS-42",
        ),
        (
            "other-owner-authority-snapshot",
            _policy_input(query_snapshot=_query_snapshot(items=(OTHER_OWNER,))),
            "REJECT",
            "WMS_REJECTED",
            "WMS-42",
        ),
        (
            "missing-owner-authority-snapshot",
            _policy_input(query_snapshot=_query_snapshot(items=(MISSING_OWNER,))),
            "REJECT",
            "WMS_REJECTED",
            "WMS-42",
        ),
        (
            "nil-query-outcome",
            _policy_input(query_snapshot=None),
            "HOLD",
            "WMS_OUTCOME_INVALID",
            None,
        ),
        (
            "binding-profile-version-mismatch",
            _policy_input(binding=_binding(profile_identity="wms.2026-07-28.full-factory.future")),
            "HOLD",
            "WMS_PROFILE_MISMATCH",
            None,
        ),
        (
            "missing-source-version",
            _policy_input(query_snapshot=_query_snapshot(items=(MATCH,), source_version=None)),
            "HOLD",
            "WMS_SOURCE_VERSION_MISSING",
            None,
        ),
        (
            "provider-business-reject",
            _policy_input(
                query_snapshot=_query_snapshot(
                    outcome_kind=RoughSorterInventoryQueryOutcomeKind.BUSINESS_REJECT,
                    reason_code="SKU_REJECTED",
                    message="sku rejected",
                )
            ),
            "REJECT",
            "SKU_REJECTED",
            None,
        ),
        (
            "retryable-technical-failure",
            _policy_input(
                query_snapshot=_query_snapshot(
                    outcome_kind=RoughSorterInventoryQueryOutcomeKind.TECHNICAL_FAILURE,
                    reason_code="PROVIDER_TIMEOUT",
                    message="timeout",
                    retryable=True,
                )
            ),
            "HOLD",
            "WMS_TIMEOUT",
            None,
        ),
        (
            "non-retryable-technical-failure",
            _policy_input(
                query_snapshot=_query_snapshot(
                    outcome_kind=RoughSorterInventoryQueryOutcomeKind.TECHNICAL_FAILURE,
                    reason_code="AUTH_INVALID",
                    message="auth rejected",
                    retryable=False,
                )
            ),
            "HOLD",
            "AUTH_INVALID",
            None,
        ),
        (
            "query-contract-failure",
            _policy_input(
                query_snapshot=_query_snapshot(
                    outcome_kind=RoughSorterInventoryQueryOutcomeKind.CONTRACT_FAILURE,
                    reason_code="WMS_MALFORMED_RESPONSE",
                    message="malformed",
                )
            ),
            "HOLD",
            "WMS_MALFORMED_RESPONSE",
            None,
        ),
        (
            "invalid-query-outcome",
            _policy_input(query_snapshot=_query_snapshot(outcome_kind=RoughSorterInventoryQueryOutcomeKind.INVALID)),
            "HOLD",
            "WMS_OUTCOME_INVALID",
            None,
        ),
    ],
)
def test_policy_table_is_closed_and_replayable(
    case_id: str,
    policy_input: RoughSorterInventoryAdmissionPolicyInput,
    expected_decision: str,
    expected_reason: str,
    expected_source_version: str | None,
) -> None:
    first = decide_rough_sorter_inventory_admission(policy_input)
    replayed = decide_rough_sorter_inventory_admission(policy_input)

    assert first == replayed, case_id
    assert first.decision == expected_decision, case_id
    assert first.reason_code == expected_reason, case_id
    assert first.provenance.policy_version == POLICY_VERSION, case_id
    assert first.provenance.source.operation_identity == OPERATION_IDENTITY, case_id
    assert first.provenance.source.source_version == expected_source_version, case_id
    assert first.provenance.binding == policy_input.binding_snapshot, case_id
    assert first.provenance.supported_profile_identities == tuple(sorted(policy_input.supported_profile_identities))


def test_admit_evidence_preserves_decimal_and_exact_match_basis() -> None:
    decision = decide_rough_sorter_inventory_admission(
        _policy_input(query_snapshot=_query_snapshot(items=(MATCH, MATCH, OTHER_LOT)))
    )

    assert decision.evidence.model_dump(mode="python") == {
        "material_code": "MAT-001",
        "lot_no": "LOT-2026-07",
        "warehouse_code": "WH-A",
        "matched_item_count": 2,
        "available_quantity": Decimal("17.0000000000000000002"),
    }
    assert decision.provenance.source.evidence_key == "evidence:wms:42"


def test_missing_source_version_is_not_fabricated_in_serialized_provenance() -> None:
    decision = decide_rough_sorter_inventory_admission(
        _policy_input(query_snapshot=_query_snapshot(items=(MATCH,), source_version=None))
    )

    payload = decision.model_dump(mode="json")
    assert payload["decision"] == "HOLD"
    assert payload["provenance"]["source"]["source_version"] is None


def test_success_snapshot_without_evidence_key_is_held() -> None:
    decision = decide_rough_sorter_inventory_admission(
        _policy_input(query_snapshot=_query_snapshot(items=(MATCH,), evidence_key=None))
    )

    assert decision.decision == "HOLD"
    assert decision.reason_code == "WMS_EVIDENCE_MISSING"
    assert decision.provenance.source.evidence_key is None


def test_cross_warehouse_item_is_rejected_without_claiming_it_as_matching_evidence() -> None:
    decision = decide_rough_sorter_inventory_admission(
        _policy_input(query_snapshot=_query_snapshot(items=(OTHER_WAREHOUSE,)))
    )

    assert decision.decision == "REJECT"
    assert decision.reason_code == "WMS_REJECTED"
    assert decision.evidence.matched_item_count == 0
    assert decision.evidence.available_quantity == Decimal(0)


def test_policy_module_has_no_async_or_io_boundary_calls() -> None:
    source = inspect.getsource(policy_module)
    tree = ast.parse(source)

    assert not any(isinstance(node, ast.AsyncFunctionDef) for node in ast.walk(tree))
    imported_modules = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and isinstance(node.module, str)
    }
    assert not any(
        ".ports" in module or ".services" in module or ".repositories" in module for module in imported_modules
    )
    assert not any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {"execute", "commit"}
        for node in ast.walk(tree)
    )
