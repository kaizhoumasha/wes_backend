from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from wes_plugin_sdk import RackFaceSequence, ReturnBufferDrainReady


@pytest.mark.asyncio
async def test_ready_creates_execution_once():
    from manual_picking.application.drain_execution_repository import DrainExecutionRepository

    decision = SimpleNamespace(
        workline_id=7,
        evidence_id=9,
        intent=SimpleNamespace(operation_id="drain-1"),
        result=ReturnBufferDrainReady((RackFaceSequence("R1", ("90",)),)),
    )
    db = SimpleNamespace(scalar=AsyncMock(return_value=None), add=Mock(), flush=AsyncMock())
    repository = DrainExecutionRepository()
    first = await repository.ensure_ready_in_session(db, decision=decision)
    db.scalar.return_value = first
    replay = await repository.ensure_ready_in_session(db, decision=decision)
    assert first is replay
    assert first.id == replay.id
    assert first.status == "ACTIVE"
    db.add.assert_called_once_with(first)
    first.status = "COMPLETED"
    assert (await repository.ensure_ready_in_session(db, decision=decision)).status == "COMPLETED"


@pytest.mark.asyncio
async def test_close_is_monotonic_and_preserves_first_reason():
    from manual_picking.application.drain_execution_repository import DrainExecutionRepository

    db = SimpleNamespace(flush=AsyncMock())
    execution = SimpleNamespace(status="ACTIVE")
    repository = DrainExecutionRepository()
    assert await repository.close_in_session(db, execution, status="COMPLETED", closed_at=datetime(2026, 10, 11))
    assert not await repository.close_in_session(
        db,
        execution,
        status="EXCEPTION_RETIRED",
        closed_at=datetime(2026, 10, 12),
        reason_code="DRAIN_RESULT_UNKNOWN",
        blocker_client_request_id="t",
        trigger_picking_task_id=1,
    )
    assert execution.status == "COMPLETED"
    assert execution.reason_code is None


@pytest.mark.asyncio
async def test_published_ready_fixture_has_verifiable_decision_and_original_identity():
    from test_drain_execution_postgresql import seed_ready

    from src.app.execution.models import InboundEvidence, WmsConfirmation
    from src.utils.canonical_json import canonical_json_digest

    stored = []

    async def flush():
        for index, row in enumerate(stored, 1):
            if row.id is None:
                row.id = 900 + index

    db = SimpleNamespace(add=stored.append, flush=flush)
    record = await seed_ready(db, SimpleNamespace(id=7, line_code="LINE-1"))
    evidence = next(row for row in stored if isinstance(row, InboundEvidence))
    confirmation = next(row for row in stored if isinstance(row, WmsConfirmation))
    assert evidence.published_at is not None
    assert evidence.decision_digest == canonical_json_digest(
        {"evidence_id": evidence.id, "role": "WMS_DRAIN", "result": "DRAIN_RECEIVED"}
    )
    assert record.evidence_id == evidence.id == confirmation.response_evidence_id
    assert record.intent.operation_id == evidence.operation_id == confirmation.operation_id
    assert evidence.payload_digest == canonical_json_digest(evidence.normalized_payload)
    assert confirmation.request_digest == canonical_json_digest(confirmation.request_payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["ACTIVE", "EXCEPTION_RETIRED"])
async def test_retired_pending_members_are_not_selected_by_new_batch(status):
    from manual_picking.application.drain_execution_repository import DrainExecutionRepository

    rows = tuple(SimpleNamespace(id=i, bin_code=f"B{i}") for i in (40, 41, 42))
    records = [(SimpleNamespace(return_id=41), status, SimpleNamespace(status="PENDING"), None)]
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=lambda: records)))
    db.scalars = AsyncMock(return_value=SimpleNamespace(all=lambda: ()))
    assert await DrainExecutionRepository().eligible_return_prefix(db, workline_id=7, rows=rows) == rows[:1]


@pytest.mark.asyncio
async def test_same_bin_new_return_id_does_not_inherit_old_membership():
    from manual_picking.application.drain_execution_repository import DrainExecutionRepository

    row = SimpleNamespace(id=42, bin_code="B1")
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(
                all=lambda: [
                    (SimpleNamespace(return_id=41), "EXCEPTION_RETIRED", SimpleNamespace(status="PENDING"), None)
                ]
            )
        )
    )
    db.scalars = AsyncMock(return_value=SimpleNamespace(all=lambda: ()))
    assert await DrainExecutionRepository().eligible_return_prefix(db, workline_id=7, rows=(row,)) == (row,)


