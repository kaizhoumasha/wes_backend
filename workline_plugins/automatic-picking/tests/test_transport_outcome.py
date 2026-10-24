from dataclasses import replace
from types import SimpleNamespace as N
from unittest.mock import AsyncMock

import pytest

from src.app.transport.contracts import (
    RackPosition,
    TransportCaller,
    TransportMemberOutcome,
    TransportOutcome,
    TransportOutcomeStatus,
)


def outcome():
    return TransportOutcome(
        transport_task_id="T1",
        client_request_id="C1",
        outcome_version=1,
        caller=TransportCaller(workline_id="7"),
        status=TransportOutcomeStatus.SUCCEEDED,
        reason_code=None,
        members=(TransportMemberOutcome("R1", RackPosition("FIVE"), arrival_face="A"),),
    )


@pytest.mark.asyncio
async def test_transport_receipt_uses_original_binding_without_parent_state_or_new_action():
    from automatic_picking.application.transport_outcome import AutomaticPickingTransportOutcomePublisher

    evidence = N(accept=AsyncMock(return_value=N()))
    binding = N(
        client_request_id="C1",
        workline_id=7,
        picking_task_id=12,
        step="PICKING_TASK_BIN_SOURCE_RACK_IN",
        resource_fence_id="R1",
        source_evidence_id=31,
    )
    publisher = AutomaticPickingTransportOutcomePublisher(
        bindings=N(get_by_client_request_id=AsyncMock(return_value=binding)), evidence_service=evidence
    )
    assert await publisher.publish(None, outcome()) is False
    saved = evidence.accept.call_args.kwargs
    assert saved["transport_task_id"] == "T1" and saved["picking_task_id"] == 12
    assert saved["source_identity"] == "transport:T1:outcome:1"
    assert saved["normalized_payload"]["step"] == binding.step


@pytest.mark.asyncio
async def test_transport_receipt_rejects_foreign_workline_before_accepting():
    from automatic_picking.application.transport_outcome import AutomaticPickingTransportOutcomePublisher

    evidence = N(accept=AsyncMock())
    publisher = AutomaticPickingTransportOutcomePublisher(
        bindings=N(
            get_by_client_request_id=AsyncMock(
                return_value=N(workline_id=8, client_request_id="C1", picking_task_id=12)
            )
        ),
        evidence_service=evidence,
    )
    with pytest.raises(ValueError):
        await publisher.publish(None, outcome())
    evidence.accept.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status", [TransportOutcomeStatus.UNKNOWN, TransportOutcomeStatus.REJECTED, TransportOutcomeStatus.FAILED]
)
async def test_nonphysical_rack_outcome_is_receipted_without_inventing_members(status):
    from automatic_picking.application.transport_outcome import AutomaticPickingTransportOutcomePublisher

    evidence = N(accept=AsyncMock(return_value=N()))
    binding = N(
        client_request_id="C1",
        workline_id=7,
        picking_task_id=12,
        step="PICKING_TASK_BIN_SOURCE_RACK_IN",
        resource_fence_id="R1",
        source_evidence_id=31,
    )
    publisher = AutomaticPickingTransportOutcomePublisher(
        bindings=N(get_by_client_request_id=AsyncMock(return_value=binding)), evidence_service=evidence
    )
    assert await publisher.publish(None, replace(outcome(), status=status, members=())) is False
    assert evidence.accept.call_args.kwargs["normalized_payload"]["members"] == ()
