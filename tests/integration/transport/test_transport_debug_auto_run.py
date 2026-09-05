from __future__ import annotations

import uuid
from dataclasses import asdict
from typing import Any

import pytest
from sqlalchemy import delete, func, select

from src.app.execution.models import (
    InboundEvidence,
    InboundEvidenceApplyStatus,
    InboundEvidenceConflict,
    InboundEvidenceKind,
)
from src.app.transport.contracts import (
    MoveBinsRequest,
    MoveRackRequest,
    RackBinSlot,
    RackPosition,
    RackReference,
    RcsTemplateId,
    RotateRackRequest,
    TransportHandle,
    TransportRequest,
)
from src.app.transport.debug_run_contracts import (
    CreateTransportDebugRun,
    TransportDebugBinSelection,
    TransportDebugFaceGroup,
)
from src.app.transport.debug_run_repository import TransportDebugRunRepository
from src.app.transport.debug_run_service import TransportDebugRunConflict, TransportDebugRunService
from src.app.transport.models import (
    TransportDebugRun,
    TransportDebugRunStep,
    TransportEvidence,
    TransportMember,
    TransportResourceBinding,
    TransportTask,
)
from src.app.wms_adapter.transport_wire import RESULT_OPERATION
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class _Publisher:
    async def publish_to(self, channel: str, event_type: str, payload: dict[str, object]) -> bool:
        del channel, event_type, payload
        return True


class _PersistingTransport:
    """在同一事务内冻结 Transport task/member，WMS 结果由测试显式写入。"""

    def __init__(self) -> None:
        self.created: list[tuple[str, TransportRequest]] = []

    async def assert_debug_rack_position_in_session(
        self,
        db: Any,
        rack_id: str,
        expected_position: RackPosition,
        expected_face: str,
    ) -> None:
        del db, rack_id, expected_position, expected_face

    async def create_debug_task_in_session(self, db: Any, request: TransportRequest) -> TransportHandle:
        task_id = f"transport-acceptance-{uuid.uuid4()}"
        now = timezone.now_for_db()
        task = TransportTask(
            transport_task_id=task_id,
            client_request_id=request.client_request_id,
            request_digest="0" * 64,
            kind=request.kind.value,
            caller_json=asdict(request.caller),
            request_json={},
            submit_operation_id=new_uuid7(),
            submit_timestamp_ms=int(timezone.now_utc().timestamp() * 1000),
            submit_request_body="{}",
            submit_request_body_digest="1" * 64,
            created_at=now,
            updated_at=now,
        )
        db.add(task)
        await db.flush()
        for ordinal, (object_type, object_id, source, target) in enumerate(_request_members(request)):
            db.add(
                TransportMember(
                    transport_task_id=task_id,
                    ordinal=ordinal,
                    object_type=object_type,
                    object_id=object_id,
                    source_json=asdict(source),
                    target_json=asdict(target),
                    updated_at=now,
                )
            )
        await db.flush()
        self.created.append((task_id, request))
        return TransportHandle(task_id, request.client_request_id)

    async def is_unsent_debug_task_finalizable_in_session(self, db: Any, task_id: str) -> bool:
        task = await db.scalar(select(TransportTask).where(TransportTask.transport_task_id == task_id))
        if task is None:
            return False
        evidence_id = await db.scalar(
            select(TransportEvidence.id).where(TransportEvidence.transport_task_id == task_id).limit(1)
        )
        return (
            task.caller_json.get("workline_id") == "TRANSPORT_DEBUG"
            and task.status == "PENDING"
            and task.send_started_at is None
            and task.submit_claim_token is None
            and task.submit_claim_until is None
            and evidence_id is None
        )


def _request_members(request: TransportRequest) -> list[tuple[str, str, object, object]]:
    if isinstance(request, MoveRackRequest):
        return [("RACK", request.rack_id, request.source, request.target)]
    if isinstance(request, RotateRackRequest):
        frozen_position = RackPosition("KT16") if isinstance(request.position, RackReference) else request.position
        return [("RACK", request.rack_id, frozen_position, frozen_position)]
    assert isinstance(request, MoveBinsRequest)
    return [("BIN", move.bin_id, move.source, move.target) for move in request.moves]


