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
    if step in {"MANUAL_PICKING_SOURCE_RACK_OUT", "MANUAL_PICKING_RETURN_RACK_OUT"}:
        import wes_plugin_sdk as sdk

        binding.correlation_id = "departure:01a13701-4e91-7d26-94ae-52251f9b752e"
        publisher._departure_reader = SimpleNamespace(
            by_operation_id=AsyncMock(
                return_value=SimpleNamespace(
                    status="COMPLETED",
                    evidence_id=359862255387200,
                    outcome=sdk.RackDepartureOutcome(sdk.RackDepartureReady(sdk.TransportZonePosition("WH01"))),
                )
            )
        )
        publisher._transports = SimpleNamespace(
            get_task_by_client_request=AsyncMock(
                return_value=SimpleNamespace(
                    kind="RACK_MOVE",
                    transport_task_id="TRANSPORT-1",
                    client_request_id="REQUEST-1",
                    request_json={"rack_id": "RACK-1", "target": {"kind": "ZONE", "location_code": "WH01"}},
                )
            )
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
async def test_source_return_result_uses_frozen_departure_decision() -> None:
    publisher, accept = _publisher(step="MANUAL_PICKING_SOURCE_RACK_OUT")
    binding = publisher._bindings.get_by_client_request_id.return_value
    binding.picking_task_id = "PICK-1"
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


@pytest.mark.asyncio
@pytest.mark.parametrize("step", ["MANUAL_PICKING_SOURCE_RACK_OUT", "MANUAL_PICKING_RETURN_RACK_OUT"])
async def test_departure_publication_restores_frozen_decision_and_preserves_old_binding(step):
    import wes_plugin_sdk as sdk

    publisher, accept = _publisher(step=step)
    binding = publisher._bindings.get_by_client_request_id.return_value
    op = "01a13701-4e91-7d26-94ae-52251f9b752e"
    binding.correlation_id = "departure:" + op
    binding.causal_token = 606
    binding.picking_task_id = 359761395688000
    publisher._evidences.get_by_id.return_value.kind = InboundEvidenceKind.WMS_EVENT
    snapshot = SimpleNamespace(
        status="COMPLETED",
        evidence_id=359862255387200,
        outcome=sdk.RackDepartureOutcome(sdk.RackDepartureReady(sdk.TransportZonePosition("WH01"))),
    )
    publisher._departure_reader = SimpleNamespace(by_operation_id=AsyncMock(return_value=snapshot))
    publisher._transports = SimpleNamespace(
        get_task_by_client_request=AsyncMock(
            return_value=SimpleNamespace(
                kind="RACK_MOVE",
                transport_task_id="TRANSPORT-1",
                client_request_id="REQUEST-1",
                request_json={"rack_id": "RACK-1", "target": {"kind": "ZONE", "location_code": "WH01"}},
            )
        )
    )
    outcome = _outcome()
    assert await publisher.publish(object(), outcome)
    publisher._departure_reader.by_operation_id.assert_awaited_once_with(
        publisher._departure_reader.by_operation_id.await_args.args[0],
        workline_id=31,
        operation_id=op,
        rack_id="RACK-1",
    )
    assert binding.source_evidence_id == 101 and binding.causal_token == 606
    assert binding.client_request_id == "REQUEST-1" and binding.correlation_id == "departure:" + op
    payload = accept.await_args.kwargs["normalized_payload"]
    assert payload["source_evidence_id"] == 101
    assert accept.await_args.kwargs["source_identity"] == "transport:TRANSPORT-1:outcome:1"
    assert payload["transport_task_id"] == outcome.transport_task_id and payload["outcome_version"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["missing", "pending", "wait", "identity", "transport", "target", "rack", "member"])
async def test_frozen_departure_publication_fails_closed_without_original_matching_ready(case):
    import wes_plugin_sdk as sdk

    publisher, accept = _publisher(step="MANUAL_PICKING_SOURCE_RACK_OUT")
    binding = publisher._bindings.get_by_client_request_id.return_value
    snapshot = publisher._departure_reader.by_operation_id.return_value
    task = publisher._transports.get_task_by_client_request.return_value
    outcome = _outcome()
    if case == "missing":
        publisher._departure_reader.by_operation_id.return_value = None
    elif case == "pending":
        snapshot.status, snapshot.outcome = "PENDING", None
    elif case == "wait":
        snapshot.outcome = sdk.RackDepartureOutcome(sdk.RackDepartureWait(1000))
    elif case == "identity":
        binding.correlation_id = "unbound"
    elif case == "transport":
        task.transport_task_id = "OTHER"
    elif case == "target":
        task.request_json["target"]["location_code"] = "OTHER"
    elif case == "rack":
        task.request_json["rack_id"] = "OTHER"
    else:
        binding.resource_fence_id = "OTHER"
    with pytest.raises((ValueError, LookupError)):
        await publisher.publish(object(), outcome)
    accept.assert_not_awaited()
    assert binding.source_evidence_id == 101
    publisher._evidences.get_by_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_frozen_departure_duplicate_publication_keeps_original_evidence_identity():
    publisher, accept = _publisher(step="MANUAL_PICKING_SOURCE_RACK_OUT")
    accept.return_value = SimpleNamespace(duplicate=True)
    outcome = _outcome()
    for _ in range(2):
        assert await publisher.publish(object(), outcome)
    identities = [call.kwargs["source_identity"] for call in accept.await_args_list]
    assert identities == ["transport:TRANSPORT-1:outcome:1"] * 2
    assert (
        accept.await_args_list[0].kwargs["normalized_payload"] == accept.await_args_list[1].kwargs["normalized_payload"]
    )


@pytest.mark.asyncio
async def test_606_restoration_uses_real_confirmation_and_preserves_current_670_projection():
    from datetime import datetime, timedelta

    from manual_picking.application.transport_outcome import ManualPickingTransportOutcomePublisher
    from sqlalchemy import func, select
    from test_batch_repository import _new_sessions

    from src.app.execution.models import InboundEvidence, PositionProjection, TransportDecisionBinding, WmsConfirmation
    from src.app.transport.models import TransportTask
    from src.utils.canonical_json import canonical_json_digest

    now = datetime(2026, 10, 14)
    op = "01a13701-4e91-7d26-94ae-52251f9b752e"
    request = {
        "operation": "outbound.rack.departure_decide@v1",
        "operation_id": op,
        "timestamp": 1791924194954,
        "data": {
            "task_id": None,
            "rack_id": "510021",
            "current_location": {"type": "RACK_POSITION", "location_code": "KT16"},
            "current_face": "90",
        },
    }
    response = {
        "operation_id": op,
        "code": "DECIDED",
        "timestamp": 1791924195534,
        "data": {"result": "READY", "rack_destination": {"type": "ZONE", "location_code": "WH01"}},
    }
    engine, sessions = await _new_sessions()
    try:
        async with sessions.begin() as db:
            plan = InboundEvidence(
                id=101,
                kind="WMS_EVENT",
                source_identity="old-plan",
                payload_digest="a" * 64,
                operation="outbound.picking_task.plan_delta@v1",
                operation_id="old-plan-op",
                normalized_payload={},
                received_at=now,
                apply_status="APPLIED",
            )
            ready = InboundEvidence(
                id=102,
                kind="WMS_RESULT",
                source_identity="outbound.rack.departure_decide@v1:" + op,
                workline_id=7,
                operation="outbound.rack.departure_decide@v1",
                operation_id=op,
                normalized_payload=response,
                payload_digest=canonical_json_digest(response),
                received_at=now,
                apply_status="APPLIED",
                published_at=now,
                decision_digest="c" * 64,
            )
            confirmation = WmsConfirmation(
                operation="outbound.rack.departure_decide@v1",
                operation_id=op,
                workline_id=7,
                request_payload=request,
                request_digest=canonical_json_digest(request),
                created_at=now,
                deadline_at=now + timedelta(seconds=30),
                status="COMPLETED",
                response_result="READY",
                response_evidence_id=102,
                completed_at=now,
            )
            binding = TransportDecisionBinding(
                workline_id=7,
                picking_task_id=31,
                causal_token=606,
                correlation_id="departure:" + op,
                step="MANUAL_PICKING_SOURCE_RACK_OUT",
                resource_fence_id="510021",
                client_request_id="old-request",
                source_evidence_id=101,
                created_at=now,
            )
            task = TransportTask(
                transport_task_id="old-606",
                client_request_id="old-request",
                kind="RACK_MOVE",
                request_json={"rack_id": "510021", "target": {"kind": "ZONE", "location_code": "WH01"}},
                caller_json={"workline_id": "7"},
                request_digest="a" * 64,
                submit_operation_id="old-submit",
                submit_timestamp_ms=1,
                submit_request_body="{}",
                submit_request_body_digest="b" * 64,
                status="SUCCEEDED",
                outcome_version=1,
                published_outcome_version=0,
                created_at=now,
                updated_at=now,
            )
            current = PositionProjection(
                object_type="RACK",
                object_id="510021",
                workline_id=7,
                position_json={"kind": "RACK_POSITION", "location_code": "WHE0604"},
                source_operation_id="current-result",
                source_transport_task_id="current-670",
                source_causal_token=670,
                source_effect_phase="FINAL_RESULT",
            )
            db.add_all([plan, ready, confirmation, binding, task, current])
            await db.flush()
            accept = AsyncMock(return_value=SimpleNamespace(duplicate=False))
            publisher = ManualPickingTransportOutcomePublisher(evidence_service=SimpleNamespace(accept=accept))
            outcome = TransportOutcome(
                transport_task_id="old-606",
                client_request_id="old-request",
                outcome_version=1,
                caller=TransportCaller(workline_id="7"),
                status=TransportOutcomeStatus.SUCCEEDED,
                reason_code=None,
                members=(TransportMemberOutcome("510021", RackPosition("WHE0703")),),
            )
            assert await publisher.publish(db, outcome)
            assert binding.source_evidence_id == 101 and binding.causal_token == 606
            assert current.source_causal_token == 670 and current.position_json["location_code"] == "WHE0604"
            assert task.client_request_id == "old-request" and task.submit_request_body == "{}"
            assert ready.normalized_payload == response and plan.source_identity == "old-plan"
            assert await db.scalar(select(func.count()).select_from(TransportTask)) == 1
            assert accept.await_args.kwargs["source_identity"] == "transport:old-606:outcome:1"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("depth", [1, 3])
async def test_departure_retry_result_traces_frozen_parent_identity_without_parent_state_gate(depth):
    publisher, accept = _publisher(step="MANUAL_PICKING_SOURCE_RACK_OUT")
    binding = publisher._bindings.get_by_client_request_id.return_value
    operation_id = binding.correlation_id.removeprefix("departure:")
    binding.causal_token = 606
    binding.correlation_id = "retry:1:PARENT-1"
    tasks, bindings = {}, {"REQUEST-1": binding}
    for i in range(1, depth + 1):
        client = f"PARENT-REQUEST-{i}"
        tasks[f"PARENT-{i}"] = SimpleNamespace(
            transport_task_id=f"PARENT-{i}",
            client_request_id=client,
            kind="RACK_MOVE",
            request_json={"rack_id": "RACK-1", "target": {"kind": "ZONE", "location_code": "WH01"}},
            status="RECONCILING",
            outcome_version=2,
            published_outcome_version=1,
        )
        bindings[client] = SimpleNamespace(
            workline_id=31,
            picking_task_id=None,
            resource_fence_id="RACK-1",
            source_evidence_id=101,
            step=binding.step,
            client_request_id=client,
            causal_token=606 - i,
            correlation_id="departure:" + operation_id if i == depth else f"retry:{i + 1}:PARENT-{i + 1}",
        )
    publisher._transports.get_task = AsyncMock(side_effect=lambda _db, identity: tasks.get(identity))
    publisher._bindings.get_by_client_request_id = AsyncMock(side_effect=lambda _db, identity: bindings.get(identity))
    assert await publisher.publish(object(), _outcome())
    assert publisher._departure_reader.by_operation_id.await_args.kwargs["operation_id"] == operation_id
    assert accept.await_args.kwargs["source_identity"] == "transport:TRANSPORT-1:outcome:1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["missing", "owner", "task", "source", "step", "target", "causal", "cycle", "malformed"]
)
async def test_departure_retry_chain_rejects_missing_or_drifting_frozen_parent(case):
    publisher, accept = _publisher(step="MANUAL_PICKING_SOURCE_RACK_OUT")
    child = publisher._bindings.get_by_client_request_id.return_value
    child.causal_token = 606
    op = child.correlation_id.removeprefix("departure:")
    child.correlation_id = "retry:1:PARENT"
    parent = SimpleNamespace(
        workline_id=31,
        picking_task_id=None,
        resource_fence_id="RACK-1",
        source_evidence_id=101,
        step=child.step,
        client_request_id="PARENT-REQUEST",
        causal_token=605,
        correlation_id="departure:" + op,
    )
    task = SimpleNamespace(
        transport_task_id="PARENT",
        client_request_id="PARENT-REQUEST",
        kind="RACK_MOVE",
        request_json={"rack_id": "RACK-1", "target": {"kind": "ZONE", "location_code": "WH01"}},
    )
    if case == "owner":
        parent.workline_id = 99
    elif case == "task":
        parent.picking_task_id = 99
    elif case == "source":
        parent.source_evidence_id = 99
    elif case == "step":
        parent.step = "OTHER"
    elif case == "target":
        task.request_json["target"]["location_code"] = "OTHER"
    elif case == "causal":
        parent.causal_token = 607
    elif case == "cycle":
        parent.correlation_id = "retry:2:PARENT"
    elif case == "malformed":
        child.correlation_id = "retry:bad:PARENT"
    publisher._transports.get_task = AsyncMock(return_value=None if case == "missing" else task)
    publisher._bindings.get_by_client_request_id = AsyncMock(
        side_effect=lambda _db, client: child if client == "REQUEST-1" else parent
    )
    with pytest.raises((LookupError, ValueError)):
        await publisher.publish(object(), _outcome())
    accept.assert_not_awaited()