@pytest.mark.asyncio
@pytest.mark.parametrize("ids", [(42, 41), (41, 43)])
async def test_same_batch_replay_rejects_changed_return_ids(ids):
    from manual_picking.application.drain_execution_repository import DrainExecutionRepository

    confirmation = SimpleNamespace(id=71, workline_id=7)
    execution = SimpleNamespace(id=81, workline_id=7, status="ACTIVE")
    members = [SimpleNamespace(execution_id=81, return_id=i, sequence_no=n) for n, i in enumerate((41, 42), 1)]
    db = SimpleNamespace(
        scalar=AsyncMock(side_effect=[execution, confirmation]),
        scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: members)),
    )
    with pytest.raises(ValueError, match="frozen drain return members"):
        await DrainExecutionRepository().freeze_return_members_in_session(
            db, execution_id=81, return_batch_operation_id="return-1", return_ids=ids
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,delivery,result,allowed",
    [
        ("ACTIVE", "COMPLETED", "NO_BATCH", True),
        ("COMPLETED", "COMPLETED", "NO_BATCH", True),
        ("COMPLETED", "PENDING", "NO_BATCH", False),
        ("COMPLETED", "RECONCILING", "NO_BATCH", False),
        ("COMPLETED", "COMPLETED", "READY", False),
        ("EXCEPTION_RETIRED", "COMPLETED", "NO_BATCH", True),
        ("ACTIVE", "RECONCILING", "NO_BATCH", False),
        ("ACTIVE", "COMPLETED", "READY", False),
    ],
)
async def test_determined_no_batch_releases_candidate_without_reopening_drain(status, delivery, result, allowed):
    from manual_picking.application.drain_execution_repository import DrainExecutionRepository
    from wes_plugin_sdk import (
        BinBatchNoBatch,
        BinReturnBatchOutcome,
        BinReturnBatchReady,
        BinReturnMove,
        TransportRackBinSlot,
    )

    row = SimpleNamespace(id=41)
    member = SimpleNamespace(return_id=41, sequence_no=1)
    confirmation = SimpleNamespace(id=71, status=delivery, operation_id="return-1")
    evidence = SimpleNamespace(apply_status="APPLIED", published_at=datetime(2026, 10, 11))
    reader = SimpleNamespace(
        read_return=AsyncMock(
            return_value=(
                None,
                BinReturnBatchOutcome(
                    BinBatchNoBatch(1000)
                    if result == "NO_BATCH"
                    else BinReturnBatchReady((BinReturnMove(1, "B1", TransportRackBinSlot("R1", "90", "S1")),))
                ),
            )
        )
    )
    repository = DrainExecutionRepository(reader)
    repository._members_for_confirmation = AsyncMock(return_value=(member,))
    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [(member, status, confirmation, evidence)]))
    )
    db.scalars = AsyncMock(return_value=SimpleNamespace(all=lambda: ()))
    assert await repository.eligible_return_prefix(db, workline_id=7, rows=(row,)) == ((row,) if allowed else ())


@pytest.mark.asyncio
async def test_same_batch_replay_preserves_original_members():
    from manual_picking.application.drain_execution_repository import DrainExecutionRepository

    members = [SimpleNamespace(execution_id=81, return_id=i, sequence_no=n) for n, i in enumerate((41, 42), 1)]
    db = SimpleNamespace(
        scalar=AsyncMock(side_effect=[SimpleNamespace(id=81, workline_id=7), SimpleNamespace(id=71, workline_id=7)]),
        scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: members)),
        add=Mock(),
        flush=AsyncMock(),
    )
    await DrainExecutionRepository().freeze_return_members_in_session(
        db, execution_id=81, return_batch_operation_id="return-1", return_ids=(41, 42)
    )
    db.add.assert_not_called()
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["partial", "sequence", "owner", "candidate", "execution"])
async def test_known_drain_membership_drift_rejects_plain_batch_fallback(drift):
    from manual_picking.application.drain_execution_repository import DrainExecutionRepository
    from wes_plugin_sdk import BinReturnCandidate, wms_operations

    from src.app.wms_adapter.outbound_picking.return_batch_typed import encode_request

    intent = wms_operations.outbound_bin_return_batch(
        operation_id="019f3406-2200-7b03-8b01-000000000003",
        workline_code="LINE-1",
        rack_id="R1",
        rack_face="90",
        return_candidates=(BinReturnCandidate(1, "B1", "OUTLET"), BinReturnCandidate(2, "B2", "OUTLET")),
    )
    members = [SimpleNamespace(execution_id=81, return_id=i, sequence_no=n) for n, i in enumerate((41, 42), 1)]
    if drift == "sequence":
        members[1].sequence_no = 3
    if drift == "execution":
        members[1].execution_id = 82
    records = [
        (members[0], 7, 7, "B1"),
        (members[1], 8 if drift == "owner" else 7, 7, "WRONG" if drift == "candidate" else "B2"),
    ]
    if drift == "partial":
        records.pop()
    from src.utils.canonical_json import canonical_json_digest

    payload = encode_request(intent, timestamp=1)
    db = SimpleNamespace(
        scalar=AsyncMock(
            return_value=SimpleNamespace(
                id=71,
                workline_id=7,
                operation="outbound.bin.return_batch@v1",
                operation_id=intent.operation_id,
                request_payload=payload,
                request_digest=canonical_json_digest(payload),
            )
        ),
        execute=AsyncMock(return_value=SimpleNamespace(all=lambda: records)),
    )
    with pytest.raises(ValueError, match="frozen drain return members"):
        await DrainExecutionRepository().execution_for_return_batch(
            db, workline_id=7, operation_id="019f3406-2200-7b03-8b01-000000000003"
        )


