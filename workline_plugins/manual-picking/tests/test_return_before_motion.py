"""按当前到位面求值；请求候选不是提前储位分配。"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import wes_plugin_sdk as sdk
from drain_execution_support import unbound_drain_executions
from manual_picking.application.batch_flow import ManualPickingBatchFlow
from manual_picking.application.batch_repository import BatchRepository
from manual_picking.application.batch_result import ManualPickingBatchResultFlow
from manual_picking.application.bin_line.return_model import BinLineReturn
from manual_picking.application.drain_execution_model import ManualPickingDrainExecution, ManualPickingDrainReturnMember
from manual_picking.application.drain_execution_repository import DrainExecutionRepository
from test_batch_repository import _new_sessions
from test_source_progression import setup_driver

from src.app.device.models.command import DeviceCommand
from src.app.execution.models import InboundEvidence, PositionProjection, TransportDecisionBinding, WmsConfirmation
from src.app.execution.repositories.inbound_evidence_repository import InboundEvidenceRepository
from src.app.execution.services.fact_processor import FactProcessor
from src.app.transport.models import TransportEvidence, TransportTask
from src.app.wms_adapter.outbound_picking.return_batch_typed import encode_request
from src.app.wms_integration.outbound_picking.services.bin_batch import BinBatchResultReader
from src.app.workline.models import WorkLine
from src.utils.canonical_json import canonical_json_digest

NOW = datetime(2026, 10, 14, 5)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,unpublished,active_window,current_fact,expected",
    [
        ("PENDING", False, False, False, True),
        ("ACCEPTED", False, False, False, True),
        ("RECONCILING", False, False, False, True),
        ("SUCCEEDED", True, False, False, True),
        ("REJECTED", False, True, False, True),
        ("SUCCEEDED", False, False, True, True),
        ("SUCCEEDED", False, False, False, False),
        ("REJECTED", False, False, False, False),
    ],
)
@pytest.mark.parametrize("new_owner", [32, None])
async def test_frozen_ready_yields_only_to_current_rack_owner_or_dependency(
    status, unpublished, active_window, current_fact, expected, new_owner
):
    engine, sessions = await _new_sessions()
    repo = BatchRepository()
    try:
        async with sessions.begin() as db:
            db.add_all(
                [
                    TransportDecisionBinding(
                        workline_id=7,
                        picking_task_id=31,
                        correlation_id="old-owner",
                        step="OLD_OWNER",
                        resource_fence_id="510004",
                        client_request_id="old-owner",
                        source_evidence_id=1,
                        causal_token=1,
                    ),
                    TransportTask(
                        transport_task_id="new-owner",
                        client_request_id="new-owner",
                        kind="RACK_ROTATE",
                        request_digest="a" * 64,
                        caller_json={},
                        request_json={"rack_id": "510004"},
                        submit_operation_id="new-submit",
                        submit_timestamp_ms=1,
                        submit_request_body="{}",
                        submit_request_body_digest="b" * 64,
                        status=status,
                        outcome_version=1,
                        published_outcome_version=0 if unpublished else 1,
                        created_at=NOW,
                        updated_at=NOW,
                    ),
                    TransportDecisionBinding(
                        workline_id=7,
                        correlation_id="new-owner",
                        step="NEW_OWNER",
                        resource_fence_id="510004",
                        client_request_id="new-owner",
                        source_evidence_id=1,
                        causal_token=2,
                        picking_task_id=new_owner,
                        window_target_location_code="KT16" if active_window else None,
                    ),
                ]
            )
            if current_fact:
                db.add(
                    PositionProjection(
                        object_type="RACK",
                        object_id="510004",
                        workline_id=7,
                        position_json={"kind": "RACK_POSITION", "location_code": "KT16"},
                        source_causal_token=2,
                        source_operation_id="new-result",
                        source_transport_task_id="new-owner",
                        source_effect_phase="FINAL_RESULT",
                    )
                )
            await db.flush()
            assert await repo.has_newer_rack_action(db, rack_id="510004", causal_token=1) is expected
            assert not await repo.has_newer_rack_action(db, rack_id="510004", causal_token=2)
            assert not await repo.has_newer_rack_action(db, rack_id="OTHER", causal_token=1)
            assert await repo.has_newer_rack_action(db, rack_id="510004", causal_token=None)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state,face,bins,expected",
    [
        (None, "90", ("A", "B"), 1),
        ("WAIT", "90", ("A",), 0),
        ("DONE", "90", ("A",), None),
        (None, None, ("A",), None),
        (None, "90", (), None),
    ],
)
async def test_motion_requests_current_face_once_and_waits_for_original_result(state, face, bins, expected):
    repository = SimpleNamespace(return_before_motion_state=AsyncMock(return_value=state))
    rows = tuple(SimpleNamespace(id=i, bin_code=code, created_at=NOW) for i, code in enumerate(bins, 1))
    scheduler = SimpleNamespace(create_in_session=AsyncMock())
    flow = ManualPickingBatchFlow(
        repository,
        SimpleNamespace(ready_prefix_for_update=AsyncMock(return_value=rows)),
        scheduler,
        object(),
        uuid_factory=lambda: "new-request",
        drain_executions=unbound_drain_executions(),
    )
    result = await flow.return_before_motion(
        object(),
        workline_id=7,
        workline_code="KT16",
        rack_id="510004",
        rack_face=face,
        return_location="CNV0302",
        arrived_at=NOW,
        now=NOW,
    )
    assert result == expected
    if expected == 1:
        intent = scheduler.create_in_session.await_args.args[1]
        assert intent.rack_id == "510004" and intent.rack_face == "90"
        assert tuple(c.bin_code for c in intent.return_candidates) == bins
    else:
        scheduler.create_in_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_candidates_do_not_reset_current_face_motion_phase():
    created_at = NOW + timedelta(seconds=1)
    repository = SimpleNamespace(return_before_motion_state=AsyncMock(return_value=None))
    flow = ManualPickingBatchFlow(
        repository,
        SimpleNamespace(
            ready_prefix_for_update=AsyncMock(
                return_value=(SimpleNamespace(id=2, bin_code="A", created_at=created_at),)
            )
        ),
        SimpleNamespace(create_in_session=AsyncMock()),
        object(),
        uuid_factory=lambda: "new-request",
        drain_executions=unbound_drain_executions(),
    )
    await flow.return_before_motion(
        object(),
        workline_id=7,
        workline_code="KT16",
        rack_id="510004",
        rack_face="90",
        return_location="CNV0302",
        arrived_at=NOW,
        now=created_at,
    )
    assert repository.return_before_motion_state.await_args.kwargs["arrived_at"] == NOW


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "step",
    ["MANUAL_PICKING_SOURCE_RACK_OUT", "MANUAL_PICKING_RETURN_RACK_OUT", "MANUAL_PICKING_RETIRED_DRAIN_RACK_OUT"],
)
async def test_all_workline_departures_request_returns_before_departure_decision(step):
    driver, line, *_ = setup_driver()
    driver._flow.return_before_motion = AsyncMock(return_value=1)
    assert (
        await driver._advance_workline_departure(
            object(),
            line,
            rack_id="510004",
            current_face="90",
            step=step,
            picking_task_id=None,
            arrival_transport_task_id="actual-arrival",
            now=NOW,
            round_started_at=NOW,
        )
        == 1
    )
    driver._departure_scheduler.create_in_session.assert_not_awaited()
    assert driver._rack_creator.depart == []
    assert driver._flow.return_before_motion.await_args.kwargs["rack_face"] == "90"


@pytest.mark.asyncio
async def test_motion_decision_tracks_current_arrival_and_face_and_preserves_unknown_candidates():
    engine, sessions = await _new_sessions()
    async with engine.begin() as connection:
        for table in (
            ManualPickingDrainExecution.__table__,
            ManualPickingDrainReturnMember.__table__,
            BinLineReturn.__table__,
            WorkLine.__table__,
            DeviceCommand.__table__,
        ):
            await connection.run_sync(table.create)
    op = "01a1370f-8ce3-7769-adf9-2e3f1ec14b69"
    intent = sdk.wms_operations.outbound_bin_return_batch(
        operation_id=op,
        workline_code="KT16",
        rack_id="510004",
        rack_face="90",
        return_candidates=(sdk.BinReturnCandidate(1, "A", "CNV0302"),),
    )
    payload = encode_request(intent, timestamp=1791925128404)
    repo, eligibility = BatchRepository(), DrainExecutionRepository()
    try:
        async with sessions.begin() as db:
            row = BinLineReturn(
                workline_id=7,
                bin_code="A",
                scan3_evidence_id=1,
                scan4_evidence_id=2,
                scan4_event_time=1000,
                return_state="READY",
                created_at=NOW - timedelta(seconds=1),
            )
            request = WmsConfirmation(
                operation="outbound.bin.return_batch@v1",
                operation_id=op,
                workline_id=7,
                request_payload=payload,
                request_digest=canonical_json_digest(payload),
                status="PENDING",
                created_at=NOW,
                deadline_at=NOW + timedelta(seconds=30),
            )
            db.add_all([row, request])
            await db.flush()
            assert await eligibility.eligible_return_prefix(db, workline_id=7, rows=(row,)) == ()

            async def state(rack="510004", face="90", after=NOW):
                return await repo.return_before_motion_state(
                    db, workline_id=7, rack_id=rack, rack_face=face, arrived_at=after
                )

            assert await state() == "WAIT"
            response = {
                "operation_id": op,
                "timestamp": 1791925128405,
                "code": "DECIDED",
                "data": {"result": "NO_BATCH", "retry_after_ms": 3000},
            }
            evidence = InboundEvidence(
                kind="WMS_RESULT",
                workline_id=7,
                operation=request.operation,
                operation_id=op,
                source_identity="return:" + op,
                normalized_payload=response,
                payload_digest=canonical_json_digest(response),
                received_at=NOW,
                apply_status="APPLIED",
                decision_digest="a" * 64,
                published_at=NOW,
            )
            db.add(evidence)
            await db.flush()
            request.status = "COMPLETED"
            request.response_evidence_id = evidence.id
            request.response_result = "NO_BATCH"
            await db.flush()
            assert await eligibility.eligible_return_prefix(db, workline_id=7, rows=(row,)) == (row,)
            assert await state() == "DONE"
            assert await state(face="270") is None
            assert await state(rack="510003") is None
            assert await state(after=NOW + timedelta(seconds=1)) is None
            assert not await repo.return_request_in_round(
                db, workline_id=7, operation_id=op, arrived_at=NOW + timedelta(seconds=1)
            )
            physical = TransportEvidence(
                operation_id="01a13702-3313-7fac-bfe4-6b1b04f19ed4",
                transport_task_id="rack-arrival",
                operation="transport.task.resulted@v1",
                outcome_revision=1,
                event_timestamp_ms=1791925128405,
                message_digest="b" * 64,
                payload_json={},
                ack_timestamp_ms=1791925128405,
                ack_data_json={},
                status="APPLIED",
                received_at=NOW + timedelta(seconds=10),
            )
            projection = PositionProjection(
                workline_id=7,
                object_type="RACK",
                object_id="510004",
                position_unknown=False,
                position_json={"kind": "RACK_POSITION", "location_code": "KT16"},
                arrival_face="90",
                source_operation_id=physical.operation_id,
                source_transport_task_id=physical.transport_task_id,
                source_causal_token=100,
                source_effect_phase="FINAL_RESULT",
            )
            db.add_all([physical, projection])
            await db.flush()
            actual_arrived = await repo.current_rack_arrival_at(
                db, workline_id=7, rack_id="510004", transport_task_id="rack-arrival"
            )
            assert actual_arrived == physical.received_at
            assert await state(after=actual_arrived) is None
            projection.position_unknown = True
            await db.flush()
            assert (
                await repo.current_rack_arrival_at(
                    db, workline_id=7, rack_id="510004", transport_task_id="rack-arrival"
                )
                is None
            )
            execution = ManualPickingDrainExecution(
                workline_id=7,
                drain_operation_id="current-drain",
                decision_evidence_id=evidence.id,
            )
            db.add(execution)
            await db.flush()
            db.add(
                ManualPickingDrainReturnMember(
                    execution_id=execution.id,
                    return_batch_confirmation_id=request.id,
                    return_id=row.id,
                    sequence_no=1,
                )
            )
            evidence.published_at = None
            evidence.apply_status = "RECONCILING"
            await db.flush()
            pending = await eligibility.pending_return_for_rack(
                db,
                execution_id=execution.id,
                workline_id=7,
                rack_id="510004",
            )
            assert pending[0] == intent and isinstance(pending[1].result, sdk.BinBatchNoBatch)
            assert pending[2].id == evidence.id
            assert await repo.has_unclosed_return_dependency_for_rack(
                db,
                workline_id=7,
                rack_id="510004",
                round_started_at=NOW,
            )
            assert not await repo.has_unclosed_return_dependency_for_rack(
                db,
                workline_id=7,
                rack_id="510004",
                round_started_at=NOW,
                exclude_operation_id=op,
            )
            assert not await repo.has_return_batch_transport(db, workline_id=7, operation_id=op)
            independent = WmsConfirmation(
                operation="outbound.bin.inbound_batch@v1",
                operation_id=op,
                workline_id=7,
                request_payload=payload,
                request_digest=canonical_json_digest(payload),
                status="PENDING",
                created_at=NOW,
                deadline_at=NOW + timedelta(seconds=30),
            )
            db.add(independent)
            await db.flush()
            assert await repo.has_unclosed_return_dependency_for_rack(
                db, workline_id=7, rack_id="510004", round_started_at=NOW, exclude_operation_id=op
            )
            independent.status = "COMPLETED"
            db.add(
                WorkLine(
                    id=7,
                    line_code="KT16",
                    line_name="KT16",
                    line_type="MANUAL",
                    is_active=True,
                    plugin_key="manual-picking",
                    plugin_version="0.1.0",
                )
            )
            evidence.processed_at = NOW
            await db.flush()
            received_digest = evidence.payload_digest
            receipt_repository = InboundEvidenceRepository()
            await receipt_repository.mark_applied(db, evidence, processed_at=None)
            assert evidence.apply_status == "APPLIED" and evidence.processed_at is None
            assert evidence.published_at is None and evidence.payload_digest == received_digest
            claims = await receipt_repository.claim_decision_batch(
                db,
                now=NOW,
                claim_token="resume-original",
                claim_expires_at=NOW + timedelta(seconds=30),
                limit=10,
                business_wms_routes=(("manual-picking", "0.1.0", "outbound.bin.return_batch@v1"),),
            )
            assert tuple(item.id for item in claims) == (evidence.id,)
            from test_scan_flow import _setup

            result = SimpleNamespace(apply_return_in_session=AsyncMock(return_value="RETURN_NO_BATCH"))
            consumer, received, *_ = _setup(batch_reader=BinBatchResultReader(), batch_result=result)
            consumer._worklines.line.line_code = "KT16"
            received.rows[evidence.id] = evidence
            processor = FactProcessor(
                session_factory=sessions,
                plugin_binding=SimpleNamespace(resolve_business_evidence_consumer=lambda key, version: consumer),
                decision_applier=object(),
                evidence_repository=receipt_repository,
            )
            assert await processor._apply_business_evidence(db, evidence, ("manual-picking", "0.1.0", 7), NOW)
            result.apply_return_in_session.assert_awaited_once()
            assert evidence.published_at == NOW and evidence.decision_claim_token is None
            assert evidence.payload_digest == received_digest
            assert (
                await eligibility.pending_return_for_rack(
                    db,
                    execution_id=execution.id,
                    workline_id=7,
                    rack_id="510004",
                )
                is None
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_old_ready_cannot_consume_same_bin_new_return_execution():
    intent = sdk.wms_operations.outbound_bin_return_batch(
        operation_id="old-request",
        workline_code="KT16",
        rack_id="510004",
        rack_face="90",
        return_candidates=(sdk.BinReturnCandidate(1, "A", "CNV0302"),),
    )
    outcome = sdk.BinReturnBatchOutcome(
        sdk.BinReturnBatchReady((sdk.BinReturnMove(1, "A", sdk.TransportRackBinSlot("510004", "90", "S1")),))
    )
    row = SimpleNamespace(id=99, bin_code="A", created_at=NOW, return_state="READY")
    transport = SimpleNamespace(create=AsyncMock())
    batches = SimpleNamespace(return_request_in_round=AsyncMock(return_value=False))
    result = ManualPickingBatchResultFlow(
        SimpleNamespace(read_return=AsyncMock(return_value=(intent, outcome))),
        transport,
        SimpleNamespace(ready_prefix_for_update=AsyncMock(return_value=(row,))),
        batches,
        drain_executions=unbound_drain_executions(),
    )
    assert (
        await result.apply_return_in_session(
            object(),
            SimpleNamespace(id=31, operation_id=intent.operation_id),
            workline_id=7,
            workline_code="KT16",
            confirmed_rack_id="510004",
            confirmed_face="90",
            return_location="CNV0302",
        )
        is None
    )
    assert batches.return_request_in_round.await_args.kwargs["arrived_at"] == row.created_at
    transport.create.assert_not_awaited()
    assert row.return_state == "READY"
