from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import delete, select

from src.app.device.contracts import EcsDeviceEventReport
from src.app.device.services.device_evidence_service import DeviceEvidenceService
from src.app.execution.models import InboundEvidence, InboundEvidenceApplyStatus, InboundEvidenceConflict
from src.app.transport import composition as transport_composition
from src.app.transport.contracts import (
    RackPosition,
    RackReference,
    RcsTemplateId,
    TransportCaller,
    TransportResourceConflict,
)
from src.app.transport.debug_run_contracts import (
    CreateTransportDebugRun,
    TransportDebugBinSelection,
    TransportDebugFaceGroup,
    TransportDebugRunPhase,
    TransportDebugRunStatus,
)
from src.app.transport.debug_run_repository import TransportDebugRunRepository
from src.app.transport.debug_run_service import TransportDebugRunConflict, TransportDebugRunService
from src.app.transport.models import (
    TransportCallbackReceipt,
    TransportDebugPositionProjection,
    TransportDebugRun,
    TransportDebugRunStep,
    TransportEvidence,
    TransportMember,
    TransportResourceBinding,
    TransportTask,
)
from src.app.wms_adapter.transport_wire import POSITION_OPERATION, RESULT_OPERATION
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone
from tests.support.transport_callbacks import record_valid_callback

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.app.transport.composition import TransportRuntime

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@dataclass
class _DeliveryState:
    value: str = "RESPONSE_RECEIVED"


@dataclass
class _AccessResult:
    delivery_state: _DeliveryState
    status_code: int
    json_body: dict[str, object]
    json_failure: str | None = None


class _AcceptedClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def post_json_bytes(self, path: str, *, body: bytes, **kwargs: object) -> _AccessResult:
        del path, kwargs
        message = json.loads(body)
        assert isinstance(message, dict)
        self.calls.append(message)
        data = message["data"]
        assert isinstance(data, dict)
        return _AccessResult(
            delivery_state=_DeliveryState(),
            status_code=202,
            json_body={
                "operation_id": message["operation_id"],
                "code": "RECEIVED",
                "timestamp": 1,
                "data": {"transport_task_id": data["transport_task_id"]},
            },
        )

    async def aclose(self) -> None:
        return None


class _EventPublisher:
    async def publish_to(self, channel: str, event_type: str, payload: dict[str, object]) -> bool:
        del channel, event_type, payload
        return True


def _callback_payload(
    phase: TransportDebugRunPhase,
    *,
    rack_id: str,
    bin_id: str,
    face: str,
    slot_id: str,
) -> dict[str, Any]:
    if phase is TransportDebugRunPhase.RACK_TO_STATION:
        return {
            "kind": "RACK_MOVE",
            "outcome_revision": 1,
            "rack_id": rack_id,
            "status": "SUCCEEDED",
            "final_position": {"kind": "RACK_POSITION", "location_code": "KT16"},
            "arrival_face": face,
        }
    if phase is TransportDebugRunPhase.BINS_TO_INFEED:
        return {
            "kind": "BIN_MOVE",
            "outcome_revision": 1,
            "results": [
                {
                    "container_id": bin_id,
                    "status": "SUCCEEDED",
                    "final_position": {"kind": "HANDOFF_POSITION", "location_code": "CNV0301"},
                }
            ],
        }
    if phase is TransportDebugRunPhase.BINS_TO_RACK:
        return {
            "kind": "BIN_MOVE",
            "outcome_revision": 1,
            "results": [
                {
                    "container_id": bin_id,
                    "status": "SUCCEEDED",
                    "final_position": {
                        "kind": "RACK_BIN_SLOT",
                        "rack_id": rack_id,
                        "rack_face": face,
                        "slot_id": slot_id,
                    },
                }
            ],
        }
    assert phase is TransportDebugRunPhase.RACK_TO_STORAGE
    return {
        "kind": "RACK_MOVE",
        "outcome_revision": 1,
        "rack_id": rack_id,
        "status": "SUCCEEDED",
        "final_position": {"kind": "RACK_POSITION", "location_code": "WH01-01"},
        "arrival_face": "90",
    }


