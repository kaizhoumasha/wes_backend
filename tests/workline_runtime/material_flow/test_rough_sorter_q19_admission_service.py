"""Q19 首次 typed decision 持久化与 crash/replay 契约。"""

from __future__ import annotations

from copy import deepcopy

import pytest

from src.app.runtime.capabilities.material_flow.rough_sorter_q19_admission_service import (
    RoughSorterQ19AdmissionService,
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
        self.flush_count = 0
        self.intents: list[object] = []
        self.outbox: list[object] = []

    async def flush(self) -> None:
        self.flush_count += 1


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

    outcome = await RoughSorterQ19AdmissionService(runtime).resolve(db, session=session, request=_request())

    assert isinstance(outcome, QuerySuccess)
    assert outcome.value.decision == "ADMIT"
    assert outcome.value.request_canonical_hash == runtime.project(_request()).request_canonical_hash
    assert outcome.value.evidence_reference == "query:q19:evidence-1"
    assert session.context_json["wms_admission_decision"]["source_version"]
    assert db.flush_count == 1
    assert runtime.execute_count == 1
    assert db.intents == []
    assert db.outbox == []


@pytest.mark.asyncio
async def test_crash_replay_reads_persisted_q19_decision_with_zero_new_query() -> None:
    first_runtime = QueryRuntime(_success())
    first_session = FakeSession()
    await RoughSorterQ19AdmissionService(first_runtime).resolve(FakeDb(), session=first_session, request=_request())
    persisted_context = deepcopy(first_session.context_json)

    replay_runtime = QueryRuntime(QueryTechnicalFailure("MUST_NOT_CALL", "must not call", retryable=False))
    replay_db = FakeDb()
    replay = await RoughSorterQ19AdmissionService(replay_runtime).resolve(
        replay_db,
        session=FakeSession(persisted_context),
        request=_request(),
    )

    assert isinstance(replay, QuerySuccess)
    assert replay.value.evidence_reference == "query:q19:evidence-1"
    assert replay_runtime.execute_count == 0
    assert replay_db.flush_count == 0


@pytest.mark.asyncio
async def test_invalid_or_failed_q19_outcome_is_not_persisted() -> None:
    runtime = QueryRuntime(QueryTechnicalFailure("WMS_PROVIDER_TIMEOUT", "timeout", retryable=True))
    db = FakeDb()
    session = FakeSession()

    outcome = await RoughSorterQ19AdmissionService(runtime).resolve(db, session=session, request=_request())

    assert isinstance(outcome, QueryTechnicalFailure)
    assert "wms_admission_decision" not in session.context_json
    assert db.flush_count == 0


@pytest.mark.asyncio
async def test_replay_with_different_q19_request_hash_fails_closed_without_query() -> None:
    runtime = QueryRuntime(_success())
    session = FakeSession()
    await RoughSorterQ19AdmissionService(runtime).resolve(FakeDb(), session=session, request=_request())
    changed_request = _request().model_copy(update={"station_code": "ROUGH-SORTER-CHANGED"})

    replay = await RoughSorterQ19AdmissionService(runtime).resolve(
        FakeDb(),
        session=session,
        request=changed_request,
    )

    assert isinstance(replay, QueryContractFailure)
    assert replay.reason_code == "WMS_Q19_REPLAY_REQUEST_MISMATCH"
    assert runtime.execute_count == 1