@pytest.mark.asyncio
async def test_original_return_ids_are_read_in_frozen_order_and_missing_id_rejected():
    from manual_picking.application.bin_line.return_repository import ReturnRepository

    first, second = SimpleNamespace(id=41), SimpleNamespace(id=42)
    db = SimpleNamespace(scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [second, first])))
    assert await ReturnRepository().by_ids_for_update(db, workline_id=7, return_ids=(41, 42)) == (first, second)
    db.scalars.return_value = SimpleNamespace(all=lambda: [second])
    with pytest.raises(ValueError, match="frozen return execution missing"):
        await ReturnRepository().by_ids_for_update(db, workline_id=7, return_ids=(41, 42))


@pytest.mark.asyncio
async def test_latest_return_query_requires_original_execution_confirmation_and_face():
    from manual_picking.application.drain_execution_repository import DrainExecutionRepository
    from sqlalchemy.dialects import postgresql

    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=list)))
    assert (
        await DrainExecutionRepository().latest_return_evidence_for_execution(
            db, execution_id=81, workline_id=7, rack_id="R1", rack_face="90"
        )
        is None
    )
    query = db.execute.await_args.args[0].compile(dialect=postgresql.dialect())
    assert set(query.params.values()) >= {81, 7, "outbound.bin.return_batch@v1", "COMPLETED"}
    assert "manual_picking_drain_return_members.execution_id" in str(query)
    assert " -> " not in str(query)


@pytest.mark.asyncio
async def test_no_batch_fixture_sets_digest_before_publication():
    from test_drain_execution_postgresql import seed_return_response

    from src.utils.canonical_json import canonical_json_digest

    stored = []

    async def flush():
        for row in stored:
            assert row.published_at is None or row.decision_digest is not None
            if row.id is None:
                row.id = 901

    db = SimpleNamespace(add=stored.append, flush=flush)
    confirmation = SimpleNamespace(operation="outbound.bin.return_batch@v1", operation_id="return-1")
    evidence = await seed_return_response(db, SimpleNamespace(id=7), confirmation, no_batch=True)
    assert evidence.published_at is not None
    assert evidence.decision_digest == canonical_json_digest(
        {"evidence_id": 901, "role": "WMS_BATCH", "result": "RETURN_NO_BATCH"}
    )


@pytest.mark.asyncio
async def test_latest_return_face_uses_typed_original_intent_within_execution():
    from manual_picking.application.drain_execution_repository import DrainExecutionRepository

    now = datetime(2026, 10, 11)
    newest = SimpleNamespace(id=72, operation_id="other-face", completed_at=now)
    original = SimpleNamespace(id=71, operation_id="original", completed_at=now)
    evidence = SimpleNamespace(operation_id="original")
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(
                all=lambda: [(SimpleNamespace(operation_id="other-face"), newest), (evidence, original)]
            )
        )
    )
    reader = SimpleNamespace(
        read_return_intent=Mock(
            side_effect=[
                SimpleNamespace(operation_id="other-face", rack_id="R1", rack_face="270"),
                SimpleNamespace(operation_id="original", rack_id="R1", rack_face="90"),
            ]
        )
    )
    repository = DrainExecutionRepository(reader)
    repository._members_for_confirmation = AsyncMock(return_value=(SimpleNamespace(execution_id=81),))
    assert await repository.latest_return_evidence_for_execution(
        db, execution_id=81, workline_id=7, rack_id="R1", rack_face="90"
    ) == (evidence, now)
    assert reader.read_return_intent.call_count == 2
    repository._members_for_confirmation.assert_awaited_once()
    assert repository._members_for_confirmation.await_args.kwargs["confirmation"] is original


