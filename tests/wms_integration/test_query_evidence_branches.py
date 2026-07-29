"""WMS QUERY evidence/breaker 原子写入分支覆盖。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from src.app.wms_integration.models import WmsEvidenceStatus
from src.app.wms_integration.ports.query_outcome import (
    QueryBusinessReject,
    QueryContractFailure,
    QuerySuccess,
    QueryTechnicalFailure,
)
from src.app.wms_integration.query_evidence import (
    WmsEffectStatusCallEvidenceWriter,
    WmsQueryCallPermit,
    WmsRegistryCallEvidenceWriter,
    _outcome_snapshot,
    _source_version_conflict,
    classify_source_version,
)
from src.app.wms_integration.repositories.evidence_repository import WmsCallEvidenceRepository


class _FakeDb:
    def __init__(self) -> None:
        self.commit_count = 0
        self.rollback_count = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    async def commit(self) -> None:
        self.commit_count += 1

    async def rollback(self) -> None:
        self.rollback_count += 1


class _SessionFactory:
    def __init__(self) -> None:
        self.sessions: list[_FakeDb] = []

    def __call__(self) -> _FakeDb:
        db = _FakeDb()
        self.sessions.append(db)
        return db


class _Breaker:
    def __init__(self, *, fail_before: bool = False, fail_record: bool = False) -> None:
        self.fail_before = fail_before
        self.fail_record = fail_record
        self.successes: list[dict[str, object]] = []
        self.failures: list[dict[str, object]] = []

    async def before_call(self, _db, **_kwargs):
        if self.fail_before:
            raise RuntimeError("breaker read failed")
        return SimpleNamespace(
            allowed=True,
            reason="closed",
            retry_after_seconds=1.0,
            probe_generation=3,
        )

    async def record_success(self, _db, **kwargs) -> None:
        if self.fail_record:
            raise RuntimeError("breaker write failed")
        self.successes.append(kwargs)

    async def record_failure(self, _db, **kwargs) -> None:
        if self.fail_record:
            raise RuntimeError("breaker write failed")
        self.failures.append(kwargs)


class _EvidenceRepo:
    def __init__(self, previous: object | None = None) -> None:
        self.previous = previous
        self.locks: list[tuple[str, str, str]] = []

    async def lock_query_source_version(
        self,
        _db,
        *,
        provider_profile_identity: str,
        operation_name: str,
        request_canonical_hash: str,
    ) -> None:
        self.locks.append((provider_profile_identity, operation_name, request_canonical_hash))

    async def get_latest_query_success(self, _db, **_kwargs):
        return self.previous


class _EvidenceService:
    def __init__(self, *, repo: _EvidenceRepo | None = None, fail: bool = False) -> None:
        self.repo = repo or _EvidenceRepo()
        self.fail = fail
        self.records: list[dict[str, object]] = []

    async def record_sync_call(self, _db, **kwargs):
        if self.fail:
            raise RuntimeError("evidence write failed")
        self.records.append(kwargs)
        return SimpleNamespace(evidence_key=kwargs["evidence_key"])


class _TypedValue(BaseModel):
    source_version: str | None = None
    value: str = "ok"


@pytest.mark.asyncio
async def test_effect_status_writer_validates_profile_and_maps_before_call() -> None:
    with pytest.raises(ValueError, match="profile identity"):
        WmsEffectStatusCallEvidenceWriter(
            session_factory=_SessionFactory(),
            provider_profile_identity="",
            evidence_service=_EvidenceService(),
            breaker_service=_Breaker(),
        )

    factory = _SessionFactory()
    writer = WmsEffectStatusCallEvidenceWriter(
        session_factory=factory,
        provider_profile_identity="profile",
        evidence_service=_EvidenceService(),
        breaker_service=_Breaker(),
    )
    permit = await writer.before_call(operation_identity="status", target_code="WMS")

    assert permit == WmsQueryCallPermit(True, "closed", 1.0, 3)
    assert factory.sessions[0].commit_count == 1


@pytest.mark.asyncio
async def test_effect_status_before_call_rolls_back_on_breaker_failure() -> None:
    factory = _SessionFactory()
    writer = WmsEffectStatusCallEvidenceWriter(
        session_factory=factory,
        provider_profile_identity="profile",
        evidence_service=_EvidenceService(),
        breaker_service=_Breaker(fail_before=True),
    )

    with pytest.raises(RuntimeError, match="breaker read"):
        await writer.before_call(operation_identity="status", target_code="WMS")
    assert factory.sessions[0].rollback_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "expected_status", "breaker_side"),
    [
        (QuerySuccess(_TypedValue()), WmsEvidenceStatus.SUCCEEDED, "success"),
        (QueryBusinessReject("REJECT", "no"), WmsEvidenceStatus.FAILED, "success"),
        (QueryTechnicalFailure("DOWN", "no", True), WmsEvidenceStatus.FAILED, "failure"),
    ],
)
async def test_effect_status_record_maps_outcome_and_breaker(
    outcome: object,
    expected_status: WmsEvidenceStatus,
    breaker_side: str,
) -> None:
    factory = _SessionFactory()
    evidence = _EvidenceService()
    breaker = _Breaker()
    writer = WmsEffectStatusCallEvidenceWriter(
        session_factory=factory,
        provider_profile_identity="profile",
        evidence_service=evidence,
        breaker_service=breaker,
    )

    key = await writer.record(
        operation_identity="status",
        target_code="WMS",
        request_snapshot={"request": "safe"},
        outcome=outcome,
        permit=WmsQueryCallPermit(allowed=True, probe_generation=7),
    )

    assert key.startswith("status:status:")
    assert evidence.records[0]["status"] is expected_status
    assert bool(breaker.successes) is (breaker_side == "success")
    assert bool(breaker.failures) is (breaker_side == "failure")
    assert factory.sessions[0].commit_count == 1


@pytest.mark.asyncio
async def test_effect_status_record_skips_breaker_and_rolls_back_failures() -> None:
    factory = _SessionFactory()
    breaker = _Breaker(fail_record=True)
    writer = WmsEffectStatusCallEvidenceWriter(
        session_factory=factory,
        provider_profile_identity="profile",
        evidence_service=_EvidenceService(),
        breaker_service=breaker,
    )
    key = await writer.record(
        operation_identity="status",
        target_code="WMS",
        request_snapshot={},
        outcome=QueryContractFailure("BAD", "bad"),
        permit=WmsQueryCallPermit(allowed=False),
    )
    assert key.startswith("status:status:")

    with pytest.raises(RuntimeError, match="breaker write"):
        await writer.record(
            operation_identity="status",
            target_code="WMS",
            request_snapshot={},
            outcome=QueryTechnicalFailure("DOWN", "no", True),
            permit=WmsQueryCallPermit(allowed=True),
        )
    assert factory.sessions[-1].rollback_count == 1


def test_outcome_snapshot_covers_typed_fallback_and_failure_shapes() -> None:
    assert _outcome_snapshot(QuerySuccess(_TypedValue(value="typed"))) == {"value": "typed"}
    assert _outcome_snapshot(QuerySuccess(object())) == {"result_type": "object"}
    assert _outcome_snapshot(QueryBusinessReject("NO", "rejected")) == {
        "reason_code": "NO",
        "message": "rejected",
    }
    assert _outcome_snapshot(QueryTechnicalFailure("DOWN", "failed", True)) == {
        "reason_code": "DOWN",
        "message": "failed",
        "retryable": True,
    }
    assert _outcome_snapshot(QueryContractFailure("BAD", "invalid")) == {"outcome_type": "QueryContractFailure"}


def test_source_version_classification_accepts_same_payload_and_validates_history() -> None:
    assert (
        classify_source_version(
            previous_version="2",
            previous_response_hash="same",
            source_version="2",
            response_hash="same",
        )
        is None
    )
    assert _source_version_conflict(previous=None, source_version="1", response_hash="new") is None
    assert (
        _source_version_conflict(
            previous=SimpleNamespace(response_snapshot=[]),
            source_version="1",
            response_hash="new",
        )
        == "WMS_SOURCE_VERSION_HISTORY_INVALID"
    )
    assert (
        _source_version_conflict(
            previous=SimpleNamespace(response_snapshot={"source_version": 1}),
            source_version="1",
            response_hash="new",
        )
        == "WMS_SOURCE_VERSION_HISTORY_INVALID"
    )
    assert (
        _source_version_conflict(
            previous=SimpleNamespace(response_snapshot={"source_version": "2", "typed_response_hash": "old"}),
            source_version="1",
            response_hash="new",
        )
        == "WMS_SOURCE_VERSION_REGRESSION"
    )


@pytest.mark.asyncio
async def test_registry_writer_maps_before_call_and_rolls_back_failures() -> None:
    factory = _SessionFactory()
    writer = WmsRegistryCallEvidenceWriter(
        session_factory=factory,
        evidence_service=_EvidenceService(),
        breaker_service=_Breaker(),
    )
    assert await writer.before_call(operation_identity="query", target_code="WMS") == WmsQueryCallPermit(
        True,
        "closed",
        1.0,
        3,
    )

    failing_factory = _SessionFactory()
    failing = WmsRegistryCallEvidenceWriter(
        session_factory=failing_factory,
        evidence_service=_EvidenceService(),
        breaker_service=_Breaker(fail_before=True),
    )
    with pytest.raises(RuntimeError, match="breaker read"):
        await failing.before_call(operation_identity="query", target_code="WMS")
    assert failing_factory.sessions[0].rollback_count == 1


async def _record_registry(
    *,
    outcome: object,
    permit: WmsQueryCallPermit,
    previous: object | None = None,
    response_hash: str | None = None,
    fail_evidence: bool = False,
):
    factory = _SessionFactory()
    repo = _EvidenceRepo(previous)
    evidence = _EvidenceService(repo=repo, fail=fail_evidence)
    breaker = _Breaker()
    writer = WmsRegistryCallEvidenceWriter(
        session_factory=factory,
        evidence_service=evidence,
        breaker_service=breaker,
    )
    record = await writer.record(
        operation_identity="query",
        target_code="WMS",
        profile_identity="profile",
        profile_digest="profile-hash",
        endpoint_digest="endpoint-hash",
        request_snapshot={"request": "safe"},
        request_canonical_hash="request-hash",
        response_hash=response_hash,
        attempt_count=2,
        http_status=200,
        outcome=outcome,
        permit=permit,
    )
    return record, factory, repo, evidence, breaker


@pytest.mark.asyncio
async def test_registry_record_success_locks_version_and_records_breaker_success() -> None:
    record, factory, repo, evidence, breaker = await _record_registry(
        outcome=QuerySuccess(_TypedValue(source_version="2")),
        permit=WmsQueryCallPermit(allowed=True, probe_generation=9),
        response_hash="typed-hash",
    )

    assert isinstance(record.outcome, QuerySuccess)
    assert repo.locks == [("profile", "query", "request-hash")]
    assert evidence.records[0]["status"] is WmsEvidenceStatus.SUCCEEDED
    assert evidence.records[0]["response_snapshot"] == {
        "outcome_kind": "QuerySuccess",
        "attempt_count": 2,
        "typed_response_hash": "typed-hash",
        "source_version": "2",
    }
    assert breaker.successes
    assert factory.sessions[0].commit_count == 1


@pytest.mark.asyncio
async def test_registry_record_atomically_turns_version_conflict_into_failed_evidence() -> None:
    previous = SimpleNamespace(response_snapshot={"source_version": "2", "typed_response_hash": "old"})
    record, _factory, _repo, evidence, breaker = await _record_registry(
        outcome=QuerySuccess(_TypedValue(source_version="2")),
        permit=WmsQueryCallPermit(allowed=True),
        previous=previous,
        response_hash="new",
    )

    assert isinstance(record.outcome, QueryContractFailure)
    assert record.outcome.reason_code == "WMS_SOURCE_VERSION_PAYLOAD_CONFLICT"
    assert evidence.records[0]["status"] is WmsEvidenceStatus.FAILED
    assert evidence.records[0]["reason_code"] == "WMS_SOURCE_VERSION_PAYLOAD_CONFLICT"
    assert breaker.failures


@pytest.mark.asyncio
async def test_registry_record_without_version_or_permit_skips_lock_and_breaker() -> None:
    record, _factory, repo, evidence, breaker = await _record_registry(
        outcome=QueryTechnicalFailure("DOWN", "unavailable", True),
        permit=WmsQueryCallPermit(allowed=False),
    )

    assert isinstance(record.outcome, QueryTechnicalFailure)
    assert repo.locks == []
    assert evidence.records[0]["retryable"] is True
    assert evidence.records[0]["response_snapshot"] == {
        "outcome_kind": "QueryTechnicalFailure",
        "attempt_count": 2,
    }
    assert breaker.successes == []
    assert breaker.failures == []


@pytest.mark.asyncio
async def test_registry_record_business_reject_counts_as_breaker_success() -> None:
    record, _factory, _repo, _evidence, breaker = await _record_registry(
        outcome=QueryBusinessReject("NO", "rejected"),
        permit=WmsQueryCallPermit(allowed=True),
        response_hash="hash",
    )

    assert isinstance(record.outcome, QueryBusinessReject)
    assert breaker.successes


@pytest.mark.asyncio
async def test_registry_record_rolls_back_evidence_failure() -> None:
    with pytest.raises(RuntimeError, match="evidence write"):
        await _record_registry(
            outcome=QuerySuccess(_TypedValue()),
            permit=WmsQueryCallPermit(allowed=False),
            fail_evidence=True,
        )


class _RepositoryResult:
    rowcount = None

    def scalar_one_or_none(self):
        return "row"

    def scalars(self):
        return self

    def all(self):
        return ["row"]


class _RepositoryDb:
    def __init__(self) -> None:
        self.statements: list[object] = []
        self.flush_count = 0

    async def execute(self, statement):
        self.statements.append(statement)
        return _RepositoryResult()

    async def flush(self) -> None:
        self.flush_count += 1


@pytest.mark.asyncio
async def test_evidence_repository_query_lock_and_optional_branches() -> None:
    repository = WmsCallEvidenceRepository()
    db = _RepositoryDb()

    assert await repository.list_recent_for_drift_scan(db, operation_name="query") == ["row"]
    assert (
        await repository.get_latest_query_success(
            db,
            provider_profile_identity="profile",
            operation_name="query",
            request_canonical_hash="hash",
        )
        == "row"
    )
    await repository.lock_query_source_version(
        db,
        provider_profile_identity="profile",
        operation_name="query",
        request_canonical_hash="hash",
    )
    assert await repository.delete_by_ids(db, []) == 0
    assert len(db.statements) == 3
