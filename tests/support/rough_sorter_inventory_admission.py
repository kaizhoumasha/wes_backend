"""粗分机库存准入成功合同的测试工厂。"""

from __future__ import annotations

from decimal import Decimal

from src.app.runtime.capabilities.material_flow.contracts.rough_sorter_inventory_admission import (
    RoughSorterInventoryAdmissionDecision,
)
from src.app.runtime.system_capabilities.wms.rough_sorter_inventory_admission.contracts import (
    RoughSorterInventoryAdmissionOutput,
)


def admitted_inventory_output(
    *,
    material_code: str,
    lot_no: str,
    warehouse_code: str,
    available_quantity: Decimal = Decimal("1"),
    source_version: str = "fixture-v1",
) -> RoughSorterInventoryAdmissionOutput:
    """构造 evidence、provenance 与成功摘要一致的临时 capability 输出。"""

    decision = RoughSorterInventoryAdmissionDecision.model_validate(
        {
            "decision": "ADMIT",
            "reason_code": "WMS_ADMITTED",
            "evidence": {
                "material_code": material_code,
                "lot_no": lot_no,
                "warehouse_code": warehouse_code,
                "matched_item_count": 1,
                "available_quantity": available_quantity,
            },
            "provenance": {
                "policy_version": "rough-sorter-inventory-admission.v1",
                "source": {
                    "operation_identity": "wms.inventory.query_inventory@v1",
                    "outcome_kind": "SUCCESS",
                    "query_owner_code": "OWNER-01",
                    "evidence_key": "evidence:test:admission",
                    "source_version": source_version,
                },
                "binding": {
                    "binding_id": 1,
                    "binding_version": 1,
                    "profile_identity": "wms.2026-07-06.material-flow.sandbox",
                    "plugin_config_hash": "a" * 64,
                    "generated_index_digest": "b" * 64,
                },
                "supported_profile_identities": ("wms.2026-07-06.material-flow.sandbox",),
            },
        }
    )
    return RoughSorterInventoryAdmissionOutput(
        accepted=True,
        material_code=material_code,
        batch_no=lot_no,
        warehouse_code=warehouse_code,
        matched_item_count=1,
        available_quantity=available_quantity,
        source_version=source_version,
        admission_decision=decision,
    )


__all__ = ["admitted_inventory_output"]
