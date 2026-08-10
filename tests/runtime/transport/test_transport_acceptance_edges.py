"""Transport 计划中容易被快乐路径遗漏的可靠性验收。"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.app.transport.contracts import (
    BinMove,
    HandoffPosition,
    RackBinSlot,
    RackFace,
    RackPosition,
    TransportCaller,
    TransportContractError,
    TransportOutcome,
    TransportSubmitCode,
    TransportSubmitResult,
)
from src.app.transport.models import (
    TransportEvidence,
    TransportMember,
    TransportPositionProjection,
    TransportResourceBinding,
    TransportTask,
)
from src.app.transport.repository import TransportRepository
from src.app.transport.service import TransportService
from src.app.wms_adapter.transport_wire import RESULT_OPERATION
from src.utils.timezone import timezone
from tests.support.sqlmodel_metadata import register_required_sqlmodel_metadata

register_required_sqlmodel_metadata()


class ConfigurableProvider:
    def __init__(
        self,
        code: TransportSubmitCode = TransportSubmitCode.RECEIVED,
        *,
        retry_after_ms: int | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.code = code
        self.retry_after_ms = retry_after_ms
        self.error = error
        self.calls = 0

    async def submit(self, request: object, *, transport_task_id: str) -> TransportSubmitResult:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return TransportSubmitResult(self.code, transport_task_id, retry_after_ms=self.retry_after_ms)


class RecordingPublisher:
    def __init__(self, *, fail_once: bool = False) -> None:
        self.fail_once = fail_once
        self.outcomes: list[TransportOutcome] = []

    async def publish(self, outcome: TransportOutcome) -> None:
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("publisher unavailable")
        self.outcomes.append(outcome)


class TimeoutOncePublisher(RecordingPublisher):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def publish(self, outcome: TransportOutcome) -> None:
        self.calls += 1
        if self.calls == 1:
            await asyncio.Event().wait()
        await super().publish(outcome)


@pytest_asyncio.fixture(autouse=True)
async def _clean_transport_tables(db_engine: object) -> None:
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions.begin() as db:
        for model in (
            TransportEvidence,
            TransportResourceBinding,
            TransportMember,
            TransportPositionProjection,
            TransportTask,
        ):
            await db.execute(delete(model))


def _service(
    db_engine: object,
    *,
    provider: ConfigurableProvider | None = None,
    publisher: RecordingPublisher | None = None,
) -> TransportService:
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    return TransportService(
        sessions,
        TransportRepository(),
        provider or ConfigurableProvider(),
        publisher or RecordingPublisher(),
    )


def _caller() -> TransportCaller:
    return TransportCaller("SORTER", "STATION_A", "run-acceptance")


async def _load_task(db_engine: object, transport_task_id: str) -> TransportTask:
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as db:
        task = await db.scalar(select(TransportTask).where(TransportTask.transport_task_id == transport_task_id))
        assert task is not None
        return task


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "entry_name",
    ["submit_pending_tasks", "process_pending_evidence", "reconcile_overdue_tasks", "publish_pending_outcomes"],
)
async def test_internal_batch_entries_require_a_positive_bounded_limit(db_engine: object, entry_name: str) -> None:
    service = _service(db_engine)

    with pytest.raises(ValueError, match="positive integer"):
        await getattr(service, entry_name)(0)


@pytest.mark.asyncio
async def test_rotate_requires_a_confirmed_current_position_and_opposite_face(db_engine: object) -> None:
    service = _service(db_engine)

    with pytest.raises(TransportContractError, match="current face is unknown"):
        await service.rotate_rack("rotate-missing", _caller(), "rack-rotate", RackPosition("ROTATE"), RackFace.B)

    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions.begin() as db:
        db.add(
            TransportPositionProjection(
                object_type="RACK",
                object_id="rack-rotate",
                position_json={"kind": "RACK_POSITION", "location_code": "ROTATE"},
                arrival_face="A",
                source_event_id="seed",
                updated_at=timezone.now_for_db(),
            )
        )

    with pytest.raises(TransportContractError, match="current position is not confirmed"):
        await service.rotate_rack("rotate-position", _caller(), "rack-rotate", RackPosition("OTHER"), RackFace.B)
    with pytest.raises(TransportContractError, match="target face equals current face"):
        await service.rotate_rack("rotate-face", _caller(), "rack-rotate", RackPosition("ROTATE"), RackFace.A)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "expected_status", "expected_reason", "released"),
    [
        (TransportSubmitCode.DUPLICATE, "ACCEPTED", None, False),
        (TransportSubmitCode.REJECTED, "REJECTED", "TRANSPORT_REJECTED", True),
        (TransportSubmitCode.CONFLICT, "RECONCILING", "TRANSPORT_SUBMIT_CONFLICT", False),
    ],
)
async def test_submit_ack_terminal_matrix(
    db_engine: object,
    code: TransportSubmitCode,
    expected_status: str,
    expected_reason: str | None,
    released: bool,
) -> None:
    service = _service(db_engine, provider=ConfigurableProvider(code))
    handle = await service.move_rack(
        f"request-{code.value}",
        _caller(),
        f"rack-{code.value}",
        RackPosition("A"),
        RackPosition("B"),
    )

    assert await service.submit_pending_tasks(1) == 1
    task = await _load_task(db_engine, handle.transport_task_id)
    assert (task.status, task.reason_code) == (expected_status, expected_reason)

    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as db:
        bindings = list(
            await db.scalars(
                select(TransportResourceBinding).where(
                    TransportResourceBinding.transport_task_id == handle.transport_task_id
                )
            )
        )
    assert bindings
    assert all((binding.released_at is not None) is released for binding in bindings)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "retry_after_ms"),
    [
        (TransportSubmitCode.NOT_SENT, None),
        (TransportSubmitCode.UNAVAILABLE, None),
        (TransportSubmitCode.BUSY, -1),
        (TransportSubmitCode.BUSY, True),
    ],
)
async def test_confirmed_retryable_results_clear_send_marker_and_use_fixed_delay(
    db_engine: object,
    code: TransportSubmitCode,
    retry_after_ms: int | None,
) -> None:
    service = _service(db_engine, provider=ConfigurableProvider(code, retry_after_ms=retry_after_ms))
    handle = await service.move_rack(
        f"retry-{code.value}-{retry_after_ms}",
        _caller(),
        f"rack-retry-{code.value}-{retry_after_ms}",
        RackPosition("A"),
        RackPosition("B"),
    )

    assert await service.submit_pending_tasks(1) == 1
    task = await _load_task(db_engine, handle.transport_task_id)
    assert task.status == "PENDING"
    assert task.send_started_at is None
    assert task.next_submit_at == task.updated_at + timedelta(seconds=2)


@pytest.mark.asyncio
async def test_timeout_is_unknown_and_never_retried(db_engine: object) -> None:
    provider = ConfigurableProvider(error=TimeoutError())
    service = _service(db_engine, provider=provider)
    handle = await service.move_rack(
        "timeout-request",
        _caller(),
        "rack-timeout",
        RackPosition("A"),
        RackPosition("B"),
    )

    assert await service.submit_pending_tasks(1) == 1
    assert (await _load_task(db_engine, handle.transport_task_id)).status == "RECONCILING"
    assert await service.submit_pending_tasks(1) == 0
    assert provider.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("task_id", "operation"),
    [("missing-task", RESULT_OPERATION), ("existing", "transport.task.unsupported@v1")],
)
async def test_unmatched_or_unsupported_evidence_is_retained_as_conflict(
    db_engine: object,
    task_id: str,
    operation: str,
) -> None:
    service = _service(db_engine)
    if task_id == "existing":
        task_id = (
            await service.move_rack(
                "evidence-task",
                _caller(),
                "rack-evidence",
                RackPosition("A"),
                RackPosition("B"),
            )
        ).transport_task_id
    await service.record_evidence(
        event_id=f"event-{operation}",
        transport_task_id=task_id,
        operation=operation,
        payload={"kind": "RACK_MOVE", "results": []},
    )

    assert await service.process_pending_evidence(1) == 1
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as db:
        evidence = await db.scalar(select(TransportEvidence).where(TransportEvidence.transport_task_id == task_id))
    assert evidence is not None
    assert evidence.status == "CONFLICT"


@pytest.mark.asyncio
async def test_failed_publish_is_reclaimed_after_lease_expiry(db_engine: object) -> None:
    publisher = RecordingPublisher(fail_once=True)
    service = _service(db_engine, publisher=publisher)
    handle = await service.move_rack(
        "publish-request",
        _caller(),
        "rack-publish",
        RackPosition("A"),
        RackPosition("B"),
    )
    service.provider.code = TransportSubmitCode.REJECTED
    await service.submit_pending_tasks(1)

    assert await service.publish_pending_outcomes(1) == 0

    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions.begin() as db:
        await db.execute(
            update(TransportTask)
            .where(TransportTask.transport_task_id == handle.transport_task_id)
            .values(outcome_claim_until=timezone.now_for_db() - timedelta(seconds=1))
        )

    assert await service.publish_pending_outcomes(1) == 1
    task = await _load_task(db_engine, handle.transport_task_id)
    assert task.published_outcome_version == task.outcome_version == 1
    assert [outcome.outcome_version for outcome in publisher.outcomes] == [1]


@pytest.mark.asyncio
async def test_failed_publish_does_not_starve_later_outcomes(db_engine: object) -> None:
    publisher = RecordingPublisher(fail_once=True)
    service = _service(db_engine, publisher=publisher)
    for ordinal in range(2):
        await service.move_rack(
            f"publish-error-{ordinal}",
            _caller(),
            f"rack-publish-error-{ordinal}",
            RackPosition("A"),
            RackPosition("B"),
        )
    service.provider.code = TransportSubmitCode.REJECTED
    assert await service.submit_pending_tasks(2) == 2

    assert await service.publish_pending_outcomes(2) == 1
    assert len(publisher.outcomes) == 1


@pytest.mark.asyncio
async def test_timed_out_publish_does_not_block_later_outcomes_or_mark_success(
    db_engine: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = TimeoutOncePublisher()
    service = _service(db_engine, publisher=publisher)
    handles = []
    for ordinal in range(2):
        handles.append(
            await service.move_rack(
                f"publish-timeout-{ordinal}",
                _caller(),
                f"rack-publish-timeout-{ordinal}",
                RackPosition("A"),
                RackPosition("B"),
            )
        )
    service.provider.code = TransportSubmitCode.REJECTED
    assert await service.submit_pending_tasks(2) == 2
    monkeypatch.setattr("src.app.transport.service._PUBLISH_TIMEOUT_SECONDS", 0.01, raising=False)

    assert await asyncio.wait_for(service.publish_pending_outcomes(2), timeout=0.2) == 1
    tasks = [await _load_task(db_engine, handle.transport_task_id) for handle in handles]

    assert publisher.calls == 2
    assert len(publisher.outcomes) == 1
    assert sorted(task.published_outcome_version for task in tasks) == [0, 1]


@pytest.mark.asyncio
async def test_known_partial_failure_forms_failed_outcome_and_releases_resources(db_engine: object) -> None:
    publisher = RecordingPublisher()
    service = _service(db_engine, publisher=publisher)
    handle = await service.move_bins(
        "partial-failure",
        _caller(),
        (
            BinMove("bin-success", RackBinSlot("rack-partial", "1"), HandoffPosition("ROLLER_IN")),
            BinMove("bin-failed", RackBinSlot("rack-partial", "2"), HandoffPosition("ROLLER_OUT")),
        ),
    )
    payload = {
        "kind": "BIN_MOVE",
        "results": [
            {
                "object_id": "bin-success",
                "status": "SUCCEEDED",
                "final_position": {"kind": "HANDOFF_POSITION", "location_code": "ROLLER_IN"},
            },
            {
                "object_id": "bin-failed",
                "status": "FAILED",
                "final_position": {"kind": "RACK_BIN_SLOT", "rack_id": "rack-partial", "slot_id": "2"},
                "failure_code": "CTU_PICK_FAILED",
            },
        ],
    }
    await service.record_evidence(
        event_id="partial-failure-result",
        transport_task_id=handle.transport_task_id,
        operation=RESULT_OPERATION,
        payload=payload,
    )

    assert await service.process_pending_evidence(1) == 1
    assert await service.publish_pending_outcomes(1) == 1
    task = await _load_task(db_engine, handle.transport_task_id)
    assert (task.status, task.reason_code) == ("FAILED", "CTU_PICK_FAILED")
    assert publisher.outcomes[0].status.value == "FAILED"
