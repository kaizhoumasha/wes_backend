"""人工来源架到位独立于原搬运终态，并由持久扫描恢复批次。"""

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import wes_plugin_sdk as sdk
from manual_picking.application.manual_source_arrival import ManualSourceArrivalService
from manual_picking.application.manual_source_arrival_model import ManualPickingSourceArrival
from manual_picking.application.manual_source_arrival_repository import ManualSourceArrivalRepository
from sqlalchemy import select
from test_batch_repository import _new_sessions
from test_scan_flow import _setup, _wms
from test_source_progression import setup_driver

from src.app.execution.models import InboundEvidenceKind, TransportDecisionBinding
from src.app.execution.plugin_binding import BusinessEvidenceDisposition
from src.app.transport.models import TransportMember, TransportTask
from src.app.wms_integration.outbound_picking.models import PickingTask
from src.app.wms_integration.outbound_picking.models.plan_members import PickingTaskBinSourceRack
from src.app.workline.models import WorkLine
from src.utils.timezone import timezone


async def seeded():
    engine, sessions = await _new_sessions()
    async with engine.begin() as conn:
        await conn.run_sync(WorkLine.__table__.create)
    async with sessions.begin() as db:
        db.add(
            WorkLine(
                id=7,
                line_code="KT18",
                line_name="KT18",
                line_type="AUTO",
                is_active=True,
                plugin_key="manual-picking",
                position_bindings={"FIVE_RACK": {"location_id": "KT18"}},
            )
        )
        db.add(
            PickingTask(
                id=31,
                task_id="PICK-1",
                task_type="MANUAL",
                status="EXECUTING",
                workline_id=7,
                queue_revision=1,
                dispatch_sequence=1,
                issued_at_ms=1,
                issued_evidence_id=51,
            )
        )
        db.add(
            PickingTaskBinSourceRack(
                id=11, picking_task_id=31, rack_id="510022", rack_face="270", plan_revision=16, source_evidence_id=51
            )
        )
        db.add(
            TransportDecisionBinding(
                id=21,
                workline_id=7,
                picking_task_id=31,
                source_evidence_id=51,
                correlation_id="source-11",
                step="PICKING_TASK_BIN_SOURCE_RACK_IN",
                resource_fence_id="510022",
                client_request_id="client-1",
                causal_token=100,
                window_target_location_code="KT18",
            )
        )
        db.add(
            TransportTask(
                transport_task_id="transport-1",
                client_request_id="client-1",
                kind="RACK_MOVE",
                authority_workline_id=7,
                caller_json={},
                request_json={"rack_id": "510022", "target_face": "270"},
                request_digest="a" * 64,
                submit_operation_id="operation-1",
                submit_timestamp_ms=1,
                submit_request_body="{}",
                submit_request_body_digest=hashlib.sha256(b"{}").hexdigest(),
                status="ACCEPTED",
                created_at=timezone.now_for_db(),
                updated_at=timezone.now_for_db(),
            )
        )
        db.add(
            TransportMember(
                transport_task_id="transport-1",
                ordinal=0,
                object_type="RACK",
                object_id="510022",
                source_json={"kind": "RACK", "rack_id": "510022"},
                target_json={"kind": "RACK_POSITION", "location_code": "KT18"},
                status="ACCEPTED",
                updated_at=timezone.now_for_db(),
            )
        )
    return engine, sessions


def confirmation():
    return {
        "workline_id": 7,
        "picking_task_id": 31,
        "plan_revision": 16,
        "rack_id": "510022",
        "rack_face": "270",
        "location_code": "KT18",
        "transport_task_id": "transport-1",
        "expected_request_body_digest": hashlib.sha256(b"{}").hexdigest(),
        "confirmed_by": "现场负责人",
        "confirmation_reference": "用户确认510022/270在KT18作业位",
    }


@pytest.mark.asyncio
async def test_manual_arrival_audits_once_preserves_original_and_recovers_after_restart():
    engine, sessions = await seeded()
    audit = SimpleNamespace(create_audit_log=AsyncMock(return_value=SimpleNamespace(id=901)))
    service = ManualSourceArrivalService(sessions=sessions, audit=audit)
    try:
        assert await service.confirm(**confirmation()) == 901
        assert await service.confirm(**confirmation()) == 901
        assert audit.create_audit_log.await_count == 1
        async with sessions.begin() as db:
            original = await db.scalar(select(TransportTask))
            member = await db.scalar(select(TransportMember))
            assert (original.status, original.outcome_version, original.published_outcome_version) == ("ACCEPTED", 0, 0)
            assert (original.submit_operation_id, original.submit_request_body) == ("operation-1", "{}")
            assert member.status == "ACCEPTED"
            fact = await ManualSourceArrivalRepository().current(db, 7)
            assert (fact.rack_id, fact.rack_face, fact.location_code, fact.audit_id) == ("510022", "270", "KT18", 901)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["body", "rack", "face", "cancelled", "newer", "rejected"])
