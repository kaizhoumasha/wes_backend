"""Transport 计划中容易被快乐路径遗漏的可靠性验收。"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.app.execution.models import PositionProjection
from src.app.transport.contracts import (
    TRANSPORT_DEBUG_CALLER_WORKLINE_ID,
    BinExchangePair,
    BinMove,
    HandoffPosition,
    MoveBinsRequest,
    MoveRackRequest,
    RackBinSlot,
    RackPosition,
    RackReference,
    RcsTemplateId,
    TransportCaller,
    TransportContractError,
    TransportOutcome,
    TransportSubmitCode,
    TransportSubmitResult,
    ZonePosition,
)
from src.app.transport.models import (
    TransportCallbackReceipt,
    TransportDebugPositionProjection,
    TransportEvidence,
    TransportMember,
    TransportTask,
)
from src.app.transport.repository import TransportRepository
from src.app.transport.service import TransportService
from src.app.wms_adapter.transport_wire import RESULT_OPERATION, validate_callback_envelope
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone
from tests.support.sqlmodel_metadata import register_required_sqlmodel_metadata
from tests.support.transport_callbacks import record_valid_callback
from tests.support.transport_projections import confirm_rack_faces, ensure_projection_authority

register_required_sqlmodel_metadata()


class ConfigurableProvider:
    def __init__(
        self,
        code: TransportSubmitCode = TransportSubmitCode.RECEIVED,
        *,
        error: BaseException | None = None,
    ) -> None:
        self.code = code
        self.error = error
        self.calls = 0

    async def submit(
        self,
        *,
        operation_id: str,
        transport_task_id: str,
        request_body: bytes,
        request_body_digest: str,
        observation: object = None,
    ) -> TransportSubmitResult:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return TransportSubmitResult(self.code, transport_task_id)


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


class FailOnceOutcomeBookkeepingRepository(TransportRepository):
    def __init__(self) -> None:
        self.fail_bookkeeping = False

    async def get_task(
        self,
        db: AsyncSession,
        transport_task_id: str,
        *,
        for_update: bool = False,
    ) -> TransportTask | None:
        if self.fail_bookkeeping:
            self.fail_bookkeeping = False
            raise RuntimeError("simulated crash before outcome bookkeeping")
        return await super().get_task(db, transport_task_id, for_update=for_update)


class BlockedOutcomeBookkeepingRepository(TransportRepository):
    def __init__(self) -> None:
        self.block_bookkeeping = False
        self.before_bookkeeping = asyncio.Event()
        self.release = asyncio.Event()

    async def get_task(
        self,
        db: AsyncSession,
        transport_task_id: str,
        *,
        for_update: bool = False,
    ) -> TransportTask | None:
        if self.block_bookkeeping:
            self.block_bookkeeping = False
            self.before_bookkeeping.set()
            await self.release.wait()
        return await super().get_task(db, transport_task_id, for_update=for_update)


@pytest_asyncio.fixture(autouse=True)
async def _clean_transport_tables(db_engine: object) -> None:
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions.begin() as db:
        for model in (
            TransportDebugPositionProjection,
            TransportEvidence,
            TransportCallbackReceipt,
            TransportMember,
            PositionProjection,
            TransportTask,
        ):
            await db.execute(delete(model))


def _service(
    db_engine: object,
    *,
    provider: ConfigurableProvider | None = None,
) -> TransportService:
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    return TransportService(
        sessions,
        TransportRepository(),
        provider or ConfigurableProvider(),
        result_timeout=timedelta(seconds=420),
    )


def _caller() -> TransportCaller:
    return TransportCaller("SORTER", "STATION_A")


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
    args = (0, RecordingPublisher()) if entry_name == "publish_pending_outcomes" else (0,)

    with pytest.raises(ValueError, match="positive integer"):
        await getattr(service, entry_name)(*args)


@pytest.mark.asyncio
async def test_rotate_uses_explicit_position_without_projection_admission(db_engine: object) -> None:
    service = _service(db_engine)

    await service.rotate_rack(new_uuid7(), _caller(), "rack-rotate", RackPosition("ROTATE"), "270")

    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions.begin() as db:
        workline_id = await ensure_projection_authority(db)
        db.add(
            PositionProjection(
                object_type="RACK",
                object_id="rack-rotate",
                workline_id=workline_id,
                position_json={"kind": "RACK_POSITION", "location_code": "ROTATE"},
                arrival_face="90",
                source_operation_id="seed",
                source_transport_task_id="seed",
                updated_at=timezone.now_for_db(),
            )
        )

    await service.rotate_rack(new_uuid7(), _caller(), "rack-rotate", RackPosition("OTHER"), "270")

    await service.rotate_rack(new_uuid7(), _caller(), "rack-rotate", RackPosition("ROTATE"), "90")


@pytest.mark.asyncio
async def test_auto_debug_successor_rejects_a_rack_on_the_wrong_face(db_engine: object) -> None:
    service = _service(db_engine)
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions.begin() as db:
        db.add(
            TransportDebugPositionProjection(
                object_type="RACK",
                object_id="rack-auto-face-drift",
                position_json={"kind": "RACK_POSITION", "location_code": "KT16"},
                position_unknown=False,
                arrival_face="270",
                source_operation_id="seed",
                source_transport_task_id="seed",
                updated_at=timezone.now_for_db(),
            )
        )

    async with sessions.begin() as db:
        with pytest.raises(TransportContractError, match="position or face does not match"):
            await service.assert_debug_rack_position_in_session(
                db,
                "rack-auto-face-drift",
                RackPosition("KT16"),
                "90",
            )


@pytest.mark.asyncio
async def test_debug_rotate_retains_individual_facts_without_guessing_cross_task_order(db_engine: object) -> None:
    service = _service(db_engine)
    caller = TransportCaller(TRANSPORT_DEBUG_CALLER_WORKLINE_ID, "STATION-DEBUG")
    move = await service.move_rack(
        new_uuid7(),
        caller,
        "rack-debug-projection",
        RackReference("rack-debug-projection"),
        RackPosition("KT19"),
        "90",
        RcsTemplateId.CTU01,
    )
    await record_valid_callback(
        service,
        operation_id="debug-rack-move-result",
        transport_task_id=move.transport_task_id,
        operation=RESULT_OPERATION,
        timestamp=1,
        payload={
            "kind": "RACK_MOVE",
            "outcome_revision": 1,
            "rack_id": "rack-debug-projection",
            "status": "SUCCEEDED",
            "final_position": {"kind": "RACK_POSITION", "location_code": "KT19"},
            "arrival_face": "90",
        },
    )
    assert await service.process_pending_evidence(1) == 1

    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as db:
        debug_projection = await db.scalar(
            select(TransportDebugPositionProjection).where(
                TransportDebugPositionProjection.object_type == "RACK",
                TransportDebugPositionProjection.object_id == "rack-debug-projection",
            )
        )
        assert debug_projection is not None
        assert debug_projection.position_json == {"kind": "RACK_POSITION", "location_code": "KT19"}
        assert debug_projection.arrival_face == "90"
        assert debug_projection.source_transport_task_id == move.transport_task_id

    rotate = await service.rotate_rack_for_debug(
        new_uuid7(),
        caller,
        "rack-debug-projection",
        RackPosition("KT19"),
        "270",
    )

    task = await _load_task(db_engine, rotate.transport_task_id)
    assert task.request_json["position"] == {"kind": "RACK_POSITION", "location_code": "KT19"}
    assert task.submit_request_body is not None
    wire = json.loads(task.submit_request_body)
    assert wire["data"]["source"] == {"kind": "RACK_POSITION", "location_code": "KT19"}
    assert wire["data"]["target"] == {"kind": "RACK_POSITION", "location_code": "KT19"}
    assert task.submit_request_body_digest == hashlib.sha256(task.submit_request_body.encode()).hexdigest()
    async with sessions() as db:
        member = await db.scalar(
            select(TransportMember).where(TransportMember.transport_task_id == rotate.transport_task_id)
        )
    assert member is not None
    assert member.source_json == {"kind": "RACK_POSITION", "location_code": "KT19"}
    assert member.target_json == {"kind": "RACK_POSITION", "location_code": "KT19"}
    await record_valid_callback(
        service,
        operation_id="debug-rack-rotate-result",
        transport_task_id=rotate.transport_task_id,
        operation=RESULT_OPERATION,
        timestamp=2,
        payload={
            "kind": "RACK_ROTATE",
            "outcome_revision": 1,
            "rack_id": "rack-debug-projection",
            "status": "SUCCEEDED",
            "final_position": {"kind": "RACK_POSITION", "location_code": "KT19"},
            "arrival_face": "270",
        },
    )
    assert await service.process_pending_evidence(1) == 1
    async with sessions() as db:
        debug_projection = await db.scalar(
            select(TransportDebugPositionProjection).where(
                TransportDebugPositionProjection.object_type == "RACK",
                TransportDebugPositionProjection.object_id == "rack-debug-projection",
            )
        )
        assert debug_projection is not None
        assert debug_projection.arrival_face == "90"
        assert debug_projection.source_transport_task_id == move.transport_task_id
        assert debug_projection.position_unknown is True
        assert (await _load_task(db_engine, rotate.transport_task_id)).outcome_json["members"][0][
            "arrival_face"
        ] == "270"
        assert (
            await db.scalar(select(PositionProjection).where(PositionProjection.object_id == "rack-debug-projection"))
            is None
        )


@pytest.mark.asyncio
async def test_debug_rotate_rejects_reference_even_when_projection_exists(db_engine: object) -> None:
    service = _service(db_engine)
    caller = TransportCaller(TRANSPORT_DEBUG_CALLER_WORKLINE_ID, "STATION-DEBUG")
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions.begin() as db:
        db.add(
            TransportDebugPositionProjection(
                object_type="RACK",
                object_id="rack-debug-unknown-position",
                position_json={"kind": "RACK", "location_code": "rack-debug-unknown-position"},
                arrival_face="90",
                source_operation_id="seed",
                source_transport_task_id="seed",
                updated_at=timezone.now_for_db(),
            )
        )

    with pytest.raises(TransportContractError, match="explicit rack position"):
        await service.rotate_rack_for_debug(
            new_uuid7(),
            caller,
            "rack-debug-unknown-position",
            RackReference("rack-debug-unknown-position"),
            "270",
        )


@pytest.mark.asyncio
async def test_debug_rotate_rejects_success_at_a_different_requested_position(db_engine: object) -> None:
    service = _service(db_engine)
    caller = TransportCaller(TRANSPORT_DEBUG_CALLER_WORKLINE_ID, "STATION-DEBUG")
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions.begin() as db:
        db.add(
            TransportDebugPositionProjection(
                object_type="RACK",
                object_id="rack-debug-wrong-station",
                position_json={"kind": "RACK_POSITION", "location_code": "KT16"},
                position_unknown=False,
                arrival_face="90",
                source_operation_id="seed",
                source_transport_task_id="seed",
                updated_at=timezone.now_for_db(),
            )
        )

    rotate = await service.rotate_rack_for_debug(
        new_uuid7(),
        caller,
        "rack-debug-wrong-station",
        RackPosition("KT16"),
        "270",
    )
    await record_valid_callback(
        service,
        operation_id="debug-rack-wrong-station-result",
        transport_task_id=rotate.transport_task_id,
        operation=RESULT_OPERATION,
        timestamp=2,
        payload={
            "kind": "RACK_ROTATE",
            "outcome_revision": 1,
            "rack_id": "rack-debug-wrong-station",
            "status": "SUCCEEDED",
            "final_position": {"kind": "RACK_POSITION", "location_code": "OTHER"},
            "arrival_face": "270",
        },
    )

    assert await service.process_pending_evidence(1) == 1
    task = await _load_task(db_engine, rotate.transport_task_id)
    assert task.status == "RECONCILING"
    assert task.reason_code == "TRANSPORT_EVIDENCE_CONFLICT"
    async with sessions() as db:
        projection = await db.scalar(
            select(TransportDebugPositionProjection).where(
                TransportDebugPositionProjection.object_type == "RACK",
                TransportDebugPositionProjection.object_id == "rack-debug-wrong-station",
            )
        )
    assert projection is not None
    assert projection.position_json == {"kind": "RACK_POSITION", "location_code": "KT16"}
    assert projection.arrival_face == "90"


@pytest.mark.asyncio
async def test_create_debug_task_in_session_persists_inside_the_callers_transaction(db_engine: object) -> None:
    service = _service(db_engine)
    caller = TransportCaller(TRANSPORT_DEBUG_CALLER_WORKLINE_ID, "STATION-DEBUG")
    request = MoveRackRequest(
        new_uuid7(),
        caller,
        "rack-debug",
        RackReference("rack-debug"),
        RackPosition("KT16"),
        "90",
        RcsTemplateId.CTU01,
    )
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async with sessions.begin() as db:
        handle = await service.create_debug_task_in_session(db, request)
        task = await db.scalar(select(TransportTask).where(TransportTask.transport_task_id == handle.transport_task_id))

    assert task is not None
    assert task.client_request_id == request.client_request_id


@pytest.mark.asyncio
async def test_create_debug_task_in_session_rejects_non_debug_caller(db_engine: object) -> None:
    service = _service(db_engine)
    request = MoveRackRequest(
        new_uuid7(),
        TransportCaller("SORTER", "STATION-01"),
        "rack-1",
        RackReference("rack-1"),
        RackPosition("KT16"),
        "90",
        RcsTemplateId.CTU01,
    )
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async with sessions.begin() as db:
        with pytest.raises(TransportContractError, match="debug task requires TRANSPORT_DEBUG caller"):
            await service.create_debug_task_in_session(db, request)


@pytest.mark.asyncio
async def test_create_debug_task_in_session_rejects_bin_move_for_stale_rack_face(db_engine: object) -> None:
    service = _service(db_engine)
    now = timezone.now_for_db()
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions.begin() as db:
        db.add(
            TransportDebugPositionProjection(
                object_type="RACK",
                object_id="rack-auto-return",
                position_json={"kind": "RACK_POSITION", "location_code": "KT16"},
                position_unknown=False,
                arrival_face="270",
                source_operation_id=new_uuid7(),
                source_transport_task_id="transport-prior-rotate",
                updated_at=now,
            )
        )

    request = MoveBinsRequest(
        new_uuid7(),
        TransportCaller(TRANSPORT_DEBUG_CALLER_WORKLINE_ID, "TRANSPORT_DEBUG_AUTO"),
        (
            BinMove(
                "bin-auto-return",
                HandoffPosition("CNV0302"),
                RackBinSlot("rack-auto-return", "90", "SLOT-1"),
            ),
        ),
    )
    async with sessions.begin() as db:
        await service.create_debug_task_in_session(db, request)


@pytest.mark.asyncio
async def test_debug_bin_exchange_preserves_requested_faces(db_engine: object) -> None:
    service = _service(db_engine)
    caller = TransportCaller(TRANSPORT_DEBUG_CALLER_WORKLINE_ID, "STATION-DEBUG")
    for rack_id, face in (("rack-debug-left", "90"), ("rack-debug-right", "270")):
        move = await service.move_rack(
            new_uuid7(),
            caller,
            rack_id,
            RackReference(rack_id),
            RackPosition(f"{rack_id}-position"),
            face,
            RcsTemplateId.CTU01,
        )
        await record_valid_callback(
            service,
            operation_id=f"{rack_id}-result",
            transport_task_id=move.transport_task_id,
            operation=RESULT_OPERATION,
            timestamp=1,
            payload={
                "kind": "RACK_MOVE",
                "outcome_revision": 1,
                "rack_id": rack_id,
                "status": "SUCCEEDED",
                "final_position": {"kind": "RACK_POSITION", "location_code": f"{rack_id}-position"},
                "arrival_face": face,
            },
        )
    assert await service.process_pending_evidence(2) == 2

    exchange = await service.exchange_bins_for_debug(
        new_uuid7(),
        caller,
        (
            BinExchangePair(
                "bin-debug-left",
                RackBinSlot("rack-debug-left", "90", "LEFT-1"),
                "bin-debug-right",
                RackBinSlot("rack-debug-right", "270", "RIGHT-1"),
            ),
        ),
    )

    assert (await _load_task(db_engine, exchange.transport_task_id)).kind == "BIN_EXCHANGE"


@pytest.mark.asyncio
async def test_debug_rotate_and_exchange_reject_non_debug_caller(db_engine: object) -> None:
    service = _service(db_engine)
    caller = TransportCaller("SORTER", "STATION-01")

    with pytest.raises(TransportContractError, match="debug rack rotation requires TRANSPORT_DEBUG caller"):
        await service.rotate_rack_for_debug(
            new_uuid7(),
            caller,
            "rack-debug",
            RackPosition("KT19"),
            "270",
        )
    with pytest.raises(TransportContractError, match="debug bin exchange requires TRANSPORT_DEBUG caller"):
        await service.exchange_bins_for_debug(
            new_uuid7(),
            caller,
            (
                BinExchangePair(
                    "bin-left",
                    RackBinSlot("rack-left", "90", "LEFT-1"),
                    "bin-right",
                    RackBinSlot("rack-right", "270", "RIGHT-1"),
                ),
            ),
        )


@pytest.mark.asyncio
async def test_debug_bin_move_uses_frozen_request_face_without_business_projection(db_engine: object) -> None:
    service = _service(db_engine)
    moves = (
        BinMove(
            "bin-debug",
            RackBinSlot("rack-debug", "90", "A1"),
            HandoffPosition("CNV0301"),
        ),
    )

    await service.move_bins(new_uuid7(), TransportCaller("SORTER", "CTU01"), moves)
    with pytest.raises(TransportContractError, match="requires TRANSPORT_DEBUG caller"):
        await service.move_bins_for_debug(new_uuid7(), TransportCaller("SORTER", "CTU01"), moves)

    handle = await service.move_bins_for_debug(
        new_uuid7(),
        TransportCaller(TRANSPORT_DEBUG_CALLER_WORKLINE_ID, "CTU01"),
        moves,
    )
    task = await _load_task(db_engine, handle.transport_task_id)

    assert task.request_json["moves"][0]["source"]["rack_face"] == "90"
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as db:
        assert await db.scalar(select(PositionProjection).where(PositionProjection.object_id == "rack-debug")) is None


@pytest.mark.asyncio
async def test_bin_move_uses_requested_face_despite_stale_projection(db_engine: object) -> None:
    service = _service(db_engine)
    move_on_face_a = (BinMove("bin-face", RackBinSlot("rack-face", "90", "1"), HandoffPosition("ROLLER_IN")),)

    await service.move_bins(new_uuid7(), _caller(), move_on_face_a)

    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions.begin() as db:
        workline_id = await ensure_projection_authority(db)
        db.add(
            PositionProjection(
                object_type="RACK",
                object_id="rack-face",
                workline_id=workline_id,
                position_json={"kind": "RACK_POSITION", "location_code": "STORAGE"},
                arrival_face="270",
                source_operation_id="seed",
                source_transport_task_id="seed",
                updated_at=timezone.now_for_db(),
            )
        )

    await service.move_bins(new_uuid7(), _caller(), move_on_face_a)

    async with sessions.begin() as db:
        await db.execute(
            update(PositionProjection).where(PositionProjection.object_id == "rack-face").values(arrival_face="90")
        )

    handle = await service.move_bins(new_uuid7(), _caller(), move_on_face_a)
    assert handle.transport_task_id.startswith("transport-")


@pytest.mark.asyncio
async def test_bin_exchange_uses_requested_faces_without_projection(db_engine: object) -> None:
    service = _service(db_engine)
    exchange_pairs = (
        BinExchangePair(
            "bin-left",
            RackBinSlot("rack-left", "90", "1"),
            "bin-right",
            RackBinSlot("rack-right", "270", "1"),
        ),
    )

    await service.exchange_bins(new_uuid7(), _caller(), exchange_pairs)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "expected_status", "expected_reason"),
    [
        (TransportSubmitCode.DUPLICATE, "ACCEPTED", None),
        (TransportSubmitCode.REJECTED, "REJECTED", "TRANSPORT_REJECTED"),
        (TransportSubmitCode.CONFLICT, "RECONCILING", "TRANSPORT_SUBMIT_CONFLICT"),
    ],
)
async def test_submit_ack_terminal_matrix(
    db_engine: object,
    code: TransportSubmitCode,
    expected_status: str,
    expected_reason: str | None,
) -> None:
    service = _service(db_engine, provider=ConfigurableProvider(code))
    handle = await service.move_rack(
        new_uuid7(),
        _caller(),
        f"rack-{code.value}",
        RackPosition("A"),
        RackPosition("B"),
        "90",
    )

    assert await service.submit_pending_tasks(1) == 1
    task = await _load_task(db_engine, handle.transport_task_id)
    assert (task.status, task.reason_code) == (expected_status, expected_reason)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code",
    [
        TransportSubmitCode.NOT_SENT,
        TransportSubmitCode.UNAVAILABLE,
    ],
)
async def test_confirmed_retryable_results_clear_send_marker_and_use_fixed_delay(
    db_engine: object,
    code: TransportSubmitCode,
) -> None:
    service = _service(db_engine, provider=ConfigurableProvider(code))
    handle = await service.move_rack(
        new_uuid7(),
        _caller(),
        f"rack-retry-{code.value}",
        RackPosition("A"),
        RackPosition("B"),
        "90",
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
        new_uuid7(),
        _caller(),
        "rack-timeout",
        RackPosition("A"),
        RackPosition("B"),
        "90",
    )

    assert await service.submit_pending_tasks(1) == 1
    task = await _load_task(db_engine, handle.transport_task_id)
    assert task.status == "PENDING"
    assert task.reason_code == "SUBMIT_DELIVERY_UNKNOWN"
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
                new_uuid7(),
                _caller(),
                "rack-evidence",
                RackPosition("A"),
                RackPosition("B"),
                "90",
            )
        ).transport_task_id
    await record_valid_callback(
        service,
        operation_id=f"event-{operation}",
        transport_task_id=task_id,
        operation=operation,
        timestamp=1,
        payload={
            "kind": "RACK_MOVE",
            "outcome_revision": 1,
            "rack_id": "rack-evidence",
            "status": "SUCCEEDED",
            "final_position": {"kind": "RACK_POSITION", "location_code": "B"},
            "arrival_face": "90",
        },
    )

    assert await service.process_pending_evidence(1) == int(task_id != "missing-task")
    assert await service.process_pending_evidence(1) == 0
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as db:
        evidence = await db.scalar(select(TransportEvidence).where(TransportEvidence.transport_task_id == task_id))
    assert evidence is not None
    assert evidence.status == "CONFLICT"


@pytest.mark.asyncio
async def test_conflicting_callback_replay_persists_a_durable_receipt_conflict(db_engine: object) -> None:
    service = _service(db_engine)
    handle = await service.move_rack(
        new_uuid7(),
        _caller(),
        "rack-callback-conflict",
        RackPosition("A"),
        RackPosition("B"),
        "90",
    )
    operation_id = new_uuid7()
    payload = {
        "kind": "RACK_MOVE",
        "outcome_revision": 1,
        "rack_id": "rack-callback-conflict",
        "status": "SUCCEEDED",
        "final_position": {"kind": "RACK_POSITION", "location_code": "B"},
        "arrival_face": "90",
    }

    first = await record_valid_callback(
        service,
        operation_id=operation_id,
        transport_task_id=handle.transport_task_id,
        operation=RESULT_OPERATION,
        timestamp=1,
        payload=payload,
    )
    conflicting = await record_valid_callback(
        service,
        operation_id=operation_id,
        transport_task_id=handle.transport_task_id,
        operation=RESULT_OPERATION,
        timestamp=1,
        payload={
            **payload,
            "final_position": {"kind": "RACK_POSITION", "location_code": "OTHER"},
        },
    )
    original_replay = await record_valid_callback(
        service,
        operation_id=operation_id,
        transport_task_id=handle.transport_task_id,
        operation=RESULT_OPERATION,
        timestamp=1,
        payload=payload,
    )

    assert first["code"] == "RECEIVED"
    assert conflicting["code"] == "CONFLICT"
    assert original_replay["code"] == "DUPLICATE"
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as db:
        receipt = await db.scalar(
            select(TransportCallbackReceipt).where(
                TransportCallbackReceipt.operation == RESULT_OPERATION,
                TransportCallbackReceipt.operation_id == operation_id,
            )
        )
    assert receipt is not None
    assert receipt.response_http_status == 202
    assert receipt.response_code == "RECEIVED"
    assert receipt.response_data_json == {"transport_task_id": handle.transport_task_id}
    assert receipt.conflict_code == "TRANSPORT_CALLBACK_IDENTITY_CONFLICT"
    assert receipt.conflict_detected_at is not None
    async with sessions() as db:
        evidence = await db.scalar(
            select(TransportEvidence).where(
                TransportEvidence.operation == RESULT_OPERATION,
                TransportEvidence.operation_id == operation_id,
            )
        )
    assert evidence is not None
    assert evidence.status == "PENDING"
    assert evidence.conflict_code is None


@pytest.mark.asyncio
async def test_locked_evidence_read_refreshes_a_stale_identity_map_entry(db_engine: object) -> None:
    service = _service(db_engine)
    handle = await service.move_rack(
        new_uuid7(),
        _caller(),
        "rack-evidence-refresh",
        RackPosition("A"),
        RackPosition("B"),
        "90",
    )
    await record_valid_callback(
        service,
        operation_id=new_uuid7(),
        transport_task_id=handle.transport_task_id,
        operation=RESULT_OPERATION,
        timestamp=1,
        payload={
            "kind": "RACK_MOVE",
            "outcome_revision": 1,
            "rack_id": "rack-evidence-refresh",
            "status": "SUCCEEDED",
            "final_position": {"kind": "RACK_POSITION", "location_code": "B"},
            "arrival_face": "90",
        },
    )

    repository = TransportRepository()
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions.begin() as db:
        candidate = await db.scalar(
            select(TransportEvidence).where(TransportEvidence.transport_task_id == handle.transport_task_id)
        )
        assert candidate is not None
        await db.execute(
            update(TransportEvidence)
            .where(TransportEvidence.id == candidate.id)
            .values(status="CONFLICT", claim_token=None, conflict_code="TRANSPORT_CALLBACK_IDENTITY_CONFLICT")
            .execution_options(synchronize_session=False)
        )

        locked = await repository.get_evidence(db, candidate.id, for_update=True)

    assert locked is candidate
    assert locked.status == "CONFLICT"
    assert locked.claim_token is None


@pytest.mark.asyncio
async def test_failed_publish_is_reclaimed_after_lease_expiry(db_engine: object) -> None:
    publisher = RecordingPublisher(fail_once=True)
    service = _service(db_engine)
    handle = await service.move_rack(
        new_uuid7(),
        _caller(),
        "rack-publish",
        RackPosition("A"),
        RackPosition("B"),
        "90",
    )
    service.provider.code = TransportSubmitCode.REJECTED
    await service.submit_pending_tasks(1)

    assert await service.publish_pending_outcomes(1, publisher) == 0

    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions.begin() as db:
        await db.execute(
            update(TransportTask)
            .where(TransportTask.transport_task_id == handle.transport_task_id)
            .values(outcome_claim_until=timezone.now_for_db() - timedelta(seconds=1))
        )

    assert await service.publish_pending_outcomes(1, publisher) == 1
    task = await _load_task(db_engine, handle.transport_task_id)
    assert task.published_outcome_version == task.outcome_version == 1
    assert [outcome.outcome_version for outcome in publisher.outcomes] == [1]


@pytest.mark.asyncio
async def test_publish_success_before_bookkeeping_crash_is_retried_with_same_version(db_engine: object) -> None:
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    repository = FailOnceOutcomeBookkeepingRepository()
    publisher = RecordingPublisher()
    provider = ConfigurableProvider(TransportSubmitCode.REJECTED)
    service = TransportService(sessions, repository, provider, result_timeout=timedelta(seconds=420))
    handle = await service.move_rack(
        new_uuid7(),
        _caller(),
        "rack-publish-bookkeeping-crash",
        RackPosition("A"),
        RackPosition("B"),
        "90",
    )
    await service.submit_pending_tasks(1)
    repository.fail_bookkeeping = True

    with pytest.raises(RuntimeError, match="before outcome bookkeeping"):
        await service.publish_pending_outcomes(1, publisher)

    async with sessions.begin() as db:
        await db.execute(
            update(TransportTask)
            .where(TransportTask.transport_task_id == handle.transport_task_id)
            .values(outcome_claim_until=timezone.now_for_db() - timedelta(seconds=1))
        )
    assert await service.publish_pending_outcomes(1, publisher) == 1
    task = await _load_task(db_engine, handle.transport_task_id)
    assert task.published_outcome_version == task.outcome_version == 1
    assert [outcome.outcome_version for outcome in publisher.outcomes] == [1, 1]


@pytest.mark.asyncio
async def test_stale_outcome_worker_cannot_bookkeep_over_a_newer_claimed_version(db_engine: object) -> None:
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    blocked_repository = BlockedOutcomeBookkeepingRepository()
    publisher = RecordingPublisher()
    provider = ConfigurableProvider(TransportSubmitCode.CONFLICT)
    stale_service = TransportService(sessions, blocked_repository, provider, result_timeout=timedelta(seconds=420))
    winner_service = TransportService(sessions, TransportRepository(), provider, result_timeout=timedelta(seconds=420))
    handle = await stale_service.move_rack(
        new_uuid7(),
        _caller(),
        "rack-publish-stale-token",
        RackPosition("A"),
        RackPosition("B"),
        "90",
    )
    await stale_service.submit_pending_tasks(1)
    blocked_repository.block_bookkeeping = True
    stale_publish = asyncio.create_task(stale_service.publish_pending_outcomes(1, publisher))
    await blocked_repository.before_bookkeeping.wait()
    operation_id = "operation-publish-stale-token-result"
    result = {
        "transport_task_id": handle.transport_task_id,
        "kind": "RACK_MOVE",
        "outcome_revision": 1,
        "rack_id": "rack-publish-stale-token",
        "status": "SUCCEEDED",
        "final_position": {"kind": "RACK_POSITION", "location_code": "B"},
        "arrival_face": "90",
    }

    try:
        await record_valid_callback(
            winner_service,
            operation_id=operation_id,
            transport_task_id=handle.transport_task_id,
            operation=RESULT_OPERATION,
            timestamp=1,
            payload=result,
        )
        assert await winner_service.process_pending_evidence(1) == 1
        async with sessions.begin() as db:
            await db.execute(
                update(TransportTask)
                .where(TransportTask.transport_task_id == handle.transport_task_id)
                .values(outcome_claim_until=timezone.now_for_db() - timedelta(seconds=1))
            )
        assert await winner_service.publish_pending_outcomes(1, publisher) == 1
        blocked_repository.release.set()
        assert await stale_publish == 0
    finally:
        blocked_repository.release.set()
        await asyncio.gather(stale_publish, return_exceptions=True)

    task = await _load_task(db_engine, handle.transport_task_id)
    assert task.published_outcome_version == task.outcome_version == 2
    assert [outcome.outcome_version for outcome in publisher.outcomes] == [1, 2]


@pytest.mark.asyncio
async def test_failed_publish_does_not_starve_later_outcomes(db_engine: object) -> None:
    publisher = RecordingPublisher(fail_once=True)
    service = _service(db_engine)
    for ordinal in range(2):
        await service.move_rack(
            new_uuid7(),
            _caller(),
            f"rack-publish-error-{ordinal}",
            RackPosition("A"),
            RackPosition("B"),
            "90",
        )
    service.provider.code = TransportSubmitCode.REJECTED
    assert await service.submit_pending_tasks(2) == 2

    assert await service.publish_pending_outcomes(2, publisher) == 1
    assert len(publisher.outcomes) == 1


@pytest.mark.asyncio
async def test_timed_out_publish_does_not_block_later_outcomes_or_mark_success(
    db_engine: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = TimeoutOncePublisher()
    service = _service(db_engine)
    handles = []
    for ordinal in range(2):
        handles.append(
            await service.move_rack(
                new_uuid7(),
                _caller(),
                f"rack-publish-timeout-{ordinal}",
                RackPosition("A"),
                RackPosition("B"),
                "90",
            )
        )
    service.provider.code = TransportSubmitCode.REJECTED
    assert await service.submit_pending_tasks(2) == 2
    monkeypatch.setattr("src.app.transport.service._PUBLISH_TIMEOUT_SECONDS", 0.01, raising=False)

    assert await asyncio.wait_for(service.publish_pending_outcomes(2, publisher), timeout=0.2) == 1
    tasks = [await _load_task(db_engine, handle.transport_task_id) for handle in handles]

    assert publisher.calls == 2
    assert len(publisher.outcomes) == 1
    assert sorted(task.published_outcome_version for task in tasks) == [0, 1]


@pytest.mark.asyncio
async def test_known_partial_failure_forms_failed_outcome_with_member_facts(db_engine: object) -> None:
    publisher = RecordingPublisher()
    service = _service(db_engine)
    await confirm_rack_faces(db_engine, {"rack-partial": "90"})
    handle = await service.move_bins(
        new_uuid7(),
        _caller(),
        (
            BinMove("bin-success", RackBinSlot("rack-partial", "90", "1"), HandoffPosition("ROLLER_IN")),
            BinMove("bin-failed", RackBinSlot("rack-partial", "90", "2"), HandoffPosition("ROLLER_OUT")),
        ),
    )
    payload = {
        "kind": "BIN_MOVE",
        "outcome_revision": 1,
        "results": [
            {
                "container_id": "bin-failed",
                "status": "FAILED",
                "final_position": {
                    "kind": "RACK_BIN_SLOT",
                    "rack_id": "rack-partial",
                    "rack_face": "A",
                    "slot_id": "2",
                },
                "failure_code": "RCS_EXECUTION_FAILED",
            },
            {
                "container_id": "bin-success",
                "status": "SUCCEEDED",
                "final_position": {"kind": "HANDOFF_POSITION", "location_code": "ROLLER_IN"},
            },
        ],
    }
    await record_valid_callback(
        service,
        operation_id="partial-failure-result",
        transport_task_id=handle.transport_task_id,
        operation=RESULT_OPERATION,
        timestamp=1,
        payload=payload,
    )

    assert await service.process_pending_evidence(1) == 1
    assert await service.publish_pending_outcomes(1, publisher) == 1
    task = await _load_task(db_engine, handle.transport_task_id)
    assert (task.status, task.reason_code) == ("FAILED", "RCS_EXECUTION_FAILED")
    assert publisher.outcomes[0].status.value == "FAILED"


@pytest.mark.asyncio
async def test_cancelled_rack_result_maps_to_failed_with_final_position(db_engine: object) -> None:
    final_position = {"kind": "RACK_POSITION", "location_code": "KT16"}
    publisher = RecordingPublisher()
    service = _service(db_engine)
    handle = await service.move_rack(
        new_uuid7(),
        _caller(),
        "510028",
        RackPosition("WHE0710"),
        RackPosition("KT16"),
        "90",
    )
    payload = {
        "kind": "RACK_MOVE",
        "outcome_revision": 1,
        "rack_id": "510028",
        "status": "CANCELLED",
    }
    payload["final_position"] = final_position
    await record_valid_callback(
        service,
        operation_id=new_uuid7(),
        transport_task_id=handle.transport_task_id,
        operation=RESULT_OPERATION,
        timestamp=1,
        payload=payload,
    )

    assert await service.process_pending_evidence(1) == 1
    assert await service.publish_pending_outcomes(1, publisher) == 1
    task = await _load_task(db_engine, handle.transport_task_id)
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as db:
        member = await db.scalar(
            select(TransportMember).where(TransportMember.transport_task_id == handle.transport_task_id)
        )
        projection = await db.scalar(
            select(PositionProjection).where(
                PositionProjection.object_type == "RACK", PositionProjection.object_id == "510028"
            )
        )
    assert (task.status, task.reason_code, task.last_applied_wms_outcome_revision) == (
        "FAILED",
        "RCS_TASK_CANCELLED",
        1,
    )
    assert member is not None
    assert (member.status, member.final_position_json, member.position_unknown, member.failure_code) == (
        "FAILED",
        final_position,
        False,
        "RCS_TASK_CANCELLED",
    )
    assert projection is None
    assert publisher.outcomes[0].status.value == "FAILED"


@pytest.mark.asyncio
@pytest.mark.parametrize("face_fields", [{}, {"arrival_face": None}, {"arrival_face": ""}, {"arrival_face": "270"}])
@pytest.mark.parametrize("target_face", [None, "270"])
async def test_ctu03_optional_arrival_persists_actual_face(
    db_engine: object, face_fields: dict[str, str | None], target_face: str | None
) -> None:
    import json

    service = _service(db_engine)
    service._dispatch_gate = SimpleNamespace(is_task_dispatch_allowed=AsyncMock(return_value=True))
    handle = await service.move_rack(
        new_uuid7(),
        TransportCaller(TRANSPORT_DEBUG_CALLER_WORKLINE_ID),
        "rack-return-any-face",
        RackReference("rack-return-any-face"),
        ZonePosition("WH05"),
        rcs_template_id=RcsTemplateId.CTU03,
        target_face=target_face,
    )
    task = await _load_task(db_engine, handle.transport_task_id)
    assert json.loads(task.submit_request_body)["data"].get("target_face") == target_face
    assert await service.submit_pending_tasks(1) == 1
    await record_valid_callback(
        service,
        operation_id="ctu03-actual-arrival",
        transport_task_id=handle.transport_task_id,
        operation=RESULT_OPERATION,
        timestamp=1,
        payload=validate_callback_envelope(
            {
                "operation_id": new_uuid7(),
                "operation": RESULT_OPERATION,
                "timestamp": 1,
                "data": {
                    "transport_task_id": handle.transport_task_id,
                    "kind": "RACK_MOVE",
                    "outcome_revision": 1,
                    "rack_id": "rack-return-any-face",
                    "status": "SUCCEEDED",
                    "final_position": {"kind": "RACK_POSITION", "location_code": "STORAGE-17"},
                    **face_fields,
                },
            }
        )["data"],
    )
    assert await service.process_pending_evidence(1) == 1
    task = await _load_task(db_engine, handle.transport_task_id)
    assert task.status == "SUCCEEDED"
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as db:
        projection = await db.scalar(
            select(TransportDebugPositionProjection).where(
                TransportDebugPositionProjection.object_id == "rack-return-any-face"
            )
        )
    assert projection is not None
    expected_face = face_fields.get("arrival_face") or target_face
    assert projection.arrival_face == expected_face
    assert task.outcome_json["members"][0]["arrival_face"] == expected_face
    async with sessions() as db:
        evidence = await db.scalar(
            select(TransportEvidence).where(TransportEvidence.operation_id == "ctu03-actual-arrival")
        )
        member = await db.scalar(
            select(TransportMember).where(TransportMember.transport_task_id == task.transport_task_id)
        )
    assert evidence is not None and evidence.status == "APPLIED"
    assert evidence.payload_json["arrival_face"] == (face_fields.get("arrival_face") or None)
    assert member is not None and member.arrival_face == expected_face
    replay = await record_valid_callback(
        service,
        operation_id="ctu03-actual-arrival",
        transport_task_id=handle.transport_task_id,
        operation=RESULT_OPERATION,
        timestamp=1,
        payload=evidence.payload_json,
    )
    assert replay["code"] == "DUPLICATE"
    assert (await _load_task(db_engine, handle.transport_task_id)).outcome_version == task.outcome_version
    assert projection.position_json == {"kind": "RACK_POSITION", "location_code": "STORAGE-17"}


@pytest.mark.asyncio
@pytest.mark.parametrize("target_face", [None, "90"])
async def test_failed_known_rack_result_accepts_missing_face_even_when_target_requested(
    db_engine: object, target_face: str | None
) -> None:
    service = _service(db_engine)
    service._dispatch_gate = SimpleNamespace(is_task_dispatch_allowed=AsyncMock(return_value=True))
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions.begin() as db:
        db.add(
            TransportDebugPositionProjection(
                object_type="RACK",
                object_id="rack-failed-face",
                position_json={"kind": "RACK_POSITION", "location_code": "OLD"},
                position_unknown=False,
                arrival_face="270",
                source_operation_id="seed",
                source_transport_task_id="seed",
                updated_at=timezone.now_for_db(),
            )
        )
    handle = await service.move_rack(
        new_uuid7(),
        TransportCaller(TRANSPORT_DEBUG_CALLER_WORKLINE_ID),
        "rack-failed-face",
        RackReference("rack-failed-face"),
        ZonePosition("WH05"),
        target_face=target_face,
        rcs_template_id=RcsTemplateId.CTU03,
    )
    assert await service.submit_pending_tasks(1) == 1
    await record_valid_callback(
        service,
        operation_id="failed-face-result",
        transport_task_id=handle.transport_task_id,
        operation=RESULT_OPERATION,
        timestamp=1,
        payload={
            "kind": "RACK_MOVE",
            "outcome_revision": 1,
            "rack_id": "rack-failed-face",
            "status": "FAILED",
            "failure_code": "RCS_EXECUTION_FAILED",
            "final_position": {"kind": "RACK_POSITION", "location_code": "STOPPED"},
            "arrival_face": None,
        },
    )
    assert await service.process_pending_evidence(1) == 1
    task = await _load_task(db_engine, handle.transport_task_id)
    async with sessions() as db:
        projection = await db.scalar(
            select(TransportDebugPositionProjection).where(
                TransportDebugPositionProjection.object_id == "rack-failed-face"
            )
        )
    assert projection is not None
    assert (task.status, task.reason_code) == ("FAILED", "RCS_EXECUTION_FAILED")
    assert projection.arrival_face == "270"
    assert projection.position_json == {"kind": "RACK_POSITION", "location_code": "OLD"}
    assert projection.position_unknown is True
    assert task.outcome_json["members"][0]["arrival_face"] is None
    assert task.outcome_json["members"][0]["final_position"] == {"kind": "RACK_POSITION", "location_code": "STOPPED"}
