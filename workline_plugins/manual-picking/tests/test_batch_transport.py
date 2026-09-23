"""WMS 已冻结的批次目标准确转为一条 CTU BIN_MOVE。"""

from importlib import import_module

import pytest
import wes_plugin_sdk as sdk

from src.app.transport.contracts import BinMove, HandoffPosition, RackBinSlot


def test_inbound_ready_uses_wms_source_and_workline_handoff() -> None:
    module = import_module("manual_picking.application.batch_transport")
    intent = sdk.wms_operations.outbound_bin_inbound_batch(
        operation_id="op-1", task_id="PICK-1", plan_revision=1, rack_id="R1", rack_face="90"
    )
    ready = sdk.BinInboundBatchReady(
        (sdk.BinInboundBatchMember("A000000001", sdk.TransportRackBinSlot("R1", "90", "S1")),)
    )

    assert module.inbound_moves(intent, ready, inlet_location="CNV0301") == (
        BinMove("A000000001", RackBinSlot("R1", "90", "S1"), HandoffPosition("CNV0301")),
    )
    with pytest.raises(ValueError, match="source rack face"):
        module.inbound_moves(
            intent,
            sdk.BinInboundBatchReady(
                (sdk.BinInboundBatchMember("A000000001", sdk.TransportRackBinSlot("R2", "90", "S1")),)
            ),
            inlet_location="CNV0301",
        )


def test_return_ready_preserves_requested_fifo_prefix_and_wms_target() -> None:
    module = import_module("manual_picking.application.batch_transport")
    intent = sdk.wms_operations.outbound_bin_return_batch(
        operation_id="op-2",
        workline_code="LINE-1",
        rack_id="R1",
        rack_face="90",
        return_candidates=(
            sdk.BinReturnCandidate(1, "A000000001", "CNV0302"),
            sdk.BinReturnCandidate(2, "A000000002", "CNV0302"),
        ),
    )
    ready = sdk.BinReturnBatchReady((sdk.BinReturnMove(1, "A000000001", sdk.TransportRackBinSlot("R1", "90", "S1")),))

    assert module.return_moves(intent, ready) == (
        BinMove("A000000001", HandoffPosition("CNV0302"), RackBinSlot("R1", "90", "S1")),
    )
    with pytest.raises(ValueError, match="FIFO prefix"):
        module.return_moves(
            intent,
            sdk.BinReturnBatchReady((sdk.BinReturnMove(1, "A000000002", sdk.TransportRackBinSlot("R1", "90", "S2")),)),
        )
    with pytest.raises(ValueError, match="target rack face"):
        module.return_moves(
            intent,
            sdk.BinReturnBatchReady((sdk.BinReturnMove(1, "A000000001", sdk.TransportRackBinSlot("R1", "270", "S2")),)),
        )
