"""确定放置事实的纯业务决定；不解释设备报文或 扫码原文 来源。"""

from dataclasses import dataclass

from wes_plugin_sdk import (
    CompleteExecution,
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
    source: PickingBinCell | PickingRackSlot
    target: PickingTargetRackSlot
    barcode: str
    occurred_at: int
    plan_revision: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class MaterialPlacementPendingFact(FactReference):
    reason_code: str


@dataclass(frozen=True, slots=True, kw_only=True)
class MaterialReportedFact(FactReference):
    removed_evidence_id: int | None


@handler(fact_type=MaterialReportedFact, name="automatic_material_reported", supported_versions=("1.0",))
def material_reported(fact: MaterialReportedFact) -> tuple[CompleteExecution, ...]:
    return (CompleteExecution(fact.material_execution_id, fact.fact_id, "MOVEMENT_RECORDED"),)


@handler(fact_type=MaterialPutFact, name="automatic_material_put", supported_versions=("1.0", "1.1"))
def material_put(fact: MaterialPutFact) -> tuple[MaterialMovementReportIntent, ...]:
    return (
        wms_operations.outbound_material_movement_report(
            operation_id=fact.material_execution_id,
            task_id=fact.task_id,
            source_locator=fact.source,
            barcode=fact.barcode,
            to_locator=PickingRackSlot(fact.target.rack_id, fact.target.rack_face, fact.target.slot_id),
            occurred_at=fact.occurred_at,
            plan_revision=fact.plan_revision,
            material_execution_id=fact.material_execution_id,
            fact_id=fact.fact_id,
        ),
    )


@handler(fact_type=MaterialPlacementPendingFact, name="automatic_placement_pending", supported_versions=("1.0", "1.1"))
def placement_pending(fact: MaterialPlacementPendingFact) -> tuple[DeferExecution, ...]:
    return (DeferExecution(fact.material_execution_id, fact.fact_id, fact.reason_code),)
