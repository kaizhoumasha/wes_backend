"""确定放置事实的纯业务决定；不解释设备报文或 PkgID 来源。"""

from dataclasses import dataclass

from wes_plugin_sdk import (
    DeferExecution,
    FactReference,
    MaterialMovementReportIntent,
    PickingBinCell,
    PickingRackSlot,
    PickingTargetRackSlot,
    handler,
    wms_operations,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class MaterialPutFact(FactReference):
    task_id: str
    source: PickingBinCell
    target: PickingTargetRackSlot
    pkg_id: str
    occurred_at: int


@dataclass(frozen=True, slots=True, kw_only=True)
class MaterialPlacementPendingFact(FactReference):
    reason_code: str


@handler(fact_type=MaterialPutFact, name="automatic_material_put", supported_versions=("1.0",))
def material_put(fact: MaterialPutFact) -> tuple[MaterialMovementReportIntent, ...]:
    return (
        wms_operations.outbound_material_movement_report(
            operation_id=fact.material_execution_id,
            task_id=fact.task_id,
            source_locator=fact.source,
            pkg_id=fact.pkg_id,
            to_locator=PickingRackSlot(fact.target.rack_id, fact.target.rack_face, fact.target.slot_id),
            occurred_at=fact.occurred_at,
            material_execution_id=fact.material_execution_id,
            fact_id=fact.fact_id,
        ),
    )


@handler(fact_type=MaterialPlacementPendingFact, name="automatic_placement_pending", supported_versions=("1.0",))
def placement_pending(fact: MaterialPlacementPendingFact) -> tuple[DeferExecution, ...]:
    return (DeferExecution(fact.material_execution_id, fact.fact_id, fact.reason_code),)
