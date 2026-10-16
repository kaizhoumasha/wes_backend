"""纯 SDK 的正常 PUT 结果报告决定。"""

import pytest
from wes_plugin_sdk import DeferExecution, PickingBinCell, PickingTargetRackSlot


def test_put_fact_creates_typed_report_with_original_identity():
    from automatic_picking.handlers.placement import MaterialPutFact, material_put

    fact = MaterialPutFact(
        fact_id="evidence:7",
        evidence_id="7",
        fact_version="1.0",
        material_execution_id="pickup-7b",
        task_id="TASK-7B",
        source=PickingBinCell("SOURCE", "A", "BIN", "CELL"),
        target=PickingTargetRackSlot("TARGET", "A", "S1", 1, 1),
        pkg_id="权威原文",
        occurred_at=987,
    )
    (report,) = material_put(fact)
    assert (report.operation_id, report.task_id, report.pkg_id, report.occurred_at) == (
        "pickup-7b",
        "TASK-7B",
        "权威原文",
        987,
    )
    assert report.material_execution_id == "pickup-7b" and report.fact_id == "evidence:7"
    assert report.to_locator.rack_id == "TARGET" and report.to_locator.slot_id == "S1"


def test_unknown_placement_defers_without_report_or_close():
    from automatic_picking.handlers.placement import MaterialPlacementPendingFact, placement_pending

    fact = MaterialPlacementPendingFact(
        fact_id="evidence:7",
        evidence_id="7",
        fact_version="1.0",
        material_execution_id="pickup-7b",
        reason_code="PUT_FACT_PENDING",
    )
    assert placement_pending(fact) == (DeferExecution("pickup-7b", "evidence:7", "PUT_FACT_PENDING"),)