async def test_manual_arrival_fails_closed_on_identity_or_current_requirement_drift(change):
    engine, sessions = await seeded()
    audit = SimpleNamespace(create_audit_log=AsyncMock(return_value=SimpleNamespace(id=901)))
    args = confirmation()
    try:
        async with sessions.begin() as db:
            if change == "body":
                args["expected_request_body_digest"] = "0" * 64
            elif change == "rack":
                args["rack_id"] = "510024"
            elif change == "face":
                args["rack_face"] = "90"
            elif change == "cancelled":
                (await db.scalar(select(PickingTaskBinSourceRack))).cancelled_evidence_id = 99
            elif change == "rejected":
                (await db.scalar(select(TransportTask))).status = "REJECTED"
            else:
                db.add(
                    TransportDecisionBinding(
                        workline_id=7,
                        picking_task_id=31,
                        source_evidence_id=51,
                        correlation_id="later",
                        step="MANUAL_PICKING_SOURCE_RACK_ROTATE",
                        resource_fence_id="510022",
                        client_request_id="newer",
                        causal_token=101,
                    )
                )
                db.add(
                    TransportTask(
                        transport_task_id="newer",
                        client_request_id="newer",
                        kind="RACK_ROTATE",
                        authority_workline_id=7,
                        caller_json={},
                        request_json={},
                        request_digest="b" * 64,
                        submit_operation_id="newer",
                        submit_timestamp_ms=1,
                        submit_request_body="{}",
                        submit_request_body_digest="b" * 64,
                        status="ACCEPTED",
                        created_at=timezone.now_for_db(),
                        updated_at=timezone.now_for_db(),
                    )
                )
        with pytest.raises(ValueError):
            await ManualSourceArrivalService(sessions=sessions, audit=audit).confirm(**args)
        assert audit.create_audit_log.await_count == 0
        async with sessions.begin() as db:
            assert await db.scalar(select(ManualPickingSourceArrival)) is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_audit_failure_rolls_back_fact_and_confirmation_drift_conflicts():
    engine, sessions = await seeded()
    audit = SimpleNamespace(create_audit_log=AsyncMock(side_effect=RuntimeError("audit unavailable")))
    try:
        with pytest.raises(RuntimeError, match="audit unavailable"):
            await ManualSourceArrivalService(sessions=sessions, audit=audit).confirm(**confirmation())
        async with sessions.begin() as db:
            assert await db.scalar(select(ManualPickingSourceArrival)) is None
        audit.create_audit_log = AsyncMock(return_value=SimpleNamespace(id=901))
        service = ManualSourceArrivalService(sessions=sessions, audit=audit)
        await service.confirm(**confirmation())
        args = confirmation()
        args["confirmation_reference"] = "changed"
        with pytest.raises(ValueError, match="confirmation changed"):
            await service.confirm(**args)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_current_manual_working_face_drives_inbound_while_original_remains_accepted_and_other_rack_queues():
    driver, line, task, _positions, plans, flow, creator, *_ = setup_driver()
    plans.rows = [
        SimpleNamespace(
            id=11,
            rack_id="510022",
            rack_face="270",
            source_evidence_id=51,
            plan_revision=16,
            cancelled_evidence_id=None,
        ),
        SimpleNamespace(
            id=12,
            rack_id="510024",
            rack_face="270",
            source_evidence_id=52,
            plan_revision=17,
            cancelled_evidence_id=None,
        ),
    ]
    fact = ManualPickingSourceArrival(
        rack_id="510022",
        rack_face="270",
        location_code="FIVE-POS",
        picking_task_id=task.id,
        plan_revision=16,
        source_evidence_id=51,
        source_transport_task_id="transport-1",
        source_causal_token=100,
        arrival_face="270",
        updated_at=timezone.now_for_db(),
    )
    driver._batches.manual_source_arrival = AsyncMock(return_value=fact)
    driver._batches.working_rack_position = AsyncMock(return_value=fact)
    driver._transports.get_task = AsyncMock(return_value=SimpleNamespace(status="ACCEPTED"))
    flow.created = True
    assert await driver._advance_current_rack(object(), line, task) == 1
    assert [(call["rack_id"], call["rack_face"], call["plan_revision"]) for call in flow.calls] == [
        ("510022", "270", 16)
    ]
    assert creator.rotate == []


