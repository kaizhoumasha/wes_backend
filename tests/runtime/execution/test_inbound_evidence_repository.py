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


class _EvidenceResult:
    def __init__(self, evidences: list[InboundEvidence]) -> None:
        self._evidences = evidences

    def scalars(self) -> _EvidenceResult:
        return self

    def all(self) -> list[InboundEvidence]:
        return self._evidences


class _RecordingDb:
    def __init__(self, evidences: list[InboundEvidence] | None = None) -> None:
        self.statement = None
        self.evidences = evidences or []

    async def execute(self, statement):  # type: ignore[no-untyped-def]
        self.statement = statement
        return _EvidenceResult(self.evidences) if self.evidences else _EmptyResult()

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
    assert "wes_biz.line_run_epochs.status = 'ACTIVE'" in sql
    assert "inbound_evidences.decision_next_attempt_at IS NULL" in sql

    index = next(
        item for item in InboundEvidence.__table__.indexes if item.name == "ix_inbound_evidences_decision_eligible"
    )
    predicate = str(index.dialect_options["postgresql"]["where"])
    assert "NOT (kind = 'DEVICE_RESULT' AND material_execution_id IS NULL)" in predicate


@pytest.mark.asyncio
async def test_decision_claim_only_sets_lease_without_incrementing_attempts() -> None:
    now = datetime(2026, 8, 17, 12)
    evidence = InboundEvidence(
        id=7,
        kind="DEVICE_EVENT",
        source_identity="SCAN-7",
        payload_digest="a" * 64,
        normalized_payload={"data": {}},
        received_at=now,
        device_code="DEVICE-1",
        contract_version="1.0",
        decision_attempt_count=3,
    )
    db = _RecordingDb([evidence])

    claimed = await InboundEvidenceRepository().claim_decision_batch(
        db,  # type: ignore[arg-type]
        now=now,
        claim_token="claim",
        claim_expires_at=now + timedelta(seconds=30),
        limit=100,
    )

    assert claimed == [evidence]
    assert evidence.decision_attempt_count == 3
    assert evidence.decision_claim_token == "claim"
    assert evidence.decision_claim_expires_at == now + timedelta(seconds=30)