def _service(session_factory: Any, transport: _PersistingTransport) -> TransportDebugRunService:
    return TransportDebugRunService(
        session_factory,
        TransportDebugRunRepository(),
        transport,  # type: ignore[arg-type]
        event_publisher=_Publisher(),
    )


def _configuration(suffix: str, *, faces: tuple[str, ...]) -> tuple[str, CreateTransportDebugRun]:
    rack_id = f"rack-auto-{suffix}"
    groups = tuple(
        TransportDebugFaceGroup(
            face=face,
            bins=tuple(
                TransportDebugBinSelection(
                    bin_id=f"bin-{suffix}-{group_index}-{bin_index}",
                    slot_id=f"slot-{group_index}-{bin_index}",
                )
                for bin_index in range(1, 5 if group_index == 0 else 2)
            ),
        )
        for group_index, face in enumerate(faces)
    )
    return rack_id, CreateTransportDebugRun(rack_id=rack_id, face_groups=groups)


async def _persist_scan12(
    session_factory: Any,
    *,
    source_event_id: str,
    bin_id: str,
    timestamp_ms: int,
    device_code: str = "SCAN12",
    apply_status: InboundEvidenceApplyStatus = InboundEvidenceApplyStatus.APPLIED,
) -> None:
    async with session_factory.begin() as db:
        db.add(
            InboundEvidence(
                kind=InboundEvidenceKind.DEVICE_EVENT,
                source_identity=source_event_id,
                payload_digest="a" * 64,
                normalized_payload={
                    "device_code": device_code,
                    "contract_key": "device.event",
                    "contract_version": "1.0",
                    "event_type": "SCAN_COMPLETED",
                    "timestamp": timestamp_ms,
                    "source_event_id": source_event_id,
                    "is_debug": True,
                    "data": {"barcode": bin_id},
                },
                received_at=timezone.now_for_db(),
                device_code=device_code,
                contract_key="device.event",
                contract_version="1.0",
                apply_status=apply_status,
            )
        )


async def _complete_current_transport(
    session_factory: Any,
    service: TransportDebugRunService,
    run_id: str,
    transport: _PersistingTransport,
    *,
    storage_position: str = "WH01-01",
    arrival_face_override: str | None = None,
    position_unknown: bool = False,
) -> None:
    snapshot = await service.get_run(run_id)
    assert snapshot.current_step is not None and snapshot.current_step.transport_task_id is not None
    task_id = snapshot.current_step.transport_task_id
    request = dict(transport.created)[task_id]
    async with session_factory.begin() as db:
        task = await db.scalar(select(TransportTask).where(TransportTask.transport_task_id == task_id))
        members = list(
            await db.scalars(
                select(TransportMember)
                .where(TransportMember.transport_task_id == task_id)
                .order_by(TransportMember.ordinal.asc())
            )
        )
        assert task is not None and members
        task.status = "SUCCEEDED"
        task.updated_at = timezone.now_for_db()
        for member in members:
            member.status = "SUCCEEDED"
            member.position_unknown = position_unknown
            member.updated_at = timezone.now_for_db()
            if position_unknown:
                member.final_position_json = None
                member.arrival_face = None
                continue
            if isinstance(request, RotateRackRequest):
                member.final_position_json = {"kind": "RACK_POSITION", "location_code": "KT16"}
                member.arrival_face = arrival_face_override or request.target_face
            elif isinstance(request, MoveRackRequest) and request.rcs_template_id is RcsTemplateId.CTU03:
                member.final_position_json = {"kind": "RACK_POSITION", "location_code": storage_position}
                member.arrival_face = arrival_face_override or request.target_face
            elif isinstance(request, MoveRackRequest):
                member.final_position_json = member.target_json
                member.arrival_face = arrival_face_override or request.target_face
            else:
                member.final_position_json = member.target_json
                member.arrival_face = None