@pytest.mark.asyncio
async def test_new_rack_action_invalidates_manual_fact_without_deleting_audit_or_fact():
    from manual_picking.application.batch_repository import BatchRepository

    engine, sessions = await seeded()
    audit = SimpleNamespace(create_audit_log=AsyncMock(return_value=SimpleNamespace(id=901)))
    try:
        await ManualSourceArrivalService(sessions=sessions, audit=audit).confirm(**confirmation())
        async with sessions.begin() as db:
            fact = await BatchRepository().manual_source_arrival(db, workline_id=7)
            assert fact is not None
            assert (
                await BatchRepository().current_rack_arrival_at(db, workline_id=7, rack_id="510022") == fact.observed_at
            )
            db.add(
                TransportDecisionBinding(
                    workline_id=7,
                    picking_task_id=31,
                    source_evidence_id=51,
                    correlation_id="later",
                    step="MANUAL_PICKING_SOURCE_RACK_ROTATE",
                    resource_fence_id="510022",
                    client_request_id="newer",
                    causal_token=101,
                )
            )
            db.add(
                TransportTask(
                    transport_task_id="newer",
                    client_request_id="newer",
                    kind="RACK_ROTATE",
                    authority_workline_id=7,
                    caller_json={},
                    request_json={},
                    request_digest="b" * 64,
                    submit_operation_id="newer",
                    submit_timestamp_ms=1,
                    submit_request_body="{}",
                    submit_request_body_digest="b" * 64,
                    status="ACCEPTED",
                    created_at=timezone.now_for_db(),
                    updated_at=timezone.now_for_db(),
                )
            )
        async with sessions.begin() as db:
            assert await BatchRepository().manual_source_arrival(db, workline_id=7) is None
            assert (await ManualSourceArrivalRepository().current(db, 7)).audit_id == 901
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["owner", "face", "revision", "source", "cancelled", "newer"])
async def test_manual_fact_cannot_authorize_another_requirement_or_newer_motion(change):
    driver, line, task, _positions, plans, flow, _creator, *_ = setup_driver()
    fact = SimpleNamespace(
        picking_task_id=task.id,
        rack_id="R1",
        rack_face="90",
        arrival_face="90",
        location_code="FIVE-POS",
        plan_revision=1,
        source_evidence_id=51,
        source_transport_task_id="arrival-1",
        source_causal_token=100,
        updated_at=timezone.now_for_db(),
    )
    if change == "owner":
        fact.picking_task_id = 999
    elif change == "face":
        fact.rack_face = "0"
    elif change == "revision":
        fact.plan_revision = 2
    elif change == "source":
        fact.source_evidence_id = 99
    elif change == "cancelled":
        plans.rows[0].cancelled_evidence_id = 99
    else:
        driver._batches.has_newer_rack_action = AsyncMock(return_value=True)
    driver._batches.manual_source_arrival = AsyncMock(return_value=fact)
    driver._batches.working_rack_position = AsyncMock(return_value=fact)
    driver._transports.get_task = AsyncMock(return_value=SimpleNamespace(status="ACCEPTED"))
    flow.created = True
    assert await driver._advance_current_rack(object(), line, task) == 0
    assert flow.calls == []


@pytest.mark.asyncio
async def test_replay_retired_confirmation_never_reactivates_previous_working_rack():
    engine, sessions = await seeded()
    audit = SimpleNamespace(create_audit_log=AsyncMock(side_effect=[SimpleNamespace(id=901), SimpleNamespace(id=902)]))
    service = ManualSourceArrivalService(sessions=sessions, audit=audit)
    try:
        await service.confirm(**confirmation())
        async with sessions.begin() as db:
            db.add(
                PickingTaskBinSourceRack(
                    id=12,
                    picking_task_id=31,
                    rack_id="510024",
                    rack_face="270",
                    plan_revision=17,
                    source_evidence_id=52,
                )
            )
            db.add(
                TransportDecisionBinding(
                    id=22,
                    workline_id=7,
                    picking_task_id=31,
                    source_evidence_id=52,
                    correlation_id="second",
                    step="PICKING_TASK_BIN_SOURCE_RACK_IN",
                    resource_fence_id="510024",
                    client_request_id="client-2",
                    causal_token=101,
                    window_target_location_code="KT18",
                )
            )
            db.add(
                TransportTask(
                    transport_task_id="transport-2",
                    client_request_id="client-2",
                    kind="RACK_MOVE",
                    authority_workline_id=7,
                    caller_json={},
                    request_json={"rack_id": "510024", "target_face": "270"},
                    request_digest="a" * 64,
                    submit_operation_id="operation-2",
                    submit_timestamp_ms=1,
                    submit_request_body="{}",
                    submit_request_body_digest=hashlib.sha256(b"{}").hexdigest(),
                    status="ACCEPTED",
                    created_at=timezone.now_for_db(),
                    updated_at=timezone.now_for_db(),
                )
            )
            db.add(
                TransportMember(
                    transport_task_id="transport-2",
                    ordinal=0,
                    object_type="RACK",
                    object_id="510024",
                    source_json={"kind": "RACK", "location_code": "510024"},
                    target_json={"kind": "RACK_POSITION", "location_code": "KT18"},
                    status="ACCEPTED",
                    updated_at=timezone.now_for_db(),
                )
            )
        second = confirmation()
        second.update(
            plan_revision=17,
            rack_id="510024",
            transport_task_id="transport-2",
            confirmation_reference="后续现场明确确认510024取代510022到作业位",
        )
        assert await service.confirm(**second) == 902
        assert await service.confirm(**confirmation()) == 901
        drift = confirmation()
        drift["confirmation_reference"] = "changed"
        with pytest.raises(ValueError, match="confirmation changed"):
            await service.confirm(**drift)
        async with sessions.begin() as db:
            assert (await ManualSourceArrivalRepository().current(db, 7)).rack_id == "510024"
            assert len(list(await db.scalars(select(ManualPickingSourceArrival)))) == 2
        assert audit.create_audit_log.await_count == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", [None, "rack", "face", "revision", "source"])
