"""Device evidence 接收与应用的可靠性边界。"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import datetime

import pytest

from src.app.device.contracts import DeviceEvidenceReceipt, EcsCommandResultReport, EcsDeviceEventReport
from src.app.device.models.command import CommandStatus, DeviceCommand
from src.app.device.services.device_evidence_service import (
    DeviceEvidenceConflictError,
    DeviceEvidenceService,
    DeviceResultConflictError,
    DeviceResultOutOfOrderError,
    UnknownDeviceCommandError,
)
from src.app.execution.models.inbound_evidence import (
    InboundEvidence,
    InboundEvidenceConflict,
    InboundEvidenceKind,
)
from src.app.execution.services.inbound_evidence_service import InboundEvidenceService
from src.utils.timezone import timezone


class FakeBegin(AbstractAsyncContextManager[object]):
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *_args: object) -> None:
        return None


class FakeSessionFactory:
    def begin(self) -> FakeBegin:
        return FakeBegin()


class FakeEvidenceRepository:
    def __init__(self) -> None:
        self.evidences: dict[str, InboundEvidence] = {}
        self.conflicts: list[InboundEvidenceConflict] = []
        self.identity_locks: list[str] = []
        self.next_id = 1

    async def lock_source_identity(self, _db: object, source_identity: str) -> None:
        self.identity_locks.append(source_identity)

    async def get_by_source_identity_for_update(self, _db: object, source_identity: str) -> InboundEvidence | None:
        return self.evidences.get(source_identity)

    async def get_device_result_for_command_for_update(self, _db: object, command_code: str) -> InboundEvidence | None:
        return next(
            (
                item
                for item in self.evidences.values()
                if item.command_code == command_code and getattr(item.kind, "value", item.kind) == "DEVICE_RESULT"
            ),
            None,
        )

    async def add(self, _db: object, evidence: InboundEvidence) -> InboundEvidence:
        evidence.id = self.next_id
        self.next_id += 1
        self.evidences[evidence.source_identity] = evidence
        return evidence

    async def add_conflict(self, _db: object, conflict: InboundEvidenceConflict) -> InboundEvidenceConflict:
        self.conflicts.append(conflict)
        return conflict

    async def claim_next_pending(
        self,
        _db: object,
        *,
        kinds: tuple[object, ...],
    ) -> InboundEvidence | None:
        return next(
            (item for item in self.evidences.values() if item.apply_status == "PENDING" and item.kind in kinds),
            None,
        )

    async def mark_applied(self, _db: object, evidence: InboundEvidence, *, processed_at: datetime) -> None:
        evidence.apply_status = "APPLIED"
        evidence.processed_at = processed_at

    async def mark_reconciling(self, _db: object, evidence: InboundEvidence, *, processed_at: datetime) -> None:
        evidence.apply_status = "RECONCILING"
        evidence.processed_at = processed_at


class FakeCommandRepository:
    def __init__(self, command: DeviceCommand | None) -> None:
        self.command = command

    async def get_by_command_code(
        self,
        _db: object,
        command_code: str,
        *,
        for_update: bool = False,
    ) -> DeviceCommand | None:
        if self.command is None or self.command.command_code != command_code:
            return None
        return self.command


class FakeEpochRepository:
    def __init__(self, event_epoch_id: int | None = None) -> None:
        self.event_epoch_id = event_epoch_id

    async def get_active_binding_for_device(self, _db: object, device_code: str):
        if self.event_epoch_id is None:
            return None
        return type(
            "Binding",
            (),
            {
                "line_run_epoch_id": self.event_epoch_id,
                "device_code": device_code,
                "contract_key": "arm.pick",
                "contract_version": "2.0",
            },
        )()


class FakeTaskQueue:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.execution_wakes = 0
        self.error = error

    def enqueue_execution_facts(self) -> None:
        self.execution_wakes += 1
        if self.error is not None:
            raise self.error


class FakePublisher:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.events: list[tuple[str, str, dict[str, object]]] = []
        self.error = error

    async def publish_to(self, channel: str, event_type: str, payload: dict[str, object]) -> bool:
        self.events.append((channel, event_type, payload))
        if self.error is not None:
            raise self.error
        return True


def _command() -> DeviceCommand:
    now = datetime(2026, 8, 13)
    return DeviceCommand(
        id=31,
        command_code="CMD-001",
        device_code="ARM-01",
        line_run_epoch_id=11,
        device_binding_id=21,
        execution_ref_type="MATERIAL_EXECUTION",
        execution_ref_id="EXEC-001",
        material_execution_id=21,
        contract_key="arm.pick",
        contract_version="2.0",
        task_type="PICK",
        params={},
        payload_digest="a" * 64,
        deadline_at=datetime(2026, 8, 13, 0, 1),
        status=CommandStatus.ACKNOWLEDGED,
        created_at=now,
        updated_at=now,
    )


def _result(**overrides: object) -> EcsCommandResultReport:
    payload: dict[str, object] = {
        "command_code": "CMD-001",
        "device_code": "ARM-01",
        "result": "SUCCESS",
        "finish_time": 1_786_579_204_000,
        "data": {},
        "error_detail": None,
    }
    payload.update(overrides)
    return EcsCommandResultReport.model_validate(payload)


def _event(**overrides: object) -> EcsDeviceEventReport:
    payload: dict[str, object] = {
        "device_code": "ARM-01",
        "event_type": "SCAN_COMPLETED",
        "timestamp": 1_786_579_204_000,
        "data": {"event_id": "EVENT-001", "location": "STATION_SCAN1", "barcode": "PKG12345678"},
    }
    payload.update(overrides)
    return EcsDeviceEventReport.model_validate(payload)


def _service(
    command: DeviceCommand | None,
    *,
    event_epoch_id: int | None = None,
    task_queue: FakeTaskQueue | None = None,
    publisher: FakePublisher | None = None,
) -> tuple[DeviceEvidenceService, FakeEvidenceRepository]:
    evidences = FakeEvidenceRepository()
    return (
        DeviceEvidenceService(
            session_factory=FakeSessionFactory(),  # type: ignore[arg-type]
            inbound_evidence_service=InboundEvidenceService(repository=evidences),
            processing_repository=evidences,  # type: ignore[arg-type]
            command_repository=FakeCommandRepository(command),  # type: ignore[arg-type]
            epoch_repository=FakeEpochRepository(event_epoch_id),  # type: ignore[arg-type]
            task_queue_gateway=task_queue,  # type: ignore[arg-type]
            event_publisher=publisher,  # type: ignore[arg-type]
        ),
        evidences,
    )


@pytest.mark.asyncio
async def test_result_is_persisted_before_receipt_and_duplicate_is_idempotent() -> None:
    service, repository = _service(_command())

    first = await service.accept_result(_result())
    duplicate = await service.accept_result(_result())

    assert first.evidence_id == duplicate.evidence_id
    assert first.duplicate is False
    assert duplicate.duplicate is True
    assert list(repository.evidences) == ["RESULT:CMD-001"]
    assert repository.identity_locks == ["RESULT:CMD-001", "RESULT:CMD-001"]
    assert repository.evidences["RESULT:CMD-001"].contract_key == "arm.pick"
    assert repository.evidences["RESULT:CMD-001"].contract_version == "2.0"
    assert repository.evidences["RESULT:CMD-001"].normalized_payload["finish_time"] == 1_786_579_204_000


@pytest.mark.asyncio
async def test_ingress_writes_only_through_inbound_evidence_application() -> None:
    application_repository = FakeEvidenceRepository()
    processing_repository = FakeEvidenceRepository()
    service = DeviceEvidenceService(
        session_factory=FakeSessionFactory(),  # type: ignore[arg-type]
        inbound_evidence_service=InboundEvidenceService(repository=application_repository),
        processing_repository=processing_repository,  # type: ignore[arg-type]
        command_repository=FakeCommandRepository(_command()),  # type: ignore[arg-type]
        epoch_repository=FakeEpochRepository(),  # type: ignore[arg-type]
    )

    receipt = await service.accept_result(_result())

    assert receipt.evidence_id == application_repository.evidences["RESULT:CMD-001"].id
    assert processing_repository.evidences == {}


@pytest.mark.asyncio
async def test_same_source_event_id_with_different_payload_is_conflict() -> None:
    service, repository = _service(_command())
    await service.accept_result(_result())

    with pytest.raises(DeviceEvidenceConflictError):
        await service.accept_result(_result(data={"position": "OTHER"}))

    assert repository.conflicts[0].reason_code == "SOURCE_IDENTITY_PAYLOAD_CONFLICT"


@pytest.mark.asyncio
async def test_one_command_accepts_only_one_result_payload() -> None:
    service, repository = _service(_command())
    await service.accept_result(_result())

    with pytest.raises(DeviceEvidenceConflictError):
        await service.accept_result(_result(finish_time=1_786_579_205_000))

    assert repository.conflicts[0].reason_code == "SOURCE_IDENTITY_PAYLOAD_CONFLICT"


@pytest.mark.asyncio
async def test_unknown_command_does_not_create_accepted_evidence() -> None:
    service, repository = _service(None)

    with pytest.raises(UnknownDeviceCommandError):
        await service.accept_result(_result())

    assert repository.evidences == {}


@pytest.mark.asyncio
async def test_unknown_command_can_be_retried_after_command_appears() -> None:
    service, repository = _service(None)
    with pytest.raises(UnknownDeviceCommandError):
        await service.accept_result(_result())

    service._commands.command = _command()
    receipt = await service.accept_result(_result())

    accepted = repository.evidences[receipt.source_event_id]
    assert accepted.apply_status == "PENDING"
    assert accepted.command_code == "CMD-001"
    assert accepted.line_run_epoch_id == 11


@pytest.mark.asyncio
async def test_result_identity_mismatch_is_frozen_before_rejection() -> None:
    service, repository = _service(_command())

    with pytest.raises(DeviceResultConflictError) as mismatch:
        await service.accept_result(_result(device_code="ARM-OTHER"))

    rejected = repository.evidences["RESULT:CMD-001"]
    assert rejected.apply_status == "IGNORED"
    assert rejected.line_run_epoch_id == 11
    assert mismatch.value.receipt == DeviceEvidenceReceipt(
        evidence_id=rejected.id,
        source_event_id="RESULT:CMD-001",
        duplicate=False,
        trace_id=None,
        apply_status="IGNORED",
    )
    with pytest.raises(DeviceEvidenceConflictError) as conflict:
        await service.accept_result(_result(device_code="ARM-THIRD"))
    assert conflict.value.receipt.evidence_id == rejected.id
    assert conflict.value.receipt.source_event_id == "RESULT:CMD-001"
    assert conflict.value.receipt.apply_status == "IGNORED"


@pytest.mark.asyncio
async def test_result_before_dispatch_fences_command_and_is_rejected() -> None:
    command = _command()
    command.status = CommandStatus.PENDING
    service, repository = _service(command)

    with pytest.raises(DeviceResultOutOfOrderError) as out_of_order:
        await service.accept_result(_result())

    rejected = repository.evidences["RESULT:CMD-001"]
    assert command.status == CommandStatus.RECONCILING
    assert command.reconciliation_reason == "RESULT_BEFORE_DISPATCH"
    assert rejected.apply_status == "IGNORED"
    assert rejected.command_code is None
    assert out_of_order.value.receipt == DeviceEvidenceReceipt(
        evidence_id=rejected.id,
        source_event_id="RESULT:CMD-001",
        duplicate=False,
        trace_id=None,
        apply_status="IGNORED",
    )

    with pytest.raises(DeviceResultOutOfOrderError) as duplicate:
        await service.accept_result(_result())

    assert duplicate.value.receipt == DeviceEvidenceReceipt(
        evidence_id=rejected.id,
        source_event_id="RESULT:CMD-001",
        duplicate=True,
        trace_id=None,
        apply_status="IGNORED",
    )
    assert len(repository.evidences) == 1


@pytest.mark.asyncio
async def test_event_freezes_nullable_epoch_on_first_observation() -> None:
    service, repository = _service(None)

    receipt = await service.accept_event(_event())

    assert repository.evidences[receipt.source_event_id].line_run_epoch_id is None
    assert repository.identity_locks == [receipt.source_event_id]
    assert receipt.source_event_id.startswith("EVENT:")
    assert repository.evidences[receipt.source_event_id].contract_key == "third_party_integration"
    assert repository.evidences[receipt.source_event_id].contract_version == "1.1"


@pytest.mark.asyncio
async def test_event_freezes_active_epoch_when_contract_matches() -> None:
    service, repository = _service(None, event_epoch_id=11)

    receipt = await service.accept_event(_event())

    assert repository.evidences[receipt.source_event_id].line_run_epoch_id == 11


@pytest.mark.asyncio
async def test_accepted_event_retry_after_epoch_switch_reuses_frozen_evidence() -> None:
    service, repository = _service(None, event_epoch_id=11)
    first = await service.accept_event(_event())

    service._epochs.event_epoch_id = 12
    duplicate = await service.accept_event(_event())

    assert duplicate.evidence_id == first.evidence_id
    assert duplicate.duplicate is True
    assert repository.evidences[duplicate.source_event_id].line_run_epoch_id == 11
    assert repository.conflicts == []


@pytest.mark.asyncio
async def test_event_contract_metadata_uses_active_wes_binding() -> None:
    service, repository = _service(None, event_epoch_id=11)

    receipt = await service.accept_event(_event())

    accepted = repository.evidences[receipt.source_event_id]
    assert accepted.apply_status == "PENDING"
    assert accepted.line_run_epoch_id == 11
    assert accepted.contract_key == "arm.pick"
    assert accepted.contract_version == "2.0"


@pytest.mark.asyncio
async def test_same_event_payload_remains_idempotent_after_original_epoch_closes() -> None:
    epochs = FakeEpochRepository(event_epoch_id=11)
    evidences = FakeEvidenceRepository()
    service = DeviceEvidenceService(
        session_factory=FakeSessionFactory(),  # type: ignore[arg-type]
        inbound_evidence_service=InboundEvidenceService(repository=evidences),
        processing_repository=evidences,  # type: ignore[arg-type]
        command_repository=FakeCommandRepository(None),  # type: ignore[arg-type]
        epoch_repository=epochs,  # type: ignore[arg-type]
    )
    event = _event()
    first = await service.accept_event(event)

    epochs.event_epoch_id = None
    duplicate = await service.accept_event(event)

    assert duplicate.evidence_id == first.evidence_id
    assert duplicate.duplicate is True
    assert evidences.evidences[first.source_event_id].line_run_epoch_id == 11
    assert evidences.evidences[first.source_event_id].contract_key == "arm.pick"
    assert evidences.evidences[first.source_event_id].contract_version == "2.0"


@pytest.mark.asyncio
async def test_result_evidence_is_only_authority_that_closes_acknowledged_command() -> None:
    command = _command()
    queue = FakeTaskQueue()
    service, repository = _service(command, task_queue=queue)
    receipt = await service.accept_result(_result())

    assert command.status == CommandStatus.ACKNOWLEDGED
    assert await service.process_one() is True
    assert command.status == CommandStatus.SUCCEEDED
    assert command.result_evidence_id == receipt.evidence_id
    persisted_evidence = repository.evidences[receipt.source_event_id]
    assert persisted_evidence.material_execution_id == command.material_execution_id
    assert persisted_evidence.apply_status == "APPLIED"
    assert queue.execution_wakes == 1


@pytest.mark.asyncio
async def test_applied_evidence_update_is_published_after_processing() -> None:
    command = _command()
    publisher = FakePublisher()
    service, repository = _service(command, publisher=publisher)
    receipt = await service.accept_result(_result())

    assert await service.process_one() is True

    assert repository.evidences[receipt.source_event_id].apply_status == "APPLIED"
    channel, event_type, payload = publisher.events[0]
    assert (channel, event_type) == ("device:evidence:stream", "device_evidence.updated")
    assert payload == {
        "evidence_id": receipt.evidence_id,
        "kind": "DEVICE_RESULT",
        "source_event_id": receipt.source_event_id,
        "device_code": "ARM-01",
        "command_code": "CMD-001",
        "event_type": None,
        "apply_status": "APPLIED",
        "processed_at": timezone.to_utc(repository.evidences[receipt.source_event_id].processed_at).isoformat(),
    }


@pytest.mark.asyncio
async def test_evidence_update_publish_failure_does_not_rollback_processing() -> None:
    command = _command()
    publisher = FakePublisher(error=RuntimeError("redis down"))
    service, repository = _service(command, publisher=publisher)
    receipt = await service.accept_result(_result())

    assert await service.process_one() is True
    assert repository.evidences[receipt.source_event_id].apply_status == "APPLIED"
    assert command.status == CommandStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_failed_result_normalizes_whitepaper_error_and_closes_failed() -> None:
    command = _command()
    service, repository = _service(command)

    receipt = await service.accept_result(
        _result(
            result="FAILED",
            error_detail={"code": "E-MOTOR-01", "msg": "Servo motor timeout"},
        )
    )

    normalized = repository.evidences[receipt.source_event_id].normalized_payload
    assert normalized["error_detail"] == {"code": "E-MOTOR-01", "message": "Servo motor timeout"}
    assert await service.process_one() is True
    assert command.status == CommandStatus.FAILED
    assert command.failure_code == "DEVICE_REPORTED_FAILURE"


@pytest.mark.asyncio
async def test_execution_wake_failure_does_not_rollback_applied_device_evidence() -> None:
    command = _command()
    queue = FakeTaskQueue(error=RuntimeError("queue unavailable"))
    service, repository = _service(command, task_queue=queue)
    receipt = await service.accept_result(_result())

    assert await service.process_one() is True

    assert repository.evidences[receipt.source_event_id].apply_status == "APPLIED"
    assert command.status == CommandStatus.SUCCEEDED
    assert queue.execution_wakes == 1


@pytest.mark.asyncio
async def test_foundation_result_closes_command_and_stays_applied_without_business_identity() -> None:
    command = _command()
    command.material_execution_id = None
    queue = FakeTaskQueue()
    service, repository = _service(command, task_queue=queue)
    receipt = await service.accept_result(_result())

    assert await service.process_one() is True

    evidence = repository.evidences[receipt.source_event_id]
    assert command.status == CommandStatus.SUCCEEDED
    assert evidence.apply_status == "APPLIED"
    assert evidence.material_execution_id is None
    assert evidence.published_at is None
    assert evidence.decision_digest is None
    assert queue.execution_wakes == 0


@pytest.mark.asyncio
async def test_result_without_optional_fields_keeps_omission_through_async_apply() -> None:
    command = _command()
    service, repository = _service(command)
    result = _result()

    receipt = await service.accept_result(result)

    assert "trace_id" not in repository.evidences[receipt.source_event_id].normalized_payload
    assert await service.process_one() is True
    assert command.status == CommandStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_result_evidence_can_close_dispatching_command_before_ack_writeback() -> None:
    command = _command()
    command.status = CommandStatus.DISPATCHING
    command.claim_token = "dispatch-claim"
    command.claimed_at = datetime(2026, 8, 13)
    command.claim_expires_at = datetime(2026, 8, 13, 0, 0, 30)
    service, repository = _service(command)
    receipt = await service.accept_result(_result())

    assert await service.process_one() is True
    assert command.status == CommandStatus.SUCCEEDED
    assert command.result_evidence_id == receipt.evidence_id
    assert command.claim_token is None
    assert command.claimed_at is None
    assert command.claim_expires_at is None
    assert repository.evidences[receipt.source_event_id].apply_status == "APPLIED"


@pytest.mark.asyncio
async def test_result_for_untrusted_command_state_enters_reconciliation_without_flipping_terminal() -> None:
    command = _command()
    command.status = CommandStatus.SUCCEEDED
    service, repository = _service(command)
    receipt = await service.accept_result(_result())

    assert await service.process_one() is True
    assert command.status == CommandStatus.SUCCEEDED
    assert repository.evidences[receipt.source_event_id].apply_status == "RECONCILING"


@pytest.mark.asyncio
async def test_reconciling_evidence_update_is_published_after_processing() -> None:
    command = _command()
    command.status = CommandStatus.SUCCEEDED
    publisher = FakePublisher()
    service, repository = _service(command, publisher=publisher)
    receipt = await service.accept_result(_result())

    assert await service.process_one() is True

    assert repository.evidences[receipt.source_event_id].apply_status == "RECONCILING"
    assert publisher.events[0][1] == "device_evidence.updated"
    assert publisher.events[0][2]["apply_status"] == "RECONCILING"


@pytest.mark.asyncio
async def test_device_evidence_worker_does_not_claim_wms_evidence() -> None:
    service, repository = _service(_command())
    repository.evidences["WMS-1"] = InboundEvidence(
        id=99,
        kind=InboundEvidenceKind.WMS_RESULT,
        source_identity="op:WMS-1",
        payload_digest="a" * 64,
        normalized_payload={"data": {}},
        received_at=datetime(2026, 8, 17),
        material_execution_id=21,
        operation="op",
        operation_id="WMS-1",
    )

    assert await service.process_one() is False
    assert repository.evidences["WMS-1"].apply_status == "PENDING"
