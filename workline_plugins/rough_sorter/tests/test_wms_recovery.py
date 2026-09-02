from __future__ import annotations

import json
import time
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from src.app.wms_adapter.inbound_wire import MAX_INBOUND_BODY_BYTES, RECOVERY_OPERATION
from src.utils.timezone import timezone

from rough_sorter.application.wms_recovery import (
    RecoveryEventEvidenceRecorder,
    RecoveryEventHandler,
    RecoveryEventPersistenceResult,
)

OPERATION_ID = "019f12d0-58d7-7b4d-a23a-1b90aa5d4472"


class _Recorder:
    def __init__(self, *, code: str = "RECEIVED") -> None:
        self.code = code
        self.calls = []

    async def record(self, envelope, *, received_at):  # type: ignore[no-untyped-def]
        self.calls.append((envelope, received_at))
        return RecoveryEventPersistenceResult(
            code=self.code,
            timestamp_ms=2,
        )


class _UnavailableRecorder:
    async def record(self, envelope, *, received_at):  # type: ignore[no-untyped-def]
        raise RuntimeError("database unavailable")


class _Transaction(AbstractAsyncContextManager[object]):
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:  # type: ignore[no-untyped-def]
        return None


class _Sessions:
    def begin(self) -> _Transaction:
        return _Transaction()


class _EvidenceService:
    async def accept(self, db, **kwargs):  # type: ignore[no-untyped-def]
        return SimpleNamespace(
            duplicate=False,
            evidence=SimpleNamespace(id=31, received_at=kwargs["received_at"]),
        )


class _Executions:
    def __init__(self) -> None:
        self.values = {
            "EXEC-1": SimpleNamespace(
                id=21,
                execution_code="EXEC-1",
                material_trace_id="TRACE-1",
                line_run_epoch_id=11,
                status="RECONCILING",
                last_transition_evidence_id=30,
            ),
        }

    async def get_by_execution_code_for_update(self, db, execution_code):  # type: ignore[no-untyped-def]
        del db
        return self.values.get(execution_code)


class _CausalEvidences:
    async def get_by_id_for_update(self, db, evidence_id):  # type: ignore[no-untyped-def]
        del db
        if evidence_id != 30:
            return None
        return SimpleNamespace(id=30, material_execution_id=21, line_run_epoch_id=11)


class _TaskQueue:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.execution_wakes = 0
        self.error = error

    def enqueue_execution_facts(self) -> None:
        self.execution_wakes += 1
        if self.error is not None:
            raise self.error


def _body(
    *,
    decision: str = "CONTINUE",
    position: object = ...,
    material_execution_id: str = "EXEC-1",
    reconciling_evidence_id: str = "30",
) -> bytes:
    actual_position = {"type": "HANDOFF_POSITION", "location_code": "LINE-OUT"} if position is ... else position
    return json.dumps(
        {
            "operation_id": OPERATION_ID,
            "operation": RECOVERY_OPERATION,
            "timestamp": 1,
            "data": {
                "recovery_id": "REC-1",
                "material_execution_id": material_execution_id,
                "material_trace_id": "TRACE-1",
                "reconciling_evidence_id": reconciling_evidence_id,
                "decision": decision,
                "authoritative_position": actual_position,
                "reason_code": "MANUAL_CONFIRMED",
            },
        },
        separators=(",", ":"),
    ).encode()


@pytest.mark.asyncio
async def test_valid_recovery_is_persisted_before_received_ack_without_applying_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_received_at = datetime(2026, 8, 17, 1, 2, 3)
    monkeypatch.setattr(timezone, "now_for_db", lambda: db_received_at)
    recorder = _Recorder()
    response = await RecoveryEventHandler(recorder).handle(_body())

    assert (response.http_status, response.body["code"]) == (202, "RECEIVED")
    assert response.body["operation_id"] == OPERATION_ID
    assert len(recorder.calls) == 1
    assert recorder.calls[0][0].data.decision == "CONTINUE"
    assert recorder.calls[0][1] == db_received_at
    assert recorder.calls[0][1].tzinfo is None


@pytest.mark.asyncio
async def test_recorder_interprets_naive_database_received_at_as_utc_in_non_utc_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_received_at = datetime(2026, 8, 17, 1, 2, 3)
    with monkeypatch.context() as process:
        process.setenv("TZ", "Asia/Shanghai")
        process.setattr(timezone, "now_for_db", lambda: db_received_at)
        time.tzset()
        response = await RecoveryEventHandler(
            RecoveryEventEvidenceRecorder(
                _Sessions(),
                _EvidenceService(),
                material_execution_repository=_Executions(),
                inbound_evidence_repository=_CausalEvidences(),
            ),  # type: ignore[arg-type]
        ).handle(_body())
    time.tzset()

    expected_ms = int(datetime(2026, 8, 17, 1, 2, 3, tzinfo=UTC).timestamp() * 1000)
    assert response.body["timestamp"] == expected_ms


@pytest.mark.asyncio
async def test_duplicate_and_conflict_are_mapped_after_persistence_result() -> None:
    duplicate = await RecoveryEventHandler(_Recorder(code="DUPLICATE")).handle(_body())
    conflict = await RecoveryEventHandler(_Recorder(code="CONFLICT")).handle(_body())

    assert (duplicate.http_status, duplicate.body["code"]) == (200, "DUPLICATE")
    assert (conflict.http_status, conflict.body["code"]) == (409, "CONFLICT")


