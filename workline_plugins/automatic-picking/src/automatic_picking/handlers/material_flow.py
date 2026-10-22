"""单盘已精确关联后的纯业务决定，不解析设备报文或执行事务。"""

from dataclasses import dataclass

from wes_plugin_sdk import (
    FactReference,
    PickingBinCell,
    PickingMaterialAccept,
    PickingMaterialIntent,
    PickingMaterialWait,
    PickingRackSlot,
    Wait,
    handler,
    wms_operations,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class MaterialScannedFact(FactReference):
    task_id: str
    source_locator: PickingBinCell | PickingRackSlot
    barcode: str
    scanned_at: int
    plan_revision: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class MaterialDecidedFact(FactReference):
    pickup_id: str
    work_id: int | None
    result: PickingMaterialAccept | PickingMaterialWait


@handler(fact_type=MaterialScannedFact, name="automatic_material_scanned", supported_versions=("1.0", "1.1"))
def material_scanned(fact: MaterialScannedFact) -> tuple[PickingMaterialIntent, ...]:
    return (
        wms_operations.outbound_material_decide(
            operation_id=fact.material_execution_id,
            task_id=fact.task_id,
            source_locator=fact.source_locator,
            barcode=fact.barcode,
            scanned_at=fact.scanned_at,
            plan_revision=fact.plan_revision,
            material_execution_id=fact.material_execution_id,
            fact_id=fact.fact_id,
        ),
    )


@handler(fact_type=MaterialDecidedFact, name="automatic_material_decided", supported_versions=("1.0",))
def material_decided(fact: MaterialDecidedFact) -> tuple[Wait, ...]:
    if type(fact.result) is PickingMaterialAccept:
        reason = "MATERIAL_ACCEPTED"
    elif type(fact.result) is PickingMaterialWait:
        reason = "MATERIAL_DECIDE_WAIT"
    else:
        raise TypeError("6C only applies confirmed ACCEPT or WAIT")
    return (Wait(fact.material_execution_id, fact.fact_id, reason),)
