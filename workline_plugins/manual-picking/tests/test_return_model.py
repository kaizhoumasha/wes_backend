"""回程 execution 的持久约束与 FIFO。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
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
            SimpleNamespace(has_conflicting_return_target=AsyncMock(return_value=False)),
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