@pytest.mark.asyncio
async def test_invalid_recovery_is_rejected_without_persistence() -> None:
    recorder = _Recorder()
    invalid = await RecoveryEventHandler(recorder).handle(_body(decision="CONTINUE", position=None))
    unsupported = json.dumps(
        {"operation_id": OPERATION_ID, "operation": "inbound.future@v1", "timestamp": 1, "data": {}}
    ).encode()
    unknown = await RecoveryEventHandler(recorder).handle(unsupported)

    assert (invalid.http_status, invalid.body["code"]) == (422, "REJECTED")
    assert invalid.body["data"] == {"reason_code": "INVALID_DATA"}
    assert (unknown.http_status, unknown.body["data"]) == (
        422,
        {"reason_code": "UNSUPPORTED_OPERATION"},
    )
    assert recorder.calls == []


@pytest.mark.asyncio
async def test_handler_enforces_preassociation_body_and_json_limits() -> None:
    recorder = _Recorder()
    too_large = await RecoveryEventHandler(recorder).handle(b"x" * (MAX_INBOUND_BODY_BYTES + 1))
    invalid_json = await RecoveryEventHandler(recorder).handle(b"{")
    exact = _body() + b" " * (MAX_INBOUND_BODY_BYTES - len(_body()))
    accepted = await RecoveryEventHandler(recorder).handle(exact)

    assert (too_large.http_status, too_large.body) == (413, {})
    assert (invalid_json.http_status, invalid_json.body) == (400, {})
    assert accepted.http_status == 202


@pytest.mark.asyncio
async def test_persistence_failure_returns_unavailable_without_false_ack() -> None:
    response = await RecoveryEventHandler(_UnavailableRecorder()).handle(_body())

    assert (response.http_status, response.body["code"]) == (503, "UNAVAILABLE")


@pytest.mark.asyncio
async def test_recorder_freezes_single_execution_and_current_causal_evidence_before_ack() -> None:
    evidence_service = _EvidenceService()
    queue = _TaskQueue()
    recorder = RecoveryEventEvidenceRecorder(
        _Sessions(),
        evidence_service,
        material_execution_repository=_Executions(),
        inbound_evidence_repository=_CausalEvidences(),
        task_queue_gateway=queue,  # type: ignore[arg-type]
    )

    response = await RecoveryEventHandler(recorder).handle(_body())

    assert (response.http_status, response.body["code"]) == (202, "RECEIVED")
    assert queue.execution_wakes == 1


@pytest.mark.asyncio
async def test_recovery_wake_failure_does_not_change_committed_received_result() -> None:
    queue = _TaskQueue(error=RuntimeError("queue unavailable"))
    recorder = RecoveryEventEvidenceRecorder(
        _Sessions(),
        _EvidenceService(),
        material_execution_repository=_Executions(),
        inbound_evidence_repository=_CausalEvidences(),
        task_queue_gateway=queue,  # type: ignore[arg-type]
    )

    response = await RecoveryEventHandler(recorder).handle(_body())

    assert (response.http_status, response.body["code"]) == (202, "RECEIVED")
    assert queue.execution_wakes == 1


@pytest.mark.asyncio
async def test_recorder_maps_trace_mismatch_to_execution_correlation_conflict() -> None:
    executions = _Executions()
    executions.values["EXEC-1"].material_trace_id = "OTHER"
    recorder = RecoveryEventEvidenceRecorder(
        _Sessions(),
        _EvidenceService(),
        material_execution_repository=executions,
        inbound_evidence_repository=_CausalEvidences(),
    )

    response = await RecoveryEventHandler(recorder).handle(_body())

    assert (response.http_status, response.body["code"]) == (409, "CONFLICT")
    assert response.body["data"] == {"reason_code": "EXECUTION_CORRELATION_CONFLICT"}


@pytest.mark.asyncio
async def test_recorder_maps_stale_reconciling_evidence_to_execution_correlation_conflict() -> None:
    executions = _Executions()
    executions.values["EXEC-1"].last_transition_evidence_id = 29
    recorder = RecoveryEventEvidenceRecorder(
        _Sessions(),
        _EvidenceService(),
        material_execution_repository=executions,
        inbound_evidence_repository=_CausalEvidences(),
    )

    response = await RecoveryEventHandler(recorder).handle(_body())

    assert (response.http_status, response.body["code"]) == (409, "CONFLICT")
    assert response.body["data"] == {"reason_code": "EXECUTION_CORRELATION_CONFLICT"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        pytest.param(_body(material_execution_id="MISSING"), id="missing-execution"),
        pytest.param(_body(reconciling_evidence_id="not-an-evidence-id"), id="invalid-causal-identity"),
        pytest.param(_body(reconciling_evidence_id="030"), id="noncanonical-leading-zero"),
        pytest.param(_body(reconciling_evidence_id="9" * 10_000), id="oversized-causal-identity"),
    ],
)
async def test_recorder_maps_other_execution_correlation_failures_to_conflict(body: bytes) -> None:
    recorder = RecoveryEventEvidenceRecorder(
        _Sessions(),
        _EvidenceService(),
        material_execution_repository=_Executions(),
        inbound_evidence_repository=_CausalEvidences(),
    )

    response = await RecoveryEventHandler(recorder).handle(body)

    assert (response.http_status, response.body["code"]) == (409, "CONFLICT")
    assert response.body["data"] == {"reason_code": "EXECUTION_CORRELATION_CONFLICT"}