async def _partially_complete_current_bin_transport(
    session_factory: Any,
    service: TransportDebugRunService,
    run_id: str,
    transport: _PersistingTransport,
) -> None:
    snapshot = await service.get_run(run_id)
    assert snapshot.current_step is not None and snapshot.current_step.transport_task_id is not None
    task_id = snapshot.current_step.transport_task_id
    request = dict(transport.created)[task_id]
    assert isinstance(request, MoveBinsRequest) and len(request.moves) > 1
    async with session_factory.begin() as db:
        task = await db.scalar(select(TransportTask).where(TransportTask.transport_task_id == task_id))
        member = await db.scalar(
            select(TransportMember)
            .where(TransportMember.transport_task_id == task_id)
            .order_by(TransportMember.ordinal.asc())
            .limit(1)
        )
        assert task is not None and member is not None
        task.status = "ACCEPTED"
        task.updated_at = timezone.now_for_db()
        member.status = "SUCCEEDED"
        member.final_position_json = member.target_json
        member.arrival_face = None
        member.updated_at = timezone.now_for_db()


async def _advance_to_scan_wait(
    session_factory: Any,
    service: TransportDebugRunService,
    transport: _PersistingTransport,
    run_id: str,
) -> None:
    await _complete_current_transport(session_factory, service, run_id, transport)
    assert await service.advance_run(run_id) is True
    assert await service.advance_run(run_id) is True
    await _complete_current_transport(session_factory, service, run_id, transport)
    assert await service.advance_run(run_id) is True
    assert (await service.get_run(run_id)).current_phase == "WAIT_SCAN12"


async def _cleanup(session_factory: Any, *, rack_id: str, source_prefix: str) -> None:
    async with session_factory.begin() as db:
        evidence_ids = list(
            await db.scalars(
                select(InboundEvidence.id).where(InboundEvidence.source_identity.like(f"{source_prefix}%"))
            )
        )
        if evidence_ids:
            await db.execute(
                delete(InboundEvidenceConflict).where(InboundEvidenceConflict.first_evidence_id.in_(evidence_ids))
            )
        run_ids = list(await db.scalars(select(TransportDebugRun.run_id).where(TransportDebugRun.rack_id == rack_id)))
        task_ids: list[str] = []
        if run_ids:
            task_ids = list(
                await db.scalars(
                    select(TransportDebugRunStep.transport_task_id).where(
                        TransportDebugRunStep.run_id.in_(run_ids),
                        TransportDebugRunStep.transport_task_id.is_not(None),
                    )
                )
            )
        if task_ids:
            await db.execute(delete(TransportEvidence).where(TransportEvidence.transport_task_id.in_(task_ids)))
            await db.execute(
                delete(TransportResourceBinding).where(TransportResourceBinding.transport_task_id.in_(task_ids))
            )
            await db.execute(delete(TransportMember).where(TransportMember.transport_task_id.in_(task_ids)))
            await db.execute(delete(TransportTask).where(TransportTask.transport_task_id.in_(task_ids)))
        if run_ids:
            await db.execute(delete(TransportDebugRunStep).where(TransportDebugRunStep.run_id.in_(run_ids)))
            await db.execute(delete(TransportDebugRun).where(TransportDebugRun.run_id.in_(run_ids)))
        await db.execute(delete(InboundEvidence).where(InboundEvidence.source_identity.like(f"{source_prefix}%")))


