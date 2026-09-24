"""人工拣料 Transport 结果按原业务 binding 保存，不把接收当作物理完成。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.execution.models.inbound_evidence import InboundEvidenceApplyStatus, InboundEvidenceKind
from src.app.transport.contracts import (
    HandoffPosition,
    RackPosition,
    TransportCaller,
    TransportMemberOutcome,
    TransportOutcome,
    TransportOutcomeStatus,
    ZonePosition,
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
        picking_task_id=None,
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
        evidence_repository=SimpleNamespace(get_by_id=AsyncMock(return_value=source)),
        evidence_service=evidence_service,
    )
    return publisher, evidence_service.accept


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [TransportOutcomeStatus.SUCCEEDED, TransportOutcomeStatus.UNKNOWN])
async def test_transport_result_is_durable_applied_evidence_bound_to_original_task(
    status: TransportOutcomeStatus,
) -> None:
    publisher, accept = _publisher()

    should_wake_execution = await publisher.publish(object(), _outcome(status=status))

    assert should_wake_execution is True
    kwargs = accept.await_args.kwargs
    assert kwargs["kind"] is InboundEvidenceKind.TRANSPORT_RESULT
    assert kwargs["source_identity"] == "transport:TRANSPORT-1:outcome:1"
    assert kwargs["transport_task_id"] == "TRANSPORT-1"
    assert kwargs["workline_id"] == 31
    assert kwargs["apply_status"] is InboundEvidenceApplyStatus.APPLIED
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
async def test_return_rack_inbound_result_is_published_for_api_recovery() -> None:
    publisher, accept = _publisher(step="PICKING_TASK_RETURN_RACK_IN")

    assert await publisher.publish(object(), _outcome()) is True
    assert accept.await_args.kwargs["normalized_payload"]["step"] == "PICKING_TASK_RETURN_RACK_IN"


@pytest.mark.asyncio
async def test_inbound_batch_transport_result_matches_all_frozen_bin_members() -> None:
    from manual_picking.application.transport_outcome import ManualPickingTransportOutcomePublisher

    binding = SimpleNamespace(
        workline_id=31,
        client_request_id="REQUEST-1",
        step="MANUAL_PICKING_INBOUND_BATCH",
        resource_fence_id="batch-1",
        correlation_id="batch-1:0",
        source_evidence_id=101,
    )
    source = SimpleNamespace(
        id=101, kind=InboundEvidenceKind.WMS_RESULT, operation="outbound.bin.inbound_batch@v1", operation_id="batch-1"
    )
    task = SimpleNamespace(
        kind="BIN_MOVE",
        transport_task_id="TRANSPORT-1",
        client_request_id="REQUEST-1",
        request_json={
            "moves": [{"bin_code": "A000000001", "target": {"kind": "HANDOFF_POSITION", "location_code": "CNV0301"}}]
        },
    )
    accept = AsyncMock(return_value=SimpleNamespace(duplicate=False))
    publisher = ManualPickingTransportOutcomePublisher(
        binding_repository=SimpleNamespace(get_by_client_request_id=AsyncMock(return_value=binding)),
        evidence_repository=SimpleNamespace(get_by_id=AsyncMock(return_value=source)),
        evidence_service=SimpleNamespace(accept=accept),
        transports=SimpleNamespace(get_task_by_client_request=AsyncMock(return_value=task)),
    )
    outcome = TransportOutcome(
        transport_task_id="TRANSPORT-1",
        client_request_id="REQUEST-1",
        outcome_version=1,
        caller=TransportCaller(workline_id="31"),
        status=TransportOutcomeStatus.SUCCEEDED,
        reason_code=None,
        members=(TransportMemberOutcome("A000000001", HandoffPosition("CNV0301")),),
    )

    assert await publisher.publish(object(), outcome)
    assert accept.await_args.kwargs["normalized_payload"]["batch_operation_id"] == "batch-1"
    assert accept.await_args.kwargs["normalized_payload"]["step"] == "MANUAL_PICKING_INBOUND_BATCH"


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


@pytest.mark.asyncio
async def test_rotate_result_uses_original_plan_evidence_and_face() -> None:
    publisher, accept = _publisher(step="MANUAL_PICKING_SOURCE_RACK_ROTATE")
    outcome = TransportOutcome(
        transport_task_id="TRANSPORT-1",
        client_request_id="REQUEST-1",
        outcome_version=1,
        caller=TransportCaller(workline_id="31"),
        status=TransportOutcomeStatus.SUCCEEDED,
        reason_code=None,
        members=(TransportMemberOutcome("RACK-1", RackPosition("FIVE-RACK-POSITION"), arrival_face="270"),),
    )
    assert await publisher.publish(object(), outcome)
    assert accept.await_args.kwargs["normalized_payload"]["step"] == "MANUAL_PICKING_SOURCE_RACK_ROTATE"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [TransportOutcomeStatus.SUCCEEDED, TransportOutcomeStatus.UNKNOWN])
async def test_transfer_departure_result_preserves_original_wms_decision(status: TransportOutcomeStatus) -> None:
    from manual_picking.application.transport_outcome import ManualPickingTransportOutcomePublisher

    binding = SimpleNamespace(
        workline_id=31,
        client_request_id="REQUEST-1",
        step="MANUAL_PICKING_TRANSFER_RACK_OUT",
        resource_fence_id="RACK-1",
        correlation_id="departure-op",
        source_evidence_id=101,
        picking_task_id=31,
    )
    source = SimpleNamespace(
        id=101,
        kind=InboundEvidenceKind.WMS_RESULT,
        operation="outbound.rack.departure_decide@v1",
        operation_id="departure-op",
    )
    accept = AsyncMock(return_value=SimpleNamespace(duplicate=False))
    publisher = ManualPickingTransportOutcomePublisher(
        binding_repository=SimpleNamespace(get_by_client_request_id=AsyncMock(return_value=binding)),
        evidence_repository=SimpleNamespace(get_by_id=AsyncMock(return_value=source)),
        evidence_service=SimpleNamespace(accept=accept),
    )
    outcome = TransportOutcome(
        transport_task_id="TRANSPORT-1",
        client_request_id="REQUEST-1",
        outcome_version=1,
        caller=TransportCaller(workline_id="31"),
        status=status,
        reason_code=None,
        members=(TransportMemberOutcome("RACK-1", ZonePosition("WH05")),)
        if status is TransportOutcomeStatus.SUCCEEDED
        else (),
    )
    assert await publisher.publish(object(), outcome)
    payload = accept.await_args.kwargs["normalized_payload"]
    assert payload["step"] == "MANUAL_PICKING_TRANSFER_RACK_OUT"
    assert payload["source_evidence_id"] == 101


@pytest.mark.asyncio
async def test_source_return_result_uses_original_plan_evidence() -> None:
    publisher, accept = _publisher(step="MANUAL_PICKING_SOURCE_RACK_OUT")
    binding = publisher._bindings.get_by_client_request_id.return_value
    binding.picking_task_id = "PICK-1"
    source = publisher._evidences.get_by_id.return_value
    source.kind = InboundEvidenceKind.WMS_RESULT
    source.operation = "outbound.rack.departure_decide@v1"
    outcome = TransportOutcome(
        transport_task_id="TRANSPORT-1",
        client_request_id="REQUEST-1",
        outcome_version=1,
        caller=TransportCaller(workline_id="31"),
        status=TransportOutcomeStatus.SUCCEEDED,
        reason_code=None,
        members=(TransportMemberOutcome("RACK-1", ZonePosition("WH01")),),
    )
    assert await publisher.publish(object(), outcome)
    payload = accept.await_args.kwargs["normalized_payload"]
    assert payload["step"] == "MANUAL_PICKING_SOURCE_RACK_OUT"
    assert payload["picking_task_id"] == "PICK-1" and payload["rack_id"] == "RACK-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "step", ["MANUAL_PICKING_RETURN_BUFFER_DRAIN_RACK_IN", "MANUAL_PICKING_RETURN_BUFFER_DRAIN_RACK_OUT"]
)
@pytest.mark.parametrize("status", [TransportOutcomeStatus.SUCCEEDED, TransportOutcomeStatus.UNKNOWN])
async def test_drain_transport_publishes_original_ready_evidence_without_task(step, status):
    publisher, accept = _publisher(step=step)
    _configure_drain(publisher)
    source = publisher._evidences.get_by_id.return_value
    assert await publisher.publish(object(), _outcome(status=status))
    assert await publisher.publish(object(), _outcome(status=status))
    payload = accept.await_args.kwargs["normalized_payload"]
    assert "picking_task_id" not in payload
    assert payload["drain_operation_id"] == source.operation_id
    assert accept.await_args.kwargs["source_identity"] == "transport:TRANSPORT-1:outcome:1"
    source.normalized_payload["data"]["racks"][0]["rack_id"] = "WRONG"
    with pytest.raises(ValueError):
        await publisher.publish(object(), _outcome(status=status))


def _configure_drain(publisher):
    from wes_plugin_sdk import BinReturnCandidate, wms_operations

    from src.app.wms_adapter.return_buffer_drain.typed import encode_request
    from src.app.wms_integration.return_buffer_drain import ReturnBufferDrainResultReader
    from src.utils.canonical_json import canonical_json_digest

    operation_id = "019f0000-0000-7000-8000-000000000001"
    binding = publisher._bindings.get_by_client_request_id.return_value
    binding.correlation_id = f"drain:{operation_id}:rack:RACK-1"
    binding.picking_task_id = None
    source = publisher._evidences.get_by_id.return_value
    source.kind = InboundEvidenceKind.WMS_RESULT
    source.operation = "workline.return_buffer.drain_rack_decide@v1"
    source.operation_id = operation_id
    source.workline_id = 31
    source.normalized_payload = {
        "operation_id": operation_id,
        "code": "DECIDED",
        "timestamp": 1,
        "data": {"result": "READY", "racks": [{"rack_id": "RACK-1", "rack_face": ["90"]}]},
    }
    source.payload_digest = canonical_json_digest(source.normalized_payload)
    intent = wms_operations.workline_return_buffer_drain_rack_decide(
        operation_id=operation_id,
        workline_code="LINE-31",
        required_slot_count=1,
    )
    payload = encode_request(intent, timestamp=1)
    confirmation = SimpleNamespace(
        operation=source.operation,
        operation_id=operation_id,
        workline_id=31,
        status="COMPLETED",
        response_evidence_id=source.id,
        request_payload=payload,
        request_digest=canonical_json_digest(payload),
        response_result="READY",
    )
    repository = AsyncMock()
    repository.get_by_identity.return_value = confirmation
    publisher._drain_reader = ReturnBufferDrainResultReader(repository)
    return confirmation, source, repository


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "drift", ["missing_confirmation", "pending", "request_digest", "response_digest", "link", "owner"]
)
async def test_drain_transport_requires_host_validated_confirmation_and_evidence(drift):
    publisher, accept = _publisher(step="MANUAL_PICKING_RETURN_BUFFER_DRAIN_RACK_OUT")
    confirmation, source, repository = _configure_drain(publisher)
    if drift == "missing_confirmation":
        repository.get_by_identity.return_value = None
    elif drift == "pending":
        confirmation.status = "PENDING"
    elif drift == "request_digest":
        confirmation.request_digest = "0" * 64
    elif drift == "response_digest":
        source.payload_digest = "0" * 64
    elif drift == "link":
        confirmation.response_evidence_id = 999
    else:
        confirmation.workline_id = 99
    with pytest.raises(ValueError):
        await publisher.publish(object(), _outcome())
    accept.assert_not_awaited()
