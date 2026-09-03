from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import BigInteger

from src.app.transport.debug_run_contracts import (
    CreateTransportDebugRun,
    TransportDebugBinSelection,
    TransportDebugFaceGroup,
)
from src.app.transport.debug_run_service import (
    TransportDebugRunConflict,
    TransportDebugRunContractError,
    TransportDebugRunService,
)
from src.app.transport.models import TransportDebugRun, TransportDebugRunStep, TransportTask
from src.core.uuid7 import is_uuid7

NOW = datetime(2026, 9, 2, 12, 0, 0)


def test_debug_run_operator_ids_match_production_snowflake_width() -> None:
    columns = TransportDebugRun.__table__.c

    assert isinstance(columns.created_by_user_id.type, BigInteger)
    assert isinstance(columns.aborted_by_user_id.type, BigInteger)


class _Context(AbstractAsyncContextManager[object]):
    def __init__(self, factory: _Sessions) -> None:
        self.factory = factory

    async def __aenter__(self) -> object:
        self.factory.open_transactions += 1
        return self.factory.db

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.factory.open_transactions -= 1
        if exc_type is None:
            self.factory.commits += 1


class _Sessions:
    def __init__(self) -> None:
        self.db = object()
        self.open_transactions = 0
        self.commits = 0

    def begin(self) -> _Context:
        return _Context(self)

    def __call__(self) -> _Context:
        return _Context(self)


class _Repository:
    def __init__(self) -> None:
        self.runs: dict[str, TransportDebugRun] = {}
        self.steps: dict[str, list[TransportDebugRunStep]] = {}
        self.tasks: dict[str, TransportTask] = {}
        self.active_binding = False
        self.current_step_batch_sizes: list[int] = []

    async def get_active_run(self, db: object, *, for_update: bool = False) -> TransportDebugRun | None:
        del db, for_update
        return next((run for run in self.runs.values() if run.active_scope == "GLOBAL"), None)

    async def add_run(self, db: object, run: TransportDebugRun, first_step: TransportDebugRunStep) -> None:
        del db
        self.runs[run.run_id] = run
        self.steps[run.run_id] = [first_step]

    async def get_run(self, db: object, run_id: str, *, for_update: bool = False) -> TransportDebugRun | None:
        del db, for_update
        return self.runs.get(run_id)

    async def get_current_step(
        self,
        db: object,
        run: TransportDebugRun,
        *,
        for_update: bool = False,
    ) -> TransportDebugRunStep | None:
        del db, for_update
        return self.steps[run.run_id][run.current_step_ordinal]

    async def list_steps(self, db: object, run_id: str) -> list[TransportDebugRunStep]:
        del db
        return self.steps[run_id]

    async def list_current_steps(
        self,
        db: object,
        runs: list[TransportDebugRun],
    ) -> dict[str, TransportDebugRunStep]:
        del db
        self.current_step_batch_sizes.append(len(runs))
        return {run.run_id: self.steps[run.run_id][run.current_step_ordinal] for run in runs}

    async def list_recent_runs(
        self,
        db: object,
        *,
        limit: int,
        before_created_at: datetime | None,
        before_id: int | None,
    ) -> list[TransportDebugRun]:
        del db
        values = sorted(self.runs.values(), key=lambda run: (run.created_at, run.id or 0), reverse=True)
        if before_created_at is not None and before_id is not None:
            values = [run for run in values if (run.created_at, run.id or 0) < (before_created_at, before_id)]
        return values[:limit]

    async def get_transport_task(self, db: object, transport_task_id: str) -> TransportTask | None:
        del db
        return self.tasks.get(transport_task_id)

    async def list_transport_tasks(
        self,
        db: object,
        transport_task_ids: list[str],
    ) -> dict[str, TransportTask]:
        del db
        return {task_id: self.tasks[task_id] for task_id in transport_task_ids if task_id in self.tasks}

    async def has_active_transport_binding(self, db: object, run_id: str) -> bool:
        del db, run_id
        return self.active_binding

    async def list_active_transport_binding_task_ids(self, db: object, run_id: str) -> set[str]:
        del db, run_id
        return set(self.tasks) if self.active_binding else set()


class _Publisher:
    def __init__(self, sessions: _Sessions) -> None:
        self.sessions = sessions
        self.events: list[dict[str, object]] = []

    async def publish_to(self, channel: str, event_type: str, payload: dict[str, object]) -> bool:
        assert self.sessions.open_transactions == 0
        self.events.append({"channel": channel, "event_type": event_type, **payload})
        return True


