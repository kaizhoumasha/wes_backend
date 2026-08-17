from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy.dialects import postgresql

from src.app.execution.models import InboundEvidence
from src.app.execution.repositories.inbound_evidence_repository import InboundEvidenceRepository


class _EmptyResult:
    def scalars(self) -> _EmptyResult:
        return self

    def all(self) -> list[object]:
        return []


class _RecordingDb:
    def __init__(self) -> None:
        self.statement = None

    async def execute(self, statement):  # type: ignore[no-untyped-def]
        self.statement = statement
        return _EmptyResult()

    async def flush(self) -> None:
        return None


@pytest.mark.asyncio
async def test_decision_claim_and_partial_index_exclude_foundation_device_results() -> None:
    now = datetime(2026, 8, 17, 12)
    db = _RecordingDb()

    await InboundEvidenceRepository().claim_decision_batch(
        db,  # type: ignore[arg-type]
        now=now,
        claim_token="claim",
        claim_expires_at=now + timedelta(seconds=30),
        limit=100,
    )

    assert db.statement is not None
    sql = str(
        db.statement.compile(  # type: ignore[union-attr]
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    exclusion = "NOT (wes_biz.inbound_evidences.kind = 'DEVICE_RESULT' AND "
    assert exclusion in sql
    assert "wes_biz.inbound_evidences.material_execution_id IS NULL" in sql

    index = next(
        item for item in InboundEvidence.__table__.indexes if item.name == "ix_inbound_evidences_decision_eligible"
    )
    predicate = str(index.dialect_options["postgresql"]["where"])
    assert "NOT (kind = 'DEVICE_RESULT' AND material_execution_id IS NULL)" in predicate
