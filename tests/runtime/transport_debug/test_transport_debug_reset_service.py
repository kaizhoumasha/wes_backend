"""Transport 联调定向重置验收。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.app.execution.models import PositionProjection
from src.app.transport.contracts import (
    TRANSPORT_DEBUG_CALLER_WORKLINE_ID,
    BinMove,
    HandoffPosition,
    RackBinSlot,
    RackPosition,
    RcsTemplateId,
    TransportCaller,
    TransportContractError,
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
from src.app.transport_debug.debug_reset import TransportDebugStep, TransportDebugStepConfirmation
from src.app.transport_debug.repository import TransportDebugRunRepository
from src.app.transport_debug.reset_service import TransportDebugResetService
from src.app.wms_adapter.transport_wire import RESULT_OPERATION
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone
from tests.support.sqlmodel_metadata import register_required_sqlmodel_metadata
from tests.support.transport_projections import ensure_projection_authority

register_required_sqlmodel_metadata()


class _UnusedProvider:
    async def submit(self, **_kwargs: object) -> object:
        raise AssertionError("debug reset tests must not submit")


def _service(db_engine: object) -> TransportService:
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    return TransportService(sessions, TransportRepository(), _UnusedProvider(), result_timeout=timedelta(seconds=420))


def _reset_service(db_engine: object) -> TransportDebugResetService:
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    return TransportDebugResetService(sessions, TransportRepository(), TransportDebugRunRepository())


def _caller() -> TransportCaller:
    return TransportCaller("SORTER", "STATION_A")


async def _load_task(db_engine: object, transport_task_id: str) -> TransportTask:
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as db:
        task = await db.scalar(select(TransportTask).where(TransportTask.transport_task_id == transport_task_id))
        assert task is not None
        return task


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


@pytest.mark.asyncio
async def test_debug_reset_rejects_task_linked_to_active_debug_run_before_delete() -> None:
    class _Task:
        transport_task_id = "transport-guarded"
        status = "RECONCILING"
        outcome_version = 1

    class _Repository:
        delete_called = False

        async def get_task(self, _db: object, _task_id: str, *, for_update: bool = False) -> object:
            assert for_update is True
            return _Task()

        async def get_debug_reset_counts(self, _db: object, _task_id: str) -> tuple[int, ...]:
            return (0, 0, 0, 0)

        async def delete_debug_task_aggregate(self, _db: object, _task_id: str) -> tuple[int, ...]:
            self.delete_called = True
            return (0, 0, 0, 0, 1, 1)

    class _Guard:
        async def is_task_linked_to_active_run(self, _db: object, transport_task_id: str) -> bool:
            assert transport_task_id == "transport-guarded"
            return True

    class _Sessions:
        @asynccontextmanager
        async def begin(self):
            yield object()

    repository = _Repository()
    service = TransportDebugResetService(  # type: ignore[arg-type]
        _Sessions(),
        repository,
        _Guard(),
    )

    with pytest.raises(TransportContractError, match="active transport debug run task cannot be reset"):
        await service.reset_debug_task("transport-guarded")

    assert repository.delete_called is False


@pytest.mark.asyncio
async def test_debug_step_confirmation_is_audited_before_local_reset(
    db_engine: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(db_engine)
    audit = AsyncMock()
    monkeypatch.setattr("src.app.transport_debug.reset_service.audit_log_service.create_audit_log", audit)
    handle = await service.move_rack(
        new_uuid7(),
        TransportCaller(TRANSPORT_DEBUG_CALLER_WORKLINE_ID, "CTU01"),
        "510056",
        ZonePosition("WH05"),
        RackPosition("KT16"),
        "90",
        RcsTemplateId.CTU01,
    )

    await _reset_service(db_engine).reset_debug_task(
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
        ZonePosition("WH05"),
        "90",
        RcsTemplateId.CTU03,
    )

    with pytest.raises(TransportContractError, match="does not match frozen Transport request"):
        await _reset_service(db_engine).reset_debug_task(
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
        ZonePosition("WH05"),
        RackPosition("KT16"),
        "90",
        RcsTemplateId.CTU01,
    )
    debug = await service.move_rack(
        new_uuid7(),
        TransportCaller(TRANSPORT_DEBUG_CALLER_WORKLINE_ID, "CTU01"),
        "rack-debug-kind",
        RackPosition("WH05"),
        RackPosition("KT16"),
        "90",
    )

    with pytest.raises(TransportContractError, match="requires a TRANSPORT_DEBUG task"):
        await _reset_service(db_engine).reset_debug_task(
            normal.transport_task_id,
            TransportDebugStepConfirmation(
                step=TransportDebugStep.RACK_TO_STATION,
                assertion="PHYSICAL_TARGET_REACHED",
            ),
        )
    with pytest.raises(TransportContractError, match="does not match Transport task kind"):
        await _reset_service(db_engine).reset_debug_task(
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
    monkeypatch.setattr("src.app.transport_debug.reset_service.audit_log_service.create_audit_log", audit)
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

    await _reset_service(db_engine).reset_debug_task(
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
    reset_service = _reset_service(db_engine)
    delete_aggregate = AsyncMock(wraps=reset_service._repository.delete_debug_task_aggregate)
    monkeypatch.setattr(reset_service._repository, "delete_debug_task_aggregate", delete_aggregate)
    monkeypatch.setattr(
        "src.app.transport_debug.reset_service.audit_log_service.create_audit_log",
        AsyncMock(side_effect=RuntimeError("audit unavailable")),
    )
    handle = await service.move_rack(
        new_uuid7(),
        TransportCaller(TRANSPORT_DEBUG_CALLER_WORKLINE_ID, "CTU01"),
        "510056",
        ZonePosition("WH05"),
        RackPosition("KT16"),
        "90",
        RcsTemplateId.CTU01,
    )

    with pytest.raises(RuntimeError, match="audit unavailable"):
        await reset_service.reset_debug_task(
            handle.transport_task_id,
            TransportDebugStepConfirmation(
                step=TransportDebugStep.RACK_TO_STATION,
                assertion="PHYSICAL_TARGET_REACHED",
            ),
        )

    delete_aggregate.assert_not_awaited()


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

    preview = await _reset_service(db_engine).preview_debug_task_reset(target.transport_task_id)

    assert preview.transport_task_id == target.transport_task_id
    assert preview.status == "RECONCILING"
    assert preview.callback_receipt_count == 0
    assert preview.position_projection_count == 0
    assert preview.evidence_count == 0
    assert preview.outcome_version == 0
    assert preview.member_count == 1

    result = await _reset_service(db_engine).reset_debug_task(target.transport_task_id)

    assert result.transport_task_id == target.transport_task_id
    assert result.deleted_callback_receipt_count == 0
    assert result.deleted_evidence_count == 0
    assert result.deleted_position_projection_count == 0
    assert result.deleted_member_count == 1
    async with sessions() as db:
        task_ids = set((await db.scalars(select(TransportTask.transport_task_id))).all())
        target_members = (
            await db.scalars(
                select(TransportMember).where(TransportMember.transport_task_id == target.transport_task_id)
            )
        ).all()
    assert task_ids == {keep.transport_task_id}
    assert target_members == []


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

    preview = await _reset_service(db_engine).preview_debug_task_reset(handle.transport_task_id)

    assert preview.status == "PENDING"
    result = await _reset_service(db_engine).reset_debug_task(handle.transport_task_id)

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
        workline_id = await ensure_projection_authority(db)
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
                    position_json={"kind": "RACK_POSITION", "location_code": "B"},
                    source_operation_id=operation_id,
                    source_transport_task_id=keep.transport_task_id,
                    updated_at=now,
                ),
            ]
        )

    await _reset_service(db_engine).reset_debug_task(target.transport_task_id)

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
    with pytest.raises(TransportContractError, match=r"1\.\.80"):
        await _reset_service(db_engine).preview_debug_task_reset(transport_task_id)