@pytest.mark.parametrize("earlier_valid_source", [False, True])
async def test_manual_arrival_ready_response_creates_real_bin_transport_slice(mismatch, earlier_valid_source):
    import wes_plugin_sdk as sdk
    from manual_picking.application.batch_result import ManualPickingBatchResultFlow
    from test_scan_flow import _setup, _wms

    from src.app.execution.models import InboundEvidenceKind
    from src.app.execution.plugin_binding import BusinessEvidenceDisposition

    intent = sdk.wms_operations.outbound_bin_inbound_batch(
        operation_id="batch-manual", task_id="PICK-001", rack_id="RACK-1", rack_face="90"
    )
    ready = sdk.BinInboundBatchReady(
        (sdk.BinInboundBatchMember("A000000001", sdk.TransportRackBinSlot("RACK-1", "90", "S1")),)
    )
    reader = SimpleNamespace(read_inbound=AsyncMock(return_value=(intent, SimpleNamespace(result=ready))))
    transport = SimpleNamespace(create=AsyncMock())
    batches = SimpleNamespace(
        record_allocation=AsyncMock(), has_unclosed_equivalent_inbound=AsyncMock(return_value=False)
    )
    result = ManualPickingBatchResultFlow(reader, transport, object(), batches)
    flow, evidences, *_ = _setup(batch_reader=reader, batch_result=result, missing_projection=("RACK", "RACK-1"))
    source = (await flow._source_racks.list_bin_source_racks(object(), 31))[0]
    source.source_evidence_id = 51
    source.plan_revision = 13
    earlier = SimpleNamespace(**(vars(source) | {"plan_revision": 5, "source_evidence_id": 50}))
    flow._source_racks.list_bin_source_racks = AsyncMock(
        return_value=[earlier, source] if earlier_valid_source else [source]
    )
    fact = SimpleNamespace(
        picking_task_id=31,
        plan_revision=13,
        rack_id="RACK-1",
        rack_face="90",
        source_evidence_id=51,
        source_transport_task_id="arrival-1",
        location_code="FIVE-POSITION",
    )
    location = flow._worklines.line.position_bindings["FIVE_RACK"]["location_id"]
    fact.location_code = location
    if mismatch == "rack":
        fact.rack_id = "510024"
    elif mismatch == "face":
        fact.rack_face = "270"
    elif mismatch == "revision":
        fact.plan_revision = 2
    elif mismatch == "source":
        fact.source_evidence_id = 52
    flow._batch_progress.manual_source_arrival = AsyncMock(return_value=fact)
    flow._batch_progress.working_rack_position = AsyncMock(return_value=fact)
    original = SimpleNamespace(status="ACCEPTED", transport_task_id="arrival-1")
    flow._transport_reader = SimpleNamespace(get_task=AsyncMock(return_value=original))
    evidence = _wms(30, InboundEvidenceKind.WMS_RESULT, intent.operation_id, {})
    evidence.operation = "outbound.bin.inbound_batch@v1"
    evidences.rows[30] = evidence
    applied = await flow.apply_in_session(object(), 30, workline_id=7)
    if mismatch is None:
        assert applied.disposition is BusinessEvidenceDisposition.APPLIED
        transport.create.assert_awaited_once()
        moves = transport.create.await_args.kwargs["moves"]
        assert moves[0].bin_code == "A000000001" and moves[0].source.rack_id == "RACK-1"
    else:
        transport.create.assert_not_awaited()
    assert original.status == "ACCEPTED"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["parent", "member", "window", "location", "deactivated"])
