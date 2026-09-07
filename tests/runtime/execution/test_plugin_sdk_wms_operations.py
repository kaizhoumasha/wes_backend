"""固定 WMS intent/outcome 的纯 SDK 合同。"""

from dataclasses import FrozenInstanceError

import pytest
import wes_plugin_sdk as sdk
from wes_plugin_sdk import DevicePosition, wms_operations
from wes_plugin_sdk.wms_types import (
    AdmissionAccepted,
    AdmissionOutcome,
    FactRecorded,
    Measurements,
    OperationWait,
    PickingTaskPrepareIntent,
    PlacementOutcome,
    SixInOne,
)


def test_fixed_facade_creates_independent_immutable_intents() -> None:
    source = DevicePosition("handoff", "MEASUREMENT_POSITION", "trace")
    admission = wms_operations.inbound_material_admission_decide(
        material_execution_id="execution",
        fact_id="fact",
        operation_id="admission",
        material_trace_id="trace",
        six_in_one=SixInOne("lot", "date", "1", "product", "pn", "po"),
        measurements=Measurements("1.20", "0"),
        shape_result="PASS",
        line_run_epoch_id="epoch",
        workline_code="line",
        source_position=source,
    )
    target = wms_operations.inbound_material_target_decide(
        material_execution_id="execution",
        fact_id="fact",
        operation_id="target",
        material_trace_id="trace",
        pkg_id="pkg",
        inbound_admission_id="admission",
        source_position=DevicePosition("outlet", "PIPELINE_OUTLET", "trace"),
        current_rack_id="rack",
    )
    assert admission.operation_id != target.operation_id
    assert admission.source_position is source
    assert not hasattr(admission, "operation")
    assert not hasattr(admission, "request_data")
    with pytest.raises(FrozenInstanceError):
        admission.operation_id = "other"
    with pytest.raises(TypeError):
        wms_operations.inbound_material_target_decide(operation="anything", request_data={})


def test_prepare_has_picking_task_owner_and_is_not_an_execution_decision() -> None:
    from typing import get_args

    intent = wms_operations.outbound_picking_task_prepare(
        operation_id="prepare",
        task_id="task",
        work_line_code="line",
    )
    assert type(intent) is PickingTaskPrepareIntent
    assert not hasattr(intent, "material_execution_id")
    assert not isinstance(intent, get_args(sdk.Decision))


def test_outcomes_are_closed_and_wms_facts_carry_typed_outcome() -> None:
    outcome = AdmissionOutcome(AdmissionAccepted("pkg", "admission"))
    fact = sdk.WmsResultReadyFact("fact", "evidence", "1", "execution", "operation", outcome)
    assert fact.outcome is outcome
    with pytest.raises(TypeError):
        AdmissionOutcome({"result": "ACCEPT"})
    with pytest.raises(TypeError):
        PlacementOutcome(OperationWait("later", 1))
    with pytest.raises(TypeError):
        sdk.WmsResultReadyFact("fact", "evidence", "1", "execution", "operation", {})
    assert PlacementOutcome(FactRecorded()).result.duplicate is False
    assert not hasattr(sdk, "CreateWmsConfirmation")


def test_facade_rejects_untyped_nested_values_and_wrong_position() -> None:
    with pytest.raises(TypeError):
        wms_operations.inbound_material_target_decide(
            material_execution_id="execution",
            fact_id="fact",
            operation_id="target",
            material_trace_id="trace",
            pkg_id="pkg",
            inbound_admission_id="admission",
            source_position={"type": "HANDOFF_POSITION", "location_code": "handoff"},
            current_rack_id="rack",
        )
    with pytest.raises(ValueError):
        wms_operations.inbound_material_target_decide(
            material_execution_id="execution",
            fact_id="fact",
            operation_id="target",
            material_trace_id="trace",
            pkg_id="pkg",
            inbound_admission_id="admission",
            source_position=DevicePosition("ng", "NG_POSITION", "trace"),
            current_rack_id="rack",
        )
    with pytest.raises(ValueError):
        Measurements("1e1000000000", "0")
    with pytest.raises(ValueError):
        OperationWait("later", True)