class _Audit:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create_audit_log(self, db: object, **values: Any) -> object:
        self.calls.append({"db": db, **values})
        return object()


def _request(*, face: str = " 90 ", slot_id: str = "510056A3F2C101") -> CreateTransportDebugRun:
    return CreateTransportDebugRun(
        rack_id="510056",
        face_groups=(
            TransportDebugFaceGroup(
                face=face,
                bins=(TransportDebugBinSelection("A000001922", slot_id),),
            ),
        ),
    )


def _service() -> tuple[TransportDebugRunService, _Repository, _Sessions, _Publisher]:
    sessions = _Sessions()
    repository = _Repository()
    publisher = _Publisher(sessions)
    service = TransportDebugRunService(
        sessions,  # type: ignore[arg-type]
        repository,  # type: ignore[arg-type]
        SimpleNamespace(),  # type: ignore[arg-type]
        clock=lambda: NOW,
        event_publisher=publisher,
    )
    return service, repository, sessions, publisher


async def test_create_run_freezes_exact_operator_input_and_first_step_identity() -> None:
    service, repository, sessions, publisher = _service()

    snapshot = await service.create_run(_request(), actor_id=7)

    run = repository.runs[snapshot.run_id]
    step = repository.steps[snapshot.run_id][0]
    assert run.configuration_json["face_groups"] == [
        {
            "face": " 90 ",
            "bins": [{"bin_id": "A000001922", "slot_id": "510056A3F2C101"}],
        }
    ]
    assert (step.phase, step.status, step.group_index) == ("RACK_TO_STATION", "PENDING", 0)
    assert is_uuid7(step.client_request_id)
    assert snapshot.face_groups[0].face == " 90 "
    assert snapshot.current_step is not None
    assert snapshot.current_step.client_request_id == step.client_request_id
    assert snapshot.can_abort is False
    assert sessions.commits == 1
    assert publisher.events == [
        {
            "channel": "transport:debug-run:stream",
            "event_type": "transport_debug_run.updated",
            "run_id": run.run_id,
            "version": 1,
            "status": "RUNNING",
            "updated_at": "2026-09-02T12:00:00+00:00",
        }
    ]


async def test_create_run_accepts_operator_input_without_resource_mounts() -> None:
    service, _, _, _ = _service()
    input_request = CreateTransportDebugRun(
        rack_id="FIELD-RACK-07",
        face_groups=(
            TransportDebugFaceGroup(
                face="270",
                bins=(TransportDebugBinSelection("FIELD-BIN-03", "FIELD-SLOT-02"),),
            ),
        ),
    )

    snapshot = await service.create_run(input_request, actor_id=7)

    assert snapshot.rack_id == "FIELD-RACK-07"
    assert snapshot.face_groups[0].bins[0] == TransportDebugBinSelection("FIELD-BIN-03", "FIELD-SLOT-02")


async def test_create_run_rejects_second_global_active_run() -> None:
    service, _, _, _ = _service()
    await service.create_run(_request(), actor_id=7)

    with pytest.raises(TransportDebugRunConflict, match="active debug run"):
        await service.create_run(_request(face="270"), actor_id=8)


async def test_list_runs_uses_stable_cursor_and_rejects_invalid_cursor() -> None:
    service, repository, _, _ = _service()
    first = await service.create_run(_request(), actor_id=7)
    repository.runs[first.run_id].active_scope = None
    repository.runs[first.run_id].status = "COMPLETED"
    repository.runs[first.run_id].id = 1
    second = await service.create_run(_request(face="270"), actor_id=8)
    repository.runs[second.run_id].id = 2

    page = await service.list_runs(limit=1, cursor=None)
    next_page = await service.list_runs(limit=1, cursor=page.next_cursor)

    assert [item.run_id for item in page.items] == [second.run_id]
    assert [item.run_id for item in next_page.items] == [first.run_id]
    assert repository.current_step_batch_sizes == [1, 1]
    with pytest.raises(TransportDebugRunContractError, match="cursor"):
        await service.list_runs(limit=1, cursor="not-a-cursor")


