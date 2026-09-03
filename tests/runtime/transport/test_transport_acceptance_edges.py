"""Transport 计划中容易被快乐路径遗漏的可靠性验收。"""

from __future__ import annotations

import asyncio
from datetime import timedelta
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
from src.app.transport.debug_reset import TransportDebugStep, TransportDebugStepConfirmation
from src.app.transport.models import (
    TransportCallbackReceipt,
    TransportDebugPositionProjection,
    TransportEvidence,
    TransportMember,
    TransportResourceBinding,
    TransportTask,
)
from src.app.transport.repository import TransportRepository
from src.app.transport.service import TransportService
from src.app.wms_adapter.transport_wire import RESULT_OPERATION
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
            TransportResourceBinding,
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
async def test_rotate_requires_a_confirmed_current_position_and_opposite_face(db_engine: object) -> None:
    service = _service(db_engine)

    with pytest.raises(TransportContractError, match="current face is unknown"):
        await service.rotate_rack(new_uuid7(), _caller(), "rack-rotate", RackPosition("ROTATE"), "270")

    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions.begin() as db:
        workline_id, line_run_epoch_id = await ensure_projection_authority(db)
        db.add(
            PositionProjection(
                object_type="RACK",
                object_id="rack-rotate",
                workline_id=workline_id,
                line_run_epoch_id=line_run_epoch_id,
                position_json={"kind": "RACK_POSITION", "location_code": "ROTATE"},
                arrival_face="90",
                source_operation_id="seed",
                source_transport_task_id="seed",
                updated_at=timezone.now_for_db(),
            )
        )

    with pytest.raises(TransportContractError, match="current position is not confirmed"):
        await service.rotate_rack(new_uuid7(), _caller(), "rack-rotate", RackPosition("OTHER"), "270")

    with pytest.raises(TransportContractError, match="target face equals current face"):
        await service.rotate_rack(new_uuid7(), _caller(), "rack-rotate", RackPosition("ROTATE"), "90")


@pytest.mark.asyncio
async def test_debug_rotate_uses_latest_applied_debug_transport_fact(db_engine: object) -> None:
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
        RackReference("rack-debug-projection"),
        "270",
    )

    task = await _load_task(db_engine, rotate.transport_task_id)
    assert task.request_json["position"] == {"kind": "RACK", "location_code": "rack-debug-projection"}
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
        assert debug_projection.arrival_face == "270"
        assert debug_projection.source_transport_task_id == rotate.transport_task_id
        assert (
            await db.scalar(select(PositionProjection).where(PositionProjection.object_id == "rack-debug-projection"))
            is None
        )


@pytest.mark.asyncio
async def test_debug_rotate_rack_reference_rejects_a_non_exact_current_projection(db_engine: object) -> None:
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

    with pytest.raises(TransportContractError, match="current exact position is unknown"):
        await service.rotate_rack_for_debug(
            new_uuid7(),
            caller,
            "rack-debug-unknown-position",
            RackReference("rack-debug-unknown-position"),
            "270",
        )


@pytest.mark.asyncio
async def test_debug_rotate_rack_reference_rejects_success_at_a_different_exact_position(db_engine: object) -> None:
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
        RackReference("rack-debug-wrong-station"),
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
        binding = await db.scalar(
            select(TransportResourceBinding).where(
                TransportResourceBinding.transport_task_id == rotate.transport_task_id,
                TransportResourceBinding.released_at.is_(None),
            )
        )
    assert projection is not None
    assert projection.position_json == {"kind": "RACK_POSITION", "location_code": "KT16"}
    assert projection.arrival_face == "90"
    assert binding is not None


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
        with pytest.raises(TransportContractError, match="rack current face does not match request"):
            await service.create_debug_task_in_session(db, request)