def test_fact_and_replacement_facades_keep_separate_identity_and_typed_position() -> None:
    cell = DevicePosition("cell", "RACK_CELL", "trace", "rack", "slot", "bin", "bin-cell")
    placement = wms_operations.inbound_material_placement_report(
        material_execution_id="execution",
        fact_id="fact",
        operation_id="placement",
        material_trace_id="trace",
        pkg_id="pkg",
        inbound_admission_id="admission",
        target_assignment_id="assignment",
        target_position=cell,
        placement_sequence=1,
        command_code="command",
        placed_at=123,
    )
    ng = wms_operations.inbound_material_ng_placement_report(
        material_execution_id="execution",
        fact_id="fact",
        operation_id="ng-placement",
        material_trace_id="trace",
        ng_evidence_id="ng-evidence",
        ng_position=DevicePosition("ng", "NG_POSITION", "trace"),
        reason_code="REJECTED",
        business_context="context",
    )
    replacement = wms_operations.inbound_source_rack_replacement_plan_decide(
        material_execution_id="execution",
        fact_id="fact",
        operation_id="replacement",
        material_trace_id="trace",
        current_rack_id="rack",
    )
    assert placement.target_position is cell
    assert ng.pkg_id is None
    assert replacement.current_rack_id == cell.rack_id
    assert len({placement.operation_id, ng.operation_id, replacement.operation_id}) == 3
    with pytest.raises(FrozenInstanceError):
        placement.target_position.bin_code = "other"


@pytest.mark.parametrize(
    "outcome_type",
    [sdk.AdmissionOutcome, sdk.TargetOutcome, sdk.ReplacementPlanOutcome, sdk.PlacementOutcome, sdk.NgPlacementOutcome],
)
def test_inbound_outcomes_exclude_prepare_only_reasons(outcome_type: type) -> None:
    with pytest.raises(ValueError, match="REVISION_CONFLICT"):
        outcome_type(sdk.OperationConflict("REVISION_CONFLICT"))
    with pytest.raises(ValueError, match="field_path"):
        outcome_type(sdk.OperationRejected("INVALID_DATA", "/data/task_id"))
    assert isinstance(outcome_type(sdk.OperationUnavailable()).result, sdk.OperationUnavailable)


def test_prepare_result_excludes_inbound_only_branches() -> None:
    with pytest.raises(TypeError):
        sdk.PickingTaskPrepareOutcome(sdk.OperationBusy(100))
    with pytest.raises(ValueError, match="POSITION_CONFLICT"):
        sdk.PickingTaskPrepareOutcome(sdk.OperationConflict("POSITION_CONFLICT"))
    result = sdk.PickingTaskPrepareOutcome(sdk.OperationConflict("REVISION_CONFLICT"))
    assert result.result.reason_code == "REVISION_CONFLICT"


def test_target_and_replacement_results_reuse_typed_positions() -> None:
    cell = DevicePosition("cell", "RACK_CELL", "trace", "rack", "slot", "bin", "bin-cell")
    assigned = sdk.TargetOutcome(sdk.TargetAssigned("assignment", cell, 1, "12.20"))
    assert assigned.result.target_position is cell
    old = sdk.RackMovePlan("old", sdk.TransportRackReference("old"), sdk.TransportRackPosition("park"), "face")
    new = sdk.RackMovePlan("new", sdk.TransportZonePosition("zone"), sdk.TransportRackPosition("work"), "face")
    ready = sdk.ReplacementPlanOutcome(sdk.ReplacementReady("replacement", old, new))
    assert ready.result.new_empty_rack is new
    with pytest.raises(TypeError):
        sdk.ReplacementReady("replacement", old, {"rack_id": "new"})
    with pytest.raises(ValueError):
        sdk.RackMovePlan("new", sdk.TransportRackReference("other"), sdk.TransportRackPosition("work"), "face")


def test_return_rack_arrival_facade_is_complete_immutable_and_typed() -> None:
    intent = wms_operations.outbound_return_rack_arrival_report(
        operation_id="arrival",
        task_id="task",
        transport_task_id="transport",
        outcome_revision=1,
        rack_id="rack",
        final_position=sdk.TransportRackPosition("work"),
        arrival_face="到达面",
    )
    assert type(intent) is sdk.ReturnRackArrivalReportIntent
    assert intent.final_position.location_code == "work"
    assert not hasattr(intent, "material_execution_id")
    with pytest.raises(FrozenInstanceError):
        intent.arrival_face = "B"
    values = {
        "operation_id": "arrival",
        "task_id": "task",
        "transport_task_id": "transport",
        "outcome_revision": 1,
        "rack_id": "rack",
        "final_position": sdk.TransportRackPosition("work"),
        "arrival_face": "A",
    }
    for field, invalid in [
        ("outcome_revision", True),
        ("outcome_revision", 0),
        ("outcome_revision", 2**63),
        ("transport_task_id", "x" * 81),
        ("transport_task_id", "含空格 id"),
        ("task_id", "x" * 101),
        ("rack_id", "x" * 101),
        ("arrival_face", "x" * 11),
        ("arrival_face", ""),
        ("final_position", sdk.TransportRackPosition("x" * 101)),
    ]:
        with pytest.raises(ValueError):
            wms_operations.outbound_return_rack_arrival_report(**(values | {field: invalid}))
    for position in ({"type": "RACK_POSITION", "location_code": "work"}, sdk.TransportZonePosition("work")):
        with pytest.raises(TypeError):
            wms_operations.outbound_return_rack_arrival_report(**(values | {"final_position": position}))