async def test_abort_requires_attention_terminal_tasks_and_no_active_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    service, repository, _, _ = _service()
    snapshot = await service.create_run(_request(), actor_id=7)
    run = repository.runs[snapshot.run_id]
    step = repository.steps[snapshot.run_id][0]
    step.transport_task_id = "transport-1"
    repository.tasks["transport-1"] = _transport_task("RECONCILING")

    with pytest.raises(TransportDebugRunConflict, match="NEEDS_ATTENTION"):
        await service.abort_run(
            run.run_id,
            assertion="PHYSICAL_STATE_VERIFIED",
            reason="现场确认全部机构静止",
            actor_id=7,
        )

    run.status = "NEEDS_ATTENTION"
    run.attention_code = "TRANSPORT_DELIVERY_UNKNOWN"
    with pytest.raises(TransportDebugRunConflict, match="terminal"):
        await service.abort_run(
            run.run_id,
            assertion="PHYSICAL_STATE_VERIFIED",
            reason="现场确认全部机构静止",
            actor_id=7,
        )

    repository.tasks["transport-1"].status = "FAILED"
    repository.active_binding = True
    with pytest.raises(TransportDebugRunConflict, match="binding"):
        await service.abort_run(
            run.run_id,
            assertion="PHYSICAL_STATE_VERIFIED",
            reason="现场确认全部机构静止",
            actor_id=7,
        )

    repository.active_binding = False
    audit = _Audit()
    monkeypatch.setattr("src.app.transport.debug_run_service.audit_log_service", audit)
    aborted = await service.abort_run(
        run.run_id,
        assertion="PHYSICAL_STATE_VERIFIED",
        reason="现场确认全部机构静止",
        actor_id=7,
    )

    assert aborted.status == "ABORTED"
    assert run.active_scope is None
    assert run.aborted_reason == "现场确认全部机构静止"
    assert repository.tasks["transport-1"].status == "FAILED"
    assert len(audit.calls) == 1


async def test_abort_finalizes_only_a_provably_unsent_debug_task(monkeypatch: pytest.MonkeyPatch) -> None:
    service, repository, _, _ = _service()
    snapshot = await service.create_run(_request(), actor_id=7)
    run = repository.runs[snapshot.run_id]
    step = repository.steps[snapshot.run_id][0]
    step.transport_task_id = "transport-1"
    repository.tasks["transport-1"] = _transport_task("PENDING")
    run.status = "NEEDS_ATTENTION"
    run.attention_code = "EVIDENCE_SOURCE_EVENT_CONFLICT"
    repository.active_binding = True

    class _Transport:
        async def is_unsent_debug_task_finalizable_in_session(self, _db: object, task_id: str) -> bool:
            return task_id == "transport-1"

        async def finalize_unsent_debug_task_in_session(self, _db: object, task_id: str) -> bool:
            assert task_id == "transport-1"
            repository.tasks[task_id].status = "FAILED"
            repository.tasks[task_id].reason_code = "TRANSPORT_DEBUG_ABORTED_BEFORE_SEND"
            repository.active_binding = False
            return True

    service._transport = _Transport()  # type: ignore[assignment]
    monkeypatch.setattr("src.app.transport.debug_run_service.audit_log_service", _Audit())

    assert (await service.get_run(run.run_id)).can_abort is True

    aborted = await service.abort_run(
        run.run_id,
        assertion="PHYSICAL_STATE_VERIFIED",
        reason="现场确认从未派发且全部机构静止",
        actor_id=7,
    )

    assert aborted.status == "ABORTED"
    assert repository.tasks["transport-1"].status == "FAILED"


async def test_abort_rejects_bad_assertion_and_empty_reason() -> None:
    service, repository, _, _ = _service()
    snapshot = await service.create_run(_request(), actor_id=7)
    repository.runs[snapshot.run_id].status = "NEEDS_ATTENTION"

    with pytest.raises(TransportDebugRunContractError, match="assertion"):
        await service.abort_run(snapshot.run_id, assertion="FORCE", reason="confirmed", actor_id=7)
    with pytest.raises(TransportDebugRunContractError, match="reason"):
        await service.abort_run(
            snapshot.run_id,
            assertion="PHYSICAL_STATE_VERIFIED",
            reason="   ",
            actor_id=7,
        )


def _transport_task(status: str) -> TransportTask:
    return TransportTask(
        transport_task_id="transport-1",
        client_request_id="01990f0d-1800-7000-8000-000000000001",
        request_digest="0" * 64,
        kind="RACK_MOVE",
        caller_json={"workline_id": "TRANSPORT_DEBUG"},
        request_json={},
        submit_operation_id="01990f0d-1800-7000-8000-000000000002",
        submit_timestamp_ms=1,
        submit_request_body="{}",
        submit_request_body_digest="1" * 64,
        status=status,
        created_at=NOW,
        updated_at=NOW,
    )