async def test_recorded_confirmation_replays_after_current_business_basis_changes(change):
    engine, sessions = await seeded()
    audit = SimpleNamespace(create_audit_log=AsyncMock(return_value=SimpleNamespace(id=901)))
    service = ManualSourceArrivalService(sessions=sessions, audit=audit)
    try:
        await service.confirm(**confirmation())
        async with sessions.begin() as db:
            before = await ManualSourceArrivalRepository().current(db, 7)
            first_observed = before.observed_at
            if change == "parent":
                (await db.scalar(select(PickingTask))).status = "CANCELLED"
            elif change == "member":
                (await db.scalar(select(PickingTaskBinSourceRack))).cancelled_evidence_id = 99
            elif change == "window":
                (await db.scalar(select(TransportDecisionBinding))).window_released_at = timezone.now_for_db()
            elif change == "location":
                (await db.get(WorkLine, 7)).position_bindings = {"FIVE_RACK": {"location_id": "NEW"}}
            else:
                (await db.get(WorkLine, 7)).is_active = False
        assert await service.confirm(**confirmation()) == 901
        async with sessions.begin() as db:
            fact = await ManualSourceArrivalRepository().current(db, 7)
            assert fact.observed_at == first_observed
        assert audit.create_audit_log.await_count == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_persisted_manual_arrival_unblocks_inbound_dependency_but_preserves_other_rack_motion():
    from manual_picking.application.batch_repository import BatchRepository

    engine, sessions = await seeded()
    audit = SimpleNamespace(create_audit_log=AsyncMock(return_value=SimpleNamespace(id=901)))
    try:
        async with sessions.begin() as db:
            assert await BatchRepository().has_unclosed_action_for_face(db, 7, "PICK-1", 16, "510022", "270")
        await ManualSourceArrivalService(sessions=sessions, audit=audit).confirm(**confirmation())
        async with sessions.begin() as db:
            assert not await BatchRepository().has_unclosed_action_for_face(db, 7, "PICK-1", 16, "510022", "270")
            original = await db.scalar(select(TransportTask))
            assert original.status == "ACCEPTED"
            assert not await BatchRepository().has_current_rack_dependency(db, 7, "510022", original.created_at)
            assert await BatchRepository().has_unclosed_action_for_face(db, 7, "PICK-1", 16, "510022", "90")
            original.status = "SUCCEEDED"
            original.outcome_version = 1
            await db.flush()
            assert await BatchRepository().has_unclosed_action_for_face(db, 7, "PICK-1", 16, "510022", "270")
            original.published_outcome_version = 1
            await db.flush()
            assert not await BatchRepository().has_unclosed_action_for_face(db, 7, "PICK-1", 16, "510022", "270")
            db.add(
                TransportTask(
                    transport_task_id="independent",
                    client_request_id="independent",
                    kind="RACK_ROTATE",
                    authority_workline_id=7,
                    caller_json={},
                    request_json={"rack_id": "510022"},
                    request_digest="b" * 64,
                    submit_operation_id="independent",
                    submit_timestamp_ms=1,
                    submit_request_body="{}",
                    submit_request_body_digest="b" * 64,
                    status="ACCEPTED",
                    created_at=timezone.now_for_db(),
                    updated_at=timezone.now_for_db(),
                )
            )
        async with sessions.begin() as db:
            assert await BatchRepository().has_unclosed_action_for_face(db, 7, "PICK-1", 16, "510022", "270")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_current_working_fact_survives_member_cancel_and_masks_old_rack_after_departure():
    from manual_picking.application.batch_repository import BatchRepository

    from src.app.execution.models import PositionProjection

    engine, sessions = await seeded()
    audit = SimpleNamespace(create_audit_log=AsyncMock(return_value=SimpleNamespace(id=901)))
    try:
        await ManualSourceArrivalService(sessions=sessions, audit=audit).confirm(**confirmation())
        async with sessions.begin() as db:
            (await db.get(PickingTask, 31)).status = "EXECUTION_COMPLETED"
            (await db.scalar(select(PickingTaskBinSourceRack))).cancelled_evidence_id = 99
            (await db.scalar(select(TransportDecisionBinding))).window_released_at = timezone.now_for_db()
            db.add(
                PositionProjection(
                    object_type="RACK",
                    object_id="510004",
                    workline_id=7,
                    position_json={"kind": "RACK_POSITION", "location_code": "KT18"},
                    position_unknown=False,
                    arrival_face=None,
                    source_causal_token=50,
                    source_effect_phase="FINAL_RESULT",
                    source_transport_task_id="old-004",
                    source_operation_id="old-result",
                )
            )
        repo = BatchRepository()
        async with sessions.begin() as db:
            assert await repo.working_rack_position(db, workline_id=7, rack_id="510004", location_code="KT18") is None
            current = await repo.working_rack_position(db, workline_id=7, rack_id="510022", location_code="KT18")
            assert current.rack_id == "510022" and current.arrival_face == "270"
            assert not await repo.has_current_rack_dependency(
                db,
                7,
                "510022",
                (await db.scalar(select(TransportTask))).created_at,
                arrival_transport_task_id=current.source_transport_task_id,
            )
            windows = await repo.cancelled_source_windows(db, workline_id=7, target_location_code="KT18")
            assert len(windows) == 1 and windows[0][0].resource_fence_id == "510022"
            assert windows[0][1].status == "ACCEPTED"
            assert windows[0][2].arrival_face == "270"
            original = await db.scalar(select(TransportTask))
            assert original.status == "ACCEPTED" and original.outcome_version == 0
            db.add(
                TransportDecisionBinding(
                    workline_id=7,
                    picking_task_id=31,
                    source_evidence_id=99,
                    correlation_id="departure",
                    step="MANUAL_PICKING_SOURCE_RACK_OUT",
                    resource_fence_id="510022",
                    client_request_id="departure",
                    causal_token=101,
                )
            )
            db.add(
                TransportTask(
                    transport_task_id="departure",
                    client_request_id="departure",
                    kind="RACK_MOVE",
                    authority_workline_id=7,
                    caller_json={},
                    request_json={"rack_id": "510022"},
                    request_digest="b" * 64,
                    submit_operation_id="departure",
                    submit_timestamp_ms=1,
                    submit_request_body="{}",
                    submit_request_body_digest="b" * 64,
                    status="ACCEPTED",
                    created_at=timezone.now_for_db(),
                    updated_at=timezone.now_for_db(),
                )
            )
        async with sessions.begin() as db:
            assert await repo.working_rack_position(db, workline_id=7, rack_id="510022", location_code="KT18") is None
            assert await repo.working_rack_position(db, workline_id=7, rack_id="510004", location_code="KT18") is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("rack", ["RACK-1", "510004"])