@pytest.mark.parametrize("faces", [("90",), ("90", "270")], ids=["single-face", "two-faces"])
async def test_selected_faces_complete_in_order_and_return_only_after_every_bin_is_back(
    integration_session_factory: Any,
    faces: tuple[str, ...],
) -> None:
    suffix = uuid.uuid4().hex[:8]
    source_prefix = f"auto-{suffix}"
    rack_id, request = _configuration(suffix, faces=faces)
    transport = _PersistingTransport()
    service = _service(integration_session_factory, transport)
    await _cleanup(integration_session_factory, rack_id=rack_id, source_prefix=source_prefix)
    await _persist_scan12(
        integration_session_factory,
        source_event_id=f"{source_prefix}-old",
        bin_id=request.face_groups[0].bins[0].bin_id,
        timestamp_ms=int(timezone.now_utc().timestamp() * 1000),
    )
    try:
        run = await service.create_run(request, actor_id=7)
        expected_kinds: list[str] = []
        for group_index, group in enumerate(request.face_groups):
            if group_index == 0:
                expected_kinds.append("RACK_MOVE")
                rack_out = transport.created[-1][1]
                assert isinstance(rack_out, MoveRackRequest)
                assert rack_out.rcs_template_id is RcsTemplateId.CTU01
                assert asdict(rack_out.source) == {"kind": "RACK", "location_code": rack_id}
                assert asdict(rack_out.target) == {"kind": "RACK_POSITION", "location_code": "KT16"}
                assert rack_out.target_face == group.face
                await _complete_current_transport(integration_session_factory, service, run.run_id, transport)
                assert await service.advance_run(run.run_id) is True
            else:
                assert await service.advance_run(run.run_id) is True
                expected_kinds.append("RACK_ROTATE")
                rotate = transport.created[-1][1]
                assert isinstance(rotate, RotateRackRequest)
                assert asdict(rotate.position) == {"kind": "RACK", "location_code": rack_id}
                assert rotate.target_face == group.face
                await _complete_current_transport(integration_session_factory, service, run.run_id, transport)
                assert await service.advance_run(run.run_id) is True

            assert await service.advance_run(run.run_id) is True
            expected_kinds.append("BIN_MOVE")
            to_infeed = transport.created[-1][1]
            assert isinstance(to_infeed, MoveBinsRequest)
            assert 1 <= len(to_infeed.moves) <= 4
            assert {move.target.location_code for move in to_infeed.moves} == {"CNV0301"}  # type: ignore[attr-defined]
            await _complete_current_transport(integration_session_factory, service, run.run_id, transport)
            assert await service.advance_run(run.run_id) is True

            scan_wait = await service.get_run(run.run_id)
            assert scan_wait.current_phase == "WAIT_SCAN12"
            assert scan_wait.observed_bin_ids == ()
            assert scan_wait.current_step is not None and scan_wait.current_step.evidence_not_before_ms is not None
            scan_timestamp = scan_wait.current_step.evidence_not_before_ms
            for bin_index, selection in enumerate(group.bins):
                scanned_bin_id = f"{selection.bin_id}-{'ABCD'[bin_index % 4]}"
                await _persist_scan12(
                    integration_session_factory,
                    source_event_id=f"{source_prefix}-g{group_index}-scan{bin_index}",
                    bin_id=scanned_bin_id,
                    timestamp_ms=scan_timestamp,
                    device_code="STATION_SCAN12",
                )
                if bin_index == 0:
                    await _persist_scan12(
                        integration_session_factory,
                        source_event_id=f"{source_prefix}-g{group_index}-duplicate",
                        bin_id=scanned_bin_id,
                        timestamp_ms=scan_timestamp,
                    )
            assert await service.advance_run(run.run_id) is True
            assert (await service.get_run(run.run_id)).current_phase == "BINS_TO_RACK"
            assert await service.advance_run(run.run_id) is True
            expected_kinds.append("BIN_MOVE")
            to_rack = transport.created[-1][1]
            assert isinstance(to_rack, MoveBinsRequest)
            assert {move.source.location_code for move in to_rack.moves} == {"CNV0302"}  # type: ignore[attr-defined]
            assert {
                (move.bin_id, move.target.rack_face, move.target.slot_id)
                for move in to_rack.moves
                if isinstance(move.target, RackBinSlot)
            } == {(selection.bin_id, group.face, selection.slot_id) for selection in group.bins}
            assert all(
                not isinstance(created_request, MoveRackRequest)
                or created_request.rcs_template_id is not RcsTemplateId.CTU03
                for _, created_request in transport.created
            )
            if len(group.bins) > 1:
                created_count = len(transport.created)
                await _partially_complete_current_bin_transport(
                    integration_session_factory,
                    service,
                    run.run_id,
                    transport,
                )
                assert await service.advance_run(run.run_id) is False
                partial = await service.get_run(run.run_id)
                assert partial.current_phase == "BINS_TO_RACK"
                assert len(transport.created) == created_count
            await _complete_current_transport(integration_session_factory, service, run.run_id, transport)
            assert await service.advance_run(run.run_id) is True

        assert (await service.get_run(run.run_id)).current_phase == "RACK_TO_STORAGE"
        assert await service.advance_run(run.run_id) is True
        expected_kinds.append("RACK_MOVE")
        rack_return = transport.created[-1][1]
        assert isinstance(rack_return, MoveRackRequest)
        assert rack_return.rcs_template_id is RcsTemplateId.CTU03
        assert asdict(rack_return.source) == {"kind": "RACK", "location_code": rack_id}
        assert asdict(rack_return.target) == {"kind": "ZONE", "location_code": "WH01"}
        assert rack_return.target_face == "90"
        assert [created_request.kind.value for _, created_request in transport.created] == expected_kinds
        await _complete_current_transport(
            integration_session_factory,
            service,
            run.run_id,
            transport,
            storage_position="WH01-01",
        )
        assert await service.advance_run(run.run_id) is True
        assert (await service.get_run(run.run_id)).status == "COMPLETED"
    finally:
        await _cleanup(integration_session_factory, rack_id=rack_id, source_prefix=source_prefix)


