"""单盘出库结果可进入封闭 Fact，Work 仍使用独立请求端口。"""

import pytest
import wes_plugin_sdk as sdk
from wes_plugin_sdk import wms_operations


@pytest.mark.parametrize(
    "outcome",
    [
        sdk.PickingMaterialOutcome(sdk.PickingMaterialWait(1000)),
        sdk.MaterialMovementReportOutcome(sdk.FactRecorded()),
    ],
)
def test_outbound_material_outcomes_are_typed_wms_facts(outcome):
    fact = sdk.WmsResultReadyFact(
        fact_id="evidence:31",
        evidence_id="31",
        fact_version="1.0",
        material_execution_id="EXEC-1",
        operation_id="019f3405-2200-7b01-8b01-000000000001",
        outcome=outcome,
    )
    assert fact.outcome is outcome  # nosec B101 - pytest assertion


def test_work_plan_cannot_be_used_as_a_single_material_fact():
    with pytest.raises(TypeError):
        sdk.WmsResultReadyFact(
            fact_id="evidence:31",
            evidence_id="31",
            fact_version="1.0",
            material_execution_id="EXEC-1",
            operation_id="019f3405-2200-7b01-8b01-000000000001",
            outcome=sdk.BinWorkPlanOutcome(sdk.BinWorkPlanNoWork()),
        )


def test_picking_material_intent_carries_local_decision_context_without_changing_business_data():
    intent = wms_operations.outbound_material_decide(
        operation_id="019f3405-2200-7b01-8b01-000000000001",
        task_id="TASK-1",
        plan_revision=2,
        source_locator=sdk.PickingRackSlot("SOURCE-1", "A", "S-1"),
        barcode="原始,条码",
        scanned_at=123,
        material_execution_id="EXEC-1",
        fact_id="evidence:31",
    )
    assert intent.material_execution_id == "EXEC-1"  # nosec B101 - pytest assertion
    assert intent.fact_id == "evidence:31"  # nosec B101 - pytest assertion
    assert intent.barcode == "原始,条码"  # nosec B101 - pytest assertion
