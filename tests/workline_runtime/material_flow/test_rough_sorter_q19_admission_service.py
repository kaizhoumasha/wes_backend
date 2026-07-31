"""Q19 首次 typed decision 持久化与 crash/replay 契约。"""

from __future__ import annotations

from copy import deepcopy

import pytest

from src.app.runtime.capabilities.material_flow.rough_sorter_q19_admission_service import (
    RoughSorterQ19AdmissionService,
    _decision_from_result,
)
from src.app.runtime.orchestration.repositories.rough_sorter_q19_admission_repository import (
    RoughSorterQ19AdmissionRepository,
)
from src.app.wms_integration.operation_registry import QUERY_OPERATIONS
from src.app.wms_integration.ports.query_outcome import (
    QueryContractFailure,
    QuerySuccess,
    QueryTechnicalFailure,
)
from src.app.wms_integration.query_projection import project_wms_query_request
from tests.contracts.wms_integration.provider_profile_support import build_compiled_provider_profile
from tests.mock.wms_operation_fixtures import REQUEST_FIXTURES, RESULT_FIXTURES


class FakeSession:
    def __init__(self, context_json=None) -> None:
        self.context_json = context_json or {}


class FakeDb:
    def __init__(self) -> None:
        self.intents: list[object] = []
        self.outbox: list[object] = []

    async def flush(self) -> None:
        raise AssertionError("Q19 service 不得绕过 Repository 直接 flush")


class FakeRepository:
    def __init__(self, session: FakeSession) -> None:
        self.session = session
        self.locked_session_ids: list[int] = []
        self.persisted_decisions: list[object] = []

    async def get_for_update(self, _db: object, session_id: int) -> FakeSession | None:
        self.locked_session_ids.append(session_id)
        return self.session

    def load_decision(self, session: FakeSession):
        return session.context_json.get("wms_admission_decision")

    async def persist_decision(self, _db: object, session: FakeSession, decision: object) -> None:
        self.persisted_decisions.append(decision)
        session.context_json = {
            **session.context_json,
            "wms_admission_decision": decision.model_dump(mode="json"),
        }


class QueryRuntime:
    def __init__(self, outcome) -> None:
        self.outcome = outcome
        self.execute_count = 0
        self.operation = QUERY_OPERATIONS[-1]
        self.endpoint = build_compiled_provider_profile().operations[self.operation.identity]

    def project(self, request):
        return project_wms_query_request(
            operation=self.operation,
            endpoint=self.endpoint,
            request=request,
        )

    async def execute(self, _request):
        self.execute_count += 1
        return self.outcome


def _request():
    operation = QUERY_OPERATIONS[-1]
    return operation.request_model.model_validate(REQUEST_FIXTURES[operation.identity])


def _success():
    operation = QUERY_OPERATIONS[-1]
    result = operation.result_model.model_validate(RESULT_FIXTURES[operation.identity])
    return QuerySuccess(result, evidence_key="query:q19:evidence-1")


@pytest.mark.asyncio
async def test_first_typed_q19_decision_is_flushed_before_return_and_creates_no_side_effect_rows() -> None:
    runtime = QueryRuntime(_success())
    db = FakeDb()
    session = FakeSession()
    repository = FakeRepository(session)

    outcome = await RoughSorterQ19AdmissionService(runtime, repository=repository).resolve(
        db,
        session_id=17,
        request=_request(),
    )

    assert isinstance(outcome, QuerySuccess)
    assert outcome.value.decision == "ADMIT"
    assert outcome.value.request_canonical_hash == runtime.project(_request()).request_canonical_hash
    assert outcome.value.evidence_reference == "query:q19:evidence-1"
    assert session.context_json["wms_admission_decision"]["source_version"]
    assert repository.locked_session_ids == [17]
    assert repository.persisted_decisions == [outcome.value]
    assert runtime.execute_count == 1
    assert db.intents == []
    assert db.outbox == []


@pytest.mark.asyncio
async def test_crash_replay_reads_persisted_q19_decision_with_zero_new_query() -> None:
    first_runtime = QueryRuntime(_success())
    first_session = FakeSession()
    await RoughSorterQ19AdmissionService(first_runtime, repository=FakeRepository(first_session)).resolve(
        FakeDb(),
        session_id=17,
        request=_request(),
    )
    persisted_context = deepcopy(first_session.context_json)

    replay_runtime = QueryRuntime(QueryTechnicalFailure("MUST_NOT_CALL", "must not call", retryable=False))
    replay_db = FakeDb()
    replay_repository = FakeRepository(FakeSession(persisted_context))
    replay = await RoughSorterQ19AdmissionService(replay_runtime, repository=replay_repository).resolve(
        replay_db,
        session_id=17,
        request=_request(),
    )

    assert isinstance(replay, QuerySuccess)
    assert replay.value.evidence_reference == "query:q19:evidence-1"
    assert replay_runtime.execute_count == 0
    assert replay_repository.persisted_decisions == []


@pytest.mark.asyncio
async def test_invalid_or_failed_q19_outcome_is_not_persisted() -> None:
    runtime = QueryRuntime(QueryTechnicalFailure("WMS_PROVIDER_TIMEOUT", "timeout", retryable=True))
    db = FakeDb()
    session = FakeSession()
    repository = FakeRepository(session)

    outcome = await RoughSorterQ19AdmissionService(runtime, repository=repository).resolve(
        db,
        session_id=17,
        request=_request(),
    )

    assert isinstance(outcome, QueryTechnicalFailure)
    assert "wms_admission_decision" not in session.context_json
    assert repository.persisted_decisions == []