async def _complete_transport_step(
    *,
    runtime: TransportRuntime,
    debug_run_service: TransportDebugRunService,
    client: _AcceptedClient,
    run_id: str,
    rack_id: str,
    bin_id: str,
    face: str,
    slot_id: str,
    callback_operation_ids: list[str],
    assert_semantic_duplicate: bool = False,
) -> None:
    before = await debug_run_service.get_run(run_id)
    phase = before.current_phase
    assert phase is not TransportDebugRunPhase.WAIT_SCAN12
    if before.current_step is None or before.current_step.transport_task_id is None:
        assert await debug_run_service.advance_run(run_id) is True

    waiting = await debug_run_service.get_run(run_id)
    assert waiting.current_step is not None
    assert waiting.current_step.transport_task_id is not None
    transport_task_id = waiting.current_step.transport_task_id
    submitted_before = len(client.calls)
    assert await runtime.service.submit_pending_tasks(1) == 1
    assert len(client.calls) == submitted_before + 1
    submitted_data = client.calls[-1]["data"]
    assert isinstance(submitted_data, dict)
    assert submitted_data["transport_task_id"] == transport_task_id

    payload = _callback_payload(
        phase,
        rack_id=rack_id,
        bin_id=bin_id,
        face=face,
        slot_id=slot_id,
    )
    operation_id = new_uuid7()
    callback_operation_ids.append(operation_id)
    callback_timestamp = int(timezone.now_utc().timestamp() * 1000)
    if phase is TransportDebugRunPhase.BINS_TO_RACK:
        early = await record_valid_callback(
            runtime.service,
            operation_id=operation_id,
            transport_task_id=transport_task_id,
            operation=RESULT_OPERATION,
            timestamp=callback_timestamp,
            payload=payload,
        )
        assert early == {
            "http_status": 409,
            "code": "CONFLICT",
            "timestamp": callback_timestamp,
            "data": {
                "transport_task_id": transport_task_id,
                "reason_code": "MEMBER_POSITION_EVIDENCE_PENDING",
            },
        }
        assert (await debug_run_service.get_run(run_id)).observed_bin_ids == ()
        results = payload["results"]
        assert isinstance(results, list)
        for result in results:
            assert isinstance(result, dict)
            position_operation_id = new_uuid7()
            callback_operation_ids.append(position_operation_id)
            position = await record_valid_callback(
                runtime.service,
                operation_id=position_operation_id,
                transport_task_id=transport_task_id,
                operation=POSITION_OPERATION,
                timestamp=callback_timestamp,
                payload={
                    "container_id": result["container_id"],
                    "milestone": "TARGET_PLACED",
                    "final_position": result["final_position"],
                },
            )
            assert position["code"] == "RECEIVED"
        assert await runtime.service.process_pending_evidence(10) == len(results)
        assert await debug_run_service.advance_run(run_id) is True
        progress = await debug_run_service.get_run(run_id)
        assert progress.current_phase is TransportDebugRunPhase.BINS_TO_RACK
        assert progress.observed_bin_ids == tuple(result["container_id"] for result in results)

    received = await record_valid_callback(
        runtime.service,
        operation_id=operation_id,
        transport_task_id=transport_task_id,
        operation=RESULT_OPERATION,
        timestamp=callback_timestamp,
        payload=payload,
    )
    assert received["code"] == "RECEIVED"

    if assert_semantic_duplicate:
        duplicate_operation_id = new_uuid7()
        callback_operation_ids.append(duplicate_operation_id)
        duplicate = await record_valid_callback(
            runtime.service,
            operation_id=duplicate_operation_id,
            transport_task_id=transport_task_id,
            operation=RESULT_OPERATION,
            timestamp=callback_timestamp,
            payload=payload,
        )
        assert duplicate["http_status"] == 200
        assert duplicate["code"] == "DUPLICATE"

    assert await runtime.service.process_pending_evidence(10) == 1
    assert await debug_run_service.advance_run(run_id) is True


async def _cleanup(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    rack_id: str,
    callback_operation_ids: list[str],
    inbound_evidence_ids: list[int],
) -> None:
    async with session_factory.begin() as db:
        run_ids = list(await db.scalars(select(TransportDebugRun.run_id).where(TransportDebugRun.rack_id == rack_id)))
        task_ids = set(
            list(
                await db.scalars(
                    select(TransportDebugRunStep.transport_task_id).where(
                        TransportDebugRunStep.run_id.in_(run_ids),
                        TransportDebugRunStep.transport_task_id.is_not(None),
                    )
                )
            )
            if run_ids
            else []
        )
        task_ids.update(
            await db.scalars(
                select(TransportMember.transport_task_id).where(
                    TransportMember.object_type == "RACK",
                    TransportMember.object_id == rack_id,
                )
            )
        )
        if callback_operation_ids:
            await db.execute(
                delete(TransportCallbackReceipt).where(
                    TransportCallbackReceipt.operation_id.in_(callback_operation_ids)
                )
            )
        if inbound_evidence_ids:
            await db.execute(
                delete(InboundEvidenceConflict).where(
                    InboundEvidenceConflict.first_evidence_id.in_(inbound_evidence_ids)
                )
            )
            await db.execute(delete(InboundEvidence).where(InboundEvidence.id.in_(inbound_evidence_ids)))
        if task_ids:
            await db.execute(delete(TransportEvidence).where(TransportEvidence.transport_task_id.in_(task_ids)))
            await db.execute(
                delete(TransportResourceBinding).where(TransportResourceBinding.transport_task_id.in_(task_ids))
            )
            await db.execute(
                delete(TransportDebugPositionProjection).where(
                    TransportDebugPositionProjection.source_transport_task_id.in_(task_ids)
                )
            )
            await db.execute(delete(TransportMember).where(TransportMember.transport_task_id.in_(task_ids)))
        if run_ids:
            await db.execute(delete(TransportDebugRunStep).where(TransportDebugRunStep.run_id.in_(run_ids)))
            await db.execute(delete(TransportDebugRun).where(TransportDebugRun.run_id.in_(run_ids)))
        if task_ids:
            await db.execute(delete(TransportTask).where(TransportTask.transport_task_id.in_(task_ids)))