@pytest.mark.asyncio
async def test_debug_bin_exchange_uses_debug_rack_face_projections(db_engine: object) -> None:
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

    with pytest.raises(TransportContractError, match="current face is unknown"):
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
async def test_debug_step_confirmation_is_audited_before_local_reset(
    db_engine: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(db_engine)
    audit = AsyncMock()
    monkeypatch.setattr("src.app.transport.service.audit_log_service.create_audit_log", audit)
    handle = await service.move_rack(
        new_uuid7(),
        TransportCaller(TRANSPORT_DEBUG_CALLER_WORKLINE_ID, "CTU01"),
        "510056",
        ZonePosition("WH01"),
        RackPosition("KT16"),
        "90",
        RcsTemplateId.CTU01,
    )

    await service.reset_debug_task(
        handle.transport_task_id,
        TransportDebugStepConfirmation(
            step=TransportDebugStep.RACK_TO_STATION,
            assertion="PHYSICAL_TARGET_REACHED",
        ),
    )

    audit.assert_awaited_once()
    audit_args = audit.await_args.kwargs["args"]
    assert audit_args["model"] == "TransportTask"
    assert audit_args["operation"] == "debug_step_confirm"
    assert audit_args["record_id"] == handle.transport_task_id
    assert audit_args["changes"]["source"] == "OPERATOR_DEBUG"
    assert audit_args["changes"]["business_authoritative"] is False
    assert audit_args["changes"]["step"] == "RACK_TO_STATION"
    assert audit_args["changes"]["frozen_targets"] == [
        {
            "object_id": "510056",
            "target": {"kind": "RACK_POSITION", "location_code": "KT16"},
            "arrival_face": "90",
            "rcs_template_id": "CTU01",
        }
    ]
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as db:
        assert await db.get(TransportTask, handle.transport_task_id) is None


@pytest.mark.asyncio
async def test_debug_step_confirmation_rejects_same_kind_wrong_direction(db_engine: object) -> None:
    service = _service(db_engine)
    handle = await service.move_rack(
        new_uuid7(),
        TransportCaller(TRANSPORT_DEBUG_CALLER_WORKLINE_ID, "CTU01"),
        "510056",
        RackPosition("KT16"),
        ZonePosition("WH01"),
        "90",
        RcsTemplateId.CTU03,
    )

    with pytest.raises(TransportContractError, match="does not match frozen Transport request"):
        await service.reset_debug_task(
            handle.transport_task_id,
            TransportDebugStepConfirmation(
                step=TransportDebugStep.RACK_TO_STATION,
                assertion="PHYSICAL_TARGET_REACHED",
            ),
        )

    assert await _load_task(db_engine, handle.transport_task_id) is not None


@pytest.mark.asyncio
async def test_debug_step_confirmation_rejects_non_debug_task_and_kind_mismatch(db_engine: object) -> None:
    service = _service(db_engine)
    normal = await service.move_rack(
        new_uuid7(),
        _caller(),
        "rack-normal",
        ZonePosition("WH01"),
        RackPosition("KT16"),
        "90",
        RcsTemplateId.CTU01,
    )
    debug = await service.move_rack(
        new_uuid7(),
        TransportCaller(TRANSPORT_DEBUG_CALLER_WORKLINE_ID, "CTU01"),
        "rack-debug-kind",
        RackPosition("WH01"),
        RackPosition("KT16"),
        "90",
    )

    with pytest.raises(TransportContractError, match="requires a TRANSPORT_DEBUG task"):
        await service.reset_debug_task(
            normal.transport_task_id,
            TransportDebugStepConfirmation(
                step=TransportDebugStep.RACK_TO_STATION,
                assertion="PHYSICAL_TARGET_REACHED",
            ),
        )
    with pytest.raises(TransportContractError, match="does not match Transport task kind"):
        await service.reset_debug_task(
            debug.transport_task_id,
            TransportDebugStepConfirmation(
                step=TransportDebugStep.BINS_TO_INFEED,
                assertion="PHYSICAL_TARGET_REACHED",
            ),
        )

    assert await _load_task(db_engine, normal.transport_task_id) is not None
    assert await _load_task(db_engine, debug.transport_task_id) is not None


@pytest.mark.asyncio
async def test_debug_bin_step_confirmation_audits_frozen_handoff_targets(
    db_engine: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(db_engine)
    audit = AsyncMock()
    monkeypatch.setattr("src.app.transport.service.audit_log_service.create_audit_log", audit)
    handle = await service.move_bins_for_debug(
        new_uuid7(),
        TransportCaller(TRANSPORT_DEBUG_CALLER_WORKLINE_ID, "CTU01"),
        (
            BinMove(
                "A000001922",
                RackBinSlot("510056", "90", "510056A3F2C101"),
                HandoffPosition("CNV0301"),
            ),
            BinMove(
                "A000002653",
                RackBinSlot("510056", "90", "510056A2F2C101"),
                HandoffPosition("CNV0301"),
            ),
        ),
    )

    await service.reset_debug_task(
        handle.transport_task_id,
        TransportDebugStepConfirmation(
            step=TransportDebugStep.BINS_TO_INFEED,
            assertion="PHYSICAL_TARGET_REACHED",
        ),
    )

    assert audit.await_args.kwargs["args"]["changes"]["frozen_targets"] == [
        {
            "object_id": "A000001922",
            "target": {"kind": "HANDOFF_POSITION", "location_code": "CNV0301"},
            "arrival_face": None,
        },
        {
            "object_id": "A000002653",
            "target": {"kind": "HANDOFF_POSITION", "location_code": "CNV0301"},
            "arrival_face": None,
        },
    ]


def test_debug_step_confirmation_rejects_any_other_assertion() -> None:
    with pytest.raises(ValueError, match="assertion must be PHYSICAL_TARGET_REACHED"):
        TransportDebugStepConfirmation(
            step=TransportDebugStep.RACK_TO_STATION,
            assertion="ACK_ACCEPTED",
        )


@pytest.mark.asyncio
async def test_debug_step_audit_failure_does_not_start_local_deletion(
    db_engine: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(db_engine)
    delete_aggregate = AsyncMock(wraps=service._repository.delete_debug_task_aggregate)
    monkeypatch.setattr(service._repository, "delete_debug_task_aggregate", delete_aggregate)
    monkeypatch.setattr(
        "src.app.transport.service.audit_log_service.create_audit_log",
        AsyncMock(side_effect=RuntimeError("audit unavailable")),
    )
    handle = await service.move_rack(
        new_uuid7(),
        TransportCaller(TRANSPORT_DEBUG_CALLER_WORKLINE_ID, "CTU01"),
        "510056",
        ZonePosition("WH01"),
        RackPosition("KT16"),
        "90",
        RcsTemplateId.CTU01,
    )

    with pytest.raises(RuntimeError, match="audit unavailable"):
        await service.reset_debug_task(
            handle.transport_task_id,
            TransportDebugStepConfirmation(
                step=TransportDebugStep.RACK_TO_STATION,
                assertion="PHYSICAL_TARGET_REACHED",
            ),
        )

    delete_aggregate.assert_not_awaited()


@pytest.mark.asyncio
async def test_bin_move_requires_a_confirmed_matching_rack_face(db_engine: object) -> None:
    service = _service(db_engine)
    move_on_face_a = (BinMove("bin-face", RackBinSlot("rack-face", "90", "1"), HandoffPosition("ROLLER_IN")),)

    with pytest.raises(TransportContractError, match="rack current face is unknown"):
        await service.move_bins(new_uuid7(), _caller(), move_on_face_a)

    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions.begin() as db:
        workline_id, line_run_epoch_id = await ensure_projection_authority(db)
        db.add(
            PositionProjection(
                object_type="RACK",
                object_id="rack-face",
                workline_id=workline_id,
                line_run_epoch_id=line_run_epoch_id,
                position_json={"kind": "RACK_POSITION", "location_code": "STORAGE"},
                arrival_face="270",
                source_operation_id="seed",
                source_transport_task_id="seed",
                updated_at=timezone.now_for_db(),
            )
        )

    with pytest.raises(TransportContractError, match="rack current face does not match request"):
        await service.move_bins(new_uuid7(), _caller(), move_on_face_a)

    async with sessions.begin() as db:
        await db.execute(
            update(PositionProjection).where(PositionProjection.object_id == "rack-face").values(arrival_face="90")
        )

    handle = await service.move_bins(new_uuid7(), _caller(), move_on_face_a)
    assert handle.transport_task_id.startswith("transport-")


@pytest.mark.asyncio
async def test_bin_exchange_requires_confirmed_rack_faces(db_engine: object) -> None:
    service = _service(db_engine)
    exchange_pairs = (
        BinExchangePair(
            "bin-left",
            RackBinSlot("rack-left", "90", "1"),
            "bin-right",
            RackBinSlot("rack-right", "270", "1"),
        ),
    )

    with pytest.raises(TransportContractError, match="rack current face is unknown"):
        await service.exchange_bins(new_uuid7(), _caller(), exchange_pairs)


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
    assert (await _load_task(db_engine, handle.transport_task_id)).status == "RECONCILING"
    assert await service.submit_pending_tasks(1) == 0
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_debug_reset_previews_and_deletes_only_the_selected_task(db_engine: object) -> None:
    service = _service(db_engine)
    target = await service.move_rack(
        new_uuid7(),
        _caller(),
        "rack-reset-target",
        RackPosition("A"),
        RackPosition("B"),
        "90",
    )
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions.begin() as db:
        await db.execute(
            update(TransportTask)
            .where(TransportTask.transport_task_id == target.transport_task_id)
            .values(status="RECONCILING", reason_code="TRANSPORT_DELIVERY_UNKNOWN")
        )
    keep = await service.move_rack(
        new_uuid7(),
        _caller(),
        "rack-reset-keep",
        RackPosition("C"),
        RackPosition("D"),
        "90",
    )

    preview = await service.preview_debug_task_reset(target.transport_task_id)

    assert preview.transport_task_id == target.transport_task_id
    assert preview.status == "RECONCILING"
    assert preview.callback_receipt_count == 0
    assert preview.position_projection_count == 0
    assert preview.evidence_count == 0
    assert preview.outcome_version == 0
    assert preview.member_count == 1
    assert preview.binding_count == 1
    assert preview.active_binding_count == 1

    result = await service.reset_debug_task(target.transport_task_id)

    assert result.transport_task_id == target.transport_task_id
    assert result.deleted_callback_receipt_count == 0
    assert result.deleted_evidence_count == 0
    assert result.deleted_position_projection_count == 0
    assert result.deleted_member_count == 1
    assert result.deleted_binding_count == 1
    async with sessions() as db:
        task_ids = set((await db.scalars(select(TransportTask.transport_task_id))).all())
        target_members = (
            await db.scalars(
                select(TransportMember).where(TransportMember.transport_task_id == target.transport_task_id)
            )
        ).all()
        target_bindings = (
            await db.scalars(
                select(TransportResourceBinding).where(
                    TransportResourceBinding.transport_task_id == target.transport_task_id
                )
            )
        ).all()
    assert task_ids == {keep.transport_task_id}
    assert target_members == []
    assert target_bindings == []


@pytest.mark.asyncio
async def test_debug_reset_allows_pending_task_without_extra_eligibility_rules(db_engine: object) -> None:
    service = _service(db_engine)
    handle = await service.move_rack(
        new_uuid7(),
        _caller(),
        "rack-reset-pending",
        RackPosition("A"),
        RackPosition("B"),
        "90",
    )

    preview = await service.preview_debug_task_reset(handle.transport_task_id)

    assert preview.status == "PENDING"
    result = await service.reset_debug_task(handle.transport_task_id)

    assert result.transport_task_id == handle.transport_task_id
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as db:
        task = await db.scalar(select(TransportTask).where(TransportTask.transport_task_id == handle.transport_task_id))
    assert task is None


@pytest.mark.asyncio
async def test_debug_reset_preserves_another_task_projection_when_operation_id_is_reused(db_engine: object) -> None:
    service = _service(db_engine)
    target = await service.move_rack(
        new_uuid7(),
        _caller(),
        "rack-reset-collision-target",
        RackPosition("A"),
        RackPosition("B"),
        "90",
    )
    keep = await service.move_rack(
        new_uuid7(),
        _caller(),
        "rack-reset-collision-keep",
        RackPosition("A"),
        RackPosition("B"),
        "90",
    )
    operation_id = str(new_uuid7())
    now = timezone.now_for_db()
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions.begin() as db:
        workline_id, line_run_epoch_id = await ensure_projection_authority(db)
        db.add_all(
            [
                TransportEvidence(
                    operation_id=operation_id,
                    transport_task_id=target.transport_task_id,
                    operation="transport.task.member_position_changed@v1",
                    event_timestamp_ms=1,
                    message_digest="a" * 64,
                    payload_json={"transport_task_id": target.transport_task_id},
                    ack_timestamp_ms=2,
                    ack_data_json={"transport_task_id": target.transport_task_id},
                    received_at=now,
                ),
                TransportEvidence(
                    operation_id=operation_id,
                    transport_task_id=keep.transport_task_id,
                    operation=RESULT_OPERATION,
                    outcome_revision=1,
                    event_timestamp_ms=1,
                    message_digest="b" * 64,
                    payload_json={"transport_task_id": keep.transport_task_id},
                    ack_timestamp_ms=2,
                    ack_data_json={"transport_task_id": keep.transport_task_id},
                    received_at=now,
                ),
                PositionProjection(
                    object_type="RACK",
                    object_id="rack-reset-collision-keep",
                    workline_id=workline_id,
                    line_run_epoch_id=line_run_epoch_id,
                    position_json={"kind": "RACK_POSITION", "location_code": "B"},
                    source_operation_id=operation_id,
                    source_transport_task_id=keep.transport_task_id,
                    updated_at=now,
                ),
            ]
        )

    await service.reset_debug_task(target.transport_task_id)

    async with sessions() as db:
        projection = await db.scalar(
            select(PositionProjection).where(PositionProjection.object_id == "rack-reset-collision-keep")
        )
    assert projection is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("transport_task_id", ["   ", "invalid\x00id"])
async def test_debug_reset_rejects_invalid_task_id_before_database(
    db_engine: object,
    transport_task_id: str,
) -> None:
    service = _service(db_engine)

    with pytest.raises(TransportContractError, match=r"1\.\.80"):
        await service.preview_debug_task_reset(transport_task_id)


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

    assert await service.process_pending_evidence(1) == 1
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
    service = TransportService(sessions, repository, provider)
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
    stale_service = TransportService(sessions, blocked_repository, provider)
    winner_service = TransportService(sessions, TransportRepository(), provider)
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
async def test_known_partial_failure_forms_failed_outcome_and_releases_resources(db_engine: object) -> None:
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