async def test_restart_reuses_the_persisted_step_and_does_not_create_a_second_transport_task(
    integration_session_factory: Any,
) -> None:
    suffix = uuid.uuid4().hex[:8]
    source_prefix = f"restart-{suffix}"
    rack_id, request = _configuration(suffix, faces=("90",))
    first_transport = _PersistingTransport()
    first_service = _service(integration_session_factory, first_transport)
    try:
        run = await first_service.create_run(request, actor_id=7)
        async with integration_session_factory() as db:
            before = int(await db.scalar(select(func.count()).select_from(TransportTask)) or 0)

        restarted_transport = _PersistingTransport()
        restarted_service = _service(integration_session_factory, restarted_transport)
        assert await restarted_service.advance_run(run.run_id) is False
        async with integration_session_factory() as db:
            after = int(await db.scalar(select(func.count()).select_from(TransportTask)) or 0)

        assert after == before
        assert restarted_transport.created == []
    finally:
        await _cleanup(integration_session_factory, rack_id=rack_id, source_prefix=source_prefix)


async def test_only_one_debug_run_can_hold_the_global_active_scope(integration_session_factory: Any) -> None:
    suffix = uuid.uuid4().hex[:8]
    source_prefix = f"single-{suffix}"
    rack_id, request = _configuration(suffix, faces=("90",))
    service = _service(integration_session_factory, _PersistingTransport())
    try:
        await service.create_run(request, actor_id=7)
        with pytest.raises(TransportDebugRunConflict, match="active debug run"):
            await service.create_run(request, actor_id=8)
    finally:
        await _cleanup(integration_session_factory, rack_id=rack_id, source_prefix=source_prefix)


async def test_persisted_transport_callback_conflict_blocks_the_next_physical_step(
    integration_session_factory: Any,
) -> None:
    suffix = uuid.uuid4().hex[:8]
    source_prefix = f"transport-conflict-{suffix}"
    rack_id, request = _configuration(suffix, faces=("90",))
    transport = _PersistingTransport()
    service = _service(integration_session_factory, transport)
    await _cleanup(integration_session_factory, rack_id=rack_id, source_prefix=source_prefix)
    try:
        run = await service.create_run(request, actor_id=7)
        await _complete_current_transport(integration_session_factory, service, run.run_id, transport)
        current = await service.get_run(run.run_id)
        assert current.current_step is not None and current.current_step.transport_task_id is not None
        task_id = current.current_step.transport_task_id
        now = timezone.now_for_db()
        async with integration_session_factory.begin() as db:
            db.add(
                TransportEvidence(
                    operation_id=new_uuid7(),
                    transport_task_id=task_id,
                    operation=RESULT_OPERATION,
                    outcome_revision=2,
                    event_timestamp_ms=1,
                    message_digest="c" * 64,
                    payload_json={},
                    ack_timestamp_ms=1,
                    ack_data_json={},
                    status="CONFLICT",
                    received_at=now,
                    processed_at=now,
                    conflict_code="TRANSPORT_EVIDENCE_CONFLICT",
                )
            )

        assert await service.advance_run(run.run_id) is True
        blocked = await service.get_run(run.run_id)
        assert blocked.status == "NEEDS_ATTENTION"
        assert blocked.attention_code == "TRANSPORT_EVIDENCE_CONFLICT"
        assert blocked.current_phase == "RACK_TO_STATION"
        assert len(transport.created) == 1
    finally:
        await _cleanup(integration_session_factory, rack_id=rack_id, source_prefix=source_prefix)


