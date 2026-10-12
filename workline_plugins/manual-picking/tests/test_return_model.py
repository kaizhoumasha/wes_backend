"""回程 execution 的持久约束与 FIFO。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from drain_execution_support import unbound_drain_executions
from manual_picking.application.batch_result import ManualPickingBatchResultFlow
from manual_picking.application.bin_line.return_model import BinLineReturn
from manual_picking.application.bin_line.return_repository import ReturnRepository
from manual_picking.application.scan_flow import ManualPickingScanFlow
from sqlalchemy import delete, event, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from wes_plugin_sdk import (
    BinReturnBatchOutcome,
    BinReturnBatchReady,
    BinReturnCandidate,
    BinReturnMove,
    TransportRackBinSlot,
    wms_operations,
)

from src.app.execution.models import InboundEvidence
from src.app.workline.models import WorkLine


@pytest.mark.asyncio
async def test_return_fifo_isolates_unready_bin_and_rejects_second_open_bin() -> None:
    _ = (InboundEvidence, WorkLine)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def attach_schema(connection, _record):  # type: ignore[no-untyped-def]
        connection.execute("ATTACH DATABASE ':memory:' AS wes_biz")

    table = BinLineReturn.__table__
    try:
        async with engine.begin() as db:
            await db.run_sync(table.create)
            for evidence_id, code, event_time, state in (
                (2, "BOX-A", 1000, "MOVE_PENDING"),
                (1, "BOX-B", 2000, "READY"),
            ):
                await db.execute(
                    insert(table).values(
                        workline_id=7,
                        bin_code=code,
                        scan3_evidence_id=evidence_id,
                        scan4_evidence_id=evidence_id + 10,
                        scan4_event_time=event_time,
                        return_state=state,
                    )
                )
            await db.execute(insert(table).values(workline_id=7, bin_code="BOX-C", scan3_evidence_id=4))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions.begin() as db:
            rows = await ReturnRepository().ready_prefix_for_update(db, 7, limit=1)
            assert [row.bin_code for row in rows] == ["BOX-B"]
            pending = await ReturnRepository().current_bin_for_update(db, 7, "BOX-A")
            assert pending is not None and pending.return_state == "MOVE_PENDING"
            assert await ReturnRepository().count_current(db, 7) == 3
            await db.execute(update(table).where(table.c.bin_code == "BOX-A").values(return_state="READY"))
            rows = await ReturnRepository().ready_prefix_for_update(db, 7)
            assert [row.bin_code for row in rows] == ["BOX-A", "BOX-B"]
            await db.execute(update(table).where(table.c.bin_code == "BOX-A").values(return_state="RETURN_REQUESTED"))
            db.expire_all()
            rows = await ReturnRepository().ready_prefix_for_update(db, 7, limit=1)
            assert [row.bin_code for row in rows] == ["BOX-B"]
            assert [row.bin_code for row in await ReturnRepository().requested_for_update(db, 7)] == ["BOX-A"]
            assert await ReturnRepository().count_current(db, 7) == 3
        with pytest.raises(IntegrityError):
            async with sessions.begin() as db:
                await db.execute(insert(table).values(workline_id=7, bin_code="BOX-A", scan3_evidence_id=3))
        async with sessions.begin() as db:
            await db.execute(update(table).where(table.c.bin_code == "BOX-A").values(return_state="EXITED"))
            await db.execute(insert(table).values(workline_id=7, bin_code="BOX-A", scan3_evidence_id=3))
            await db.execute(delete(table).where(table.c.return_state == "EXITED"))
            assert await ReturnRepository().count_current(db, 7) == 3
            assert [row.bin_code for row in await ReturnRepository().ready_prefix_for_update(db, 7)] == ["BOX-B"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_return_batch_rows_share_evidence_and_rollback_together() -> None:
    _ = (InboundEvidence, WorkLine)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def attach_schema(connection, _record):  # type: ignore[no-untyped-def]
        connection.execute("ATTACH DATABASE ':memory:' AS wes_biz")

    table = BinLineReturn.__table__
    try:
        async with engine.begin() as db:
            await db.run_sync(table.create)
            for evidence_id, code in ((1, "BOX-A"), (2, "BOX-B")):
                await db.execute(
                    insert(table).values(
                        workline_id=7,
                        bin_code=code,
                        scan3_evidence_id=evidence_id,
                        scan4_evidence_id=evidence_id + 10,
                        scan4_event_time=evidence_id,
                        return_state="READY",
                    )
                )

        intent = wms_operations.outbound_bin_return_batch(
            operation_id="batch-1",
            workline_code="LINE-1",
            rack_id="R1",
            rack_face="90",
            return_candidates=(
                BinReturnCandidate(1, "BOX-A", "OUT"),
                BinReturnCandidate(2, "BOX-B", "OUT"),
            ),
        )
        outcome = BinReturnBatchOutcome(
            BinReturnBatchReady(
                (
                    BinReturnMove(1, "BOX-A", TransportRackBinSlot("R1", "90", "S1")),
                    BinReturnMove(2, "BOX-B", TransportRackBinSlot("R1", "90", "S2")),
                )
            )
        )
        reader = SimpleNamespace(read_return=AsyncMock(return_value=(intent, outcome)))
        transport = SimpleNamespace(create=AsyncMock())
        flow = ManualPickingBatchResultFlow(
            reader,
            transport,
            ReturnRepository(),
            SimpleNamespace(
                has_unclosed_return_transport_for_face=AsyncMock(return_value=False),
                has_conflicting_return_target=AsyncMock(return_value=False),
            ),
            drain_executions=unbound_drain_executions(),
        )
        sessions = async_sessionmaker(engine, expire_on_commit=False)

        with pytest.raises(RuntimeError, match="abort"):
            async with sessions.begin() as db:
                assert (
                    await flow.apply_return_in_session(
                        db,
                        SimpleNamespace(id=31, operation_id="batch-1"),
                        workline_id=7,
                        workline_code="LINE-1",
                        confirmed_rack_id="R1",
                        confirmed_face="90",
                        return_location="OUT",
                    )
                    == "RETURN_READY"
                )
                rows = (await db.scalars(select(BinLineReturn).order_by(BinLineReturn.scan4_event_time))).all()
                assert [(row.return_state, row.return_batch_evidence_id) for row in rows] == [
                    ("RETURN_REQUESTED", 31),
                    ("RETURN_REQUESTED", 31),
                ]
                raise RuntimeError("abort")

        async with sessions.begin() as db:
            rows = (await db.scalars(select(BinLineReturn).order_by(BinLineReturn.scan4_event_time))).all()
            assert [(row.return_state, row.return_batch_evidence_id) for row in rows] == [
                ("READY", None),
                ("READY", None),
            ]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_scan3_recovery_replaces_current_return_atomically() -> None:
    _ = (InboundEvidence, WorkLine)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def attach_schema(connection, _record):  # type: ignore[no-untyped-def]
        connection.execute("ATTACH DATABASE ':memory:' AS wes_biz")

    table = BinLineReturn.__table__
    try:
        async with engine.begin() as db:
            await db.run_sync(table.create)
            await db.execute(
                insert(table).values(
                    workline_id=7,
                    bin_code="A000002456",
                    scan3_evidence_id=1,
                    scan4_evidence_id=2,
                    scan4_event_time=2000,
                    scan3_command_code="OLD-S3",
                    scan4_command_code="OLD-S4",
                    return_state="MOVE_PENDING",
                )
            )
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        flow = ManualPickingScanFlow(
            commands=object(),
            admissions=object(),
            returns=ReturnRepository(),
            evidences=SimpleNamespace(
                get_by_id_for_update=AsyncMock(return_value=SimpleNamespace(normalized_payload={"timestamp": 1000}))
            ),
            command_reader=SimpleNamespace(
                get_by_command_code=AsyncMock(
                    return_value=SimpleNamespace(status="RECONCILING", execution_ref_id="old")
                )
            ),
            drain_executions=unbound_drain_executions(),
        )
        flow._move = AsyncMock(return_value="NEW-S3")
        evidence = SimpleNamespace(id=3, normalized_payload={"timestamp": 3000})
        # 事务失败不能留下 VOIDED 旧轮次或孤立的新轮次。
        with pytest.raises(RuntimeError, match="rollback"):
            async with sessions.begin() as db:
                assert await flow._apply_scan3(db, evidence, 7, {}, "A000002456-B") == "MOVE_FORWARD"
                raise RuntimeError("rollback")
        async with sessions.begin() as db:
            current = await ReturnRepository().current_bin_for_update(db, 7, "A000002456")
            assert current.return_state == "MOVE_PENDING"
            assert await flow._apply_scan3(db, evidence, 7, {}, "A000002456-B") == "MOVE_FORWARD"
        async with sessions.begin() as db:
            rows = (await db.scalars(select(BinLineReturn).order_by(table.c.scan3_evidence_id))).all()
            assert [(r.scan3_evidence_id, r.return_state) for r in rows] == [(1, "VOIDED"), (3, "NONE")]
            assert rows[0].scan4_evidence_id == 2 and rows[0].scan4_command_code == "OLD-S4"
            assert rows[1].scan3_command_code == "NEW-S3" and rows[1].scan4_evidence_id is None
            assert await ReturnRepository().by_command_code_for_update(db, "OLD-S4") is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,result",
    [
        ("ACTIVE", "READY"),
        ("EXCEPTION_RETIRED", "READY"),
        ("COMPLETED", "READY"),
        ("COMPLETED", "NO_BATCH"),
        ("EXCEPTION_RETIRED", "NO_BATCH"),
    ],
)
async def test_drain_unallocated_candidates_start_independent_request_after_completion(status, result) -> None:
    from manual_picking.application.drain_execution_model import (
        ManualPickingDrainExecution,
        ManualPickingDrainReturnMember,
    )
    from manual_picking.application.drain_execution_repository import DrainExecutionRepository
    from manual_picking.application.drain_flow import ManualPickingDrainFlow
    from test_drain_execution_postgresql import seed_ready

    from src.app.execution.models import WmsConfirmation
    from src.app.execution.repositories.wms_confirmation_repository import WmsConfirmationRepository
    from src.app.execution.services.wms_confirmation_service import WmsConfirmationLifecycleService
    from src.app.wms_integration.outbound_picking.services.bin_batch import BinBatchResultReader, BinBatchScheduler
    from src.app.wms_integration.outbound_picking.services.return_batch_owner import ReturnBatchOwnerService
    from src.utils.canonical_json import canonical_json_digest
    from src.utils.timezone import timezone

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def attach_schema(connection, _record):
        connection.execute("ATTACH DATABASE ':memory:' AS wes_biz")

    try:
        async with engine.begin() as db:
            for model in (
                InboundEvidence,
                WmsConfirmation,
                BinLineReturn,
                ManualPickingDrainExecution,
                ManualPickingDrainReturnMember,
            ):
                await db.run_sync(model.__table__.create)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        line = SimpleNamespace(id=7, line_code="LINE-1", is_active=True)
        confirmations = WmsConfirmationRepository()
        # SQLite covers response/application/selection, not PostgreSQL advisory locking.
        confirmations.lock_identity = AsyncMock()
        scheduler = BinBatchScheduler(
            WmsConfirmationLifecycleService(
                repository=confirmations,
                workline_owner=ReturnBatchOwnerService(
                    SimpleNamespace(get_for_authority_update=AsyncMock(return_value=line))
                ),
            )
        )
        reader, repository, returns = BinBatchResultReader(), DrainExecutionRepository(), ReturnRepository()
        flow = ManualPickingDrainFlow(
            object(), returns, object(), object(), scheduler, reader, drain_executions=repository
        )
        batch_result = ManualPickingBatchResultFlow(
            reader,
            SimpleNamespace(create=AsyncMock()),
            returns,
            SimpleNamespace(
                has_unclosed_return_transport_for_face=AsyncMock(return_value=False),
                has_conflicting_return_target=AsyncMock(return_value=False),
            ),
            drain_executions=repository,
        )
        async with sessions.begin() as db:
            decision = await seed_ready(db, line)
            execution = await repository.ensure_ready_in_session(db, decision=decision)
            originals = []
            for index, code in enumerate(("A", "B", "C", "D"), 1):
                row = BinLineReturn(
                    workline_id=7,
                    bin_code=code,
                    scan3_evidence_id=100 + index,
                    scan4_evidence_id=200 + index,
                    scan4_event_time=index,
                    return_state="READY",
                )
                db.add(row)
                originals.append(row)
            await db.flush()
            assert await flow.return_in_session(db, line, decision, "R1", "OUT", timezone.now_for_db())
            original = await db.scalar(
                select(WmsConfirmation).where(WmsConfirmation.operation == "outbound.bin.return_batch@v1")
            )
            response = {
                "operation_id": original.operation_id,
                "code": "DECIDED",
                "timestamp": 2,
                "data": {
                    "result": "READY",
                    "moves": [
                        {
                            "sequence_no": i,
                            "bin_code": code,
                            "target": {"type": "RACK_BIN_SLOT", "rack_id": "R1", "rack_face": "90", "slot_id": f"S{i}"},
                        }
                        for i, code in enumerate(("A", "B"), 1)
                    ],
                },
            }
            if result == "NO_BATCH":
                response["data"] = {"result": "NO_BATCH", "retry_after_ms": 1000}
            now = timezone.now_for_db()
            evidence = InboundEvidence(
                kind="WMS_RESULT",
                source_identity=f"{original.operation}:{original.operation_id}",
                operation=original.operation,
                operation_id=original.operation_id,
                workline_id=7,
                normalized_payload=response,
                payload_digest=canonical_json_digest(response),
                received_at=now,
                processed_at=now,
                apply_status="APPLIED",
            )
            db.add(evidence)
            await db.flush()
            original.status, original.response_result = "COMPLETED", result
            original.response_evidence_id, original.completed_at = evidence.id, now
            await db.flush()
            assert (
                await batch_result.apply_return_in_session(
                    db,
                    evidence,
                    workline_id=7,
                    workline_code="LINE-1",
                    confirmed_rack_id="R1",
                    confirmed_face="90",
                    return_location="OUT",
                )
                == f"RETURN_{result}"
            )
            if result == "READY":
                assert [row.return_state for row in originals] == [
                    "RETURN_REQUESTED",
                    "RETURN_REQUESTED",
                    "READY",
                    "READY",
                ]
            else:
                assert [row.return_state for row in originals] == ["READY"] * 4
            unallocated = tuple(originals[2:] if result == "READY" else originals)
            ready = tuple(await returns.ready_prefix_for_update(db, 7))
            # Receipt or unconsumed response must not release the original tail.
            assert await repository.eligible_return_prefix(db, workline_id=7, rows=ready) == ()
            evidence.decision_digest = canonical_json_digest(
                {"evidence_id": evidence.id, "role": "WMS_BATCH", "result": f"RETURN_{result}"}
            )
            evidence.published_at = now
            await db.flush()
            assert await repository.eligible_return_prefix(db, workline_id=7, rows=ready) == unallocated
            if result == "READY":
                # Assigned rows remain fenced even if their state was temporarily still READY.
                assert await repository.eligible_return_prefix(db, workline_id=7, rows=tuple(originals)) == ()
            else:
                assert await flow.active_rack_face(db, line, decision, "R1") == ("R1", "90", True)
            if status == "EXCEPTION_RETIRED":
                await repository.close_in_session(
                    db,
                    execution,
                    status="EXCEPTION_RETIRED",
                    closed_at=now,
                    reason_code="DRAIN_RESULT_UNKNOWN",
                    blocker_client_request_id="original-blocker",
                    trigger_picking_task_id=1,
                )
                assert await repository.eligible_return_prefix(db, workline_id=7, rows=ready) == ()
                assert not await flow.return_in_session(db, line, decision, "R1", "OUT", now)
                return
            # Matching authoritative exits close the assigned prefix before the next request.
            for row in originals[:2] if result == "READY" else ():
                row.return_state = "EXITED"
            await db.flush()
            if status == "COMPLETED":
                await repository.close_in_session(db, execution, status="COMPLETED", closed_at=now)
                assert execution.status == "COMPLETED"
                assert await repository.eligible_return_prefix(db, workline_id=7, rows=ready) == unallocated
                if result == "READY":
                    assert await repository.eligible_return_prefix(db, workline_id=7, rows=tuple(originals)) == ()
                evidence.published_at = None
                await db.flush()
                assert await repository.eligible_return_prefix(db, workline_id=7, rows=ready) == ()
                evidence.published_at = now
                await db.flush()
                assert not await flow.return_in_session(db, line, decision, "R1", "OUT", now)
                decision = await seed_ready(db, line)
                next_execution = await repository.ensure_ready_in_session(db, decision=decision)
                assert next_execution.id != execution.id
                assert execution.status == "COMPLETED"
            else:
                next_execution = execution
            assert await flow.return_in_session(db, line, decision, "R1", "OUT", now)
            obligations = (
                await db.scalars(
                    select(WmsConfirmation)
                    .where(WmsConfirmation.operation == original.operation)
                    .order_by(WmsConfirmation.id)
                )
            ).all()
            assert len(obligations) == 2
            assert [c["bin_code"] for c in obligations[1].request_payload["data"]["return_candidates"]] == [
                r.bin_code for r in unallocated
            ]
            assert [
                m.return_id
                for m in await repository.members_for_return_batch(
                    db, workline_id=7, operation_id=original.operation_id
                )
            ] == [r.id for r in originals]
            assert [
                m.return_id
                for m in await repository.members_for_return_batch(
                    db, workline_id=7, operation_id=obligations[1].operation_id
                )
            ] == [r.id for r in unallocated]
            next_members = await repository.members_for_return_batch(
                db, workline_id=7, operation_id=obligations[1].operation_id
            )
            assert {m.execution_id for m in next_members} == {next_execution.id}
            assert await repository.eligible_return_prefix(db, workline_id=7, rows=unallocated) == ()
            assert execution.status == status
            assert original.operation_id != obligations[1].operation_id
    finally:
        await engine.dispose()