async def test_return_ready_consumes_only_current_working_fact_with_original_accepted(rack):
    import wes_plugin_sdk as sdk
    from test_scan_flow import _setup, _wms

    from src.app.execution.models import InboundEvidenceKind
    from src.app.execution.plugin_binding import BusinessEvidenceDisposition

    intent = sdk.wms_operations.outbound_bin_return_batch(
        operation_id="return-current",
        workline_code="LINE-1",
        rack_id=rack,
        rack_face="90",
        return_candidates=(sdk.BinReturnCandidate(1, "A000002668", "OUTLET-POSITION"),),
    )
    reader = SimpleNamespace(read_return=AsyncMock(return_value=(intent, SimpleNamespace(result=None))))
    result = SimpleNamespace(apply_return_in_session=AsyncMock(return_value="RETURN_READY"))
    flow, evidences, *_ = _setup(batch_reader=reader, batch_result=result, missing_projection=("RACK", rack))
    fact = ManualPickingSourceArrival(
        workline_id=7,
        picking_task_id=31,
        plan_revision=1,
        rack_id="RACK-1",
        rack_face="90",
        location_code="FIVE-POSITION",
        source_evidence_id=51,
        source_causal_token=100,
        source_transport_task_id="arrival-1",
        observed_at=timezone.now_for_db(),
        audit_id=901,
        confirmation_digest="a" * 64,
    )
    fact.location_code = flow._worklines.line.position_bindings["FIVE_RACK"]["location_id"]
    flow._batch_progress.manual_source_arrival = AsyncMock(return_value=fact)
    flow._batch_progress.working_rack_position = AsyncMock(return_value=fact if rack == fact.rack_id else None)
    original = SimpleNamespace(
        status="ACCEPTED",
        transport_task_id="arrival-1",
        outcome_version=0,
        published_outcome_version=0,
        created_at=timezone.now_for_db(),
    )
    flow._transport_reader.get_task = AsyncMock(return_value=original)
    flow._batch_progress.has_current_rack_dependency = AsyncMock(return_value=False)
    evidence = _wms(31, InboundEvidenceKind.WMS_RESULT, intent.operation_id, {})
    evidence.operation = "outbound.bin.return_batch@v1"
    evidences.rows[31] = evidence
    applied = await flow.apply_in_session(object(), 31, workline_id=7)
    if rack == fact.rack_id:
        assert applied.disposition is BusinessEvidenceDisposition.APPLIED
        result.apply_return_in_session.assert_awaited_once()
    else:
        result.apply_return_in_session.assert_not_awaited()
    assert original.status == "ACCEPTED" and original.outcome_version == 0


