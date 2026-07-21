"""粗分机 WMS 库存准入纯 QUERY handler。"""

from __future__ import annotations

from src.app.runtime.capabilities.material_flow.contracts.rough_sorter_inventory_admission import (
    RoughSorterInventoryAdmissionPolicyInput,
    RoughSorterInventoryQueryOutcomeKind,
    RoughSorterInventoryQuerySnapshot,
)
from src.app.runtime.capabilities.material_flow.rough_sorter_inventory_admission_policy import (
    decide_rough_sorter_inventory_admission,
)
from src.app.wms_integration.ports.query_inventory_operation import (
    OPERATION_IDENTITY,
    InventoryQueryOperationPort,
    InventoryQueryOperationRequest,
)
from src.app.wms_integration.ports.query_outcome import (
    QueryBusinessReject,
    QueryContractFailure,
    QuerySuccess,
    QueryTechnicalFailure,
)

from .contracts import (
    SUPPORTED_PROFILE_IDENTITIES,
    RoughSorterInventoryAdmissionInput,
)
from .runtime_outcome import to_runtime_outcome


def _query_snapshot(outcome: object | None) -> RoughSorterInventoryQuerySnapshot | None:
    """把 Port 的封闭 typed outcome 归一化为纯 policy 输入。"""

    if outcome is None:
        return None
    if isinstance(outcome, QuerySuccess):
        return RoughSorterInventoryQuerySnapshot(
            outcome_kind=RoughSorterInventoryQueryOutcomeKind.SUCCESS,
            result=outcome.value,
            evidence_key=outcome.evidence_key,
        )
    if isinstance(outcome, QueryBusinessReject):
        return RoughSorterInventoryQuerySnapshot(
            outcome_kind=RoughSorterInventoryQueryOutcomeKind.BUSINESS_REJECT,
            evidence_key=outcome.evidence_key,
            reason_code=outcome.reason_code,
            message=outcome.message,
            retryable=False,
        )
    if isinstance(outcome, QueryTechnicalFailure):
        return RoughSorterInventoryQuerySnapshot(
            outcome_kind=RoughSorterInventoryQueryOutcomeKind.TECHNICAL_FAILURE,
            evidence_key=outcome.evidence_key,
            reason_code=outcome.reason_code,
            message=outcome.message,
            retryable=outcome.retryable,
            retry_after_seconds=outcome.retry_after_seconds,
        )
    if isinstance(outcome, QueryContractFailure):
        return RoughSorterInventoryQuerySnapshot(
            outcome_kind=RoughSorterInventoryQueryOutcomeKind.CONTRACT_FAILURE,
            evidence_key=outcome.evidence_key,
            reason_code=outcome.reason_code,
            message=outcome.message,
            retryable=False,
        )
    return RoughSorterInventoryQuerySnapshot(outcome_kind=RoughSorterInventoryQueryOutcomeKind.INVALID)


def _policy_input(
    request: RoughSorterInventoryAdmissionInput,
    outcome: object | None,
) -> RoughSorterInventoryAdmissionPolicyInput:
    return RoughSorterInventoryAdmissionPolicyInput(
        material_code=request.hhpn,
        lot_no=request.lot_code,
        warehouse_code=request.warehouse_code,
        binding_snapshot=request.binding_snapshot,
        supported_profile_identities=tuple(sorted(SUPPORTED_PROFILE_IDENTITIES)),
        source_operation=OPERATION_IDENTITY,
        query_snapshot=_query_snapshot(outcome),
    )


class RoughSorterInventoryAdmissionHandler:
    """I/O 边界：执行一次 typed QUERY，再把 outcome 交给纯 policy。"""

    def __init__(self, inventory_port: InventoryQueryOperationPort) -> None:
        self._inventory_port = inventory_port

    async def __call__(self, request: RoughSorterInventoryAdmissionInput) -> object:
        outcome = None
        if request.binding_snapshot.profile_identity in SUPPORTED_PROFILE_IDENTITIES:
            outcome = await self._inventory_port.execute(
                InventoryQueryOperationRequest(
                    material_code=request.hhpn,
                    warehouse_code=request.warehouse_code,
                    owner_code=request.owner_code,
                    lot_no=request.lot_code,
                )
            )
        return to_runtime_outcome(decide_rough_sorter_inventory_admission(_policy_input(request, outcome)))


__all__ = ["RoughSorterInventoryAdmissionHandler"]