async def test_delivery_unknown_holds_the_same_task_until_a_definite_result_arrives(
    integration_session_factory: Any,
) -> None:
    suffix = uuid.uuid4().hex[:8]
    source_prefix = f"unknown-{suffix}"
    rack_id, request = _configuration(suffix, faces=("90",))
    transport = _PersistingTransport()
    service = _service(integration_session_factory, transport)
    try:
        run = await service.create_run(request, actor_id=7)
        task_id = transport.created[-1][0]
        async with integration_session_factory.begin() as db:
            task = await db.scalar(select(TransportTask).where(TransportTask.transport_task_id == task_id))
            assert task is not None
            task.status = "RECONCILING"
            task.reason_code = "TRANSPORT_DELIVERY_UNKNOWN"
        assert await service.advance_run(run.run_id) is True
        attention = await service.get_run(run.run_id)
        assert attention.status == "NEEDS_ATTENTION"
        assert attention.attention_code == "TRANSPORT_DELIVERY_UNKNOWN"
        assert len(transport.created) == 1

        await _complete_current_transport(integration_session_factory, service, run.run_id, transport)
        assert await service.advance_run(run.run_id) is True
        recovered = await service.get_run(run.run_id)
        assert recovered.status == "RUNNING"
        assert recovered.current_phase == "BINS_TO_INFEED"
        assert len(transport.created) == 1
    finally:
        await _cleanup(integration_session_factory, rack_id=rack_id, source_prefix=source_prefix)


@pytest.mark.parametrize(
    ("arrival_face", "position_unknown"),
    [("wrong-face", False), (None, True)],
    ids=["face-mismatch", "position-unknown"],
)
async def test_ambiguous_rack_result_never_creates_the_next_task(
    integration_session_factory: Any,
    arrival_face: str | None,
    position_unknown: bool,
) -> None:
    suffix = uuid.uuid4().hex[:8]
    source_prefix = f"result-{suffix}"
    rack_id, request = _configuration(suffix, faces=("90",))
    transport = _PersistingTransport()
    service = _service(integration_session_factory, transport)
    try:
        run = await service.create_run(request, actor_id=7)
        await _complete_current_transport(
            integration_session_factory,
            service,
            run.run_id,
            transport,
            arrival_face_override=arrival_face,
            position_unknown=position_unknown,
        )
        assert await service.advance_run(run.run_id) is True
        snapshot = await service.get_run(run.run_id)
        assert snapshot.status == "NEEDS_ATTENTION"
        assert snapshot.current_phase == "RACK_TO_STATION"
        assert len(transport.created) == 1
    finally:
        await _cleanup(integration_session_factory, rack_id=rack_id, source_prefix=source_prefix)


async def test_reconciling_scan12_evidence_stops_before_bin_return(
    integration_session_factory: Any,
) -> None:
    suffix = uuid.uuid4().hex[:8]
    source_prefix = f"scan-{suffix}"
    rack_id, request = _configuration(suffix, faces=("90",))
    transport = _PersistingTransport()
    service = _service(integration_session_factory, transport)
    try:
        run = await service.create_run(request, actor_id=7)
        await _advance_to_scan_wait(integration_session_factory, service, transport, run.run_id)
        snapshot = await service.get_run(run.run_id)
        assert snapshot.current_step is not None and snapshot.current_step.evidence_not_before_ms is not None
        await _persist_scan12(
            integration_session_factory,
            source_event_id=f"{source_prefix}-reconciling",
            bin_id=request.face_groups[0].bins[0].bin_id,
            timestamp_ms=snapshot.current_step.evidence_not_before_ms,
            apply_status=InboundEvidenceApplyStatus.RECONCILING,
        )

        assert await service.advance_run(run.run_id) is True
        attention = await service.get_run(run.run_id)
        assert attention.status == "NEEDS_ATTENTION"
        assert attention.attention_code == "EVIDENCE_RECONCILING"
        assert len(transport.created) == 2
    finally:
        await _cleanup(integration_session_factory, rack_id=rack_id, source_prefix=source_prefix)