@pytest.mark.asyncio
async def test_replay_with_different_q19_request_hash_fails_closed_without_query() -> None:
    runtime = QueryRuntime(_success())
    session = FakeSession()
    repository = FakeRepository(session)
    await RoughSorterQ19AdmissionService(runtime, repository=repository).resolve(
        FakeDb(),
        session_id=17,
        request=_request(),
    )
    changed_request = _request().model_copy(update={"station_code": "ROUGH-SORTER-CHANGED"})

    replay = await RoughSorterQ19AdmissionService(runtime, repository=repository).resolve(
        FakeDb(),
        session_id=17,
        request=changed_request,
    )

    assert isinstance(replay, QueryContractFailure)
    assert replay.reason_code == "WMS_Q19_REPLAY_REQUEST_MISMATCH"
    assert runtime.execute_count == 1


@pytest.mark.asyncio
async def test_missing_q19_session_fails_closed_without_query() -> None:
    runtime = QueryRuntime(_success())
    repository = FakeRepository(FakeSession())
    repository.session = None

    outcome = await RoughSorterQ19AdmissionService(runtime, repository=repository).resolve(
        FakeDb(),
        session_id=404,
        request=_request(),
    )

    assert isinstance(outcome, QueryContractFailure)
    assert outcome.reason_code == "WMS_Q19_SESSION_NOT_FOUND"
    assert runtime.execute_count == 0


@pytest.mark.asyncio
async def test_invalid_persisted_q19_decision_fails_closed_without_query() -> None:
    runtime = QueryRuntime(_success())
    session = FakeSession({"wms_admission_decision": {"decision": "INVALID"}})

    outcome = await RoughSorterQ19AdmissionService(runtime, repository=FakeRepository(session)).resolve(
        FakeDb(),
        session_id=17,
        request=_request(),
    )

    assert isinstance(outcome, QueryContractFailure)
    assert outcome.reason_code == "WMS_Q19_PERSISTED_DECISION_INVALID"
    assert runtime.execute_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("runtime_outcome", "reason_code"),
    [
        (QuerySuccess(object(), evidence_key="evidence"), "WMS_Q19_RESULT_TYPE_MISMATCH"),
        (
            QuerySuccess(
                QUERY_OPERATIONS[-1].result_model.model_validate(RESULT_FIXTURES[QUERY_OPERATIONS[-1].identity])
            ),
            "WMS_Q19_EVIDENCE_MISSING",
        ),
    ],
)
async def test_q19_success_requires_expected_result_type_and_evidence(runtime_outcome, reason_code: str) -> None:
    runtime = QueryRuntime(runtime_outcome)
    repository = FakeRepository(FakeSession())

    outcome = await RoughSorterQ19AdmissionService(runtime, repository=repository).resolve(
        FakeDb(),
        session_id=17,
        request=_request(),
    )

    assert isinstance(outcome, QueryContractFailure)
    assert outcome.reason_code == reason_code
    assert repository.persisted_decisions == []


class _SessionRepository:
    def __init__(self, session: FakeSession) -> None:
        self.session = session
        self.calls: list[tuple[int, bool]] = []

    async def get_for_update(self, _db, session_id: int, *, populate_existing: bool):
        self.calls.append((session_id, populate_existing))
        return self.session


class _MutationRepository:
    def __init__(self) -> None:
        self.persisted: list[FakeSession] = []

    async def persist(self, _db, session: FakeSession) -> None:
        self.persisted.append(session)


@pytest.mark.asyncio
async def test_q19_repository_owns_lock_context_and_mutation_boundaries() -> None:
    session = FakeSession()
    session_repository = _SessionRepository(session)
    mutation_repository = _MutationRepository()
    repository = RoughSorterQ19AdmissionRepository(
        session_repository=session_repository,
        mutation_repository=mutation_repository,
    )
    result = QUERY_OPERATIONS[-1].result_model.model_validate(RESULT_FIXTURES[QUERY_OPERATIONS[-1].identity])
    decision = _decision_from_result(
        request_canonical_hash="a" * 64,
        result=result,
        evidence_reference="evidence",
    )

    assert await repository.get_for_update(FakeDb(), 17) is session
    assert session_repository.calls == [(17, True)]
    assert repository.load_decision(session) is None
    await repository.persist_decision(FakeDb(), session, decision)
    assert repository.load_decision(session) == decision.model_dump(mode="json")
    assert mutation_repository.persisted == [session]
    session.context_json = "invalid"
    with pytest.raises(TypeError, match="object session context"):
        await repository.persist_decision(FakeDb(), session, decision)


@pytest.mark.asyncio
async def test_q19_non_object_session_context_fails_closed_without_query() -> None:
    runtime = QueryRuntime(_success())
    repository = RoughSorterQ19AdmissionRepository(
        session_repository=_SessionRepository(FakeSession("legacy-invalid-context")),
        mutation_repository=_MutationRepository(),
    )

    outcome = await RoughSorterQ19AdmissionService(runtime, repository=repository).resolve(
        FakeDb(),
        session_id=17,
        request=_request(),
    )

    assert isinstance(outcome, QueryContractFailure)
    assert outcome.reason_code == "WMS_Q19_SESSION_CONTEXT_INVALID"
    assert runtime.execute_count == 0
