"""Device evidence 接收与应用的可靠性边界。"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import datetime

import pytest

from src.app.device.contracts import EcsCommandResult, EcsDeviceEvent
from src.app.device.models.command import CommandStatus, DeviceCommand
from src.app.device.models.evidence import DeviceEvidence, DeviceEvidenceConflict  # noqa: TC001
from src.app.device.services.device_evidence_service import (
    DeviceEventContractMismatchError,
    DeviceEvidenceConflictError,
    DeviceEvidenceService,
    DeviceResultConflictError,
    UnknownDeviceCommandError,
)


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
        self.evidences: dict[str, DeviceEvidence] = {}
        self.conflicts: list[DeviceEvidenceConflict] = []
        self.identity_locks: list[str] = []
        self.next_id = 1

    async def lock_source_event_id(self, _db: object, source_event_id: str) -> None:
        self.identity_locks.append(source_event_id)

    async def get_by_source_event_id_for_update(self, _db: object, source_event_id: str) -> DeviceEvidence | None:
        return self.evidences.get(source_event_id)

    async def get_result_for_command_for_update(self, _db: object, command_code: str) -> DeviceEvidence | None:
        return next(
            (
                item
                for item in self.evidences.values()
                if item.command_code == command_code and getattr(item.kind, "value", item.kind) == "RESULT"
            ),
            None,
        )

    async def add(self, _db: object, evidence: DeviceEvidence) -> DeviceEvidence:
        evidence.id = self.next_id
        self.next_id += 1
        self.evidences[evidence.source_event_id] = evidence
        return evidence

    async def add_conflict(self, _db: object, conflict: DeviceEvidenceConflict) -> DeviceEvidenceConflict:
        self.conflicts.append(conflict)
        return conflict

    async def claim_next_pending(self, _db: object) -> DeviceEvidence | None:
        return next((item for item in self.evidences.values() if item.apply_status == "PENDING"), None)

    async def mark_applied(self, _db: object, evidence: DeviceEvidence, *, processed_at: datetime) -> None:
        evidence.apply_status = "APPLIED"
        evidence.processed_at = processed_at

    async def mark_reconciling(self, _db: object, evidence: DeviceEvidence, *, processed_at: datetime) -> None:
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


def _result(**overrides: object) -> EcsCommandResult:
    payload: dict[str, object] = {
        "command_code": "CMD-001",
        "device_code": "ARM-01",
        "contract_key": "arm.pick",
        "contract_version": "2.0",
        "result": "SUCCESS",
        "finish_time": 1_786_579_204_000,
        "source_event_id": "RESULT-001",
        "data": {},
        "error_detail": None,
        "trace_id": "TRACE-001",
    }
    payload.update(overrides)
    return EcsCommandResult.model_validate(payload)


def _event(**overrides: object) -> EcsDeviceEvent:
    payload: dict[str, object] = {
        "device_code": "ARM-01",
        "contract_key": "arm.pick",
        "contract_version": "2.0",
        "event_type": "DEVICE_CONTRACT_EVENT",
        "timestamp": 1_786_579_204_000,
        "source_event_id": "EVENT-001",
        "data": {},
        "trace_id": "TRACE-001",
    }
    payload.update(overrides)
    return EcsDeviceEvent.model_validate(payload)


def _service(
    command: DeviceCommand | None,
    *,
    event_epoch_id: int | None = None,
) -> tuple[DeviceEvidenceService, FakeEvidenceRepository]:
    evidences = FakeEvidenceRepository()
    return (
        DeviceEvidenceService(
            session_factory=FakeSessionFactory(),  # type: ignore[arg-type]
            evidence_repository=evidences,  # type: ignore[arg-type]
            command_repository=FakeCommandRepository(command),  # type: ignore[arg-type]
            epoch_repository=FakeEpochRepository(event_epoch_id),  # type: ignore[arg-type]
        ),
        evidences,
    )


@pytest.mark.asyncio
async def test_result_is_persisted_before_receipt_and_duplicate_is_idempotent() -> None:
    service, repository = _service(_command())

    first = await service.accept_result(_result())
    duplicate = await service.accept_result(_result(trace_id="TRACE-RETRY"))

    assert first.evidence_id == duplicate.evidence_id
    assert first.duplicate is False
    assert duplicate.duplicate is True
    assert list(repository.evidences) == ["RESULT-001"]
    assert repository.identity_locks == ["RESULT-001", "RESULT-001"]


@pytest.mark.asyncio
async def test_same_source_event_id_with_different_payload_is_conflict() -> None:
    service, repository = _service(_command())
    await service.accept_result(_result())

    with pytest.raises(DeviceEvidenceConflictError):
        await service.accept_result(_result(data={"position": "OTHER"}))

    assert repository.conflicts[0].reason_code == "SOURCE_EVENT_ID_PAYLOAD_CONFLICT"


@pytest.mark.asyncio
async def test_one_command_accepts_only_one_result_identity() -> None:
    service, repository = _service(_command())
    await service.accept_result(_result())

    with pytest.raises(DeviceResultConflictError):
        await service.accept_result(_result(source_event_id="RESULT-002"))

    assert repository.conflicts[0].reason_code == "COMMAND_RESULT_CONFLICT"
    assert repository.evidences["RESULT-002"].apply_status == "IGNORED"


@pytest.mark.asyncio
async def test_unknown_command_does_not_create_accepted_evidence() -> None:
    service, repository = _service(None)

    with pytest.raises(UnknownDeviceCommandError):
        await service.accept_result(_result())

    rejected = repository.evidences["RESULT-001"]
    assert rejected.apply_status == "IGNORED"
    assert rejected.command_code is None

    with pytest.raises(DeviceEvidenceConflictError):
        await service.accept_result(_result(data={"changed": True}))


@pytest.mark.asyncio
async def test_unknown_command_rejection_cannot_rebind_when_command_appears() -> None:
    service, repository = _service(None)
    with pytest.raises(UnknownDeviceCommandError):
        await service.accept_result(_result())

    service._commands.command = _command()
    with pytest.raises(UnknownDeviceCommandError):
        await service.accept_result(_result())

    rejected = repository.evidences["RESULT-001"]
    assert rejected.apply_status == "IGNORED"
    assert rejected.command_code is None
    assert rejected.line_run_epoch_id is None


@pytest.mark.asyncio
async def test_result_identity_mismatch_is_frozen_before_rejection() -> None:
    service, repository = _service(_command())

    with pytest.raises(DeviceResultConflictError):
        await service.accept_result(_result(device_code="ARM-OTHER"))

    rejected = repository.evidences["RESULT-001"]
    assert rejected.apply_status == "IGNORED"
    assert rejected.line_run_epoch_id == 11
    with pytest.raises(DeviceEvidenceConflictError):
        await service.accept_result(_result(device_code="ARM-THIRD"))


@pytest.mark.asyncio
async def test_event_freezes_nullable_epoch_on_first_observation() -> None:
    service, repository = _service(None)

    receipt = await service.accept_event(_event())

    assert repository.evidences[receipt.source_event_id].line_run_epoch_id is None
    assert repository.identity_locks == ["EVENT-001"]


@pytest.mark.asyncio
async def test_event_freezes_active_epoch_when_contract_matches() -> None:
    service, repository = _service(None, event_epoch_id=11)

    receipt = await service.accept_event(_event())

    assert repository.evidences[receipt.source_event_id].line_run_epoch_id == 11


@pytest.mark.asyncio
async def test_event_contract_mismatch_is_frozen_before_rejection() -> None:
    service, repository = _service(None, event_epoch_id=11)

    with pytest.raises(DeviceEventContractMismatchError):
        await service.accept_event(_event(contract_version="3.0"))

    rejected = repository.evidences["EVENT-001"]
    assert rejected.apply_status == "IGNORED"
    assert rejected.line_run_epoch_id == 11
    with pytest.raises(DeviceEvidenceConflictError):
        await service.accept_event(_event(contract_version="4.0"))


@pytest.mark.asyncio
async def test_rejected_event_cannot_rebind_after_original_epoch_closes() -> None:
    epochs = FakeEpochRepository(event_epoch_id=11)
    evidences = FakeEvidenceRepository()
    service = DeviceEvidenceService(
        session_factory=FakeSessionFactory(),  # type: ignore[arg-type]
        evidence_repository=evidences,  # type: ignore[arg-type]
        command_repository=FakeCommandRepository(None),  # type: ignore[arg-type]
        epoch_repository=epochs,  # type: ignore[arg-type]
    )
    event = _event(contract_version="3.0")
    with pytest.raises(DeviceEventContractMismatchError):
        await service.accept_event(event)

    epochs.event_epoch_id = None
    with pytest.raises(DeviceEventContractMismatchError):
        await service.accept_event(event)

    assert evidences.evidences["EVENT-001"].line_run_epoch_id == 11


@pytest.mark.asyncio
async def test_result_evidence_is_only_authority_that_closes_acknowledged_command() -> None:
    command = _command()
    service, repository = _service(command)
    receipt = await service.accept_result(_result())

    assert command.status == CommandStatus.ACKNOWLEDGED
    assert await service.process_one() is True
    assert command.status == CommandStatus.SUCCEEDED
    assert command.result_evidence_id == receipt.evidence_id
    assert repository.evidences[receipt.source_event_id].apply_status == "APPLIED"


@pytest.mark.asyncio
async def test_result_without_optional_fields_keeps_omission_through_async_apply() -> None:
    command = _command()
    service, repository = _service(command)
    result = EcsCommandResult.model_validate(
        {key: value for key, value in _result().model_dump(mode="json").items() if key != "trace_id"}
    )

    receipt = await service.accept_result(result)

    assert "trace_id" not in repository.evidences[receipt.source_event_id].raw_payload
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