@pytest.mark.asyncio
async def test_cancelled_working_rack_requests_returns_then_its_own_clearance_preserving_original():
    import wes_plugin_sdk as sdk
    from manual_picking.application.batch_repository import BatchRepository

    from src.app.execution.models import WmsConfirmationStatus

    engine, sessions = await seeded()
    audit = SimpleNamespace(create_audit_log=AsyncMock(return_value=SimpleNamespace(id=901)))
    try:
        await ManualSourceArrivalService(sessions=sessions, audit=audit).confirm(**confirmation())
        async with sessions.begin() as db:
            (await db.get(PickingTask, 31)).status = "EXECUTION_COMPLETED"
            (await db.scalar(select(PickingTaskBinSourceRack))).cancelled_evidence_id = 99
            (await db.scalar(select(TransportDecisionBinding))).window_released_at = timezone.now_for_db()
        driver, line, *_ = setup_driver()
        line.position_bindings["FIVE_RACK"]["location_id"] = "KT18"
        driver._batches = BatchRepository()

        async def read_transport(db, identity):
            return await db.scalar(select(TransportTask).where(TransportTask.transport_task_id == identity))

        driver._transports.get_task = read_transport
        driver._flow.return_before_motion = AsyncMock(return_value=1)
        async with sessions.begin() as db:
            assert await driver._advance_cancelled_source_windows(db, line) == 1
            assert driver._flow.return_before_motion.await_args.kwargs["rack_id"] == "510022"
            driver._departure_scheduler.create_in_session.assert_not_awaited()
        driver._flow.return_before_motion.return_value = None
        async with sessions.begin() as db:
            assert await driver._advance_cancelled_source_windows(db, line) == 1
            intent = driver._departure_scheduler.create_in_session.await_args.args[1]
            assert intent.rack_id == "510022" and intent.current_face == "270"
        driver._departure_reader.latest_for_workline.return_value = SimpleNamespace(
            intent=intent,
            evidence_id=98,
            status=WmsConfirmationStatus.COMPLETED,
            outcome=sdk.RackDepartureOutcome(sdk.RackDepartureReady(sdk.TransportZonePosition("WH01"))),
        )
        async with sessions.begin() as db:
            assert await driver._advance_cancelled_source_windows(db, line) == 1
            assert driver._rack_creator.depart[0]["rack_id"] == "510022"
            original = await db.scalar(select(TransportTask))
            assert (original.status, original.outcome_version, original.published_outcome_version) == ("ACCEPTED", 0, 0)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phase",
    [
        "ACK",
        "unpublished",
        "published",
        "older_action",
        "ambiguous",
        "old_clearance",
        "same_rack_leave",
        "same_rack_rotate",
        "native_leave",
    ],
)
async def test_native_workpoint_takeover_needs_unique_new_action_and_published_arrival(phase):
    from datetime import timedelta

    from manual_picking.application.batch_repository import BatchRepository

    from src.app.execution.models import PositionProjection

    engine, sessions = await seeded()
    audit = SimpleNamespace(create_audit_log=AsyncMock(return_value=SimpleNamespace(id=901)))
    try:
        await ManualSourceArrivalService(sessions=sessions, audit=audit).confirm(**confirmation())
        async with sessions.begin() as db:
            fact = await ManualSourceArrivalRepository().current(db, 7)
            if phase in {"same_rack_leave", "same_rack_rotate"}:
                original_binding = await db.get(TransportDecisionBinding, 21)
                original_binding.window_released_at = timezone.now_for_db()
                await db.flush()
            for index, rack in enumerate(
                ["510034", "510039"]
                if phase == "ambiguous"
                else ["510004"]
                if phase == "old_clearance"
                else ["510022"]
                if phase in {"same_rack_leave", "same_rack_rotate"}
                else ["510034"],
                start=101,
            ):
                db.add(
                    TransportDecisionBinding(
                        workline_id=7,
                        picking_task_id=31 if phase in {"same_rack_leave", "same_rack_rotate"} else None,
                        source_evidence_id=91,
                        correlation_id=rack,
                        step="MANUAL_PICKING_SOURCE_RACK_OUT"
                        if phase in {"old_clearance", "same_rack_leave"}
                        else "MANUAL_PICKING_SOURCE_RACK_ROTATE"
                        if phase == "same_rack_rotate"
                        else "MANUAL_PICKING_RETURN_BUFFER_DRAIN_RACK_OUT"
                        if phase == "native_leave"
                        else "MANUAL_PICKING_RETURN_BUFFER_DRAIN_RACK_IN",
                        resource_fence_id=rack,
                        client_request_id=rack,
                        causal_token=index,
                        window_target_location_code="KT18",
                    )
                )
                db.add(
                    TransportTask(
                        transport_task_id=rack,
                        client_request_id=rack,
                        kind="RACK_MOVE",
                        authority_workline_id=7,
                        caller_json={},
                        request_json={"rack_id": rack},
                        request_digest="b" * 64,
                        submit_operation_id=rack,
                        submit_timestamp_ms=1,
                        submit_request_body="{}",
                        submit_request_body_digest="b" * 64,
                        status="ACCEPTED"
                        if phase == "ACK"
                        else "FAILED"
                        if phase in {"same_rack_leave", "native_leave"}
                        else "SUCCEEDED",
                        outcome_version=0 if phase == "ACK" else 1,
                        published_outcome_version=0 if phase in {"ACK", "unpublished"} else 1,
                        created_at=fact.observed_at - timedelta(seconds=1)
                        if phase == "older_action"
                        else timezone.now_for_db(),
                        updated_at=timezone.now_for_db(),
                    )
                )
                db.add(
                    PositionProjection(
                        object_type="RACK",
                        object_id=rack,
                        workline_id=7,
                        position_json={"kind": "RACK_POSITION", "location_code": "KT18"},
                        position_unknown=phase == "ACK",
                        arrival_face="270",
                        source_causal_token=index,
                        source_effect_phase="ACK_INVALIDATION" if phase == "ACK" else "FINAL_RESULT",
                        source_transport_task_id=rack,
                        source_operation_id=rack,
                    )
                )
        if phase == "native_leave":
            async with sessions.begin() as db:
                db.add(
                    TransportDecisionBinding(
                        workline_id=7,
                        source_evidence_id=92,
                        correlation_id="native-arrival",
                        step="MANUAL_PICKING_RETURN_BUFFER_DRAIN_RACK_IN",
                        resource_fence_id="510034",
                        client_request_id="native-arrival",
                        causal_token=1000,
                    )
                )
                current = await db.scalar(
                    select(TransportDecisionBinding).where(TransportDecisionBinding.client_request_id == "510034")
                )
                current.causal_token = 1001
                position = await db.scalar(select(PositionProjection).where(PositionProjection.object_id == "510034"))
                position.source_causal_token = 1001
                db.add(
                    TransportTask(
                        transport_task_id="native-arrival",
                        client_request_id="native-arrival",
                        kind="RACK_MOVE",
                        authority_workline_id=7,
                        caller_json={},
                        request_json={"rack_id": "510034"},
                        request_digest="c" * 64,
                        submit_operation_id="native-arrival",
                        submit_timestamp_ms=1,
                        submit_request_body="{}",
                        submit_request_body_digest="c" * 64,
                        status="SUCCEEDED",
                        outcome_version=1,
                        published_outcome_version=1,
                        created_at=timezone.now_for_db(),
                        updated_at=timezone.now_for_db(),
                    )
                )
                db.add(
                    TransportMember(
                        transport_task_id="native-arrival",
                        ordinal=0,
                        object_type="RACK",
                        object_id="510034",
                        source_json={},
                        target_json={"kind": "RACK_POSITION", "location_code": "KT18"},
                        status="SUCCEEDED",
                        position_unknown=False,
                        final_position_json={"kind": "RACK_POSITION", "location_code": "KT18"},
                        updated_at=timezone.now_for_db(),
                    )
                )
        repo = BatchRepository()
        async with sessions.begin() as db:
            manual = await repo.working_rack_position(db, workline_id=7, rack_id="510022", location_code="KT18")
            native = await repo.working_rack_position(db, workline_id=7, rack_id="510034", location_code="KT18")
            if phase in {"published", "native_leave"}:
                assert manual is None and native.object_id == "510034"
            elif phase in {"same_rack_leave", "same_rack_rotate"}:
                assert manual.object_id == "510022" and native is None
                original = await db.scalar(
                    select(TransportTask).where(TransportTask.transport_task_id == "transport-1")
                )
                assert not await repo.has_current_rack_dependency(db, 7, "510022", original.created_at)
                (await db.get(PickingTask, 31)).status = "EXECUTION_COMPLETED"
                (await db.scalar(select(PickingTaskBinSourceRack))).cancelled_evidence_id = 99
                await db.flush()
                windows = await repo.cancelled_source_windows(db, workline_id=7, target_location_code="KT18")
                assert len(windows) == 1 and windows[0][0].resource_fence_id == "510022"
                original.outcome_version = 1
                await db.flush()
                assert await repo.has_current_rack_dependency(db, 7, "510022", original.created_at)
            elif phase in {"unpublished", "ambiguous"}:
                assert manual is None and native is None
            else:
                assert manual.rack_id == "510022" and native is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("stale", ["task", "location"])
