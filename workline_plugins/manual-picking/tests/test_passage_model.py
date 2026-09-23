"""人工料箱经过只属于插件，不占用物料执行身份。"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from manual_picking.application.passage_model import ManualPickingPassage
from manual_picking.application.passage_repository import PassageRepository
from sqlalchemy import BigInteger, UniqueConstraint, event, insert, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.app.execution.models import InboundEvidence
from src.app.workline.models import WorkLine


def test_passage_identity_and_fifo_indexes() -> None:
    table = ManualPickingPassage.__table__
    assert "material_execution_id" not in table.columns
    assert {"workline_id", "task_id", "scan1_evidence_id", "scan4_evidence_id", "return_state"} <= set(
        table.columns.keys()
    )
    indexes = {index.name: index for index in table.indexes}
    assert any(
        constraint.name == "ux_manual_picking_passages_scan1_evidence"
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    )
    assert any(
        constraint.name == "ux_manual_picking_passages_wms_completed_evidence"
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    )
    assert "scan2_fault_evidence_id" in table.columns
    assert "scan2_fault_command_code" in table.columns
    assert isinstance(table.columns["admission_scanned_at"].type, BigInteger)
    assert any(
        constraint.name == "ux_manual_picking_passages_scan2_fault_evidence"
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    )
    assert indexes["ix_manual_picking_passages_return_fifo"].columns.keys() == [
        "workline_id",
        "scan4_received_at",
        "scan4_evidence_id",
    ]


@pytest.mark.asyncio
async def test_same_task_and_bin_can_complete_distinct_passages() -> None:
    _ = (InboundEvidence, WorkLine)  # 注册外键目标；本测试只创建插件表。
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def attach_schema(connection, _record):  # type: ignore[no-untyped-def]
        connection.execute("ATTACH DATABASE ':memory:' AS wes_biz")

    table = ManualPickingPassage.__table__
    try:
        async with engine.begin() as db:
            await db.run_sync(table.create)
            for evidence_id in (1, 2):
                await db.execute(
                    insert(table).values(
                        workline_id=7,
                        task_id="PICK-001",
                        bin_code="A000000001",
                        admission_operation_id=f"OP{evidence_id}",
                        scan1_evidence_id=evidence_id,
                        scan1_received_at=datetime(2026, 9, 13, 12),
                        disposition="OPEN",
                        return_state="NONE",
                    )
                )
            await db.execute(update(table).where(table.c.scan1_evidence_id == 1).values(wms_result="NORMAL"))
            await db.execute(update(table).where(table.c.scan1_evidence_id == 2).values(wms_result="NG"))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions.begin() as db:
            first = await PassageRepository().by_admission_operation_for_update(db, "OP1")
            second = await PassageRepository().by_admission_operation_for_update(db, "OP2")
            assert first is not None and first.wms_result == "NORMAL"
            assert second is not None and second.wms_result == "NG"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_return_candidates_never_skip_unclosed_fifo_head() -> None:
    _ = (InboundEvidence, WorkLine)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def attach_schema(connection, _record):  # type: ignore[no-untyped-def]
        connection.execute("ATTACH DATABASE ':memory:' AS wes_biz")

    table = ManualPickingPassage.__table__
    try:
        async with engine.begin() as db:
            await db.run_sync(table.create)
            for evidence_id, bin_code, second, state in (
                (3, "BOX-C", 3, "READY"),
                (2, "BOX-B", 2, "READY"),
                (1, "BOX-A", 1, "MOVE_PENDING"),
            ):
                await db.execute(
                    insert(table).values(
                        workline_id=7,
                        task_id="PICK-001",
                        bin_code=bin_code,
                        scan1_evidence_id=evidence_id,
                        scan1_received_at=datetime(2026, 9, 13, 11),
                        scan4_evidence_id=evidence_id + 10,
                        scan4_received_at=datetime(2026, 9, 13, 12, 0, second),
                        return_state=state,
                    )
                )
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions.begin() as db:
            assert await PassageRepository().ready_return_prefix_for_update(db, 7) == ()
            await db.execute(update(table).where(table.c.bin_code == "BOX-A").values(return_state="READY"))
            rows = await PassageRepository().ready_return_prefix_for_update(db, 7)
            assert [row.bin_code for row in rows] == ["BOX-A", "BOX-B", "BOX-C"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_manual_line_business_blocker_counts_only_unclosed_passages() -> None:
    class Db:
        count = 1

        async def execute(self, _statement):  # type: ignore[no-untyped-def]
            return SimpleNamespace(scalar_one=lambda: self.count)

    db = Db()
    repository = PassageRepository()
    assert await repository.get_unfinished_workload_summary(db, 7) == {
        "count": 1,
        "sample": "manual-picking passage",
    }
    db.count = 0

    assert await repository.get_unfinished_workload_summary(db, 7) == {"count": 0, "sample": None}


@pytest.mark.asyncio
async def test_archive_open_work_marks_every_unclosed_passage_as_archived() -> None:
    archived_at = datetime(2026, 9, 15, 7, 30)
    rows = [
        SimpleNamespace(disposition="OPEN", archived_at=None),
        SimpleNamespace(disposition="NORMAL", archived_at=None),
    ]

    class Db:
        flush = AsyncMock()

        async def execute(self, _statement):  # type: ignore[no-untyped-def]
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))

    count = await PassageRepository().archive_open_work(Db(), workline_id=7, archived_at=archived_at)

    assert count == 2
    assert [(row.disposition, row.archived_at) for row in rows] == [
        ("CLOSED", archived_at),
        ("CLOSED", archived_at),
    ]
