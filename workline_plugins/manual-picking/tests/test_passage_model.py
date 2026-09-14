"""人工料箱经过只属于插件，不占用物料执行身份。"""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from manual_picking.application.passage_model import ManualPickingPassage
from manual_picking.application.passage_repository import PassageRepository
from sqlalchemy import BigInteger, UniqueConstraint, event, insert, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

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
async def test_one_wms_terminal_per_task_and_bin() -> None:
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
                        scan1_evidence_id=evidence_id,
                        scan1_received_at=datetime(2026, 9, 13, 12),
                        disposition="OPEN",
                        return_state="NONE",
                    )
                )
            await db.execute(update(table).where(table.c.scan1_evidence_id == 1).values(wms_result="NORMAL"))
            with pytest.raises(IntegrityError):
                await db.execute(update(table).where(table.c.scan1_evidence_id == 2).values(wms_result="NG"))
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_unpublished_scan4_evidence_fences_later_scan() -> None:
    _ = WorkLine
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def attach_schema(connection, _record):  # type: ignore[no-untyped-def]
        connection.execute("ATTACH DATABASE ':memory:' AS wes_biz")

    table = InboundEvidence.__table__
    now = datetime(2026, 9, 13, 12)
    try:
        async with engine.begin() as db:
            await db.run_sync(table.create)
            for evidence_id, device_code, received_at in (
                (1, "S4", now + timedelta(seconds=1)),
                (2, "S3", now),
                (3, "S4", now),
                (4, "S4", now - timedelta(seconds=1)),
            ):
                await db.execute(
                    insert(table).values(
                        id=evidence_id,
                        kind="DEVICE_EVENT",
                        source_identity=f"scan:{evidence_id}",
                        payload_digest="a" * 64,
                        normalized_payload={"event_type": "SCAN_COMPLETED"},
                        received_at=received_at,
                        workline_id=7,
                        device_code=device_code,
                        apply_status="RECONCILING" if evidence_id in {1, 4} else "APPLIED",
                    )
                )
            repository = PassageRepository()
            target = SimpleNamespace(id=3, received_at=now)
            assert await repository.has_prior_unpublished_scan(db, workline_id=7, device_code="S4", evidence=target)
            await db.execute(update(table).where(table.c.id == 4).values(published_at=now, decision_digest="b" * 64))
            assert not await repository.has_prior_unpublished_scan(db, workline_id=7, device_code="S4", evidence=target)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_return_candidates_never_skip_unclosed_fifo_head() -> None:
    class _Db:
        async def execute(self, statement):  # type: ignore[no-untyped-def]
            del statement
            return SimpleNamespace(
                scalars=lambda: SimpleNamespace(
                    all=lambda: [
                        SimpleNamespace(return_state="READY"),
                        SimpleNamespace(return_state="MOVE_PENDING"),
                        SimpleNamespace(return_state="READY"),
                    ]
                )
            )

    rows = await PassageRepository().ready_return_prefix_for_update(_Db(), 7)

    assert len(rows) == 1


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
