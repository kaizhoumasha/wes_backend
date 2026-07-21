"""粗分机库存准入纯 policy：typed snapshot → 可重放 decision。"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from src.app.runtime.capabilities.material_flow.contracts.rough_sorter import REASON_WMS_TIMEOUT
from src.app.runtime.capabilities.material_flow.contracts.rough_sorter_inventory_admission import (
    RoughSorterInventoryAdmissionDecision,
    RoughSorterInventoryAdmissionEvidence,
    RoughSorterInventoryAdmissionPolicyInput,
    RoughSorterInventoryDecisionProvenance,
    RoughSorterInventoryQueryOutcomeKind,
    RoughSorterInventorySourceProvenance,
)

REASON_WMS_ADMITTED = "WMS_ADMITTED"
REASON_WMS_REJECTED = "WMS_REJECTED"
REASON_WMS_OUTCOME_INVALID = "WMS_OUTCOME_INVALID"
REASON_WMS_PROFILE_MISMATCH = "WMS_PROFILE_MISMATCH"
REASON_WMS_EVIDENCE_MISSING = "WMS_EVIDENCE_MISSING"
REASON_WMS_SOURCE_VERSION_MISSING = "WMS_SOURCE_VERSION_MISSING"
REASON_WMS_TECHNICAL_FAILURE = "WMS_TECHNICAL_FAILURE"
REASON_WMS_CONTRACT_FAILURE = "WMS_CONTRACT_FAILURE"


def decide_rough_sorter_inventory_admission(  # noqa: PLR0911 - 封闭输入逐项对应稳定决策。
    policy_input: RoughSorterInventoryAdmissionPolicyInput,
) -> RoughSorterInventoryAdmissionDecision:
    """仅根据冻结输入和 WMS authority snapshot 计算一次确定性准入决策。"""

    query = policy_input.query_snapshot
    result = query.result if query is not None else None
    matches = (
        tuple(
            item
            for item in result.items
            if item.material_code == policy_input.material_code
            and item.lot_no == policy_input.lot_no
            and item.warehouse_code == policy_input.warehouse_code
        )
        if result is not None
        else ()
    )
    evidence = RoughSorterInventoryAdmissionEvidence(
        material_code=policy_input.material_code,
        lot_no=policy_input.lot_no,
        warehouse_code=policy_input.warehouse_code,
        matched_item_count=len(matches),
        available_quantity=sum((item.available_quantity for item in matches), start=Decimal(0)),
    )
    provenance = RoughSorterInventoryDecisionProvenance(
        source=RoughSorterInventorySourceProvenance(
            operation_identity=policy_input.source_operation,
            outcome_kind=query.outcome_kind if query is not None else "MISSING",
            query_owner_code=policy_input.owner_code,
            evidence_key=query.evidence_key if query is not None else None,
            source_version=result.source_version if result is not None else None,
            reason_code=query.reason_code if query is not None else None,
            message=query.message if query is not None else None,
            retryable=query.retryable if query is not None else None,
            retry_after_seconds=query.retry_after_seconds if query is not None else None,
        ),
        binding=policy_input.binding_snapshot,
        supported_profile_identities=policy_input.supported_profile_identities,
    )

    if policy_input.binding_snapshot.profile_identity not in policy_input.supported_profile_identities:
        return _decision("HOLD", REASON_WMS_PROFILE_MISMATCH, evidence=evidence, provenance=provenance)
    if query is None or query.outcome_kind is RoughSorterInventoryQueryOutcomeKind.INVALID:
        return _decision("HOLD", REASON_WMS_OUTCOME_INVALID, evidence=evidence, provenance=provenance)
    if query.outcome_kind is RoughSorterInventoryQueryOutcomeKind.BUSINESS_REJECT:
        return _decision(
            "REJECT",
            query.reason_code or REASON_WMS_REJECTED,
            evidence=evidence,
            provenance=provenance,
        )
    if query.outcome_kind is RoughSorterInventoryQueryOutcomeKind.TECHNICAL_FAILURE:
        reason_code = REASON_WMS_TIMEOUT if query.retryable else query.reason_code or REASON_WMS_TECHNICAL_FAILURE
        return _decision("HOLD", reason_code, evidence=evidence, provenance=provenance)
    if query.outcome_kind is RoughSorterInventoryQueryOutcomeKind.CONTRACT_FAILURE:
        return _decision(
            "HOLD",
            query.reason_code or REASON_WMS_CONTRACT_FAILURE,
            evidence=evidence,
            provenance=provenance,
        )
    if result is None:
        return _decision("HOLD", REASON_WMS_OUTCOME_INVALID, evidence=evidence, provenance=provenance)
    if query.evidence_key is None:
        return _decision("HOLD", REASON_WMS_EVIDENCE_MISSING, evidence=evidence, provenance=provenance)
    if result.source_version is None:
        return _decision("HOLD", REASON_WMS_SOURCE_VERSION_MISSING, evidence=evidence, provenance=provenance)
    if not matches:
        return _decision("REJECT", REASON_WMS_REJECTED, evidence=evidence, provenance=provenance)
    return _decision("ADMIT", REASON_WMS_ADMITTED, evidence=evidence, provenance=provenance)


def _decision(
    decision: Literal["ADMIT", "REJECT", "HOLD"],
    reason_code: str,
    *,
    evidence: RoughSorterInventoryAdmissionEvidence,
    provenance: RoughSorterInventoryDecisionProvenance,
) -> RoughSorterInventoryAdmissionDecision:
    return RoughSorterInventoryAdmissionDecision(
        decision=decision,
        reason_code=reason_code,
        evidence=evidence,
        provenance=provenance,
    )


__all__ = ["decide_rough_sorter_inventory_admission"]