def test_return_rack_arrival_outcome_has_only_approved_branches() -> None:
    assert sdk.ReturnRackArrivalReportOutcome(sdk.FactRecorded(True)).result.duplicate
    assert isinstance(sdk.ReturnRackArrivalReportOutcome(sdk.OperationUnavailable()).result, sdk.OperationUnavailable)
    with pytest.raises(TypeError):
        sdk.ReturnRackArrivalReportOutcome(sdk.OperationBusy(1))
    with pytest.raises(TypeError):
        sdk.ReturnRackArrivalReportOutcome(sdk.PrepareAccepted())
    with pytest.raises(ValueError, match="POSITION_CONFLICT"):
        sdk.ReturnRackArrivalReportOutcome(sdk.OperationConflict("POSITION_CONFLICT"))
    for pointer in ("/bad~2", "/" + "a" * 256):
        with pytest.raises(ValueError, match="field_path"):
            sdk.ReturnRackArrivalReportOutcome(sdk.OperationRejected("INVALID_DATA", pointer))
    assert sdk.ReturnRackArrivalReportOutcome(sdk.OperationConflict("REVISION_CONFLICT")).result.reason_code


def test_bin_inbound_batch_facade_validates_capacity_and_preserves_face() -> None:
    values = {"operation_id": "batch", "task_id": "task", "rack_id": "rack", "rack_face": "来源面", "max_bin_count": 4}
    intent = wms_operations.outbound_bin_inbound_batch(**values)
    assert type(intent) is sdk.BinInboundBatchIntent
    assert intent.rack_face == "来源面"
    with pytest.raises(FrozenInstanceError):
        intent.max_bin_count = 1
    for name, value in [
        ("max_bin_count", True),
        ("max_bin_count", 0),
        ("max_bin_count", 5),
        ("rack_face", "x" * 11),
        ("task_id", "坏 id"),
        ("rack_id", "x" * 101),
    ]:
        with pytest.raises(ValueError):
            wms_operations.outbound_bin_inbound_batch(**(values | {name: value}))


def test_bin_inbound_batch_outcome_is_deeply_immutable_and_closed() -> None:
    locator = sdk.TransportRackBinSlot("rack", "A", "slot")
    member = sdk.BinInboundBatchMember("bin", locator)
    ready = sdk.BinInboundBatchReady((member,))
    assert sdk.BinInboundBatchOutcome(ready).result.bins == (member,)
    with pytest.raises(FrozenInstanceError):
        locator.slot_id = "other"
    with pytest.raises(TypeError):
        sdk.BinInboundBatchReady([member])
    for bins in [(), (member,) * 5, (member, member)]:
        with pytest.raises(ValueError):
            sdk.BinInboundBatchReady(bins)
    with pytest.raises(ValueError):
        sdk.BinInboundBatchReady((member, sdk.BinInboundBatchMember("other", locator)))
    with pytest.raises(TypeError):
        sdk.BinInboundBatchMember("bin", {"rack_id": "rack"})
    with pytest.raises(ValueError):
        sdk.TransportRackBinSlot("bad id", "A", "slot")
    for value in [0, 60001, True]:
        with pytest.raises(ValueError):
            sdk.BinBatchNoBatch(value)
    assert sdk.BinInboundBatchOutcome(sdk.BinInboundBatchRackFaceDone()).result is not None
    for result in [sdk.OperationBusy(1), sdk.PrepareAccepted(), sdk.FactRecorded(False)]:
        with pytest.raises(TypeError):
            sdk.BinInboundBatchOutcome(result)
    with pytest.raises(ValueError):
        sdk.BinInboundBatchOutcome(sdk.OperationConflict("POSITION_CONFLICT"))
    with pytest.raises(ValueError):
        sdk.BinInboundBatchOutcome(sdk.OperationRejected("INVALID_DATA", "/bad~2"))


@pytest.mark.parametrize("pointer", ["/bad~2", "/" + "a" * 256])
def test_return_batch_rejects_noncanonical_rejection_pointer(pointer):
    with pytest.raises(ValueError, match="field_path"):
        sdk.BinReturnBatchOutcome(sdk.OperationRejected("INVALID_DATA", pointer))


@pytest.mark.parametrize("target_face", ["\x00", "\ud800"])
def test_rack_move_plan_rejects_invalid_face(target_face):
    with pytest.raises(ValueError):
        sdk.RackMovePlan(
            "rack-1", sdk.TransportZonePosition("zone-1"), sdk.TransportRackPosition("work-position"), target_face
        )
