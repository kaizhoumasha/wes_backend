"""人工拣料 Transport 结果按原业务 binding 保存，不把接收当作物理完成。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.execution.models.inbound_evidence import InboundEvidenceApplyStatus, InboundEvidenceKind
from src.app.transport.contracts import (
    RackPosition,
    TransportCaller,
    TransportMemberOutcome,
    TransportOutcome,
    TransportOutcomeStatus,
)


def _outcome(*, status: TransportOutcomeStatus = TransportOutcomeStatus.SUCCEEDED) -> TransportOutcome:
    return TransportOutcome(
        transport_task_id="TRANSPORT-1",
        client_request_id="REQUEST-1",
        outcome_version=1,
        caller=TransportCaller(workline_id="31"),
        status=status,
        reason_code=None,
        members=(
            TransportMemberOutcome(
                object_id="RACK-1", final_position=RackPosition("FIVE-RACK-POSITION"), arrival_face="90"
            ),
        )
        if status is TransportOutcomeStatus.SUCCEEDED
        else (),
    )


def _publisher(*, step: str = "PICKING_TASK_BIN_SOURCE_RACK_IN") -> tuple[object, AsyncMock]:
    from manual_picking.application.transport_outcome import ManualPickingTransportOutcomePublisher

    binding = SimpleNamespace(
        workline_id=31,
        client_request_id="REQUEST-1",
        step=step,
        resource_fence_id="RACK-1",
        source_evidence_id=101,
    )
    source = SimpleNamespace(
        id=101,
        operation="outbound.picking_task.plan_delta@v1",
        normalized_payload={"data": {"task_id": "PICK-1"}},
    )
    evidence_service = AsyncMock()
    evidence_service.accept.return_value = SimpleNamespace(duplicate=False)
    publisher = ManualPickingTransportOutcomePublisher(
        binding_repository=SimpleNamespace(get_by_client_request_id=AsyncMock(return_value=binding)),
        evidence_repository=SimpleNamespace(get_by_id_without_lock=AsyncMock(return_value=source)),
        evidence_service=evidence_service,
    )
    return publisher, evidence_service.accept


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [TransportOutcomeStatus.SUCCEEDED, TransportOutcomeStatus.UNKNOWN])
async def test_transport_result_is_durable_pending_evidence_bound_to_original_task(
    status: TransportOutcomeStatus,
) -> None:
    publisher, accept = _publisher()

    should_wake_execution = await publisher.publish(object(), _outcome(status=status))

    assert should_wake_execution is False
    kwargs = accept.await_args.kwargs
    assert kwargs["kind"] is InboundEvidenceKind.TRANSPORT_RESULT
    assert kwargs["source_identity"] == "transport:TRANSPORT-1:outcome:1"
    assert kwargs["transport_task_id"] == "TRANSPORT-1"
    assert kwargs["workline_id"] == 31
    assert kwargs["apply_status"] is InboundEvidenceApplyStatus.PENDING
    assert kwargs["normalized_payload"]["picking_task_id"] == "PICK-1"
    assert kwargs["normalized_payload"]["rack_id"] == "RACK-1"
    assert kwargs["normalized_payload"]["status"] == status


@pytest.mark.asyncio
async def test_transport_result_rejects_wrong_workline_before_acceptance() -> None:
    publisher, accept = _publisher()
    outcome = TransportOutcome(
        transport_task_id="TRANSPORT-1",
        client_request_id="REQUEST-1",
        outcome_version=1,
        caller=TransportCaller(workline_id="32"),
        status=TransportOutcomeStatus.UNKNOWN,
        reason_code="RESULT_UNKNOWN",
        members=(),
    )

    with pytest.raises(ValueError, match="WorkLine"):
        await publisher.publish(object(), outcome)
    accept.assert_not_awaited()


@pytest.mark.asyncio
async def test_transport_result_rejects_other_plugin_step_before_acceptance() -> None:
    publisher, accept = _publisher(step="OLD_OUT")

    with pytest.raises(ValueError, match="step"):
        await publisher.publish(object(), _outcome())
    accept.assert_not_awaited()


@pytest.mark.asyncio
async def test_transport_result_rejects_rack_outside_original_binding() -> None:
    publisher, accept = _publisher()
    outcome = TransportOutcome(
        transport_task_id="TRANSPORT-1",
        client_request_id="REQUEST-1",
        outcome_version=1,
        caller=TransportCaller(workline_id="31"),
        status=TransportOutcomeStatus.SUCCEEDED,
        reason_code=None,
        members=(TransportMemberOutcome(object_id="OTHER-RACK", final_position=RackPosition("FIVE-RACK-POSITION")),),
    )

    with pytest.raises(ValueError, match="rack"):
        await publisher.publish(object(), outcome)
    accept.assert_not_awaited()