async def test_conflicting_scan12_evidence_stops_before_bin_return(
    integration_session_factory: Any,
) -> None:
    suffix = uuid.uuid4().hex[:8]
    source_prefix = f"scan-conflict-{suffix}"
    rack_id, request = _configuration(suffix, faces=("90",))
    transport = _PersistingTransport()
    service = _service(integration_session_factory, transport)
    try:
        run = await service.create_run(request, actor_id=7)
        await _advance_to_scan_wait(integration_session_factory, service, transport, run.run_id)
        snapshot = await service.get_run(run.run_id)
        assert snapshot.current_step is not None and snapshot.current_step.evidence_not_before_ms is not None
        for bin_index, selection in enumerate(request.face_groups[0].bins):
            await _persist_scan12(
                integration_session_factory,
                source_event_id=f"{source_prefix}-{bin_index}",
                bin_id=selection.bin_id,
                timestamp_ms=snapshot.current_step.evidence_not_before_ms,
            )
        async with integration_session_factory.begin() as db:
            first = await db.scalar(
                select(InboundEvidence).where(InboundEvidence.source_identity == f"{source_prefix}-0")
            )
            assert first is not None and first.id is not None
            db.add(
                InboundEvidenceConflict(
                    source_identity=first.source_identity,
                    first_evidence_id=first.id,
                    conflicting_digest="b" * 64,
                    normalized_payload={**first.normalized_payload, "data": {"barcode": "OTHER-BIN"}},
                    reason_code="SOURCE_IDENTITY_PAYLOAD_CONFLICT",
                    received_at=timezone.now_for_db(),
                )
            )

        assert await service.advance_run(run.run_id) is True
        attention = await service.get_run(run.run_id)
        assert attention.status == "NEEDS_ATTENTION"
        assert attention.attention_code == "EVIDENCE_SOURCE_EVENT_CONFLICT"
        assert len(transport.created) == 2
    finally:
        await _cleanup(integration_session_factory, rack_id=rack_id, source_prefix=source_prefix)


async def test_late_scan12_conflict_stops_after_bin_return_task_is_bound(
    integration_session_factory: Any,
) -> None:
    suffix = uuid.uuid4().hex[:8]
    source_prefix = f"scan-late-conflict-{suffix}"
    rack_id, request = _configuration(suffix, faces=("90",))
    transport = _PersistingTransport()
    service = _service(integration_session_factory, transport)
    try:
        run = await service.create_run(request, actor_id=7)
        await _advance_to_scan_wait(integration_session_factory, service, transport, run.run_id)
        snapshot = await service.get_run(run.run_id)
        assert snapshot.current_step is not None and snapshot.current_step.evidence_not_before_ms is not None
        for bin_index, selection in enumerate(request.face_groups[0].bins):
            await _persist_scan12(
                integration_session_factory,
                source_event_id=f"{source_prefix}-{bin_index}",
                bin_id=selection.bin_id,
                timestamp_ms=snapshot.current_step.evidence_not_before_ms,
            )
        assert await service.advance_run(run.run_id) is True
        assert await service.advance_run(run.run_id) is True
        bound = await service.get_run(run.run_id)
        assert bound.current_phase == "BINS_TO_RACK"
        assert bound.current_step is not None and bound.current_step.transport_task_id is not None

        async with integration_session_factory.begin() as db:
            first = await db.scalar(
                select(InboundEvidence).where(InboundEvidence.source_identity == f"{source_prefix}-0")
            )
            assert first is not None and first.id is not None
            db.add(
                InboundEvidenceConflict(
                    source_identity=first.source_identity,
                    first_evidence_id=first.id,
                    conflicting_digest="b" * 64,
                    normalized_payload={**first.normalized_payload, "data": {"barcode": "OTHER-BIN"}},
                    reason_code="SOURCE_IDENTITY_PAYLOAD_CONFLICT",
                    received_at=timezone.now_for_db(),
                )
            )

        assert await service.advance_run(run.run_id) is True
        attention = await service.get_run(run.run_id)
        assert attention.status == "NEEDS_ATTENTION"
        assert attention.attention_code == "EVIDENCE_SOURCE_EVENT_CONFLICT"
        assert len(transport.created) == 3
    finally:
        await _cleanup(integration_session_factory, rack_id=rack_id, source_prefix=source_prefix)
