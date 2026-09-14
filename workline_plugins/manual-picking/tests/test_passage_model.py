"""人工料箱经过只属于插件，不占用物料执行身份。"""

from types import SimpleNamespace

import pytest
from manual_picking.application.passage_model import ManualPickingPassage
from manual_picking.application.passage_repository import PassageRepository
from sqlalchemy import BigInteger, UniqueConstraint


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