@pytest.mark.asyncio
@pytest.mark.parametrize("apply_status,published", [("PENDING", True), ("RECONCILING", True), ("APPLIED", False)])
async def test_completed_execution_does_not_release_unapplied_or_unpublished_member(apply_status, published):
    from manual_picking.application.drain_execution_repository import DrainExecutionRepository

    row = SimpleNamespace(id=41)
    member = SimpleNamespace(return_id=41, sequence_no=1)
    confirmation = SimpleNamespace(id=71, status="COMPLETED", operation_id="return-1")
    evidence = SimpleNamespace(apply_status=apply_status, published_at=datetime(2026, 10, 11) if published else None)
    reader = SimpleNamespace(read_return=AsyncMock())
    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [(member, "COMPLETED", confirmation, evidence)]))
    )
    assert await DrainExecutionRepository(reader).eligible_return_prefix(db, workline_id=7, rows=(row,)) == ()
    reader.read_return.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("expected", [True, False])
async def test_retirement_actions_use_host_closed_result_judgment(expected):
    from manual_picking.application.drain_execution_repository import DrainExecutionRepository

    binding = SimpleNamespace(step="MANUAL_PICKING_RETURN_BATCH", resource_fence_id="batch")
    action = SimpleNamespace(transport_task_id="original", request_json={})
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [(binding, action)])))
    transports = SimpleNamespace(has_pending_definite_result_in_session=AsyncMock(return_value=expected))
    execution = SimpleNamespace(id=81, workline_id=7, decision_evidence_id=9)
    decision = SimpleNamespace(result=ReturnBufferDrainReady((RackFaceSequence("R1", ("90",)),)))
    assert await DrainExecutionRepository(transports=transports).retirement_actions(
        db, execution=execution, decision=decision
    ) == ((binding, action, expected),)
    transports.has_pending_definite_result_in_session.assert_awaited_once_with(db, action)


@pytest.mark.asyncio
async def test_retirement_task_query_constructs_against_real_model_and_issued_authority():
    from manual_picking.application.drain_execution_repository import DrainExecutionRepository
    from sqlalchemy.dialects import postgresql

    statements = []

    async def scalars(statement):
        statements.append(statement)
        return SimpleNamespace(all=list)

    assert await DrainExecutionRepository().retirement_tasks(SimpleNamespace(scalars=scalars), workline_id=7) == ()
    query = str(statements[0].compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "is_deleted" not in query
    assert "'QUEUED', 'PREPARING', 'EXECUTING'" in query
    assert "outbound.picking_task.issued@v1" in query
    assert "APPLIED" in query and "WMS_EVENT" in query
    assert "issued_evidence_id" in query and "workline_id = 7" in query


@pytest.mark.asyncio
async def test_ready_fixture_freezes_enough_slots_for_all_returned_faces():
    from test_drain_execution_postgresql import seed_ready

    from src.app.execution.models import WmsConfirmation
    from src.app.wms_integration.return_buffer_drain import ReturnBufferDrainResultReader

    stored = []

    async def flush():
        for index, row in enumerate(stored, 1):
            if row.id is None:
                row.id = 900 + index

    db = SimpleNamespace(add=stored.append, flush=flush)
    racks = (
        RackFaceSequence("R1", ("90", "270")),
        RackFaceSequence("R2", ("270", "90")),
        RackFaceSequence("R3", ("90",)),
    )
    record = await seed_ready(db, SimpleNamespace(id=7, line_code="LINE-1"), racks=racks)
    confirmation = next(row for row in stored if isinstance(row, WmsConfirmation))
    assert record.intent.required_slot_count == 5
    assert confirmation.request_payload["data"]["required_slot_count"] == 5
    evidence = stored[0]
    reader = ReturnBufferDrainResultReader(
        confirmations=SimpleNamespace(get_by_identity=AsyncMock(return_value=confirmation))
    )
    intent, outcome = await reader.read(db, evidence, workline_id=7)
    assert intent == record.intent
    assert outcome.result == record.result


@pytest.mark.asyncio
async def test_manual_withdrawal_preserves_identity_and_never_reopens():
    from manual_picking.application.drain_execution_repository import DrainExecutionRepository

    repository = DrainExecutionRepository()
    db = SimpleNamespace(flush=AsyncMock())
    execution = SimpleNamespace(status="ACTIVE", drain_operation_id="original", decision_evidence_id=91)
    now = datetime(2026, 10, 23)
    assert await repository.close_in_session(
        db,
        execution,
        status="MANUAL_WITHDRAWN",
        closed_at=now,
        reason_code="MANUAL_SCOPE_EXIT",
        blocker_client_request_id="original-accepted",
        trigger_picking_task_id=31,
    )
    assert (execution.status, execution.drain_operation_id, execution.decision_evidence_id) == (
        "MANUAL_WITHDRAWN",
        "original",
        91,
    )
    assert not await repository.close_in_session(db, execution, status="COMPLETED", closed_at=now)
    assert execution.reason_code == "MANUAL_SCOPE_EXIT"