async def test_stale_manual_fact_does_not_block_current_real_arrival_ready(stale):
    intent = sdk.wms_operations.outbound_bin_inbound_batch(
        operation_id="batch-current", task_id="PICK-001", rack_id="RACK-1", rack_face="90"
    )
    reader = SimpleNamespace(read_inbound=AsyncMock(return_value=(intent, object())))
    result = SimpleNamespace(apply_inbound_in_session=AsyncMock(return_value="INBOUND_READY"))
    flow, evidences, *_ = _setup(batch_reader=reader, batch_result=result)
    fact = SimpleNamespace(
        picking_task_id=999 if stale == "task" else 31,
        location_code="OLD-WORKPOINT"
        if stale == "location"
        else flow._worklines.line.position_bindings["FIVE_RACK"]["location_id"],
    )
    flow._batch_progress.manual_source_arrival = AsyncMock(return_value=fact)
    evidence = _wms(30, InboundEvidenceKind.WMS_RESULT, intent.operation_id, {})
    evidence.operation = "outbound.bin.inbound_batch@v1"
    evidences.rows[30] = evidence
    assert (await flow.apply_in_session(object(), 30, workline_id=7)).disposition is BusinessEvidenceDisposition.APPLIED
    result.apply_inbound_in_session.assert_awaited_once()
