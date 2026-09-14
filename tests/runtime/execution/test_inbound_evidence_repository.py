from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy.dialects import postgresql, sqlite

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
async def test_decision_claim_admits_only_command_owned_workline_business_results() -> None:
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
    assert "wes_biz.inbound_evidences.material_execution_id IS NULL" in sql
    assert "wes_biz.device_commands.execution_ref_type = 'WORKLINE_BUSINESS'" in sql
    assert "wes_biz.device_commands.command_code = wes_biz.inbound_evidences.command_code" in sql
    assert "wes_biz.device_commands.workline_id = wes_biz.inbound_evidences.workline_id" in sql
    assert "wes_biz.work_lines.is_active IS true" in sql
    assert "inbound_evidences.decision_next_attempt_at IS NULL" in sql
    assert "FOR UPDATE OF inbound_evidences SKIP LOCKED" in sql

    sqlite_sql = str(
        db.statement.compile(  # type: ignore[union-attr]
            dialect=sqlite.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    for compiled in (sql, sqlite_sql):
        assert "earlier_transport_outcomes" in compiled
        assert "earlier_transport_outcomes.apply_status = 'APPLIED'" in compiled
        assert "transport_task_id" in compiled
        assert "material_execution_id" in compiled
        assert "outcome_version" in compiled
        assert "UNKNOWN" in compiled
        assert "NOT (EXISTS" in compiled
    assert "CAST((earlier_transport_outcomes.normalized_payload ->> 'status') AS VARCHAR) = 'UNKNOWN'" in sql
    assert "CAST((earlier_transport_outcomes.normalized_payload ->> 'outcome_version') AS INTEGER)" in sql
    assert "JSON_EXTRACT(earlier_transport_outcomes.normalized_payload, '$.\"status\"') = 'UNKNOWN'" in sqlite_sql
    assert "JSON_EXTRACT(earlier_transport_outcomes.normalized_payload, '$.\"outcome_version\"')" in sqlite_sql

    index = next(
        item for item in InboundEvidence.__table__.indexes if item.name == "ix_inbound_evidences_decision_eligible"
    )
    predicate = str(index.dialect_options["postgresql"]["where"])
    assert predicate == "apply_status = 'APPLIED' AND published_at IS NULL"


@pytest.mark.asyncio
async def test_workline_wms_evidence_is_claimed_only_for_frozen_plugin_operation() -> None:
    now = datetime(2026, 8, 17, 12)
    db = _RecordingDb()

    await InboundEvidenceRepository().claim_decision_batch(
        db,  # type: ignore[arg-type]
        now=now,
        claim_token="claim",
        claim_expires_at=now + timedelta(seconds=30),
        limit=100,
        business_wms_routes=(("manual-picking", "1.0.0", "outbound.manual_bin.work_completed@v1"),),
    )

    sql = str(db.statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "work_lines.plugin_key = 'manual-picking'" in sql
    assert "work_lines.plugin_version = '1.0.0'" in sql
    assert "inbound_evidences.operation = 'outbound.manual_bin.work_completed@v1'" in sql
    assert "inbound_evidences.material_execution_id IS NOT NULL" in sql
    assert "inbound_evidences.processed_at IS NULL" in sql


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