async def test_single_face_real_transport_callbacks_and_scan12_complete_the_debug_run(
    integration_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.app.wms_adapter import factory

    suffix = uuid.uuid4().hex
    rack_id = f"rack-real-loop-{suffix}"
    bin_id = f"bin-real-loop-{suffix}"
    slot_id = f"slot-real-loop-{suffix}"
    face = "90"
    client = _AcceptedClient()
    callback_operation_ids: list[str] = []
    inbound_evidence_ids: list[int] = []
    runtime: TransportRuntime | None = None
    monkeypatch.setattr(factory, "build_wms_client", lambda **_kwargs: client)

    try:
        runtime = await transport_composition.build_transport_runtime(
            wms_base_url="http://wms.example",
            transport_submit_path="/api/v1/wes/transport-requests",
            session_factory=integration_session_factory,
        )
        debug_run_service = TransportDebugRunService(
            integration_session_factory,
            TransportDebugRunRepository(),
            runtime.service,
            event_publisher=_EventPublisher(),
        )
        request = CreateTransportDebugRun(
            rack_id=rack_id,
            face_groups=(
                TransportDebugFaceGroup(
                    face=face,
                    bins=(TransportDebugBinSelection(bin_id=bin_id, slot_id=slot_id),),
                ),
            ),
        )
        run = await debug_run_service.create_run(request, actor_id=7)

        await _complete_transport_step(
            runtime=runtime,
            debug_run_service=debug_run_service,
            client=client,
            run_id=run.run_id,
            rack_id=rack_id,
            bin_id=bin_id,
            face=face,
            slot_id=slot_id,
            callback_operation_ids=callback_operation_ids,
            assert_semantic_duplicate=True,
        )
        await _complete_transport_step(
            runtime=runtime,
            debug_run_service=debug_run_service,
            client=client,
            run_id=run.run_id,
            rack_id=rack_id,
            bin_id=bin_id,
            face=face,
            slot_id=slot_id,
            callback_operation_ids=callback_operation_ids,
        )

        scan_wait = await debug_run_service.get_run(run.run_id)
        assert scan_wait.current_phase is TransportDebugRunPhase.WAIT_SCAN12
        assert scan_wait.current_step is not None
        assert scan_wait.current_step.evidence_not_before_ms is not None
        event_timestamp = max(
            scan_wait.current_step.evidence_not_before_ms,
            int(timezone.now_utc().timestamp() * 1000),
        )
        device_evidence_service = DeviceEvidenceService(session_factory=integration_session_factory)
        receipt = await device_evidence_service.accept_event(
            EcsDeviceEventReport.model_validate(
                {
                    "device_code": "SCAN12",
                    "event_type": "SCAN_COMPLETED",
                    "timestamp": event_timestamp,
                    "data": {"barcode": bin_id},
                }
            )
        )
        inbound_evidence_ids.append(receipt.evidence_id)
        assert receipt.apply_status == InboundEvidenceApplyStatus.PENDING.value
        assert await device_evidence_service.process_one() is True
        async with integration_session_factory() as db:
            applied = await db.get(InboundEvidence, receipt.evidence_id)
            assert applied is not None
            assert applied.apply_status == InboundEvidenceApplyStatus.APPLIED

        assert await debug_run_service.advance_run(run.run_id) is True
        assert (await debug_run_service.get_run(run.run_id)).current_phase is TransportDebugRunPhase.BINS_TO_RACK

        await _complete_transport_step(
            runtime=runtime,
            debug_run_service=debug_run_service,
            client=client,
            run_id=run.run_id,
            rack_id=rack_id,
            bin_id=bin_id,
            face=face,
            slot_id=slot_id,
            callback_operation_ids=callback_operation_ids,
        )
        before_return = await debug_run_service.get_run(run.run_id)
        assert before_return.current_phase is TransportDebugRunPhase.RACK_TO_STORAGE

        await _complete_transport_step(
            runtime=runtime,
            debug_run_service=debug_run_service,
            client=client,
            run_id=run.run_id,
            rack_id=rack_id,
            bin_id=bin_id,
            face=face,
            slot_id=slot_id,
            callback_operation_ids=callback_operation_ids,
        )
        completed = await debug_run_service.get_run(run.run_id)
        assert completed.status is TransportDebugRunStatus.COMPLETED
        assert completed.current_phase is TransportDebugRunPhase.RACK_TO_STORAGE
        assert len(client.calls) == 4
        final_request = client.calls[-1]["data"]
        assert isinstance(final_request, dict)
        assert final_request["kind"] == "RACK_MOVE"
        assert final_request["rcs_template_id"] == "CTU03"
    finally:
        if runtime is not None:
            await runtime.aclose()
        await _cleanup(
            integration_session_factory,
            rack_id=rack_id,
            callback_operation_ids=callback_operation_ids,
            inbound_evidence_ids=inbound_evidence_ids,
        )


async def test_existing_rack_task_prevents_auto_run_creation_atomically(
    integration_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.app.wms_adapter import factory

    rack_id = f"rack-existing-task-{uuid.uuid4().hex}"
    client = _AcceptedClient()
    runtime: TransportRuntime | None = None
    monkeypatch.setattr(factory, "build_wms_client", lambda **_kwargs: client)

    try:
        runtime = await transport_composition.build_transport_runtime(
            wms_base_url="http://wms.example",
            transport_submit_path="/api/v1/wes/transport-requests",
            session_factory=integration_session_factory,
        )
        await runtime.service.move_rack(
            new_uuid7(),
            TransportCaller("SORTER", "STATION-A"),
            rack_id,
            RackReference(rack_id),
            RackPosition("OTHER-STATION"),
            "90",
            RcsTemplateId.CTU01,
        )
        debug_run_service = TransportDebugRunService(
            integration_session_factory,
            TransportDebugRunRepository(),
            runtime.service,
            event_publisher=_EventPublisher(),
        )
        request = CreateTransportDebugRun(
            rack_id=rack_id,
            face_groups=(
                TransportDebugFaceGroup(
                    face="90",
                    bins=(TransportDebugBinSelection(bin_id="BIN-1", slot_id="SLOT-1"),),
                ),
            ),
        )

        with pytest.raises(TransportDebugRunConflict, match="resource is already active"):
            await debug_run_service.create_run(request, actor_id=7)

        async with integration_session_factory() as db:
            assert await db.scalar(select(TransportDebugRun).where(TransportDebugRun.rack_id == rack_id)) is None
    finally:
        if runtime is not None:
            await runtime.aclose()
        await _cleanup(
            integration_session_factory,
            rack_id=rack_id,
            callback_operation_ids=[],
            inbound_evidence_ids=[],
        )


async def test_concurrent_rack_task_and_auto_run_have_exactly_one_owner(
    integration_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.app.wms_adapter import factory

    rack_id = f"rack-concurrent-owner-{uuid.uuid4().hex}"
    client = _AcceptedClient()
    runtime: TransportRuntime | None = None
    monkeypatch.setattr(factory, "build_wms_client", lambda **_kwargs: client)

    try:
        runtime = await transport_composition.build_transport_runtime(
            wms_base_url="http://wms.example",
            transport_submit_path="/api/v1/wes/transport-requests",
            session_factory=integration_session_factory,
        )
        debug_run_service = TransportDebugRunService(
            integration_session_factory,
            TransportDebugRunRepository(),
            runtime.service,
            event_publisher=_EventPublisher(),
        )
        request = CreateTransportDebugRun(
            rack_id=rack_id,
            face_groups=(
                TransportDebugFaceGroup(
                    face="90",
                    bins=(TransportDebugBinSelection(bin_id="BIN-1", slot_id="SLOT-1"),),
                ),
            ),
        )

        results = await asyncio.gather(
            runtime.service.move_rack(
                new_uuid7(),
                TransportCaller("SORTER", "STATION-A"),
                rack_id,
                RackReference(rack_id),
                RackPosition("OTHER-STATION"),
                "90",
                RcsTemplateId.CTU01,
            ),
            debug_run_service.create_run(request, actor_id=7),
            return_exceptions=True,
        )

        failures = [result for result in results if isinstance(result, BaseException)]
        assert len(failures) == 1
        assert isinstance(failures[0], (TransportDebugRunConflict, TransportResourceConflict))
        async with integration_session_factory() as db:
            active_bindings = list(
                await db.scalars(
                    select(TransportResourceBinding).where(
                        TransportResourceBinding.resource_type == "RACK",
                        TransportResourceBinding.resource_id == rack_id,
                        TransportResourceBinding.released_at.is_(None),
                    )
                )
            )
        assert len(active_bindings) == 1
    finally:
        if runtime is not None:
            await runtime.aclose()
        await _cleanup(
            integration_session_factory,
            rack_id=rack_id,
            callback_operation_ids=[],
            inbound_evidence_ids=[],
        )
